"""Capture widgets on their owner thread; replay worker events on that thread."""
import functools
import inspect
import threading
from .snapshot import freeze

_bridge=None
_namespace=None

DIRECT={
 'WafTab':['start','load_rules','save_rules','fetch_traffic','toggle_heartbeat'],
 'FlagTab':['test_conn','test_submit','start_hunt','manual_submit'],
 'MonitorTab':['toggle','fm_refresh','fm_search','fm_edit_file','fm_save_file','fm_delete_file','fm_download','fm_upload_file','fm_mkdir','export_atk','export_log'],
 'CreepTab':['do_implant','do_deploy','do_spread','do_check','do_exec','do_kill','do_ssh_backdoor','do_ssh_back_check','toggle_creep_hb'],
 'OpsTab':['do_backup','do_restore','do_scan','do_db_show','do_db_create','do_db_tables','do_db_data','do_db_sql','do_db_dump','do_db_chpass','do_harden','af_scan_remote','af_scan_local','af_deploy_patch','do_ipban','do_ipunban','do_ipban_list','do_ipban_batch','toggle_sched_backup','do_discover','do_ssh_test','do_ssh_chpass','do_ssh_cmd','do_ssh_batch','do_ps','do_kill_proc','do_block_port','do_phpinfo'],
 # Listing the local PHPGGC catalog is a fast file-system refresh. Keep it on
 # the UI thread so ToolsTab can populate during window construction, before
 # the shared MCP service has finished starting.
 'ToolsTab':['ggc_generate','df_check','df_parse_pi','br_run']}

class BusinessThread:
    def __init__(self,target=None,args=(),kwargs=None,daemon=True,**ignored):self.target=target;self.args=args;self.kwargs=kwargs or {};self.task_id=None
    def start(self):
        if _bridge:self.task_id=_bridge.submit(self.target,self.args,self.kwargs)
        else:
            self.native=threading.Thread(target=self.target,args=self.args,kwargs=self.kwargs,daemon=True);self.native.start()
        return self
    def is_alive(self):return bool(_bridge and self.task_id in _bridge.pending)
    def join(self,*args):pass

def install(namespace):
    global _namespace
    _namespace=namespace
    namespace['async_run']=lambda target,daemon=True,args=(),kwargs=None:BusinessThread(target,args,kwargs,daemon).start()
    for actor,methods in DIRECT.items():
        cls=namespace[actor]
        for name in methods:
            original=getattr(cls,name,None)
            if not original:continue
            @functools.wraps(original)
            def wrapped(self,*args,__original=original,**kwargs):
                if _bridge is None:return __original(self,*args,**kwargs)
                continuous=__original.__name__ in ('toggle','toggle_heartbeat','toggle_creep_hb','toggle_sched_backup','start_hunt')
                if continuous and _bridge.cancel_actor(self,__original.__name__):return
                return _bridge.submit(__original.__get__(self,type(self)),args,kwargs)
            setattr(cls,name,wrapped)
        for name in ('stop','stop_hunt'):
            original=getattr(cls,name,None)
            if original:
                @functools.wraps(original)
                def stop(self,*args,__original=original,**kwargs):
                    if _bridge:_bridge.cancel_actor(self)
                    return __original(self,*args,**kwargs)
                setattr(cls,name,stop)

class Bridge:
    def __init__(self,app,connection):
        global _bridge
        self.app=app;self.connection=connection;self.pending={};self.owner=threading.get_ident();self.closed=False;_bridge=self
        app.root.after(200,self.poll)
    def submit(self,fn,args=(),kwargs=None):
        if threading.get_ident()!=self.owner:
            self.app._dispatcher.post(self.submit,fn,args,kwargs);return None
        from tkinter import messagebox
        try:
            from tkinter import filedialog
            parameters={};name=getattr(fn,'__name__','')
            if name=='af_scan_local':parameters['directory']=filedialog.askdirectory(title='选择源码目录')
            elif name in ('do_restore','fm_upload_file'):parameters['file']=filedialog.askopenfilename(title='选择还原备份' if name=='do_restore' else '选择上传文件')
            elif name in ('export_atk','export_log'):parameters['file']=filedialog.asksaveasfilename(title='导出报告')
            elif name=='fm_mkdir':
                from tkinter import simpledialog
                parameters['input']=simpledialog.askstring('创建目录','目录名')
            if parameters and not all(parameters.values()):return None
            memo={};call=freeze(fn,memo,'call');arguments=freeze(list(args),memo,'args');kw=freeze(kwargs or {},memo,'kwargs')
            title=getattr(fn,'__qualname__',getattr(fn,'__name__','GodHand'))
            task=self.connection.call('snapshot',call=call,args=arguments,kwargs=kw,title=title,parameters=parameters)
            actor=getattr(fn,'__self__',None)
            if actor is None and inspect.isfunction(fn):
                actor=next((cell.cell_contents for cell in fn.__closure__ or () if type(cell.cell_contents).__name__ in DIRECT),None)
            self.pending[task['id']]={'seq':0,'bindings':memo.get('$bindings',{}),'actor':actor,'method':getattr(fn,'__name__','')}
            return task['id']
        except Exception as exc:messagebox.showerror('任务未启动',str(exc));return None
    def submit_operation(self,identity,target_id,parameters=None,timeout=120,callback=None):
        """Submit an OperationSpec through the same local service used by MCP."""
        def run():
            try:
                row=self.connection.call('operation',identity=identity,target_id=target_id,
                                         parameters=parameters or {},timeout=timeout,caller='desktop')
                self.app._dispatcher.post(self._register_operation,row,identity,callback)
            except Exception as exc:
                self.app._dispatcher.post(self._operation_error,identity,callback,exc)
        threading.Thread(target=run,name='GodHand-defense-submit',daemon=True).start()
    def _register_operation(self,row,identity,callback):
        task_id=row.get('id') if isinstance(row,dict) else None
        if not task_id:
            self._operation_error(identity,callback,RuntimeError('服务未返回任务编号'))
            return
        self.pending[task_id]={'seq':0,'bindings':{},'actor':None,'method':identity,
                               'operation':identity,'callback':callback}
        if callback:callback(row,None)
    def _operation_error(self,identity,callback,error):
        describe=getattr(self.connection,'describe_error',None)
        detail=describe(error) if describe else str(error)
        if callback:callback(None,error)
        else:self.app.log(identity+' 提交失败：'+detail,'fail')
    def cancel_task(self,task_id,callback=None):
        def run():
            try:
                row=self.connection.call('cancel',task_id=task_id)
                self.app._dispatcher.post(callback or (lambda _:None),row)
            except Exception as exc:
                describe=getattr(self.connection,'describe_error',None)
                detail=describe(exc) if describe else str(exc)
                self.app._dispatcher.post(self.app.log,'任务取消失败：'+detail,'fail')
        threading.Thread(target=run,name='GodHand-defense-cancel',daemon=True).start()
    def cancel_actor(self,actor,method=''):
        matches=[(identity,task) for identity,task in list(self.pending.items())
                 if task['actor'] is actor and (not method or task['method']==method)]
        found=bool(matches)
        if self.closed:
            for identity,_ in matches:self.pending.pop(identity,None)
            return found
        for identity,_ in matches:
            try:self.connection.call('cancel',task_id=identity)
            except Exception as exc:
                describe=getattr(self.connection,'describe_error',None)
                detail=describe(exc) if describe else str(exc)
                # A stopped backend cannot own a live task.  Remove the stale
                # local handle so the user can start the operation again.
                if detail.startswith(('后台服务未运行','连接超时')):
                    self.pending.pop(identity,None)
                self.app.log('任务取消请求失败：'+detail,'fail')
        return found
    def close(self):
        global _bridge
        self.closed=True;self.pending.clear()
        if _bridge is self:_bridge=None
    def poll(self):
        if self.closed:return
        for identity,task in list(self.pending.items()):
            try:
                # Local SQLite reads avoid HTTP waits on the Tk thread.
                events=self.connection.store.events(identity,task['seq'],150)
                for event in events:
                    task['seq']=event['seq'];self.replay(task,event)
                row=self.connection.store.task(identity)
                if row['status'] not in ('queued','running'):
                    if len(events)==150:continue
                    self.pending.pop(identity,None)
                    actor=task['actor']
                    if actor:
                        for key in ('running','monitoring','hunting','hb_running','c_hb_running','sb_running'):
                            if hasattr(actor,key):setattr(actor,key,False)
                        if hasattr(actor,'set_btns'):actor.set_btns(True)
                    if row['result'] and row['result'].get('error'):self.app.log(row['result']['error'],'fail')
                    callback=task.get('callback')
                    if callback:
                        try:callback(row,None)
                        except Exception as exc:self.app.log('防御任务界面刷新失败：'+str(exc),'fail')
            except Exception as exc:self.app.log('任务事件读取失败: '+str(exc),'fail')
        self.app.root.after(200,self.poll)
    def replay(self,task,event):
        payload=event['payload'];obj=task['bindings'].get(payload.get('field'));kind=event['kind']
        if kind=='log':
            text=payload.get('message','');level=payload.get('level','info')
            if obj and hasattr(obj,'log'):obj.log(text,level)
            elif obj and type(obj).__name__ in ('Text','ScrolledText'):
                try:obj.configure(state='normal');obj.insert('end',text);obj.see('end')
                except Exception:pass
            else:self.app.log(text,level)
        elif kind=='state' and obj:
            try:
                action=payload['action']
                if action=='configure':obj.configure(**payload['value'])
                elif action=='set':
                    if hasattr(obj,'set'):obj.set(payload['value'])
                elif action=='delete':obj.delete(*payload['args'])
                elif action=='table':
                    obj.delete(*obj.get_children())
                    for iid,row in payload['value'].items():obj.insert('','end',iid=iid,values=row.get('values',()),text=row.get('text',''))
                elif action=='list':obj.delete(0,'end');obj.insert('end',*payload['value'])
            except Exception:pass
        elif kind=='actor_state' and task['actor']:
            for key,value in payload.items():
                if key in ('_af_findings','results','_history','total_submitted','atk_stats'):setattr(task['actor'],key,value)
                elif key=='known':task['actor'].known=set(value)
