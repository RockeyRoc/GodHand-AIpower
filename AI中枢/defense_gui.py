"""Deployment-only defense page. Investigation lives in AoiAWD Web."""
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlsplit
import webbrowser


class DefenseTab:
    def __init__(self, parent, app, bridge, store):
        self.parent, self.app, self.bridge, self.store = parent, app, bridge, store
        self.target_rows = {}
        self.current_task = None
        self._build()
        self.refresh_targets()

    def _build(self):
        outer = ttk.Frame(self.parent, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='防御中心 · AoiAWD 部署', font=('微软雅黑', 16, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='Core、Web 和 MongoDB 在 Windows 本机运行；靶机只部署探针，行为记录与告警在监测页面查看。', wraplength=900).pack(anchor='w', pady=(6, 18))
        service = ttk.LabelFrame(outer, text='本机监测服务', padding=10)
        service.pack(fill='x')
        self.service_text = tk.StringVar(value='部署时自动启动配套服务，无需 Docker。')
        ttk.Label(service, textvariable=self.service_text).pack(side='left', fill='x', expand=True)
        for label, operation in [('检查', 'status'), ('启动', 'start'), ('停止', 'stop')]:
            ttk.Button(service, text=label, command=lambda op=operation: self.submit('defense_aoi_' + op, local=True)).pack(side='left', padx=3)
        form = ttk.LabelFrame(outer, text='靶机探针', padding=12)
        form.pack(fill='x', pady=12)
        self.target_var = tk.StringVar()
        ttk.Label(form, text='授权靶机').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=6)
        self.target_box = ttk.Combobox(form, textvariable=self.target_var, state='readonly')
        self.target_box.grid(row=0, column=1, sticky='ew')
        self.target_box.bind('<<ComboboxSelected>>', self.target_changed)
        ttk.Button(form, text='刷新', command=self.refresh_targets).grid(row=0, column=2, padx=6)
        self.web_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text='PHP Web 探针（无 PHP 时自动跳过）', variable=self.web_var).grid(row=1, column=1, sticky='w', pady=6)
        self.webdir = tk.StringVar(value='/var/www/html')
        self.watch = tk.StringVar()
        self.elf = tk.StringVar()
        for row, label, variable in [(2, 'Web 根目录', self.webdir), (3, '文件监测目录', self.watch), (4, 'PWN ELF 路径', self.elf)]:
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=6)
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky='ew', pady=6)
        ttk.Label(form, text='监测目录留空时使用 /tmp 和 Web 根目录；多个目录用分号分隔。PWN 路径可留空，填写后生成独立 .guardianed 程序。', wraplength=800).grid(row=5, column=1, columnspan=2, sticky='w', pady=4)
        form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(outer)
        buttons.pack(fill='x', pady=(0, 12))
        for label, action in [('检查环境', 'probe'), ('部署探针', 'deploy'), ('打开监测', 'open'), ('卸载并恢复', 'remove')]:
            ttk.Button(buttons, text=label, command=lambda op=action: self.submit('defense_aoi_' + op)).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='取消当前任务', command=self.cancel).pack(side='right')
        self.output = tk.Text(outer, height=13, wrap='word', state='disabled')
        self.output.pack(fill='both', expand=True)
        self.write('选择靶机后部署探针。重复部署复用原部署；卸载会按备份恢复 PHP 文件。旧防御动作可继续通过 MCP 使用。')

    def write(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', str(text) + '\n')
        self.output.see('end')
        self.output.configure(state='disabled')

    def refresh_targets(self):
        previous = self.target_var.get()
        self.target_rows = {f"{row.get('name') or row['ip']} · SSH {row['ip']}:{row.get('ssh_port',22)}": row
            for row in self.store.machine_records() if row.get('allowed')}
        values = list(self.target_rows)
        self.target_box.configure(values=values)
        self.target_var.set(previous if previous in self.target_rows else (values[0] if values else ''))
        self.target_changed()

    def target_changed(self, _event=None):
        row = self.target_rows.get(self.target_var.get(), {})
        self.webdir.set(row.get('webdir') or '/var/www/html')
        self.watch.set('')
        self.elf.set('')

    def submit(self, identity, local=False):
        target = self.target_rows.get(self.target_var.get())
        if not local and not target:
            messagebox.showerror('防御部署', '请先在 AI 中枢授权并选择靶机')
            return
        parameters = {}
        if identity == 'defense_aoi_deploy':
            parameters = {'web': self.web_var.get(), 'webdir': self.webdir.get().strip(),
                'elf_path': self.elf.get().strip(), 'watch_paths': [p.strip() for p in self.watch.get().split(';') if p.strip()]}
        self.bridge.submit_operation(identity, 'local' if local else target['id'], parameters, 600,
            callback=lambda row, error: self.completed(identity, row, error))

    def completed(self, operation, row, error):
        if error:
            self.write('操作未完成：' + str(error))
            return
        self.current_task = row.get('id')
        if row.get('status') in ('queued', 'running'):
            self.write('任务已提交，正在处理…')
            return
        result = row.get('result') or {}
        if row.get('status') != 'succeeded':
            self.write(result.get('error') or ('任务状态：' + row.get('status', '未知')))
            return
        value = (result.get('data') or {}).get('return') or {}
        if operation.endswith(('start', 'status')):
            self.service_text.set('Windows 监测服务运行中' if value.get('mongodb') else 'Windows 监测服务未启动')
        elif operation.endswith('stop'):
            self.service_text.set('Windows 监测服务已停止；探针保留，回传已断开')
        elif operation.endswith('deploy'):
            self.service_text.set('Windows 监测服务运行中')
            self.write('探针部署完成。PHP 文件：' + str(value.get('web_files', 0)) + '；' + value.get('web_status', '复用已有部署'))
            if value.get('guardian_path'):
                self.write('PWN 探针程序：' + value['guardian_path'])
        elif operation.endswith('probe'):
            self.write(value.get('details', '') + '\nWeb 根目录：' + value.get('webdir', ''))
        elif operation.endswith('remove'):
            self.write('探针已卸载，恢复 PHP 文件 ' + str(value.get('restored_files', 0)) + ' 个。')
        elif operation.endswith('open'):
            url = value.get('url', '')
            parsed = urlsplit(url)
            if parsed.scheme == 'http' and parsed.hostname == '127.0.0.1':
                webbrowser.open(url)
                self.write('已打开 ' + value.get('name', '') + ' 的行为监测页面。')

    def cancel(self):
        if self.current_task:
            self.bridge.cancel_task(self.current_task, lambda row: self.write('已请求取消，等待恢复结果。'))
