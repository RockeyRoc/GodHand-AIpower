"""Tk defense center that submits the same OperationSpecs used by MCP."""
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from 防御模块.engine import catalogue


ORDER = [
    "defense_probe", "defense_fail2ban_status", "defense_watchbird_status", "defense_watchbird_logs",
    "defense_watchbird_stats", "defense_lynis_audit", "defense_yara_scan", "defense_aide_check",
    "defense_workflow_run", "defense_report", "defense_fail2ban_configure", "defense_fail2ban_ban",
    "defense_fail2ban_unban", "defense_fail2ban_rollback", "defense_watchbird_configure",
    "defense_watchbird_rollback", "defense_waf_switch", "defense_waf_rollback", "defense_aide_init",
    "defense_aide_promote",
]


class DefenseTab:
    def __init__(self, parent, app, bridge, store):
        self.parent, self.app, self.bridge, self.store = parent, app, bridge, store
        self.specs = catalogue()
        self.target_rows = {}
        self.field_vars = {}
        self.field_widgets = {}
        self.current_row = None
        self._build()
        self.refresh_targets()
        self.refresh_tasks()

    def _build(self):
        outer = ttk.Frame(self.parent, padding=10)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="防御中心", font=("微软雅黑", 15, "bold")).pack(side="left")
        ttk.Label(top, text="首次连接先检查并收集证据；封禁、配置和基线更新由你对所选目标单独提交。", wraplength=750).pack(side="left", padx=16)

        split = ttk.Panedwindow(outer, orient="horizontal")
        split.pack(fill="both", expand=True, pady=(10, 0))
        controls = ttk.Frame(split, padding=(0, 0, 8, 0))
        records = ttk.Frame(split)
        split.add(controls, weight=1)
        split.add(records, weight=2)

        target_row = ttk.Frame(controls)
        target_row.pack(fill="x", pady=3)
        ttk.Label(target_row, text="授权目标", width=12).pack(side="left")
        self.target_var = tk.StringVar()
        self.target_box = ttk.Combobox(target_row, textvariable=self.target_var, state="readonly")
        self.target_box.pack(side="left", fill="x", expand=True)
        self.target_box.bind("<<ComboboxSelected>>", self._target_changed)
        ttk.Button(target_row, text="刷新", command=self.refresh_targets).pack(side="left", padx=(4, 0))

        action_row = ttk.Frame(controls)
        action_row.pack(fill="x", pady=3)
        ttk.Label(action_row, text="检查 / 操作", width=12).pack(side="left")
        self.action_var = tk.StringVar()
        actions = [identity for identity in ORDER if identity in self.specs]
        self.action_labels = {self.specs[identity].title + "  ·  " + identity: identity for identity in actions}
        self.action_box = ttk.Combobox(action_row, textvariable=self.action_var, state="readonly", values=list(self.action_labels))
        self.action_box.pack(side="left", fill="x", expand=True)
        if self.action_labels:
            self.action_var.set(next(iter(self.action_labels)))
        self.action_box.bind("<<ComboboxSelected>>", self._action_changed)
        self.description = ttk.Label(controls, text="", wraplength=440, justify="left")
        self.description.pack(fill="x", pady=(2, 7))

        self.fields_frame = ttk.LabelFrame(controls, text="参数", padding=7)
        self.fields_frame.pack(fill="x", pady=4)
        bottom = ttk.Frame(controls)
        bottom.pack(fill="x", pady=6)
        ttk.Label(bottom, text="超时秒数").pack(side="left")
        self.timeout_var = tk.StringVar(value="180")
        ttk.Entry(bottom, textvariable=self.timeout_var, width=7).pack(side="left", padx=5)
        ttk.Button(bottom, text="提交任务", command=self.submit).pack(side="right")

        tasks_head = ttk.Frame(records)
        tasks_head.pack(fill="x")
        ttk.Label(tasks_head, text="防御任务与发现", font=("微软雅黑", 11, "bold")).pack(side="left")
        ttk.Button(tasks_head, text="刷新任务", command=self.refresh_tasks).pack(side="right")
        columns = ("operation", "target", "status", "updated")
        self.tree = ttk.Treeview(records, columns=columns, show="headings", height=10)
        for col, title, width in (("operation", "能力", 200), ("target", "目标", 150), ("status", "状态", 90), ("updated", "更新时间", 140)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=col in ("operation", "target"))
        self.tree.pack(fill="both", expand=True, pady=5)
        self.tree.bind("<<TreeviewSelect>>", self._task_selected)
        task_buttons = ttk.Frame(records)
        task_buttons.pack(fill="x")
        ttk.Button(task_buttons, text="取消所选任务", command=self.cancel_selected).pack(side="left")
        ttk.Button(task_buttons, text="导出 Markdown", command=lambda: self.export_selected_report("md")).pack(side="left", padx=5)
        ttk.Button(task_buttons, text="导出 JSON", command=lambda: self.export_selected_report("json")).pack(side="left")
        self.detail = tk.Text(records, height=14, wrap="word")
        self.detail.pack(fill="both", expand=True, pady=(5, 0))
        self._action_changed()

    def _selected_action(self):
        return self.action_labels.get(self.action_var.get(), "")

    def _action_changed(self, _event=None):
        for child in self.fields_frame.winfo_children():
            child.destroy()
        self.field_vars.clear()
        self.field_widgets.clear()
        identity = self._selected_action()
        spec = self.specs.get(identity)
        if not spec:
            return
        self.description.configure(text=("只读检查" if spec.read_only else "会更改所选目标") + "。" + spec.description)
        for index, (name, descriptor) in enumerate(spec.fields.items()):
            row = ttk.Frame(self.fields_frame)
            row.grid(row=index * 2, column=0, sticky="ew", pady=2)
            self.fields_frame.columnconfigure(0, weight=1)
            label = name + (" *" if descriptor.get("required") else "")
            ttk.Label(row, text=label, width=18).pack(side="left", anchor="nw")
            default = descriptor.get("default", "")
            choices = descriptor.get("choices")
            if isinstance(default, bool):
                variable = tk.StringVar(value="true" if default else "false")
                widget = ttk.Combobox(row, textvariable=variable, state="readonly", values=("true", "false"), width=22)
            else:
                value = ",".join(map(str, default)) if isinstance(default, list) else str(default)
                variable = tk.StringVar(value=value)
                widget = ttk.Combobox(row, textvariable=variable, values=choices, state="readonly" if choices else "normal", width=30) if choices else ttk.Entry(row, textvariable=variable)
            widget.pack(side="left", fill="x", expand=True)
            self.field_vars[name] = variable
            self.field_widgets[name] = descriptor
            if descriptor.get("description"):
                ttk.Label(self.fields_frame, text=descriptor["description"], wraplength=400, justify="left").grid(row=index * 2 + 1, column=0, sticky="w", padx=(18, 2))

    def refresh_targets(self):
        previous = self.target_var.get()
        rows = self.store.machine_records()
        self.target_rows = {"本机（仅报告） · local": {"id": "local", "ip": "local", "name": "本机", "allowed": True}}
        for row in rows:
            label = f"{row.get('name') or row.get('ip')} · {row.get('ip')} · {'已授权' if row.get('allowed') else '需授权'}"
            self.target_rows[label] = row
        values = list(self.target_rows)
        self.target_box.configure(values=values)
        self.target_var.set(previous if previous in self.target_rows else next((value for value in values if self.target_rows[value].get("allowed") and self.target_rows[value].get("id") != "local"), values[0]))
        self._target_changed()

    def _target_changed(self, _event=None):
        # The protected host's IP is not the management client's source IP.
        # Never infer or auto-fill Fail2Ban's allowlist from the selected target.
        return

    def _parameters(self):
        result = {}
        for name, variable in self.field_vars.items():
            value = variable.get()
            descriptor = self.field_widgets[name]
            if descriptor.get("kind") == "list":
                result[name] = [item.strip() for item in value.split(",") if item.strip()]
            elif isinstance(descriptor.get("default"), bool):
                result[name] = value.lower() == "true"
            elif value != "" or descriptor.get("required"):
                result[name] = value
        return result

    def submit(self):
        identity = self._selected_action()
        target = self.target_rows.get(self.target_var.get())
        if not identity or not target:
            messagebox.showerror("防御中心", "请选择能力和目标")
            return
        try:
            timeout = float(self.timeout_var.get())
            parameters = self._parameters()
            task_target = target.get("id", "local")
            if identity == "defense_report":
                task_target = "local"
            elif not target.get("allowed"):
                raise PermissionError("所选目标尚未在 AI 中枢中授权")
            self.bridge.submit_operation(identity, task_target, parameters, timeout,
                                         callback=lambda row, error: self._submitted(identity, row, error))
            self.detail.configure(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("end", "正在通过 GodHand 服务提交任务…")
        except Exception as exc:
            messagebox.showerror("防御任务未提交", str(exc))

    def _submitted(self, identity, row, error):
        if error:
            describe = getattr(self.bridge.connection, "describe_error", None)
            message = describe(error) if describe else str(error)
            self.app.log(identity + " 提交失败：" + message, "fail")
            self.detail.configure(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("end", message)
            return
        if row:
            task_id = row.get("id")
            if task_id:
                self._upsert_task(row)
                self.tree.selection_set(task_id)
                self.tree.see(task_id)
                self._show_row(row)
            if row.get("status") not in ("queued", "running"):
                self.refresh_tasks()

    def _upsert_task(self, row):
        identity = row.get("id", "")
        if not identity:
            return
        values = (row.get("operation", ""), row.get("target", ""), row.get("status", ""), str(row.get("updated", ""))[:19])
        if self.tree.exists(identity):
            self.tree.item(identity, values=values)
        else:
            self.tree.insert("", "end", iid=identity, values=values)

    def refresh_tasks(self):
        for row in self.store.tasks(200):
            if str(row.get("operation", "")).startswith("defense_"):
                self._upsert_task(row)

    def _task_selected(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        try:
            row = self.store.task(selected[0])
            self.current_row = row
            self._show_row(row)
        except Exception as exc:
            self.app.log("防御任务读取失败：" + str(exc), "fail")

    def _show_row(self, row):
        self.current_row = row
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", json.dumps(row, ensure_ascii=False, indent=2, default=str))
        self._upsert_task(row)

    def cancel_selected(self):
        selected = self.tree.selection()
        if selected:
            self.bridge.cancel_task(selected[0], lambda _row: self.refresh_tasks())

    def export_selected_report(self, kind="md"):
        row = self.current_row
        if not row:
            messagebox.showinfo("防御报告", "请先选择一条防御任务")
            return
        result = row.get("result") or {}
        data = result.get("data") or {}
        outputs = data.get("outputs") or {}
        candidate = (outputs.get("markdown_report") or outputs.get("report_markdown")) if kind == "md" else outputs.get("json_report")
        if kind == "json" and not candidate:
            candidate = ""
        elif not candidate:
            self.detail.insert("end", "\n\n此任务没有单独的 Markdown 报告文件。")
            return
        task_dir = (self.store.path / "tasks" / row["id"]).resolve()
        if candidate:
            source = Path(candidate).resolve()
            try:
                source.relative_to(task_dir)
            except ValueError:
                messagebox.showerror("防御报告", "报告文件不在该任务的产物目录中")
                return
            if not source.is_file():
                messagebox.showerror("防御报告", "任务报告文件不存在")
                return
            content = source.read_bytes()
        else:
            content = json.dumps(data.get("return", data), ensure_ascii=False, indent=2, default=str).encode("utf-8")
        extension = ".md" if kind == "md" else ".json"
        filetypes = [("Markdown", "*.md"), ("所有文件", "*.*")] if kind == "md" else [("JSON", "*.json"), ("所有文件", "*.*")]
        destination = filedialog.asksaveasfilename(title="导出防御报告", defaultextension=extension, filetypes=filetypes)
        if not destination:
            return
        Path(destination).write_bytes(content)
        self.app.log("防御报告已导出：" + destination, "info")
