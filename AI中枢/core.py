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


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(temporary,path)
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
        if not self.config_path.exists():
            atomic_json(self.config_path,dict(version=1,host='127.0.0.1',port=18765,allowed_targets=[],enabled_operations=['*'],max_workers=4,max_queue=32,max_targets=100))
        self.config=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        self.config.setdefault('submission_endpoints',[])
        self.vault=self.path/'credentials.dpapi'
        if not self.vault.exists():self.write_private(dict(mcp_token=secrets.token_urlsafe(32),admin_token=secrets.token_urlsafe(32)))
        self.db=self.path/'tasks.sqlite3'
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,operation TEXT,target TEXT,competition TEXT,caller TEXT,status TEXT,created REAL,updated REAL,write_action INTEGER,owner INTEGER,request TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT,time REAL,kind TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,digest TEXT,task TEXT);
            CREATE TABLE IF NOT EXISTS mcp_targets(id TEXT PRIMARY KEY,endpoint_key TEXT UNIQUE NOT NULL,record BLOB NOT NULL,created REAL NOT NULL);
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
        with self.lock:
            v=self.read_private();v['mcp_token']=secrets.token_urlsafe(32);self.write_private(v)
        return v['mcp_token']

    def save_config(self,changes):
        with self.lock:
            config={**self.config,**changes}
            if config.get('host')!='127.0.0.1':raise ValueError('首版只允许本机 127.0.0.1')
            if not 1024<=int(config['port'])<=65535:raise ValueError('端口应在 1024..65535')
            if not 1<=int(config['max_workers'])<=16:raise ValueError('并发应在 1..16')
            if not isinstance(config['allowed_targets'],list) or not isinstance(config['enabled_operations'],list):raise ValueError('范围配置必须是列表')
            atomic_json(self.config_path,config); self.config=config

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
        path=self.root/'管理工具'/'machines.json'
        raw=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else []
        if isinstance(raw,dict):raw=raw.get('machines',list(raw.values()))
        rows=[]
        for item in raw:
            if not isinstance(item,dict):continue
            row=dict(item); identity='|'.join(str(row.get(k,'')) for k in ['ip','port','ssh_port','ssh_user','shell'])
            row['id']=hashlib.sha256(identity.encode()).hexdigest()[:16]
            row['source']='legacy'
            rows.append(row)
        return rows

    def target_is_allowed(self,row):
        permitted=self.config['allowed_targets']
        if row.get('source')=='mcp':return row.get('id') in permitted
        return any(value in permitted for value in [row.get('id'),row.get('ip'),row.get('name')])

    def machine_records(self, private=False):
        from .targets import load_imported
        rows=self.legacy_machine_records()+load_imported(self)
        for row in rows:row['allowed']=self.target_is_allowed(row)
        return rows if private else redact(rows)

    def import_targets(self,records,on_duplicate='skip'):
        from .targets import import_targets
        return import_targets(self,records,on_duplicate)

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
        allowed=self.config['enabled_operations']
        if '*' not in allowed and spec.id not in allowed:raise PermissionError('能力未启用：'+spec.id)
        if not spec.local:
            if not target or not self.target_is_allowed(target):
                raise PermissionError('请在 AI 中枢配置允许的目标；请求不能扩大范围')

