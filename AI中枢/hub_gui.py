"""Desktop connection settings with explicit service and configuration states."""
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import urllib.error

from .clientconfig import codex_path, install_codex, render, token_command
from .endpoint import MCP_URL, PORT, ensure_fixed_endpoint
from .server import client_configs


class HubPanel:
    def __init__(self, parent, app, store, connection):
        self.app, self.store, self.connection = app, store, connection
        self.canvas = tk.Canvas(parent, highlightthickness=0,
                                background=ttk.Style().lookup('TFrame', 'background') or '#0a0e17')
        scrollbar = ttk.Scrollbar(parent, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        parent = ttk.Frame(self.canvas, padding=(0, 0, 8, 0))
        content = self.canvas.create_window((0, 0), window=parent, anchor='nw')
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(content, width=event.width))
        parent.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.parent = parent
        self.phase = 'stopped'
        self.state = {'ready': False, 'starting': False}
        self.polling = False
        self.closed = False
        self.config_widgets = []
        config = store.reload_config()
        self.targets = tk.StringVar(value=', '.join(config['allowed_targets']))
        self.enabled = tk.StringVar(value=', '.join(config['enabled_operations']))
        self.platforms = tk.StringVar(value=', '.join(config.get('submission_endpoints', [])))
        self.auto = tk.BooleanVar(value=config.get('auto_authorize_imports', False))
        self.scope = tk.StringVar(value='用户级（所有项目）')
        self.advanced_visible = tk.BooleanVar(value=False)

        service = ttk.LabelFrame(parent, text='服务状态', padding=8)
        service.pack(fill='x', pady=(0, 6))
        self.status = ttk.Label(service, text='服务已停止')
        self.status.pack(anchor='w')
        ttk.Label(service, text='固定 HTTP 地址：' + MCP_URL).pack(anchor='w')
        self.next_step = ttk.Label(service, text='选择目标、保存配置后启动服务。', wraplength=680)
        self.next_step.pack(anchor='w', pady=3)
        bar = ttk.Frame(service)
        bar.pack(fill='x')
        self.start_button = ttk.Button(bar, text='启动服务', command=self.start)
        self.stop_button = ttk.Button(bar, text='停止服务', command=self.stop)
        self.check_button = ttk.Button(bar, text='连接自检', command=self.diagnose)
        for widget in (self.start_button, self.stop_button, self.check_button):
            widget.pack(side='left', padx=(0, 6))

        codex = ttk.LabelFrame(parent, text='Codex 接入', padding=8)
        codex.pack(fill='x', pady=(0, 6))
        ttk.Label(codex, text='默认 stdio：Codex 启动本机桥接进程，直接使用同一机子库，无需手工设置凭据。',
                  wraplength=680).pack(anchor='w')
        row = ttk.Frame(codex)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='配置位置').pack(side='left', padx=(0, 6))
        self.scope_box = ttk.Combobox(row, textvariable=self.scope,
                                     values=['用户级（所有项目）', '项目级（GodHand 目录）'], state='readonly', width=24)
        self.scope_box.pack(side='left')
        ttk.Button(row, text='写入 Codex 配置', command=self.install).pack(side='left', padx=6)
        ttk.Button(row, text='复制配置', command=lambda: self.copy(render(self.store))).pack(side='left')
        self.path_label = ttk.Label(codex, text='', wraplength=680)
        self.path_label.pack(anchor='w')
        self.scope.trace_add('write', lambda *_: self.refresh_path())
        self.refresh_path()
        self.preview = tk.Text(codex, height=4, wrap='word')
        self.preview.pack(fill='x', pady=(5, 0))
        self.refresh_preview()

        targets = ttk.LabelFrame(parent, text='允许目标', padding=8)
        targets.pack(fill='x', pady=(0, 6))
        self.summary = ttk.Label(targets, text='', wraplength=680)
        self.summary.pack(anchor='w')
        row = ttk.Frame(targets)
        row.pack(fill='x', pady=4)
        self.choose_button = ttk.Button(row, text='选择允许目标', command=self.choose_targets)
        self.choose_button.pack(side='left', padx=(0, 6))
        self.save_button = ttk.Button(row, text='保存配置', command=self.save)
        self.save_button.pack(side='left')
        self.config_widgets.extend([self.choose_button, self.save_button])
        self.targets.trace_add('write', lambda *_: self.refresh_summary())
        self.refresh_summary()
        ttk.Label(targets, text='修改顺序：停止服务 → 选择目标或调整高级设置 → 保存配置 → 启动服务。',
                  wraplength=680).pack(anchor='w')

        ttk.Checkbutton(parent, text='高级设置（能力、提交平台、HTTP 客户端）',
                        variable=self.advanced_visible, command=self.toggle_advanced).pack(anchor='w')
        self.advanced = ttk.LabelFrame(parent, text='高级设置', padding=8)
        for title, variable in [('启用能力（* 表示全部）', self.enabled), ('提交平台 URL（逗号分隔）', self.platforms)]:
            ttk.Label(self.advanced, text=title).pack(anchor='w')
            entry = ttk.Entry(self.advanced, textvariable=variable)
            entry.pack(fill='x', pady=(0, 4))
            self.config_widgets.append(entry)
        auto = ttk.Checkbutton(self.advanced, text='导入后自动授权新记录', variable=self.auto)
        auto.pack(anchor='w')
        self.config_widgets.append(auto)
        row = ttk.Frame(self.advanced)
        row.pack(fill='x', pady=5)
        for column, (title, command) in enumerate([
            ('导出通用配置', self.export), ('复制 HTTP 凭据命令', self.copy_token),
            ('轮换 HTTP 凭据', self.rotate)]):
            ttk.Button(row, text=title, command=command).grid(row=0, column=column, padx=(0, 5), sticky='w')
        ttk.Label(self.advanced, text='HTTP：在启动客户端的 PowerShell 中执行凭据命令，再启动客户端。\n'
                  'Codex 默认 stdio 接入不需要此步骤。', wraplength=680).pack(anchor='w')
        ttk.Label(parent, text='当前数据目录：' + str(store.path), wraplength=680).pack(anchor='w', pady=5)
        self.set_state('stopped')
        scroll_tag = 'GodHandHubScroll' + str(id(self))
        def scroll(event):
            if parent.winfo_reqheight() > self.canvas.winfo_height():
                self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
                return 'break'
        parent.bind_class(scroll_tag, '<MouseWheel>', scroll)
        def bind_scroll(widget):
            widget.bindtags((scroll_tag,) + widget.bindtags())
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(parent)

    def copy(self, text):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text)

    def refresh_path(self):
        self.path_label.configure(text=str(codex_path(self.selected_scope(), self.store.root)))

    def selected_scope(self):
        return 'user' if self.scope.get().startswith('用户') else 'project'

    def refresh_preview(self):
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('end', render(self.store))
        self.preview.configure(state='disabled')

    def refresh_summary(self):
        count = len([x for x in self.targets.get().split(',') if x.strip()])
        self.summary.configure(text=f'已选 {count} 项目标。' +
                               ('可在机子管理添加记录，或让 AI 导入后在此勾选。' if not count else 'AI 将使用已保存的连接资料。'))

    def toggle_advanced(self):
        if self.advanced_visible.get():
            self.advanced.pack(fill='x', pady=5)
        else:
            self.advanced.pack_forget()

    def set_state(self, phase, detail=''):
        if self.closed:
            return
        self.phase = phase
        self.state.update(ready=phase == 'running', starting=phase == 'starting')
        names = {'stopped': '服务已停止', 'starting': '正在启动服务…', 'running': '服务运行中',
                 'stopping': '正在停止服务…', 'saving': '正在保存配置…', 'failed': '服务连接失败'}
        self.status.configure(text=names[phase] + (' · ' + detail if detail else ''))
        notes = {'running': '可在 Codex 的 MCP 设置中重启 godhand，并确认客户端显示已连接。',
                 'stopped': '保存配置后启动服务；Codex stdio 也可按需启动后台。',
                 'failed': '按错误提示处理后重试；固定端口不会自动切换。',
                 'starting': '正在检查后台服务与当前比赛数据目录。',
                 'stopping': '正在结束本服务所属任务，请稍候。', 'saving': '正在保存目标与能力范围。'}
        self.next_step.configure(text=notes[phase])
        editable = phase in ('stopped', 'failed')
        for widget in self.config_widgets:
            widget.configure(state='normal' if editable else 'disabled')
        self.start_button.configure(state='normal' if editable else 'disabled')
        self.stop_button.configure(state='normal' if phase == 'running' else 'disabled')
        self.check_button.configure(state='normal' if phase == 'running' else 'disabled')

    def background(self, fn, done=None, failed=None):
        def run():
            try:
                result = fn()
            except Exception as exc:
                self.app._dispatcher.post(failed or self.error, str(exc))
            else:
                self.app._dispatcher.post(done or (lambda _: None), result)
        threading.Thread(target=run, daemon=True).start()

    def error(self, detail):
        if not self.closed:
            messagebox.showerror('GodHand', detail, parent=self.app.root)

    def failed(self, detail):
        self.set_state('failed', detail)

    def start(self):
        if self.phase not in ('stopped', 'failed'):
            return
        self.set_state('starting')
        def run():
            ensure_fixed_endpoint(self.store)
            return self.connection.start()
        def done(_):
            self.set_state('running', '使用独立后台' if self.connection.attached else '随本窗口运行')
            if not self.polling:
                self.polling = True
                self.parent.after(2000, self.poll)
        self.background(run, done, self.failed)

    def stop(self):
        if self.phase != 'running':
            return
        self.set_state('stopping')
        self.background(self.connection.stop, lambda _: self.set_state('stopped'), self.failed)

    def poll(self):
        if self.closed:
            return
        if self.phase == 'running':
            self.background(self.connection.call, failed=lambda detail:
                            self.failed(detail) if self.phase == 'running' else None)
        self.parent.after(2000, self.poll)

    def choose_targets(self):
        if self.phase not in ('stopped', 'failed'):
            return
        from .targets_gui import TargetPicker
        def apply(ids):
            if self.phase not in ('stopped', 'failed'):
                self.error('服务已启动，请先停止服务再修改目标。')
                return
            self.targets.set(', '.join(ids))
        self.app._godhand_target_picker = TargetPicker(
            self.app.root, self.store, [x.strip() for x in self.targets.get().split(',') if x.strip()], apply)

    def save(self):
        if self.phase not in ('stopped', 'failed'):
            return
        if self.auto.get() and not self.store.reload_config().get('auto_authorize_imports'):
            if not messagebox.askyesno('自动授权新记录', '开启后，新导入的靶机将加入允许目标。确定开启？', parent=self.app.root):
                return
        changes = {'port': PORT, 'allowed_targets': [x.strip() for x in self.targets.get().split(',') if x.strip()],
                   'enabled_operations': [x.strip() for x in self.enabled.get().split(',') if x.strip()],
                   'submission_endpoints': [x.strip() for x in self.platforms.get().split(',') if x.strip()],
                   'auto_authorize_imports': bool(self.auto.get())}
        self.set_state('saving')
        def run():
            ensure_fixed_endpoint(self.store)
            try:
                self.connection.call()
            except urllib.error.URLError as exc:
                if isinstance(exc, urllib.error.HTTPError) or not isinstance(exc.reason, ConnectionRefusedError):
                    raise RuntimeError(self.connection.describe_error(exc)) from None
            else:
                raise RuntimeError('后台服务正在运行。请先点击启动服务以附着，再停止服务后保存。')
            self.store.save_config(changes)
        def done(_):
            self.refresh_preview()
            self.set_state('stopped', '配置已保存，可启动服务')
        self.background(run, done, self.failed)

    def install(self):
        scope = self.selected_scope()
        def done(result):
            messagebox.showinfo('Codex 配置已写入', '文件：' + result['path'] + '\n备份：' +
                                (result['backup'] or '新建配置文件') + '\n\n' + result['hint'], parent=self.app.root)
        self.background(lambda: install_codex(self.store, scope), done)

    def export(self):
        directory = filedialog.askdirectory(title='选择配置导出目录', parent=self.app.root)
        if directory:
            claude, codex = client_configs(self.store)
            Path(directory, 'claude.mcp.example.json').write_text(claude, encoding='utf-8')
            Path(directory, 'codex.mcp.example.toml').write_text(codex, encoding='utf-8')
            Path(directory, 'generic-http.config.toml').write_text(render(self.store, 'http'), encoding='utf-8')
            messagebox.showinfo('配置已导出', '已导出 Codex stdio 与通用 HTTP 配置；配置文件不含凭据。', parent=self.app.root)

    def copy_token(self):
        self.copy(token_command(self.store))
        messagebox.showinfo('HTTP 凭据命令已复制', '在用于启动 HTTP 客户端的 PowerShell 中粘贴执行，随后从该终端启动客户端。\n'
                            '该命令只设置当前终端变量。Codex 默认 stdio 无需此步骤。', parent=self.app.root)

    def rotate(self):
        self.store.rotate_token()
        messagebox.showinfo('HTTP 凭据已轮换', 'HTTP 客户端请重新复制凭据命令并连接；stdio 配置保持可用。', parent=self.app.root)

    def diagnose(self):
        def done(result):
            detail = '\n'.join(item['name'] + '：' + item['detail'] for item in result['checks'])
            detail += '\n\n客户端连接状态请在 Codex 的 MCP 设置中确认。'
            if result['status'] == 'ok':
                messagebox.showinfo('后台与协议自检通过', detail, parent=self.app.root)
            else:
                self.error(detail)
        self.background(self.connection.diagnose, done)

    def close(self):
        self.closed = True
