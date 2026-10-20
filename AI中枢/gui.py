"""Two lightweight tabs on the GodHand notebook."""
import json
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from dataclasses import asdict
from .core import Store,atomic_json
from .server import Connection,client_configs
from .gui_bridge import Bridge
from 工具管理模块.registry import Registry


def attach(app):
    if not hasattr(app,'log'):
        app.log=lambda text,tag='info':app.status_bar.configure(text=str(text)[-180:])
    store=Store(data_dir=os.environ.get('GODHAND_DATA_DIR'));connection=Connection(store)
    # Locate the original notebook without changing original page construction.
    def find(widget):
        if isinstance(widget,ttk.Notebook):return widget
        for child in widget.winfo_children():
            result=find(child)
            if result:return result
    notebook=find(app.root)
    if notebook is None:raise RuntimeError('找不到主导航 Notebook')
    ops_notebook=getattr(getattr(app,'ops',None),'_godhand_ops_notebook',None)
    if ops_notebook is None:raise RuntimeError('找不到综合运维页面，无法挂载防御中心')
    if not getattr(app,'_godhand_bridge',None):app._godhand_bridge=Bridge(app,connection)
    if not getattr(app,'_godhand_defense_tab',None):
        from .defense_gui import DefenseTab
        page=ttk.Frame(ops_notebook,padding=4)
        ops_notebook.add(page,text='  防御中心  ')
        app._godhand_defense_tab=DefenseTab(page,app,app._godhand_bridge,store)
    tools=ttk.Frame(notebook,padding=8);ai=ttk.Frame(notebook,padding=12)
    notebook.add(tools,text='工具管理');notebook.add(ai,text='AI 中枢')
    inner=ttk.Notebook(tools);inner.pack(fill='both',expand=True)
    manager=ttk.Frame(inner,padding=8);legacy=ttk.Frame(inner)
    inner.add(manager,text='工具登记与任务');inner.add(legacy,text='批量脚本 / PHP 工具')
    import sys
    module=sys.modules[type(app).__module__]
    app.legacy_tools=module.ToolsTab(legacy,app.log)
    from .hub_gui import HubPanel
    registry=Registry(store)
    panel=HubPanel(ai,app,store,connection)
    app._godhand_hub_panel=panel
    background=panel.background
    search=tk.StringVar();filterbar=ttk.Frame(manager);filterbar.pack(fill='x')
    ttk.Label(filterbar,text='搜索名称 / 类型').pack(side='left');ttk.Entry(filterbar,textvariable=search).pack(side='left',fill='x',expand=True)
    tree=ttk.Treeview(manager,columns=('name','kind','status'),show='headings',height=5)
    for key,title in [('name','工具'),('kind','类型'),('status','依赖检查')]:tree.heading(key,text=title);tree.column(key,width=220)
    tree.pack(fill='x',pady=5)
    tool_rows=[]
    def refresh(*_):
        nonlocal tool_rows
        tree.delete(*tree.get_children());needle=search.get().lower()
        tool_rows=registry.status()
        for row in tool_rows:
            if needle in (row['name']+row['kind']+row['id']).lower():tree.insert('','end',iid=row['id'],values=(row['name'],row['kind'],row['reason']))
    search.trace_add('write',refresh);refresh()
    editor=tk.Text(manager,height=6);editor.pack(fill='x')
    editor.insert('end',json.dumps({'id':'my_tool','name':'我的工具','kind':'program','executable':'C:/Tools/example.exe','arguments':[],'working_directory':'','timeout':120,'parameters':{},'dependencies':[],'read_only':False},ensure_ascii=False,indent=2))
    params=tk.StringVar(value='{}');target=tk.StringVar(value='local')
    row=ttk.Frame(manager);row.pack(fill='x',pady=3);ttk.Label(row,text='参数 JSON').pack(side='left');ttk.Entry(row,textvariable=params).pack(side='left',fill='x',expand=True);ttk.Label(row,text='目标标识').pack(side='left');ttk.Entry(row,textvariable=target,width=22).pack(side='left')
    def selected():
        ids=tree.selection()
        if not ids:raise ValueError('请先选择工具')
        return ids[0]
    def guarded(fn):
        def run():
            try:fn()
            except Exception as exc:messagebox.showerror('工具管理',str(exc))
        return run
    def edit():editor.delete('1.0','end');editor.insert('end',json.dumps(asdict(registry.get(selected())),ensure_ascii=False,indent=2))
    def run_tool():
        identity=selected();values=json.loads(params.get());which=target.get();background(lambda:connection.call('tool',identity=identity,target_id=which,parameters=values,caller='desktop'))
    def tool_paths():
        from 工具管理模块.paths import KEYS,settings,resolve,save as save_paths
        win=tk.Toplevel(app.root);win.title('GodHand — 工具路径');win.transient(app.root)
        values=settings(store.root,store.path);variables={}
        ttk.Label(win,text='留空使用 tools 扩展目录及系统 PATH；选择本机程序后立即生效。').pack(anchor='w',padx=12,pady=10)
        for key in KEYS:
            row=ttk.Frame(win,padding=5);row.pack(fill='x',padx=8)
            ttk.Label(row,text=key,width=12).pack(side='left')
            var=variables[key]=tk.StringVar(value=values.get(key,''))
            ttk.Entry(row,textvariable=var,width=65).pack(side='left',fill='x',expand=True)
            def browse(v=var,k=key):
                chosen=filedialog.askopenfilename(parent=win,title='选择 '+k+' 程序／文件')
                if chosen:v.set(chosen)
            ttk.Button(row,text='浏览',command=browse).pack(side='left')
            ttk.Label(win,text='当前检测：'+(resolve(key,store.root,store.path) or '缺失'),wraplength=680).pack(anchor='w',padx=18)
        def saved():
            try:save_paths(store.root,store.path,{k:v.get().strip() for k,v in variables.items()});refresh();win.destroy()
            except Exception as exc:messagebox.showerror('路径未保存',str(exc),parent=win)
        ttk.Button(win,text='保存',command=saved).pack(pady=10)
    row=ttk.Frame(manager);row.pack(fill='x')
    for title,fn in [('刷新依赖',refresh),('工具路径',tool_paths),('编辑定义',edit),('保存定义',lambda:(registry.save(json.loads(editor.get('1.0','end'))),refresh())),('删除自定义',lambda:(registry.remove(selected()),refresh())),('启动',run_tool)]:ttk.Button(row,text=title,command=guarded(fn)).pack(side='left',padx=2)
    tasks=ttk.Treeview(manager,columns=('op','target','status'),show='headings',height=5)
    for key,title in [('op','任务'),('target','目标'),('status','状态')]:tasks.heading(key,text=title)
    tasks.pack(fill='both',expand=True,pady=5)
    log=tk.Text(manager,height=5);log.pack(fill='both',expand=True)
    def task_refresh():
        if getattr(app,'_closing',False):return
        rows=store.tasks(50);selected_task=tasks.selection()
        latest={r['id'] for r in rows}
        for identity in tasks.get_children():
            if identity not in latest:tasks.delete(identity)
        for row in rows:
            values=(row['operation'],row['target'],row['status'])
            if tasks.exists(row['id']):tasks.item(row['id'],values=values)
            else:tasks.insert('','end',iid=row['id'],values=values)
        if selected_task:
            events=store.events(selected_task[0],0,100);log.delete('1.0','end');log.insert('end','\n'.join(json.dumps(e['payload'],ensure_ascii=False) for e in events))
        app.root.after(1000,task_refresh)
    ttk.Button(manager,text='停止选中任务',command=guarded(lambda:connection.call('cancel',task_id=tasks.selection()[0]))).pack(anchor='w')
    task_refresh()
    old_close=app.on_close
    def close():
        if getattr(app,'_godhand_bridge',None):app._godhand_bridge.close()
        # Keep the UI responsive while child process cleanup runs.
        panel.close()
        background(connection.close,lambda _:old_close())
    app.on_close=close;app.root.protocol('WM_DELETE_WINDOW',close);app._godhand_connection=connection
    if os.environ.get('GODHAND_CHECK_UI')=='1':
        report={'product':app.root.title(),'window_logo':bool(app._window_logo),'banner_logo':bool(app._banner_logo),'dashboard_logo':bool(getattr(app.dash,'_logo',None)),'target_picker':True,'tabs':[notebook.tab(t,'text') for t in notebook.tabs()],'legacy_ops':len(app.ops._godhand_ops_notebook.tabs()),'defense_center':any('防御中心' in app.ops._godhand_ops_notebook.tab(t,'text') for t in app.ops._godhand_ops_notebook.tabs()),'tools':tool_rows}
        app.root.after(250,app._godhand_bridge.close)
        atomic_json(store.path/'ui-check.json',report);app.root.after(300,old_close)
    else:panel.start()
