"""Development-only extraction of the two GodHand sources.

The generated module contains original business functions and lexical workers,
but no Tk imports, widget builders, constructors, or application startup.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path

UI_CLASSES={'WafTab','FlagTab','MonitorTab','CreepTab','OpsTab','ToolsTab','MachinesTab','AWDToolkit','LogWidget'}


def literal(node,default=''):
    try:return ast.literal_eval(node)
    except Exception:return default


def fields_for(cls):
    fields={}
    for node in ast.walk(cls):
        if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id=='self':fields.setdefault(node.attr,{'kind':'value','default':''})
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target,ast.Attribute) or not isinstance(target.value,ast.Name) or target.value.id!='self':continue
                rhs=node.value;name=target.attr
                if isinstance(rhs,(ast.Constant,ast.Dict,ast.List,ast.Tuple,ast.Set)):
                    fields[name]={'kind':'literal','default':literal(rhs,None)}
                elif isinstance(rhs,ast.Call):
                    symbol=ast.unparse(rhs.func).split('.')[-1]
                    if symbol in ['Event','RLock','Lock']:fields[name]={'kind':'event' if symbol=='Event' else 'lock'}
                    elif symbol in ['Text','ScrolledText','Treeview','Listbox','StringVar','IntVar','BooleanVar','DoubleVar','Entry','Combobox']:
                        value=next((literal(k.value,False if symbol=='BooleanVar' else '') for k in rhs.keywords if k.arg=='value'),'' )
                        fields[name]={'kind':'list' if symbol=='Listbox' else 'table' if symbol=='Treeview' else 'text' if symbol in ['Text','ScrolledText'] else 'value','default':value}
                    elif symbol=='LogWidget':fields[name]={'kind':'log'}
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='insert' and len(node.args)>1:
            obj=node.func.value
            if isinstance(obj,ast.Attribute) and isinstance(obj.value,ast.Name) and obj.value.id=='self' and isinstance(node.args[1],ast.Constant):
                fields.setdefault(obj.attr,{'kind':'value'})['default']=literal(node.args[1])
    for name in list(fields):
        if name.startswith('__'):
            fields.pop(name);continue
        if name in {n.name for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}:fields.pop(name)
    for node in ast.walk(cls):
        if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Attribute) and isinstance(node.value.value,ast.Name) and node.value.value.id=='self':
            name=node.value.attr
            if name not in fields:continue
            if node.attr=='log' or name.endswith('_lw') or name.endswith('_log'):fields[name]={'kind':'log'}
            elif node.attr in ('get_children','heading','selection'):fields[name]={'kind':'table'}
            elif node.attr=='curselection':fields[name]={'kind':'list'}
    for name in fields:
        if name.endswith('_lw') or name in ('_disc_log','ps_lw','bak_lw'):fields[name]={'kind':'log'}
    return fields


def extract(root):
    root=Path(root);source=(root/'main_work.py').read_text(encoding='utf-8-sig');tree=ast.parse(source)
    codes={}
    def visit(code):
        import types
        codes[code.co_qualname+'@'+str(code.co_firstlineno)]=code
        for child in code.co_consts:
            if isinstance(child,types.CodeType):visit(child)
    visit(compile(source,str(root/'main_work.py'),'exec'))
    prefix='''"""Generated GodHand business implementation. Do not edit; see generate.py."""
from AI中枢.snapshot import ActorState, StateValue, StateTable, StateLog, DomainDialogs, DomainClock
from types import SimpleNamespace
tk = SimpleNamespace(END='end', INSERT='insert', NORMAL='normal', DISABLED='disabled', BOTH='both', LEFT='left', RIGHT='right', TOP='top', BOTTOM='bottom', X='x', Y='y')
END='end'
messagebox=DomainDialogs()
filedialog=DomainDialogs()
simpledialog=DomainDialogs()
CLOSURES={}
from AI中枢.core import app_root, resource_root
BASE=str(resource_root())
EXE_DIR=str(app_root())
'''
    nodes=[];field_data={};class_actions={}
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            text=ast.unparse(node)
            if any(x in text for x in ['tkinter','ttkbootstrap','AI中枢','工具管理模块']):continue
            nodes.append(copy.deepcopy(node))
        elif isinstance(node,ast.Assign) and all(isinstance(t,ast.Name) for t in node.targets):
            if any(t.id in ['_GODHAND','_GodHandThread'] for t in node.targets):continue
            nodes.append(copy.deepcopy(node))
        elif isinstance(node,ast.FunctionDef):
            if node.name in ['async_run','_godhand_attach']:continue
            nodes.append(copy.deepcopy(node))
        elif isinstance(node,ast.ClassDef):
            clone=copy.deepcopy(node)
            if node.name in UI_CLASSES:
                clone.bases=[ast.Name(id='ActorState',ctx=ast.Load())]
                constructor=next((copy.deepcopy(n) for n in clone.body if isinstance(n,ast.FunctionDef) and n.name=='__init__'),None)
                clone.body=[n for n in clone.body if not isinstance(n,ast.FunctionDef) or not (n.name=='__init__' or n.name.startswith('build_') or n.name in ['_target_frame','_log_frame','_btn_frame'])]
                if constructor and node.name not in ('AWDToolkit','LogWidget'):
                    constructor.name='_domain_init'
                    constructor.body=[n for n in constructor.body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr.startswith('build_'))]
                    class DomainVars(ast.NodeTransformer):
                        def visit_Call(self,n):
                            self.generic_visit(n)
                            if isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='tk' and n.func.attr in ('StringVar','IntVar','BooleanVar','DoubleVar'):
                                n.func=ast.Name(id='StateValue',ctx=ast.Load());n.args=[];n.keywords=[k for k in n.keywords if k.arg=='value']
                            return n
                    constructor=DomainVars().visit(constructor)
                    clone.body.append(constructor)
                field_data[node.name]=fields_for(node)
                clone.body.append(ast.parse('_fields = '+repr(field_data[node.name])).body[0])
                class_actions[node.name]=[n.name for n in clone.body if isinstance(n,ast.FunctionDef)]
            nodes.append(clone)
    output=prefix+ast.unparse(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])))+'\n'
    # Recover lexical workers as factories preserving their real free variables.
    def descend(node,parents=()):
        nonlocal output
        for child in ast.iter_child_nodes(node):
            if isinstance(child,(ast.ClassDef,ast.FunctionDef)):
                lineage=parents+(child.name,)
                if isinstance(child,ast.FunctionDef) and any(isinstance(n,ast.FunctionDef) and n is not child and n.lineno<child.lineno<=n.end_lineno for n in ast.walk(tree)):
                    candidates=[(k,c) for k,c in codes.items() if c.co_firstlineno==child.lineno and c.co_name==child.name]
                    for key,code in candidates:
                        factory=ast.FunctionDef(name='_factory_'+hashlib.sha256(key.encode()).hexdigest()[:12],args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=n) for n in code.co_freevars],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[copy.deepcopy(child),ast.Return(value=ast.Name(id=child.name,ctx=ast.Load()))],decorator_list=[])
                        factory.body[0].decorator_list=[]
                        output+=ast.unparse(ast.fix_missing_locations(factory))+'\nCLOSURES['+repr(key)+']='+factory.name+'\n'
                descend(child,lineage)
            else:descend(child,parents)
    descend(tree)
    (root/'AI中枢'/'business.py').write_text(output,encoding='utf-8')
    data={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'fields':field_data,'methods':class_actions}
    (root/'AI中枢'/'business_fields.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data


if __name__=='__main__':
    print(extract(Path(__file__).resolve().parent.parent)['source_sha256'])

