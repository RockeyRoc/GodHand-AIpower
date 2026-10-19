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
    tools=ttk.Frame(notebook,padding=8);ai=ttk.Frame(notebook,padding=12)
    notebook.add(tools,text='工具管理');notebook.add(ai,text='AI 中枢')
    inner=ttk.Notebook(tools);inner.pack(fill='both',expand=True)
    manager=ttk.Frame(inner,padding=8);legacy=ttk.Frame(inner)
    inner.add(manager,text='工具登记与任务');inner.add(legacy,text='批量脚本 / PHP 工具')
    import sys
    module=sys.modules[type(app).__module__]
    app.legacy_tools=module.ToolsTab(legacy,app.log)
    registry=Registry(store);state={'ready':False,'starting':False}
    label=ttk.Label(ai,text='MCP 服务准备中…');label.pack(anchor='w')
    form=ttk.Frame(ai);form.pack(fill='x',pady=8)
    port=tk.StringVar(value=str(store.config['port']));targets=tk.StringVar(value=', '.join(store.config['allowed_targets']));enabled=tk.StringVar(value=', '.join(store.config['enabled_operations']));platforms=tk.StringVar(value=', '.join(store.config.get('submission_endpoints',[])))
    for title,var in [('监听地址','127.0.0.1'),('端口',port),('允许目标（主机 IP 或标识，逗号分隔）',targets),('提交平台 URL（逗号分隔）',platforms),('启用能力（* 或能力标识，逗号分隔）',enabled)]:
        row=ttk.Frame(form);row.pack(fill='x',pady=3);ttk.Label(row,text=title,width=48).pack(side='left')
        ttk.Entry(row,textvariable=var if not isinstance(var,str) else None,state='normal' if not isinstance(var,str) else 'disabled').pack(side='left',fill='x',expand=True)
    buttons=ttk.Frame(ai);buttons.pack(fill='x',pady=5)
    preview=tk.Text(ai,height=12,wrap='word');preview.pack(fill='both',expand=True,pady=6)
    def configs():
        a,b=client_configs(store);return '# 所有 Agent 共用 /mcp；统一工具 godhand(action, arguments)\n# Claude Code .mcp.json\n'+a+'\n\n# Codex config.toml\n'+b
    def refresh_preview():preview.delete('1.0','end');preview.insert('end',configs())
    refresh_preview()
    def background(fn,done=None):
        def run():
            try:result=fn();app._dispatcher.post(done or (lambda _:None),result)
            except Exception as exc:app._dispatcher.post(messagebox.showerror,'GodHand',str(exc))
        threading.Thread(target=run,daemon=True).start()
    def started(_):
        state['ready']=True;state['starting']=False
        label.configure(text=('已附着独立服务' if connection.attached else '服务随本窗口运行')+'  '+connection.url.replace('/control','/mcp')+'  | 统一工具：godhand')
        if not getattr(app,'_godhand_bridge',None):app._godhand_bridge=Bridge(app,connection)
    def start():
        if state['starting']:return
        state['starting']=True
        def run():
            try:connection.start()
            finally:state['starting']=False
        background(run,started)
    def stop():
        background(connection.stop,lambda _:(state.update(ready=False),label.configure(text='服务已停止；启动后可继续提交任务')))
    def save():
        try:
            if state['ready']:raise ValueError('修改端口/范围前先停止服务；独立服务须在其终端停止')
            store.save_config({'port':int(port.get()),'allowed_targets':[x.strip() for x in targets.get().split(',') if x.strip()],'submission_endpoints':[x.strip() for x in platforms.get().split(',') if x.strip()],'enabled_operations':[x.strip() for x in enabled.get().split(',') if x.strip()]});refresh_preview()
        except Exception as exc:messagebox.showerror('配置未保存',str(exc))
    def export():
        directory=filedialog.askdirectory(title='选择配置示例导出目录')
        if directory:
            a,b=client_configs(store);Path(directory,'claude.mcp.example.json').write_text(a,encoding='utf-8');Path(directory,'codex.mcp.example.toml').write_text(b,encoding='utf-8')
    def token():app.root.clipboard_clear();app.root.clipboard_append(store.tokens()['mcp_token']);messagebox.showinfo('已复制凭据','在启动 Agent 的 PowerShell 中设置 $env:GODHAND_MCP_TOKEN；凭据仅复制到剪贴板，未写入示例。')
    def rotate():store.rotate_token();messagebox.showinfo('凭据已更新','旧凭据立即失效；请重新复制并重启客户端连接。')
    for title,fn in [('启动',start),('停止',stop),('保存配置',save),('健康检查',lambda:background(connection.call,lambda v:label.configure(text=json.dumps(v,ensure_ascii=False)))),('复制接入配置',lambda:(app.root.clipboard_clear(),app.root.clipboard_append(configs()))),('导出示例',export),('复制凭据',token),('轮换凭据',rotate)]:ttk.Button(buttons,text=title,command=fn).pack(side='left',padx=2)
    ttk.Label(ai,text='数据与日志：'+str(store.path)+'\n统一 MCP 工具：godhand；各 Agent 共用上方地址。允许目标留空时远程能力不可调用。').pack(anchor='w')
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
        if getattr(app,'_godhand_bridge',None):app._godhand_bridge.closed=True
        # Keep the UI responsive while child process cleanup runs.
        label.configure(text='正在停止窗口所属服务…')
        background(connection.close,lambda _:old_close())
    app.on_close=close;app.root.protocol('WM_DELETE_WINDOW',close);app._godhand_connection=connection
    if os.environ.get('GODHAND_CHECK_UI')=='1':
        report={'product':app.root.title(),'window_logo':bool(app._window_logo),'banner_logo':bool(app._banner_logo),'dashboard_logo':bool(getattr(app.dash,'_logo',None)),'tabs':[notebook.tab(t,'text') for t in notebook.tabs()],'legacy_ops':len(app.ops._godhand_ops_notebook.tabs()),'tools':tool_rows}
        atomic_json(store.path/'ui-check.json',report);app.root.after(300,old_close)
    else:start()
