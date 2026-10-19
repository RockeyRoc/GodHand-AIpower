"""Pure Python domain state for the extracted YL business functions.

StateValue/StateTable contain primitive data; they never construct or access Tk.
The GUI serializes values on its owner thread before submitting a task.
"""
import base64
import collections
import contextlib
import importlib
import inspect
import json
import os
from pathlib import Path
import queue
import threading
import time
import types

from .core import redact, resource_root

_session = threading.local()
_environment=None


class OperationInputError(ValueError):pass


def current():return getattr(_session,'value',None) or _environment


class StateValue:
    def __init__(self,value='',kind='value',name=''):
        self.value=value;self.kind=kind;self.name=name;self.properties={}
    def get(self,*args):return self.value
    def set(self,value):
        self.value=value
        if current():current().emit('state',{'field':self.name,'action':'set','value':value})
    def delete(self,*args):
        self.value=''
        if current():current().emit('state',{'field':self.name,'action':'delete','args':args})
    def insert(self,index,value,*tags):
        if self.kind=='text':
            self.value=str(self.value or '')+str(value)
            if current():current().emit('log',{'message':str(value),'level':'error' if 'error' in tags or 'err' in tags else 'info','field':self.name})
        else:self.value=str(value) if index in (0,'0','1.0') else str(self.value or '')+str(value)
    def configure(self,**kwargs):
        self.properties.update(kwargs)
        if current():current().emit('state',{'field':self.name,'action':'configure','value':kwargs})
    config=configure
    def __getitem__(self,key):return self.properties.get(key,'')
    def __setitem__(self,key,value):self.properties[key]=value
    def see(self,*args):pass
    def update(self):pass
    def update_idletasks(self):pass
    def after(self,ms,fn=None,*args):return current().clock.after(ms,fn,*args)
    def tag_config(self,*args,**kwargs):pass
    tag_configure=tag_config
    def tag_add(self,*args):pass
    def tag_remove(self,*args):pass
    def selection_get(self):return self.value
    def destroy(self):pass
    def winfo_exists(self):return True
    def pack(self,*args,**kwargs):pass
    def pack_forget(self):pass
    def curselection(self):return getattr(self,'selected',[])


class StateTable(StateValue):
    def __init__(self,rows=None,name=''):
        super().__init__('','table',name);self.rows=rows or {};self.selected=[]
    def insert(self,parent,index,iid=None,**kwargs):
        iid=str(iid or len(self.rows)+1);self.rows[iid]=kwargs
        if current():current().emit('state',{'field':self.name,'action':'table','value':self.rows})
        return iid
    def get_children(self,*args):return tuple(self.rows)
    def delete(self,*items):
        for item in items:
            for key in item if isinstance(item,(list,tuple)) else [item]:self.rows.pop(str(key),None)
    def item(self,item,option=None,**kwargs):
        row=self.rows.get(str(item),{})
        if kwargs:row.update(kwargs);self.rows[str(item)]=row
        return row.get(option,[]) if option else row
    def selection(self):return self.selected
    def selection_set(self,*items):self.selected=list(items)
    def heading(self,*args,**kwargs):pass
    def column(self,*args,**kwargs):pass

class StateList(StateValue):
    def __init__(self,items=None,name=''):
        super().__init__(items or [],'list',name);self.selected=[]
    def get(self,first=0,last=None):
        if last is not None:return self.value[int(first):None if last=='end' else int(last)+1]
        return self.value[int(first)]
    def insert(self,index,*items):
        at=len(self.value) if index=='end' else int(index)
        self.value[at:at]=list(items);self._emit()
    def delete(self,first,last=None):
        del self.value[int(first):None if last=='end' else int(last if last is not None else first)+1];self._emit()
    def _emit(self):
        if current():current().emit('state',{'field':self.name,'action':'list','value':self.value})
    def selection_set(self,*items):self.selected=list(items)


class StateLog:
    def __init__(self,name=''):self.name=name
    def log(self,message,tag='info'):
        if current():current().emit('log',{'message':str(message),'level':tag,'field':self.name})
    def clear(self):pass
    def __call__(self,*args):self.log(*args)


class DomainClock:
    def __init__(self):self.pending=[];self.closed=False
    def after(self,ms,fn=None,*args):
        if fn:self.pending.append((time.monotonic()+max(0,float(ms))/1000,fn,args))
        return len(self.pending)
    def after_cancel(self,handle):self.pending=[]
    def post(self,fn,*args):return fn(*args)
    def drain(self):
        while self.pending:
            current().check();self.pending.sort(key=lambda x:x[0]);when,fn,args=self.pending.pop(0)
            current().wait(max(0,when-time.monotonic()));fn(*args)
    def winfo_exists(self):return not self.closed
    def update_idletasks(self):pass
    def destroy(self):self.closed=True


class DomainDialogs:
    def __getattr__(self,name):
        def call(*args,**kwargs):
            env=current();text=' '.join(str(x) for x in args)
            if name in ('askyesno','askokcancel'):
                env.emit('confirmation',{'message':text,'basis':'configured task scope'});return True
            if name in ('showerror','showwarning'):raise OperationInputError(text)
            if name=='showinfo':env.emit('log',{'message':text,'level':'info'});return None
            key='directory' if 'directory' in name else 'file' if 'filename' in name else 'input'
            value=env.parameters.get(key)
            if value is None:raise OperationInputError('请明确提供参数 '+key+'：'+text)
            return value
        return call


class ImmediateThread:
    def __init__(self,target=None,args=(),kwargs=None,**ignored):self.target=target;self.args=args;self.kwargs=kwargs or {};self.active=False
    def start(self):
        self.active=True
        try:return self.target(*self.args,**self.kwargs)
        finally:self.active=False
    def join(self,*args):pass
    def is_alive(self):return self.active


class ThreadAPI:
    Thread=ImmediateThread
    def __getattr__(self,name):return getattr(threading,name)


class ActorState:
    def __getattr__(self,name):
        fields=getattr(type(self),'_fields',{})
        if name not in fields:raise AttributeError(type(self).__name__+'.'+name)
        descriptor=fields[name]
        value=make_value(descriptor,name)
        setattr(self,name,value);return value


def make_value(descriptor,name=''):
    kind=descriptor.get('kind','value');value=descriptor.get('default','')
    if kind=='literal':return value
    if kind=='event':return threading.Event()
    if kind=='lock':return threading.RLock()
    if kind=='table':return StateTable(name=name)
    if kind=='list':return StateList(name=name)
    if kind=='log':return StateLog()
    return StateValue(value,kind,name)


def freeze(value,memo=None,path=''):
    """Called ONLY on the GUI thread. Produces a portable primitive snapshot."""
    memo={} if memo is None else memo
    if value is None or isinstance(value,(str,int,float,bool)):return value
    if isinstance(value,bytes):return {'$bytes':base64.b64encode(value).decode()}
    if isinstance(value,Path):return {'$path':str(value)}
    if isinstance(value,(list,tuple,set)):return [freeze(v,memo,path) for v in value]
    if isinstance(value,dict):return {str(k):freeze(v,memo,path+'.'+str(k)) for k,v in value.items()}
    if isinstance(value,threading.Event):return {'$event':value.is_set()}
    if inspect.ismethod(value):return {'$method':value.__name__,'actor':freeze(value.__self__,memo,path)}
    if inspect.isfunction(value):
        closure={n:freeze(c.cell_contents,memo,path+'.'+n) for n,c in zip(value.__code__.co_freevars,value.__closure__ or ())}
        return {'$function':value.__code__.co_qualname+'@'+str(value.__code__.co_firstlineno),'closure':closure}
    cls=type(value).__name__
    if cls in ('StringVar','IntVar','DoubleVar','BooleanVar'):
        memo.setdefault('$bindings',{})[path]=value
        return {'$value':value.get(),'kind':'value','binding':path}
    if cls in ('Tk','Toplevel','UiDispatcher'):return {'$clock':True}
    if value.__class__.__module__ in ('_thread','threading') or 'Thread' in cls:return {'$event':False}
    # Tk types are inspected by name, avoiding a Tk import in the worker.
    if hasattr(value,'tk'):
        memo.setdefault('$bindings',{})[path]=value
        if cls=='Treeview':
            return {'$table':{str(k):value.item(k) for k in value.get_children()},'selected':list(value.selection()),'binding':path}
        if cls=='Listbox':return {'$list':list(value.get(0,'end')),'selected':list(value.curselection()),'binding':path}
        if cls in ('Text','ScrolledText'):
            return {'$value':value.get('1.0','end-1c'),'kind':'text','binding':path}
        if hasattr(value,'get') and cls not in ('Frame','TFrame','Label','Button','Notebook','Canvas','Scrollbar'):
            try:return {'$value':value.get(),'kind':'value','binding':path}
            except Exception:pass
        return {'$value':'','kind':'control','binding':path}
    if cls in ('WafTab','FlagTab','MonitorTab','CreepTab','OpsTab','ToolsTab','MachinesTab','AWDToolkit','LogWidget'):
        if id(value) in memo:return {'$ref':memo[id(value)]}
        identity=str(len(memo)+1);memo[id(value)]=identity
        if cls=='LogWidget':
            memo.setdefault('$bindings',{})[path]=value
            return {'$log':True,'binding':path}
        state={}
        for key,item in vars(value).items():
            if key in ('root','parent','_dispatcher'):state[key]={'$clock':True};continue
            if key.startswith('_godhand') or key in ('dash','_ui_bridge'):continue
            try:state[key]=freeze(item,memo,path+'.'+key)
            except (TypeError,ValueError):continue
        return {'$actor':cls,'identity':identity,'state':state}
    raise TypeError('不能序列化业务状态：'+cls)


def thaw(value,module,memo=None):
    memo={} if memo is None else memo
    if isinstance(value,list):return [thaw(v,module,memo) for v in value]
    if not isinstance(value,dict):return value
    if '$ref' in value:return memo.get(value['$ref'])
    if '$bytes' in value:return base64.b64decode(value['$bytes'])
    if '$path' in value:return Path(value['$path'])
    if '$clock' in value:return current().clock
    if '$event' in value:
        event=threading.Event()
        if value['$event']:event.set()
        return event
    if '$log' in value:return StateLog(value.get('binding',''))
    if '$value' in value:return StateValue(value['$value'],value.get('kind','value'),value.get('binding',''))
    if '$table' in value:
        table=StateTable(value['$table'],value.get('binding',''));table.selected=value.get('selected',[]);return table
    if '$list' in value:
        items=StateList(value['$list'],value.get('binding',''));items.selected=value.get('selected',[]);return items
    if '$actor' in value:
        actor=getattr(module,value['$actor']).__new__(getattr(module,value['$actor']));memo[value.get('identity','')]=actor
        for name,item in value['state'].items():setattr(actor,name,thaw(item,module,memo))
        return actor
    if '$method' in value:return getattr(thaw(value['actor'],module,memo),value['$method'])
    if '$function' in value:
        fn=module.CLOSURES[value['$function']]
        return fn(**{k:thaw(v,module,memo) for k,v in value['closure'].items()})
    return {k:thaw(v,module,memo) for k,v in value.items()}


def primitive(value):
    if isinstance(value,StateTable):return {'rows':value.rows,'selected':value.selected}
    if isinstance(value,StateValue):return value.value
    if isinstance(value,dict):return {str(k):primitive(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [primitive(v) for v in value]
    if value is None or isinstance(value,(str,int,float,bool)):return value
    return str(value)

