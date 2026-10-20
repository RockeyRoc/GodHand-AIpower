"""Local target selection; MCP target imports never edit permission settings."""
import tkinter as tk
from tkinter import ttk,messagebox


class TargetPicker:
    def __init__(self,parent,store,current,on_apply):
        self.store=store;self.on_apply=on_apply;self.current=set(current)
        self.window=tk.Toplevel(parent);self.window.title('GodHand — 选择允许目标')
        self.window.geometry('1000x540');self.window.minsize(760,400);self.window.transient(parent)
        ttk.Label(self.window,text='点击第一列切换允许项；MCP 导入项按 ID 授权。确定后仍需保存配置并启动服务。').pack(anchor='w',padx=12,pady=8)
        bar=ttk.Frame(self.window,padding=(12,0));bar.pack(fill='x')
        ttk.Label(bar,text='搜索名称 / 地址 / ID').pack(side='left')
        self.search=tk.StringVar();ttk.Entry(bar,textvariable=self.search).pack(side='left',fill='x',expand=True,padx=6)
        ttk.Button(bar,text='刷新靶机',command=self.reload).pack(side='left')
        body=ttk.Frame(self.window,padding=12);body.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(body,columns=('allow','name','ip','web','ssh','source','id'),show='headings',selectmode='none')
        for key,title,width in [('allow','允许',50),('name','名称',140),('ip','地址',160),('web','Web 端口',75),('ssh','SSH 端口',75),('source','来源',85),('id','目标 ID',240)]:
            self.tree.heading(key,text=title);self.tree.column(key,width=width,stretch=key in ('name','ip','id'))
        scroll=ttk.Scrollbar(body,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<Button-1>',self.toggle)
        self.note=ttk.Label(self.window,text='');self.note.pack(anchor='w',padx=12)
        bottom=ttk.Frame(self.window,padding=12);bottom.pack(fill='x')
        for title,fn in [('选中筛选结果',lambda:self.select_visible(True)),('取消筛选结果',lambda:self.select_visible(False)),('确定',self.apply),('取消',self.window.destroy)]:
            ttk.Button(bottom,text=title,command=fn).pack(side='left',padx=3)
        self.rows=[];self.selected=set();self.unlisted=set(current);self.initialized=False;self._signature=None;self._last_error='';self._reauth_seen={}
        self.search.trace_add('write',lambda *_:self.render())
        self.reload()
        self.window.after(1500,self._poll)

    def _poll(self):
        try:
            if not self.window.winfo_exists():return
            self.reload();self.window.after(1500,self._poll)
        except tk.TclError:pass

    def reload(self):
        try:
            def aliases(row):
                if row.get('source')!='legacy' or row.get('legacy_aliases_retired'):return [row['id']]
                return [row['id'],row.get('ip'),row.get('name')]+row.get('legacy_aliases',[])
            self.rows=[];seen=set()
            for row in self.store.machine_records():
                if row['id'] not in seen:self.rows.append(row);seen.add(row['id'])
            signature=repr(self.rows)
            if signature==self._signature:return
            self._signature=signature
            if not self.initialized:
                for row in self.rows:
                    if not row.get('requires_local_selection') and any(value in self.current for value in aliases(row)):self.selected.add(row['id'])
                known={value for row in self.rows for value in aliases(row)}
                self.unlisted=self.current-known
                self.initialized=True
            self.selected.intersection_update(row['id'] for row in self.rows)
            for row in self.rows:
                if row.get('requires_local_selection') and self._reauth_seen.get(row['id'])!=row['revision']:
                    self.selected.discard(row['id']);self._reauth_seen[row['id']]=row['revision']
            self.render()
        except Exception as exc:
            if str(exc)!=self._last_error:messagebox.showerror('无法读取靶机',str(exc),parent=self.window)
            self._last_error=str(exc)

    def render(self):
        self.tree.delete(*self.tree.get_children());needle=self.search.get().strip().casefold()
        for row in self.rows:
            if needle and needle not in ' '.join(str(row.get(k,'')) for k in ('id','name','ip')).casefold():continue
            identity=row['id']
            source={'mcp':'MCP 导入','legacy':'历史登记','desktop':'本机登记'}.get(row.get('source'),'机子管理')
            if row.get('requires_local_selection'):source+=' · 待重选'
            self.tree.insert('','end',iid=identity,values=('☑' if identity in self.selected else '☐',row.get('name',''),row.get('ip',''),row.get('port',''),row.get('ssh_port',''),source,identity))
        self.note.configure(text=f'共 {len(self.rows)} 台，已选 {len(self.selected)} 台。新导入项可用“刷新靶机”更新；原手工条目保留。')

    def toggle(self,event):
        if self.tree.identify_column(event.x)!='#1':return
        identity=self.tree.identify_row(event.y)
        if not identity:return
        if identity in self.selected:self.selected.remove(identity)
        else:self.selected.add(identity)
        self.render()

    def select_visible(self,checked):
        visible=set(self.tree.get_children())
        if checked:self.selected.update(visible)
        else:self.selected.difference_update(visible)
        self.render()

    def apply(self):
        self.on_apply(sorted(self.selected|self.unlisted));self.window.destroy()
