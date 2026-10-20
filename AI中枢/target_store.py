"""One encrypted asset repository per competition, with recoverable v1 migration."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from .core import atomic_json,protect
from .storage import atomic_bytes,exclusive_lock
from .targets import FIELDS,SECRETS,endpoint_key,normalize,differing_fields

MAX_TARGETS=5000


def _encode(row):
    return protect(json.dumps(row,ensure_ascii=False).encode('utf-8'))


def _decode(value):
    try:
        row=json.loads(protect(bytes(value),True))
        if not isinstance(row,dict):raise ValueError()
        return row
    except Exception:raise ValueError('机子记录无法解密或已损坏，原数据已保留') from None


def _historical(row,index):
    if not isinstance(row,dict):raise ValueError(f'第 {index} 条历史机子应为对象；原数据已保留')
    known={key:value for key,value in row.items() if key in FIELDS}
    for key in ('port','ssh_port','ssh_user','shell'):
        if known.get(key)=='':known.pop(key)
    normalized=normalize(known,index)
    # Unrecognized historical metadata is retained under encryption, never used
    # to set repository identity, provenance, revision, or local permission.
    extra={key:value for key,value in row.items() if key not in FIELDS and key not in
           ('id','source','allowed','conflict','revision','needs_reauthorization','requires_local_selection')}
    return {**extra,**normalized}


def _states(connection):
    return {row['name']:json.loads(row['details']) for row in connection.execute('SELECT name,details FROM target_migrations')}


def migrate(store):
    """Copy old sources once; leave originals intact and verify an encrypted backup first."""
    legacy=store.root/'管理工具'/'machines.json'
    with store.connect() as c:
        state=_states(c)
    if 'mcp_v1' in state and ('legacy_v1' in state or not legacy.exists()):return state
    owner_path=legacy.with_name('.godhand-targets-owner.json')
    with exclusive_lock(legacy.with_name('.godhand-targets-migration.lock')):
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE');state=_states(c)
            additions=[];sources={};pending={};claim=False;now=time.time()
            if 'mcp_v1' not in state:
                originals=[]
                for index,entry in enumerate(c.execute('SELECT * FROM mcp_targets ORDER BY created,id'),1):
                    row=_historical(_decode(entry['record']),index)
                    identity=entry['id']
                    if not isinstance(identity,str) or not identity or identity=='local' or len(identity)>128:
                        raise ValueError('历史 MCP 目标 ID 格式不正确；原数据已保留')
                    additions.append((identity,'mcp',row,float(entry['created'])))
                    originals.append({'id':identity,'endpoint_key':entry['endpoint_key'],
                                      'record':base64.b64encode(bytes(entry['record'])).decode('ascii'),'created':entry['created']})
                sources['mcp_v1']=originals;pending['mcp_v1']={'status':'complete','count':len(originals)}
            if 'legacy_v1' not in state and legacy.exists():
                try:
                    owner=json.loads(owner_path.read_text(encoding='utf-8')) if owner_path.exists() else None
                    if owner is not None and (not isinstance(owner,dict) or not isinstance(owner.get('data_dir'),str)
                                              or not Path(owner['data_dir']).is_absolute()):raise ValueError()
                except (OSError,ValueError):raise ValueError('旧机子迁移归属记录损坏，原数据已保留') from None
                if owner and os.path.normcase(str(Path(owner['data_dir']).resolve()))!=os.path.normcase(str(store.path)):
                    pending['legacy_v1']={'status':'another_competition','count':0}
                else:
                    raw=legacy.read_bytes()
                    if len(raw)>16*1024*1024:raise ValueError('旧机子文件超过 16 MiB；原文件已保留')
                    try:
                        rows=json.loads(raw.decode('utf-8-sig'))
                        if isinstance(rows,dict):rows=rows.get('machines',list(rows.values()))
                        if not isinstance(rows,list):raise ValueError()
                    except (ValueError,UnicodeError):raise ValueError('旧机子文件格式有误，原文件已保留，迁移未写入') from None
                    ids={}
                    for index,item in enumerate(rows,1):
                        row=_historical(item,index)
                        row['legacy_aliases']=[value for value in (item.get('ip'),item.get('name')) if isinstance(value,str) and value]
                        row.pop('legacy_aliases_retired',None)
                        old_key='|'.join(str(item.get(key,'')) for key in ('ip','port','ssh_port','ssh_user','shell'))
                        base=hashlib.sha256(old_key.encode()).hexdigest()[:16]
                        ids[base]=ids.get(base,0)+1
                        identity=base if ids[base]==1 else f'{base}_{ids[base]}'
                        additions.append((identity,'legacy',row,now+index/1000000))
                    sources['legacy_v1']={'path':str(legacy),'bytes':base64.b64encode(raw).decode('ascii')}
                    pending['legacy_v1']={'status':'complete','count':len(rows),'sha256':hashlib.sha256(raw).hexdigest()}
                    claim=owner is None
            if c.execute('SELECT COUNT(*) FROM targets').fetchone()[0]+len(additions)>MAX_TARGETS:
                raise ValueError('迁移后的机子记录超过 5000 条；原数据已保留')
            prepared=[(identity,source,row,created,_encode(row)) for identity,source,row,created in additions]
            backup=None
            if additions or sources.get('legacy_v1'):
                payload={'version':1,'created':now,'sources':sources,
                         'records':[{'id':identity,'source':source,'record':row,'created':created}
                                    for identity,source,row,created in additions]}
                raw_backup=json.dumps(payload,ensure_ascii=False).encode('utf-8')
                backup=Path('backups')/('targets-pre-unified-'+uuid.uuid4().hex+'.dpapi')
                atomic_bytes(store.path/backup,protect(raw_backup))
                if protect((store.path/backup).read_bytes(),True)!=raw_backup:raise RuntimeError('迁移备份校验失败；原数据未改动')
            for identity,source,row,created,encrypted in prepared:
                existing=c.execute('SELECT source,record FROM targets WHERE id=?',(identity,)).fetchone()
                if existing:
                    if existing['source']!=source or _decode(existing['record'])!=row:
                        raise ValueError('历史目标 ID 与现有记录冲突；迁移未写入')
                    continue
                c.execute('INSERT INTO targets(id,endpoint_key,source,record,created,updated) VALUES(?,?,?,?,?,?)',
                          (identity,endpoint_key(row),source,encrypted,created,now))
            if claim:
                atomic_json(owner_path,{'version':1,'data_dir':str(store.path),'source_sha256':pending['legacy_v1']['sha256']})
            for name,details in pending.items():
                if backup:details['backup']=backup.as_posix()
                c.execute('INSERT INTO target_migrations(name,details) VALUES(?,?)',(name,json.dumps(details,ensure_ascii=False)))
                state[name]=details
            return state


def _wire(entry):
    row=_decode(entry['record'])
    row.update(id=entry['id'],source=entry['source'],revision=entry['revision'],
               requires_local_selection=bool(entry['needs_reauthorization']))
    return row


def records(store,source=None):
    migrate(store)
    with store.connect() as c:
        query='SELECT * FROM targets';args=()
        if source is not None:query+=' WHERE source=?';args=(source,)
        entries=c.execute(query+' ORDER BY created,id',args).fetchall()
    return [_wire(entry) for entry in entries]


def import_targets(store,items,on_duplicate='skip',source='mcp'):
    if source not in ('mcp','desktop'):raise ValueError('不支持的机子来源')
    if on_duplicate not in ('skip','error'):raise ValueError('on_duplicate 应为 skip 或 error')
    if not isinstance(items,list) or not 1<=len(items)<=500:raise ValueError('每次导入 1..500 条靶机记录')
    try:size=len(json.dumps(items,ensure_ascii=False).encode('utf-8'))
    except (TypeError,ValueError):raise ValueError('靶机记录必须是 JSON 对象列表') from None
    if size>1024*1024:raise ValueError('单次靶机导入不能超过 1 MiB')
    prepared=[]
    for index,item in enumerate(items,1):
        row=normalize(item,index);prepared.append((row,endpoint_key(row),_encode(row)))
    migrate(store);results=[];added=skipped=0
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE');existing={};count=0
        for entry in c.execute('SELECT * FROM targets ORDER BY created,id'):
            existing.setdefault(entry['endpoint_key'],[]).append(_wire(entry));count+=1
        for index,(row,key,encrypted) in enumerate(prepared,1):
            matches=existing.get(key,[])
            if matches:
                if on_duplicate=='error':raise ValueError(f'第 {index} 条靶机连接已存在；整批未写入')
                prior=min(matches,key=lambda item:len(differing_fields(item,row)))
                identity=prior['id'];status='skipped';skipped+=1
                differences=differing_fields(prior,row);revision=prior['revision'];target=prior
            else:
                if count>=MAX_TARGETS:raise ValueError('机子记录已达 5000 条上限；整批未写入')
                # Deleted authorization IDs must never bind to a future import.
                identity=('mcp_' if source=='mcp' else 'target_')+uuid.uuid4().hex
                now=time.time()
                c.execute('INSERT INTO targets(id,endpoint_key,source,record,created,updated) VALUES(?,?,?,?,?,?)',
                          (identity,key,source,encrypted,now,now))
                target=dict(row,id=identity,source=source,revision=1,requires_local_selection=False)
                existing[key]=[target];count+=1;added+=1;status='imported';differences=[];revision=1
            results.append({'index':index,'id':identity,'status':status,'revision':revision,
                            'allowed':store.target_is_allowed(target),'differences':differences,
                            'matching_ids':[item['id'] for item in matches]})
    auto=bool(store.reload_config().get('auto_authorize_imports'))
    if auto and added:
        permitted=[item['id'] for item in results if item['status']=='imported']
        allowed=store.reload_config()['allowed_targets']
        store.save_config({'allowed_targets':allowed+[identity for identity in permitted if identity not in allowed]})
        for item in results:
            if item['status']=='imported':item['allowed']=True
    message=('已按本机设置自动授权新导入记录；运行前仍核对连接快照' if auto and added
             else '导入不授予权限；请在本机 AI 中枢按 ID 选择允许目标')
    return {'imported':added,'skipped':skipped,'targets':results,'authorization':message}


def update_target(store,identity,changes,expected_revision=None):
    if not isinstance(changes,dict) or not changes:raise ValueError('changes 应为非空字段对象')
    if set(changes)-FIELDS:raise ValueError('changes 包含不支持的字段')
    if expected_revision is not None and (isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<1):
        raise ValueError('expected_revision 应为正整数')
    changes=dict(changes)
    if 'password' in changes:
        if 'pass' in changes and changes['pass']!=changes['password']:raise ValueError('pass/password 不一致')
        changes['pass']=changes.pop('password')
    if any(changes.get(key)=='[REDACTED]' for key in SECRETS):raise ValueError('不能用脱敏占位符覆盖凭据')
    migrate(store)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE');entry=c.execute('SELECT * FROM targets WHERE id=?',(identity,)).fetchone()
        if entry is None:raise ValueError('目标 ID 不存在')
        if expected_revision is not None and entry['revision']!=expected_revision:
            raise ValueError('记录已被其他客户端修改，请刷新后重试')
        before=_decode(entry['record']);merged={**before,**changes}
        known=normalize({key:value for key,value in merged.items() if key in FIELDS},1)
        row={**{key:value for key,value in before.items() if key not in FIELDS},**known}
        key=endpoint_key(row);new_endpoint=key!=entry['endpoint_key']
        changed=sorted(key for key in FIELDS if before.get(key)!=row.get(key))
        if entry['source']=='legacy' and (new_endpoint or 'name' in changed):row['legacy_aliases_retired']=True
        if new_endpoint and c.execute('SELECT 1 FROM targets WHERE endpoint_key=? AND id!=?',(key,identity)).fetchone():
            raise ValueError('更新后的连接已存在；记录未改动')
        reauthorize=bool(entry['needs_reauthorization'] or new_endpoint or (entry['source']=='legacy' and 'name' in changed))
        revision=entry['revision']+(1 if changed else 0)
        if changed:
            c.execute('UPDATE targets SET endpoint_key=?,record=?,updated=?,revision=?,needs_reauthorization=? WHERE id=?',
                      (key,_encode(row),time.time(),revision,int(reauthorize),identity))
        target=dict(row,id=identity,source=entry['source'],requires_local_selection=reauthorize)
    return {'id':identity,'status':'updated' if changed else 'unchanged','revision':revision,
            'changed_fields':changed,'requires_local_selection':reauthorize,'allowed':store.target_is_allowed(target)}


def delete_target(store,identity,source=None):
    migrate(store)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        query='DELETE FROM targets WHERE id=?';args=(identity,)
        if source is not None:query+=' AND source=?';args+=(source,)
        deleted=c.execute(query,args).rowcount==1
    if deleted:
        try:store.revoke_target(identity)
        except (OSError,ValueError,RuntimeError,KeyError):
            return {'deleted':True,'id':identity,'warning':'记录已删除；本机允许范围配置无法同步，原配置已保留'}
    return {'deleted':deleted,'id':identity}


def acknowledge_selection(store,identities):
    migrate(store)
    with store.connect() as c:
        c.executemany('UPDATE targets SET needs_reauthorization=0 WHERE id=?',[(identity,) for identity in identities])


def backup_info(store,path):
    """Local diagnostic metadata only; never return decrypted record payloads."""
    try:
        data=json.loads(protect(Path(path).read_bytes(),True))
        if data.get('version')!=1 or not isinstance(data.get('records'),list) or not isinstance(data.get('sources'),dict):raise ValueError()
    except Exception:raise ValueError('迁移备份无法解密或已损坏，文件已保留') from None
    return {'version':1,'records':len(data['records']),'sources':sorted(data['sources'])}


def restore_migration_backup(store,path):
    """Local recovery into an empty repository; all recovered targets need local selection."""
    try:
        raw=Path(path).read_bytes();data=json.loads(protect(raw,True))
        if data.get('version')!=1 or not isinstance(data.get('records'),list) or len(data['records'])>MAX_TARGETS:raise ValueError()
        prepared=[];seen=set()
        for index,item in enumerate(data['records'],1):
            identity=item['id'];source=item['source'];created=float(item['created'])
            if not isinstance(identity,str) or not identity or identity=='local' or len(identity)>128 or identity in seen:raise ValueError()
            if source not in ('legacy','mcp','desktop') or not math.isfinite(created):raise ValueError()
            row=_historical(item['record'],index);seen.add(identity)
            prepared.append((identity,endpoint_key(row),source,_encode(row),created,time.time()))
    except Exception:raise ValueError('迁移备份无法解密或已损坏，文件已保留') from None
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT COUNT(*) FROM targets').fetchone()[0] or c.execute('SELECT COUNT(*) FROM target_migrations').fetchone()[0]:
            raise ValueError('恢复只允许写入全新的空数据目录；现有数据未改动')
        if c.execute('SELECT COUNT(*) FROM mcp_targets').fetchone()[0]:raise ValueError('目标目录中存在旧 MCP 记录，不能覆盖')
        backup=Path('backups')/('targets-restored-'+uuid.uuid4().hex+'.dpapi')
        atomic_bytes(store.path/backup,raw)
        if (store.path/backup).read_bytes()!=raw:raise RuntimeError('恢复备份副本校验失败')
        c.executemany('INSERT INTO targets(id,endpoint_key,source,record,created,updated,needs_reauthorization) VALUES(?,?,?,?,?,?,1)',prepared)
        details=json.dumps({'status':'restored','count':len(prepared),'backup':backup.as_posix()})
        c.executemany('INSERT INTO target_migrations(name,details) VALUES(?,?)',[('mcp_v1',details),('legacy_v1',details)])
    return {'restored':len(prepared),'requires_local_selection':True,'backup':backup.as_posix()}
