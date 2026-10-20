"""No-window YL execution: primitive snapshots in, events and results out."""
from contextlib import contextmanager,redirect_stdout,redirect_stderr
from dataclasses import asdict
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

from .core import Store,TaskResult,protect,redact,atomic_json,resource_root
from . import snapshot as state


class BusinessEnvironment:
    def __init__(self,request,path):
        self.request=request;self.path=Path(path);self.parameters=request['payload'].get('parameters',{});self.clock=state.DomainClock();self.errors=[];self.bytes=0;self.artifact_dirs=[]
        self.allowed=set()
        target=request['payload'].get('target',{})
        if target.get('ip') and target['ip']!='local':self.allowed.add(str(target['ip']))
        self.desktop=request['payload'].get('desktop',False)
        config=json.loads((Path(request['data_dir'])/'hub.json').read_text(encoding='utf-8'))
        for url in config.get('submission_endpoints',[]):
            if urlsplit(url).hostname:self.allowed.add(urlsplit(url).hostname)
        self.secrets=[]
        def collect(value,key=''):
            if isinstance(value,dict):
                if '$value' in value:collect(value['$value'],key)
                else:
                    for k,v in value.items():collect(v,k)
            elif isinstance(value,list):
                for v in value:collect(v,key)
            elif isinstance(value,str) and len(value)>2 and re.search('pass|pwd|token|secret|private',key,re.I):self.secrets.append(value)
        collect(request['payload'])
        vault=Path(request['data_dir'])/'credentials.dpapi'
        if vault.exists():self.secrets.extend(json.loads(protect(vault.read_bytes(),True)).values())
    def sanitize(self,payload):
        payload=redact(payload)
        def hide(value):
            if isinstance(value,str):
                for secret in self.secrets:value=value.replace(secret,'[REDACTED]')
                return value
            if isinstance(value,dict):return {k:hide(v) for k,v in value.items()}
            if isinstance(value,list):return [hide(v) for v in value]
            return value
        return hide(payload)
    def emit(self,kind,payload):
        if self.bytes>16*1024*1024:return
        payload=self.sanitize(payload)
        if kind=='log' and payload.get('level') in ('err','error','danger','fail'):self.errors.append(payload.get('message',''))
        line=(json.dumps({'kind':kind,'payload':payload},ensure_ascii=False,default=str)+'\n').encode()
        with (self.path/'events.jsonl').open('ab') as f:f.write(line)
        self.bytes+=len(line)
    def check(self):pass  # Parent owns timeout/cancellation and process-tree teardown.
    def wait(self,seconds):time.sleep(min(max(0,seconds),86400))
    def check_url(self,url):
        host=urlsplit(str(url)).hostname
        if not self.desktop and host not in self.allowed:raise PermissionError('目标超出当前请求范围：'+str(host))


class EventWriter:
    def __init__(self,env):self.env=env
    def write(self,text):
        if str(text).strip():self.env.emit('log',{'message':str(text),'level':'info'})
        return len(text)
    def flush(self):pass


def load_business(env):
    os.environ['GODHAND_DATA_DIR']=env.request['data_dir']
    module=importlib.import_module('AI中枢.business')
    old_base=str(module.BASE);root=str(env.request['root']);resources=str(resource_root())
    for name,value in list(vars(module).items()):
        if name.isupper() and isinstance(value,str) and value.startswith(old_base):setattr(module,name,resources+value[len(old_base):])
    module.BASE=resources;module.EXE_DIR=root;module.CONFIG_DIR=os.path.join(root,'管理工具');module.CONFIG_FILE=os.path.join(module.CONFIG_DIR,'app_config.json');module.WAF_SIG_KEY_FILE=os.path.join(module.CONFIG_DIR,'waf_key.txt')
    module._UI=env.clock;module._UI.owner=threading.get_ident()
    module._GodHandThread=state.ImmediateThread;module.threading=state.ThreadAPI()
    module.async_run=lambda target,daemon=True,args=(),kwargs=None:target(*args,**(kwargs or {}))
    import requests
    original_request=requests.Session.request
    class ScopedSession(requests.Session):
        def request(self,method,url,*args,**kwargs):
            env.check_url(url);kwargs.setdefault('timeout',10)
            return original_request(self,method,url,*args,**kwargs)
    class RequestsAPI:
        Session=ScopedSession
        def __getattr__(self,name):
            if name in ('get','post','put','delete','head','request'):
                def call(url,*args,**kwargs):
                    with ScopedSession() as session:
                        return session.request(name if name!='request' else args[0],url,*args,**kwargs)
                return call
            return getattr(requests,name)
    module.requests=RequestsAPI()
    module._session=lambda:ScopedSession()
    original_import=module._ensure_import
    def ensure(name,*args,**kwargs):
        value=original_import(name,*args,**kwargs)
        if hasattr(value,'requests'):value.requests=module.requests
        if hasattr(value,'LIB_FILE'):value.LIB_FILE=str(resource_root()/'防御模块'/'fix_library.json')
        if hasattr(value,'DEPLOY_LOG'):value.DEPLOY_LOG=str(env.path/'patch_deploy.json')
        return value
    module._ensure_import=ensure
    # Direct imports in preserved YL functions, urllib, SSH and socket discovery
    # share this worker-local connection guard as well.
    original_socket=socket.socket
    allowed_addresses=set(env.allowed)
    for host in env.allowed:
        try:allowed_addresses.update(record[4][0] for record in socket.getaddrinfo(host,None))
        except OSError:pass
    class ScopedSocket(original_socket):
        def _check_address(self,address):
            if not env.desktop and isinstance(address,tuple) and str(address[0]) not in allowed_addresses:raise PermissionError('连接地址超出固定目标快照')
        def connect(self,address):self._check_address(address);return super().connect(address)
        def connect_ex(self,address):self._check_address(address);return super().connect_ex(address)
        def sendto(self,data,*args):
            if args:self._check_address(args[-1])
            return super().sendto(data,*args)
    socket.socket=ScopedSocket
    original_targets=module.parse_targets
    def scoped_targets(raw):
        targets=original_targets(raw)
        if not env.desktop and any(str(t) not in env.allowed for t in targets):raise PermissionError('批量目标不能超出已固定目标快照')
        return targets
    module.parse_targets=scoped_targets
    def backup_dir(actor):
        path=Path(env.parameters.get('bak_dir') or env.path/'backups');path.mkdir(parents=True,exist_ok=True);env.artifact_dirs.append(path);return str(path)
    module.OpsTab._bak_dir=backup_dir
    # Background operation state does not overwrite the currently edited GUI form.
    module.WafTab.save_config=lambda actor:None
    module.FlagTab.save_config=lambda actor:None
    import hashlib
    competition=hashlib.sha256(env.request['context']['competition'].encode()).hexdigest()[:16]
    known_file=Path(env.request['data_dir'])/'competitions'/competition/'known.json'
    def load_known(actor):
        actor.known=set(json.loads(known_file.read_text(encoding='utf-8'))) if known_file.exists() else set()
    def save_known(actor):atomic_json(known_file,sorted(actor.known))
    module.FlagTab.load_known=load_known;module.FlagTab.save_known=save_known
    module.CreepTab.save_cfg=lambda actor:None
    module.MonitorTab.save_config=lambda actor:None
    from .submission import install
    install(module,env)
    return module


def actor_for(module,name,parameters,target):
    cls=getattr(module,name);actor=cls.__new__(cls)
    for key,descriptor in cls._fields.items():setattr(actor,key,state.make_value(descriptor,key))
    actor.root=actor.parent=state.current().clock
    if hasattr(cls,'_domain_init'):
        cls._domain_init(actor,state.current().clock,state.StateLog().log)
    actor.root=state.current().clock
    host=str(target.get('ip',''));web_port=str(target.get('port',80));ssh_port=str(target.get('ssh_port',22));user=str(target.get('ssh_user','ctf'));password=str(target.get('pass',target.get('password','')));shell=str(target.get('shell','/shell.php'));ssh_password=str(target.get('ssh_pass',password))
    aliases={
        **{k:host for k in ['e_target','e_targets','e_ip','e_ips','entry_ip','db_ip','af_ip','pi_ip','ssh_ip','bak_ssh_host','sb_ssh_host','e_ssh_host','e_creep_ssh_host','ssh_batch_ip','br_targets','ban_target']},
        **{k:web_port for k in ['e_port','entry_port','af_port','pi_port']},
        **{k:ssh_port for k in ['e_ssh_port','e_creep_ssh_port','db_ssh_port','bak_ssh_port','sb_ssh_port','ssh_port']},
        **{k:user for k in ['e_ssh_user','e_creep_ssh_user','db_ssh_user','bak_ssh_user','sb_ssh_user','ssh_user']},
        **{k:password for k in ['e_ssh_pass','e_creep_ssh_pass','db_ssh_pass','bak_ssh_pass','sb_ssh_pass','ssh_pass','e_spass','e_shell_pass','entry_pass','af_pass','pi_pass','df_pass']},
        **{k:shell for k in ['e_surl','e_shell_url','af_shell','pi_path']}}
    aliases.update({'entry_shell':shell,'entry_creep_pass':target.get('creep_pass',password),'e_cpass':target.get('creep_pass',password),'password':target.get('monitor_pass',password),'conn_mode':parameters.get('connection_mode','webshell'),'creep_conn_mode':parameters.get('connection_mode','webshell'),'entry_timeout':'10','e_timeout':'10','e_to':'10','entry_threads':'4','e_threads':'4','e_retry':'0','v_https':bool(target.get('https')),'v_https_flag':bool(target.get('https')),'af_https':bool(target.get('https'))})
    for key in list(aliases):
        if 'ssh_pass' in key or key=='ssh_pass':aliases[key]=ssh_password
    aliases.update({'c_ssh_port':ssh_port,'c_ssh_user':user,'c_ssh_pass':ssh_password,'e_ssh_webdir':params_webdir if (params_webdir:=parameters.get('webdir')) else target.get('webdir','/var/www/html'),'e_creep_ssh_webdir':parameters.get('webdir') or target.get('webdir','/var/www/html')})
    for key in ('db_user','db_pass','db_name'):
        if key in target:aliases[key]=str(target[key])
    if host and host!='local':
        aliases['target_url']=('https' if target.get('https') else 'http')+'://'+host+':'+web_port+'/target_monitor.php'
    # Per-operation parameters are set first; the resolved connection is authoritative.
    for key,value in parameters.items():
        if key not in cls._fields:continue
        descriptor=cls._fields[key]
        setattr(actor,key,state.StateValue(value,descriptor.get('kind','value'),key) if descriptor.get('kind') in ('value','text') else value)
    for key,value in aliases.items():
        if key in cls._fields:
            obj=getattr(actor,key)
            if isinstance(obj,state.StateValue):obj.set(value)
    actor.log=state.StateLog().log if not isinstance(getattr(actor,'log',None),state.StateLog) else actor.log
    if parameters.get('findings') is not None:actor._af_findings=parameters['findings']
    return actor


def run_operation(module,payload):
    spec=payload['spec']
    if spec['actor']=='TemplateHandler':
        from .templates import render
        name=payload.get('parameters',{}).get('template','rsa_agent.php')
        return {'return':render(name,state.current().path/'rendered'/Path(name).name)}
    actor=actor_for(module,spec['actor'],payload.get('parameters',{}),payload.get('target',{}))
    method=getattr(actor,spec['method'])
    params=payload.get('parameters',{});target=payload.get('target',{})
    if spec['method'] in ('ssh_deploy_waf','ssh_create_shell','ssh_deploy_flag','deploy_monitor','ssh_deploy_creep'):
        # Execute the original dialog's business closure with explicit primitive inputs.
        prefix=spec['actor']+'.'+spec['method']+'.<locals>.'
        candidates=[(key,factory) for key,factory in module.CLOSURES.items() if key.startswith(prefix) and key.split('@')[0].count('<locals>')==1]
        chosen=next(((k,f) for k,f in candidates if any(n in k for n in ('do_deploy@','do_create@'))),None)
        if not chosen:raise ValueError('此部署动作尚未找到可独立执行的闭包；请使用 GUI 部署入口')
        values={'host':target.get('ip',''),'port':str(target.get('ssh_port',22)),'user':target.get('ssh_user','ctf'),'pwd':target.get('ssh_pass',target.get('pass','')),'web':params.get('webdir','/var/www/html'),'sh_pass':target.get('pass','cmd'),'sh_url':target.get('shell','/shell.php')}
        if not values['web'].startswith('/') or any(c in values['web'] for c in [';','\n','\r','`','$','"',"'"]):raise ValueError('webdir 需要简单的绝对远程目录')
        for name in ('_waf_deploy_vars','_shell_vars','_flag_deploy_vars','_mon_deploy_vars','_creep_deploy_vars'):setattr(actor,name,{k:state.StateValue(v) for k,v in values.items()})
        actor._deploy_vars={k:state.StateValue(v) for k,v in {'ssh_host':values['host'],'ssh_port':values['port'],'ssh_user':values['user'],'ssh_pass':values['pwd'],'web_dir':values['web']}.items()}
        factory=chosen[1];closure={}
        for name in inspect.signature(factory).parameters:
            if name=='self':closure[name]=actor
            elif name=='win':closure[name]=state.current().clock
            elif name in values:closure[name]=state.StateValue(values[name])
            else:raise ValueError('部署还需要明确参数：'+name)
        deployment=factory(**closure)
        result=deployment(values) if inspect.signature(deployment).parameters else deployment()
    elif spec['method']=='fm_req':
        path=params.get('remote_path')
        if not path:raise ValueError('remote_path 必填')
        action='delete' if spec['id'].endswith('file_delete') else 'read'
        result=method(action,{'file':path})
        if spec['id'].endswith('file_download'):
            artifact=state.current().path/'download.txt';artifact.write_text(str(result.get('content','')),encoding='utf-8');result={'artifact':str(artifact)}
    else:
        args=params.get('args',spec.get('args',[]))
        if not args:
            values={'e_ip':state.StateValue(target.get('ip','')),'ep':state.StateValue(str(target.get('port',80))),'vh':state.StateValue(bool(target.get('https'))),'et':state.StateValue('10'),'scan_mode':state.StateValue(params.get('scan_mode','tcp')),'e_port':state.StateValue(params.get('scan_ports',str(target.get('port',80)))),'e_pid':state.StateValue(params.get('pid','')),'e_block':state.StateValue(params.get('block_port','')),'cmd':params.get('command') or ('ps aux' if spec['method']=='do_ps' else '')}
            for p in inspect.signature(method).parameters.values():
                if p.default is not inspect.Parameter.empty:break
                if p.name not in values:raise ValueError('缺少业务参数：'+p.name)
                args.append(values[p.name])
        result=method(*args)
    state.current().clock.drain()
    outputs={}
    for name,value in vars(actor).items():
        if isinstance(value,state.StateTable):outputs[name]=state.primitive(value)
        elif isinstance(value,state.StateValue) and value.kind=='text':outputs[name]=value.value[-32000:]
        elif name in ('findings','_af_findings','patches','records','known','atk_stats') and isinstance(value,(list,dict)):outputs[name]=state.primitive(value)
    return {'return':state.primitive(result),'outputs':redact(outputs)}


def run_tool(request,env):
    from .core import Store
    from 工具管理模块.registry import Registry,ToolSpec
    registry=Registry(Store(request['root'],request['data_dir']))
    spec=ToolSpec(**request['payload']['tool']);parameters=request['payload']['parameters']
    command,cwd=registry.command(spec,parameters)
    process=subprocess.Popen(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    log=env.path/'stdout.log';output=[];size=0
    with log.open('w',encoding='utf-8') as f:
        while True:
            chunk=process.stdout.readline()
            if not chunk and process.poll() is not None:break
            text=chunk.decode('utf-8','replace');clean=env.sanitize(text)
            if size<16*1024*1024:f.write(clean);f.flush();size+=len(clean.encode())
            if sum(map(len,output))<32000:output.append(clean)
            env.emit('log',{'message':clean,'level':'info'})
    code=process.wait()
    return code,{'stdout':''.join(output),'log':str(log),'return_code':code}


def main(request_path):
    path=Path(request_path);request=json.loads(protect(path.read_bytes(),True));env=BusinessEnvironment(request,path.parent);state._session.value=env;state._environment=env
    identity=request['context']['task_id'];result=None
    try:
        if request['payload']['kind']=='tool':
            code,data=run_tool(request,env)
            result=TaskResult(identity,'succeeded' if code==0 else 'failed',code,'脚本执行完成',data,[str(path.parent/'stdout.log')]).wire()
        else:
            writer=EventWriter(env)
            with redirect_stdout(writer),redirect_stderr(writer):
                payload=request['payload']
                if payload.get('kind')=='operation' and payload.get('spec',{}).get('actor')=='DefenseHandler':
                    from 防御模块.engine import execute as run_defense
                    data=run_defense(payload['spec']['id'],payload.get('target',{}),payload.get('parameters',{}),env)
                else:
                    module=load_business(env)
                    if payload['kind']=='snapshot':
                        memo={};fn=state.thaw(payload['call'],module,memo)
                        value=fn(*state.thaw(payload.get('args',[]),module,memo),**state.thaw(payload.get('kwargs',{}),module,memo));env.clock.drain();data={'return':state.primitive(value)}
                        for actor in memo.values():
                            if isinstance(actor,state.ActorState):env.emit('actor_state',{k:state.primitive(v) for k,v in vars(actor).items() if k in ('_af_findings','results','_history','known','total_submitted','atk_stats')})
                    else:data=run_operation(module,payload)
            outcome=data.get('return')
            failed=bool(env.errors) or data.get('failed') is True or outcome is False or (isinstance(outcome,list) and len(outcome)>1 and outcome[1] is False)
            result=TaskResult(identity,'failed' if failed else 'succeeded',1 if failed else 0,'GodHand 业务执行完成',data,error='\n'.join(env.errors)[-4000:]).wire()
    except Exception as exc:
        env.emit('error',{'message':str(redact(str(exc))),'type':type(exc).__name__})
        result=TaskResult(identity,'dependency_missing' if isinstance(exc,(ImportError,FileNotFoundError)) else 'failed',1,error=str(redact(str(exc)))).wire()
    finally:
        result=env.sanitize(result or TaskResult(identity,'failed',1,error='无结果').wire())
        if result.get('data'):
            report=path.parent/'report.json';atomic_json(report,result['data']);result.setdefault('artifacts',[]).append(str(report))
        for directory in env.artifact_dirs:
            result.setdefault('artifacts',[]).extend(str(p) for p in directory.glob('*') if p.is_file())
        atomic_json(path.parent/'result.json',result)
    return result.get('exit_code',1)

