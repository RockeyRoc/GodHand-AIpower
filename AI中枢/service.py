"""One bounded task owner; GUI and MCP submit identical immutable payloads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

from .core import Store,TaskContext,TaskResult,OperationSpec,protect,redact,atomic_json,app_root
from .operations import catalogue
from 工具管理模块.registry import Registry


class InstanceLock:
    def __init__(self,path):
        import msvcrt
        self.path=Path(path);self.handle=self.path.open('a+b');self.handle.seek(0)
        if self.path.stat().st_size==0:self.handle.write(b'0');self.handle.flush()
        self.handle.seek(0)
        try:msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:self.handle.close();raise RuntimeError('此数据目录已有服务实例；请连接现有实例')
    def close(self):
        if self.handle and not self.handle.closed:
            import msvcrt
            self.handle.seek(0);msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1);self.handle.close()


def kill_tree(process):
    if process.poll() is not None:return True
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    result=subprocess.run(['taskkill','/F','/T','/PID',str(process.pid)],capture_output=True,timeout=12,creationflags=flags)
    try:process.wait(timeout=3)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
    return result.returncode==0


class TaskService:
    def __init__(self,store=None):
        self.store=store or Store();self.instance=InstanceLock(self.store.path/'instance.lock')
        self.specs=catalogue();self.registry=Registry(self.store)
        self.executor=ThreadPoolExecutor(max_workers=int(self.store.config['max_workers']),thread_name_prefix='godhand-task')
        self.capacity=threading.BoundedSemaphore(int(self.store.config['max_workers'])+int(self.store.config['max_queue']))
        self.stopping=threading.Event();self.lock=threading.RLock();self.cancels={};self.target_locks={};self.futures={}
        with self.store.connect() as c:
            c.execute("UPDATE tasks SET status=CASE WHEN write_action THEN 'needs_review' ELSE 'interrupted' END,updated=? WHERE status IN ('queued','running')",(time.time(),))
            table=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'").fetchone()
            if table:c.execute("UPDATE submissions SET status='unknown',updated=? WHERE status='sending'",(time.time(),))

    def submit_operation(self,identity,target_id='',parameters=None,timeout=120,caller='mcp',competition='default',idempotency_key=''):
        if identity not in self.specs:raise ValueError('未知业务能力：'+identity)
        spec=self.specs[identity];target=self.store.resolve_target(target_id or ('local' if spec.local else ''))
        self.store.allow(spec,target)
        parameters=parameters or {}
        if not isinstance(parameters,dict):raise ValueError('parameters 应为对象')
        known=set(spec.fields)|{'file','directory','input','args','connection_mode','closure','credential_ref'}
        unexpected=set(parameters)-known
        if unexpected:raise ValueError('未知参数：'+', '.join(sorted(unexpected)))
        payload={'kind':'operation','spec':asdict(spec),'parameters':parameters,'target':target}
        context=TaskContext(identity,str(target.get('ip','local')),competition,caller,redact(parameters),float(timeout))
        return self._submit(context,payload,not spec.read_only,idempotency_key)

    def submit_call(self,call,args=(),kwargs=None,title='GodHand 界面操作',actor_key='',bindings=None,parameters=None):
        payload={'kind':'snapshot','call':call,'args':list(args),'kwargs':kwargs or {},'parameters':parameters or {},'desktop':True}
        hosts=set()
        def scan(value,key=''):
            if isinstance(value,dict):
                if '$value' in value and key in ('e_target','e_ip','entry_ip','af_ip','db_ip','ssh_ip','bak_ssh_host','sb_ssh_host','target_url'):
                    from urllib.parse import urlsplit
                    raw=str(value['$value']);host=urlsplit(raw).hostname if '://' in raw else raw
                    if host:hosts.update(h.strip() for h in host.replace(',',' ').split() if h.strip())
                for k,v in value.items():scan(v,k)
            elif isinstance(value,list):
                for v in value:scan(v,key)
        scan(call)
        if args and isinstance(args[0],dict) and '$value' in args[0] and any(name in title for name in ('do_harden','do_discover','do_ps','do_kill_proc','do_block_port')):
            hosts.update(str(args[0]['$value']).replace(',',' ').split())
        payload['lock_targets']=sorted(hosts) or ['local']
        context=TaskContext('desktop:'+title,','.join(payload['lock_targets']),caller='desktop',parameters={'title':title},timeout=3600)
        return self._submit(context,payload,True,'')

    def submit_tool(self,identity,target_id='',parameters=None,caller='mcp',idempotency_key=''):
        spec=self.registry.get(identity)
        if spec.kind=='builtin':return self.submit_operation(identity,target_id,parameters,caller=caller,idempotency_key=idempotency_key)
        target=self.store.resolve_target(target_id or 'local')
        operation=OperationSpec('tool:'+identity,spec.name,read_only=spec.read_only,local=identity not in ('tabby','winscp'))
        self.store.allow(operation,target)
        parameters=parameters or {}
        if spec.kind!='gui' or identity not in ('tabby','winscp'):self.registry.command(spec,parameters)
        context=TaskContext('tool:'+identity,str(target.get('ip','local')),caller=caller,parameters=redact(parameters),timeout=float(spec.timeout))
        return self._submit(context,{'kind':'tool','tool':asdict(spec),'target':target,'parameters':parameters},not spec.read_only,idempotency_key)

    def _submit(self,context,payload,write_action,key):
        if self.stopping.is_set():raise RuntimeError('服务正在停止接收请求')
        if not 0<float(context.timeout)<=86400:raise ValueError('超时应在 0..86400 秒')
        encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode();fingerprint=hashlib.sha256(encoded).hexdigest()
        with self.lock:
            if key:
                with self.store.connect() as c:row=c.execute('SELECT * FROM idempotency WHERE key=?',(context.caller+':'+key,)).fetchone()
                if row:
                    if row['digest']!=fingerprint:raise ValueError('相同幂等标识不能用于不同请求')
                    return self.store.task(row['task'])
            if not self.capacity.acquire(False):raise RuntimeError('任务队列已满，请稍后重试')
            task_dir=self.store.path/'tasks'/context.task_id;task_dir.mkdir(parents=True)
            request=task_dir/'request.dpapi'
            try:
                request.write_bytes(protect(json.dumps({'context':asdict(context),'payload':payload,'root':str(self.store.root),'data_dir':str(self.store.path)},ensure_ascii=False).encode()))
                with self.store.connect() as c:
                    now=time.time();c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(context.task_id,context.operation,context.target,context.competition,context.caller,'queued',now,now,int(write_action),os.getpid(),json.dumps(redact(asdict(context)),ensure_ascii=False),None))
                    if key:c.execute('INSERT INTO idempotency VALUES(?,?,?)',(context.caller+':'+key,fingerprint,context.task_id))
                self.cancels[context.task_id]=threading.Event()
                future=self.executor.submit(self._run,context,payload,request,write_action)
                self.futures[context.task_id]=future
                future.add_done_callback(lambda f,identity=context.task_id:self._settled(identity))
                return self.store.task(context.task_id)
            except Exception:self.capacity.release();request.unlink(missing_ok=True);raise

    def _settled(self,identity):
        with self.lock:self.cancels.pop(identity,None);self.futures.pop(identity,None)
        self.capacity.release()

    def _run(self,context,payload,request,write_action):
        cancel=self.cancels[context.task_id];process=None;job=None;acquired=[];offset=0
        events=request.parent/'events.jsonl';result_path=request.parent/'result.json'
        status='failed';result=None
        remote_write=write_action and payload.get('kind') in ('operation','snapshot') and any(t not in ('local','desktop','') for t in payload.get('lock_targets',[context.target]))
        try:
            if write_action:
                for target in payload.get('lock_targets',[context.target]):
                    with self.lock:target_lock=self.target_locks.setdefault(target,threading.Lock())
                    while not cancel.is_set() and not self.stopping.is_set():
                        if target_lock.acquire(timeout=.1):acquired.append(target_lock);break
                    if cancel.is_set() or self.stopping.is_set():break
            if cancel.is_set() or self.stopping.is_set():status='cancelled';return
            with self.store.connect() as c:c.execute("UPDATE tasks SET status='running',updated=? WHERE id=?",(time.time(),context.task_id))
            self.store.event(context.task_id,'started',{'operation':context.operation,'target':context.target})
            if payload['kind']=='tool' and payload['tool']['kind']=='gui':
                if payload['tool']['id'] not in ('tabby','winscp'):
                    spec=self.registry.get(payload['tool']['id']);command,cwd=self.registry.command(spec,payload['parameters'])
                    launched=subprocess.Popen(command,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    status='started';result=TaskResult(context.task_id,status,summary='外部程序已启动',data={'pid':launched.pid}).wire();return
                from tool_launchers import SSHConnection,launch_terminal,launch_file_manager
                connection=SSHConnection.from_machine(payload['target'])
                fn=launch_terminal if payload['tool']['id']=='tabby' else launch_file_manager
                launched=fn(connection,app_dir=self.store.root)
                status='started';result=TaskResult(context.task_id,status,summary='外部程序已启动，连接结果由工具显示',data={'pid':launched.pid}).wire();return
            entry=self.store.root/'main_work.py'
            command=([sys.executable] if getattr(sys,'frozen',False) else [sys.executable,'-B',str(entry)])+['--worker','--request',str(request)]
            flags=0x4 | (0 if getattr(sys,'frozen',False) else getattr(subprocess,'CREATE_NO_WINDOW',0))
            process=subprocess.Popen(command,cwd=self.store.root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
            from .winjob import Job,resume
            try:job=Job(process);resume(process)
            except Exception:process.kill();process.wait(timeout=3);raise
            started=time.monotonic()
            while process.poll() is None:
                offset=self._events(context.task_id,events,offset)
                if cancel.is_set() or self.stopping.is_set():
                    cleaned=job.terminate();process.wait(timeout=5);status='needs_review' if remote_write or not cleaned else 'cancelled'
                    self.store.event(context.task_id,'cleanup',{'process_tree_stopped':cleaned});break
                if time.monotonic()-started>context.timeout:
                    cleaned=job.terminate();process.wait(timeout=5);status='needs_review' if remote_write or not cleaned else 'timed_out'
                    self.store.event(context.task_id,'cleanup',{'process_tree_stopped':cleaned});break
                time.sleep(.1)
            offset=self._events(context.task_id,events,offset)
            if status not in ('cancelled','timed_out','needs_review'):
                if not result_path.exists():raise RuntimeError('Worker 未返回结果；退出码 '+str(process.returncode))
                result=json.loads(result_path.read_text(encoding='utf-8-sig'));status=result['status']
        except Exception as exc:
            status='failed';result=TaskResult(context.task_id,status,1,error=str(redact(str(exc)))).wire()
        finally:
            if job:job.close()
            if process and process.poll() is None:process.kill();process.wait(timeout=3)
            request.unlink(missing_ok=True)
            for target_lock in reversed(acquired):target_lock.release()
            if result is None:result=TaskResult(context.task_id,status,1 if status not in ('succeeded','started') else 0,summary=status).wire()
            with self.store.connect() as c:c.execute('UPDATE tasks SET status=?,updated=?,result=? WHERE id=?',(status,time.time(),json.dumps(redact(result),ensure_ascii=False),context.task_id))
            self.store.event(context.task_id,'finished',{'status':status})

    def _events(self,identity,path,offset):
        if not path.exists():return offset
        with path.open('rb') as f:
            f.seek(offset)
            while True:
                start=f.tell();line=f.readline()
                if not line or not line.endswith(b'\n'):return start
                try:
                    event=json.loads(line);self.store.event(identity,event['kind'],event['payload'])
                except (ValueError,KeyError):self.store.event(identity,'error',{'message':'无效 Worker 事件'})

    def cancel(self,identity):
        self.store.task(identity)
        with self.lock:
            event=self.cancels.get(identity)
            if event:event.set()
        return self.store.task(identity)

    def close(self):
        self.stopping.set()
        with self.lock:
            for event in self.cancels.values():event.set()
        self.executor.shutdown(wait=True,cancel_futures=False)
        self.instance.close()

