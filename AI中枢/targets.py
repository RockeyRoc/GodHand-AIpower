"""Append-only MCP target imports. Local configuration alone grants access."""
import hashlib
import ipaddress
import json
import re
import time

from .core import protect,redact

FIELDS={'name','ip','port','shell','pass','password','ssh_port','ssh_user','ssh_pass','https','webdir','monitor_pass','creep_pass'}
SECRETS={'pass','password','ssh_pass','monitor_pass','creep_pass'}


def endpoint_key(row):
    """Credentials and display names never change a connection identity."""
    values=[str(row.get(key) or default) for key,default in [
        ('ip',''),('port','80'),('ssh_port','22'),('ssh_user','ctf'),('shell','/shell.php')]]
    values.append(bool(row.get('https',False)))
    return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode('utf-8')).hexdigest()


def normalize(record,index):
    def invalid(field):raise ValueError(f'第 {index} 条靶机的 {field} 格式不正确')
    if not isinstance(record,dict):invalid('记录')
    if set(record)-FIELDS:raise ValueError(f'第 {index} 条靶机包含不支持的字段')
    row={}
    defaults={'name':'','ip':'','port':'80','ssh_port':'22','ssh_user':'ctf','shell':'/shell.php','https':False,'webdir':'/var/www/html'}
    for key in FIELDS:
        if key not in record and key not in defaults:continue
        value=record.get(key,defaults.get(key))
        if key in ('port','ssh_port'):
            if isinstance(value,bool) or not isinstance(value,(int,str)) or not re.fullmatch(r'[0-9]{1,5}',str(value)) or not 1<=int(value)<=65535:invalid(key)
            row[key]=str(int(value))
        elif key=='https':
            if not isinstance(value,bool):invalid(key)
            row[key]=value
        else:
            if not isinstance(value,str) or len(value)>(4096 if key in SECRETS else 2048) or '\x00' in value:invalid(key)
            if key not in SECRETS:
                value=value.strip()
                if any(ord(c)<32 or ord(c)==127 for c in value):invalid(key)
            row[key]=value
    host=row['ip'].strip('[]')
    try:host=ipaddress.ip_address(host).compressed
    except ValueError:
        try:host=host.rstrip('.').encode('idna').decode('ascii').lower()
        except UnicodeError:invalid('ip')
        if not host or len(host)>253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',part) for part in host.split('.')):invalid('ip')
    if host.casefold()=='local':invalid('ip')
    row['ip']=host
    if not row['name']:row['name']=host+':'+row['port']
    if len(row['name'])>128 or row['name'].casefold()=='local':invalid('name')
    if not re.fullmatch(r'[A-Za-z0-9._~-]{1,128}',row['ssh_user']):invalid('ssh_user')
    if not row['shell'].startswith('/') or row['shell'].startswith('//'):invalid('shell')
    if 'password' in row:
        if 'pass' in row and row['pass']!=row['password']:invalid('pass/password')
        row['pass']=row.pop('password')
    return row


def load_imported(store):
    with store.connect() as c:rows=c.execute('SELECT id,record FROM mcp_targets ORDER BY created,id').fetchall()
    result=[]
    for entry in rows:
        try:row=json.loads(protect(bytes(entry['record']),True))
        except Exception:raise ValueError('MCP 靶机记录无法解密或已损坏，原数据已保留') from None
        row.update(id=entry['id'],source='mcp');result.append(row)
    return result


def import_targets(store,records,on_duplicate='skip'):
    if on_duplicate not in ('skip','error'):raise ValueError('on_duplicate 应为 skip 或 error')
    if not isinstance(records,list) or not 1<=len(records)<=500:raise ValueError('每次导入 1..500 条靶机记录')
    try:size=len(json.dumps(records,ensure_ascii=False).encode('utf-8'))
    except (ValueError,TypeError):raise ValueError('靶机记录必须是 JSON 对象列表') from None
    if size>1024*1024:raise ValueError('单次靶机导入不能超过 1 MiB')
    prepared=[]
    for index,record in enumerate(records,1):
        row=normalize(record,index);key=endpoint_key(row)
        prepared.append((key,row,protect(json.dumps(row,ensure_ascii=False).encode('utf-8'))))
    # Legacy records remain owned by the existing host page; imports never rewrite them.
    legacy={endpoint_key(row):row for row in store.legacy_machine_records()}
    results=[];added=0;skipped=0
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        existing={row['endpoint_key']:row['id'] for row in c.execute('SELECT id,endpoint_key FROM mcp_targets')}
        for index,(key,row,encrypted) in enumerate(prepared,1):
            if key in existing or key in legacy:
                if on_duplicate=='error':raise ValueError(f'第 {index} 条靶机连接已存在；整批未写入')
                identity=existing.get(key) or legacy[key]['id'];skipped+=1;status='skipped'
            else:
                if len(existing)>=5000:raise ValueError('MCP 靶机记录已达 5000 条上限；整批未写入')
                identity='mcp_'+key[:24]
                c.execute('INSERT INTO mcp_targets(id,endpoint_key,record,created) VALUES(?,?,?,?)',(identity,key,encrypted,time.time()))
                existing[key]=identity;added+=1;status='imported'
            authorized_row=legacy[key] if key in legacy and key not in existing else {'id':identity,'source':'mcp'}
            results.append({'index':index,'id':identity,'status':status,'allowed':store.target_is_allowed(authorized_row)})
    return {'imported':added,'skipped':skipped,'targets':results,'authorization':'请在 AI 中枢选择目标并保存允许范围；MCP 导入项按 ID 授权'}
