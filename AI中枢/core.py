"""Persistent configuration, credentials and task contracts."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from .storage import exclusive_lock


def app_root():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def resource_root():
    return Path(getattr(sys, '_MEIPASS', app_root()))


def redact(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if re.search(r'pass|pwd|token|secret|private|authorization|cookie|spass|dbp$', str(k), re.I) else redact(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [redact(v) for v in value]
    if not isinstance(value,str):
        return value
    value = re.sub(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', value, flags=re.S)
    return re.sub(r'(?i)((?:password|passwd|token|api[_-]?key|secret|authorization|cookie|密码|口令)\s*[=:]\s*)([^\s,;}]+)', r'\1[REDACTED]', value)


def _replace(temporary, path):
    """os.replace with a short retry for antivirus/indexer file locks."""
    last_error = None
    for attempt in range(6):
        try:
            os.replace(temporary, path); return
        except PermissionError as exc:
            last_error = exc
            # Some Windows filters report failure after the move completed.
            if not temporary.exists(): return
            if attempt == 5: break
            time.sleep(0.05 * (attempt + 1))
    # Microsoft Store/AppContainer directories can permit ordinary file writes
    # while rejecting every rename operation.  Preserve the normal atomic path
    # above, then use a rollback-protected direct commit so first-run setup and
    # later configuration saves still work in those restricted directories.
    replacement = temporary.read_bytes()
    previous = path.read_bytes() if path.exists() else None
    created = previous is None
    try:
        with path.open('xb' if created else 'r+b') as stream:
            stream.seek(0); stream.write(replacement); stream.truncate()
            stream.flush(); os.fsync(stream.fileno())
    except Exception:
        try:
            if created:
                path.unlink(missing_ok=True)
            else:
                with path.open('r+b') as stream:
                    stream.seek(0); stream.write(previous); stream.truncate()
                    stream.flush(); os.fsync(stream.fileno())
        except Exception:
            pass
        raise last_error
    temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        _replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_text(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x',encoding='utf-8',newline='\n') as f:
            f.write(value); f.flush(); os.fsync(f.fileno())
        _replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def protect(raw, decrypt=False):
    """DPAPI current-user encryption. Private state never enters task rows."""
    if os.name != 'nt':
        raise RuntimeError('GodHand credentials require Windows DPAPI')
    class Blob(ctypes.Structure):
        _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_char))]
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_char))); target=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ctypes.WinError()
    try:return ctypes.string_at(target.data,target.size)
    finally:ctypes.windll.kernel32.LocalFree(target.data)


@dataclass
class TaskContext:
    operation: str
    target: str = ''
    competition: str = 'default'
    caller: str = 'desktop'
    parameters: dict = field(default_factory=dict)
    timeout: float = 120
    task_id: str = field(default_factory=lambda:uuid.uuid4().hex)


@dataclass
class TaskResult:
    task_id: str
    status: str
    exit_code: int = 0
    summary: str = ''
    data: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    error: str = ''

    def wire(self):return redact(asdict(self))


@dataclass
class OperationSpec:
    id: str
    title: str
    actor: str = ''
    method: str = ''
    args: list = field(default_factory=list)
    read_only: bool = False
    local: bool = False
    description: str = ''
    fields: dict = field(default_factory=dict)


class Store:
    def __init__(self, root=None, data_dir=None):
        self.root=Path(root or app_root()).resolve()
        self.path=Path(data_dir or self.root/'管理工具'/'GodHand').resolve()
        self.path.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.config_path=self.path/'hub.json'
        self.vault=self.path/'credentials.dpapi'
        with exclusive_lock(self.path/'state.lock'):
            if not self.config_path.exists():
                atomic_json(self.config_path,dict(version=1,host='127.0.0.1',port=18765,allowed_targets=[],enabled_operations=['*'],max_workers=4,max_queue=32,max_targets=100,auto_authorize_imports=False))
            self.config=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            self.config.setdefault('submission_endpoints',[])
            self.config.setdefault('auto_authorize_imports',False)
            self._config_stamp=None
            if not self.vault.exists():self.write_private(dict(mcp_token=secrets.token_urlsafe(32),admin_token=secrets.token_urlsafe(32)))
        self.db=self.path/'tasks.sqlite3'
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,operation TEXT,target TEXT,competition TEXT,caller TEXT,status TEXT,created REAL,updated REAL,write_action INTEGER,owner INTEGER,request TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT,time REAL,kind TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,digest TEXT,task TEXT);
            CREATE TABLE IF NOT EXISTS mcp_targets(id TEXT PRIMARY KEY,endpoint_key TEXT UNIQUE NOT NULL,record BLOB NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS targets(id TEXT PRIMARY KEY,endpoint_key TEXT NOT NULL,source TEXT NOT NULL,record BLOB NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 1,needs_reauthorization INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS targets_endpoint ON targets(endpoint_key);
            CREATE TABLE IF NOT EXISTS target_migrations(name TEXT PRIMARY KEY,details TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=15); c.row_factory=sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=15000')
        try:
            with c:yield c
        finally:c.close()

    def read_private(self):return json.loads(protect(self.vault.read_bytes(),True))
    def write_private(self,value):
        raw=protect(json.dumps(value,ensure_ascii=False).encode())
        p=self.vault.with_suffix('.tmp');p.write_bytes(raw);os.replace(p,self.vault)

    def tokens(self):return self.read_private()

    def rotate_token(self):
        with self.lock,exclusive_lock(self.path/'state.lock'):
            v=self.read_private();v['mcp_token']=secrets.token_urlsafe(32);self.write_private(v)
        return v['mcp_token']

    def reload_config(self):
        stat=self.config_path.stat();stamp=(stat.st_mtime_ns,stat.st_size)
        if stamp!=self._config_stamp:
            config=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            config.setdefault('submission_endpoints',[])
            config.setdefault('auto_authorize_imports',False)
            self.config=config;self._config_stamp=stamp
        return self.config

    def _write_config(self,config):
        if config.get('host')!='127.0.0.1':raise ValueError('首版只允许本机 127.0.0.1')
        if not 1024<=int(config['port'])<=65535:raise ValueError('端口应在 1024..65535')
        if not 1<=int(config['max_workers'])<=16:raise ValueError('并发应在 1..16')
        if not isinstance(config['allowed_targets'],list) or not isinstance(config['enabled_operations'],list):raise ValueError('范围配置必须是列表')
        if len(config['allowed_targets'])>5000 or any(not isinstance(value,str) for value in config['allowed_targets']):raise ValueError('允许目标应为最多 5000 项的标识列表')
        if not isinstance(config.get('auto_authorize_imports',False),bool):raise ValueError('auto_authorize_imports 应为布尔值')
        atomic_json(self.config_path,config);self.config=config;self._config_stamp=None

    def save_config(self,changes):
        with self.lock,exclusive_lock(self.path/'state.lock'):
            config={**self.reload_config(),**changes}
            self._write_config(config)
            if 'allowed_targets' in changes:
                from .target_store import acknowledge_selection
                acknowledge_selection(self,config['allowed_targets'])

    def revoke_target(self,identity):
        with self.lock,exclusive_lock(self.path/'state.lock'):
            config=dict(self.reload_config())
            config['allowed_targets']=[value for value in config['allowed_targets'] if value!=identity]
            self._write_config(config)

    def event(self,task,kind,payload):
        payload=json.dumps(redact(payload),ensure_ascii=False,default=str)
        if len(payload)>64000:payload=json.dumps({'truncated':True,'message':payload[:60000]},ensure_ascii=False)
        with self.connect() as c:c.execute('INSERT INTO events(task,time,kind,payload) VALUES(?,?,?,?)',(task,time.time(),kind,payload))

    def task(self,task_id):
        with self.connect() as c:r=c.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if r is None:raise KeyError('任务不存在')
        row=dict(r);row.pop('request',None)
        row['result']=json.loads(row['result']) if row['result'] else None
        return row

    def tasks(self,limit=100):
        with self.connect() as c:rows=c.execute('SELECT id FROM tasks ORDER BY created DESC LIMIT ?',(min(max(int(limit),1),500),)).fetchall()
        return [self.task(r['id']) for r in rows]

    def events(self,task,after=0,limit=100):
        with self.connect() as c:rows=c.execute('SELECT * FROM events WHERE task=? AND seq>? ORDER BY seq LIMIT ?',(task,int(after),min(max(int(limit),1),500))).fetchall()
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in rows]

    def legacy_machine_records(self):
        from .target_store import records
        return records(self,'legacy')

    def target_is_allowed(self,row):
        permitted=self.reload_config()['allowed_targets']
        if row.get('requires_local_selection'):return False
        if row.get('source')!='legacy' or row.get('legacy_aliases_retired'):return row.get('id') in permitted
        return any(value in permitted for value in [row.get('id'),row.get('ip'),row.get('name')]+row.get('legacy_aliases',[]))

    def machine_records(self, private=False):
        from .target_store import records
        from .targets import endpoint_key
        rows=records(self)
        endpoint_counts={}
        for row in rows:
            identity=endpoint_key(row)
            endpoint_counts[identity]=endpoint_counts.get(identity,0)+1
        for row in rows:row['allowed']=self.target_is_allowed(row)
        for row in rows:
            identity=endpoint_key(row)
            row['conflict']=endpoint_counts[identity]>1
        return rows if private else redact(rows)

    def import_targets(self,records,on_duplicate='skip'):
        from .targets import import_targets
        return import_targets(self,records,on_duplicate)

    def delete_imported_target(self,identity):
        from .targets import delete_imported_target
        return delete_imported_target(self,identity)

    def add_target(self,record):
        from .target_store import import_targets
        return import_targets(self,[record],source='desktop')

    def update_target(self,identity,changes,expected_revision=None):
        from .target_store import update_target
        return update_target(self,identity,changes,expected_revision)

    def delete_target(self,identity):
        from .target_store import delete_target
        return delete_target(self,identity)

    def target_migration_status(self):
        from .target_store import migrate
        return migrate(self)

    def restore_target_backup(self,path):
        from .target_store import restore_migration_backup
        return restore_migration_backup(self,path)

    def resolve_target(self,identity):
        rows=self.machine_records(True)
        for row in rows:
            if identity==row['id']:return row
        matches=[row for row in rows if identity in (row.get('name'),row.get('ip'))]
        if len(matches)>1:raise ValueError('目标名称或 IP 对应多条记录，请使用目标 ID')
        if matches:return matches[0]
        if identity=='local':return {'id':'local','ip':'local','name':'本地'}
        raise ValueError('目标不在主机管理记录中，请先在机子管理中保存')

    def allow(self,spec,target):
        allowed=self.reload_config()['enabled_operations']
        if '*' not in allowed and spec.id not in allowed:raise PermissionError('能力未启用：'+spec.id)
        if not spec.local:
            from .targets import endpoint_key
            try:latest=self.resolve_target(target['id']) if target else None
            except (KeyError,ValueError):latest=None
            if not latest or endpoint_key(latest)!=endpoint_key(target) or not self.target_is_allowed(latest):
                raise PermissionError('请在 AI 中枢配置允许的目标；请求不能扩大范围')

