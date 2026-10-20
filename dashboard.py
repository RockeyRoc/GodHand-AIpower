#!/usr/bin/env python3
"""Dashboard总览 — 目标数/存活数/Flag数/告警数 卡片+趋势图"""
from AI中枢.version import VERSION

import os, sys
from datetime import datetime
import tkinter as tk
from tkinter import scrolledtext
import ttkbootstrap as tb
from ttkbootstrap.constants import *


import json, threading, tempfile, re
from pathlib import Path
from collections import deque
COUNTERS = ('total_targets', 'alive_targets', 'total_flags', 'total_alerts', 'total_waf', 'total_shells')
HISTORY_LIMIT = 200
_IO_LOCK = threading.RLock()


def state_path(source_file, *, frozen=False, executable=None):
    base = Path(executable if frozen else source_file).resolve().parent
    return base / '管理工具' / 'dashboard_state.json'

def valid_counters(values):
    return {k: v for k, v in values.items()
            if k in COUNTERS and type(v) is int and 0 <= v <= 2**63 - 1}

def normalize_state(raw):
    result = dict.fromkeys(COUNTERS, 0)
    result['history'] = []
    if not isinstance(raw, dict):
        return result
    result.update(valid_counters(raw))
    history = raw.get('history', [])
    if isinstance(history, list):
        for item in history[-HISTORY_LIMIT:]:
            if not isinstance(item, dict) or not isinstance(item.get('t'), str):
                continue
            try:
                datetime.fromisoformat(item['t'])
            except ValueError:
                continue
            counters = valid_counters(item)
            if counters:
                result['history'].append({'t': item['t'], **counters})
    return result

def atomic_json_write(path, data):
    """Replace only after a complete write; preserve the old file on failure."""
    path = Path(path)
    with _IO_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

class DashboardState:
    def __init__(self, path, legacy_path=None):
        self.path = Path(path)
        self.warning = ''
        self.writable = True
        self.data = normalize_state({})
        candidate = self.path if self.path.exists() else Path(legacy_path) if legacy_path else None
        if candidate is not None and candidate.exists():
            try:
                if candidate.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('状态文件超过 2 MiB')
                with candidate.open(encoding='utf-8-sig') as stream:
                    raw = json.load(stream)
                if not isinstance(raw, dict):
                    raise ValueError('状态文件应为 JSON 对象')
                self.data = normalize_state(raw)
            except (OSError, ValueError) as exc:
                self.warning = f'状态文件读取失败，已保留原文件并暂停覆盖：{exc}'
                self.writable = False

    def update(self, values):
        values = valid_counters(values)
        if not values:
            return False
        self.data.update(values)
        snapshot = {k: self.data[k] for k in COUNTERS}
        self.data['history'].append({'t': datetime.now().isoformat(timespec='seconds'), **snapshot})
        self.data['history'] = self.data['history'][-HISTORY_LIMIT:]
        return True

    def save(self):
        if self.writable:
            atomic_json_write(self.path, self.data)

def alert_samples(history, limit=20):
    """Read actual alert totals, carrying forward partial legacy samples."""
    points = []
    value = 0
    for sample in history:
        counters = valid_counters(sample)
        value = counters.get('total_alerts', value)
        points.append((sample.get('t', ''), value))
    return points[-limit:]

class DashboardEvents:
    """Workers only enqueue Python data. All Tk calls stay on the UI thread."""
    def __init__(self, log_limit=512):
        self._lock = threading.Lock()
        self._stats = {}
        self._logs = deque(maxlen=log_limit)
        self.closed = False
        self.dropped = 0

    def stats(self, values):
        with self._lock:
            if not self.closed:
                self._stats.update(valid_counters(values))

    def log(self, message, tag):
        with self._lock:
            if self.closed:
                return
            if len(self._logs) == self._logs.maxlen:
                self.dropped += 1
            self._logs.append((redact_secrets(str(message))[:4096], tag))

    def drain(self):
        with self._lock:
            values, logs, dropped = self._stats, list(self._logs), self.dropped
            self._stats = {}
            self._logs.clear()
            self.dropped = 0
        return values, logs, dropped

    def close(self):
        with self._lock:
            self.closed = True
            self._stats.clear()
            self._logs.clear()

def redact_secrets(message):
    text = str(message)
    text = re.sub(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'''(?im)(["']?\b(?:Authorization|Cookie|Set-Cookie)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]+)''',
                  r'\1[REDACTED]', text)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    keys = r'(?:password|passwd|pwd|token|access_token|api[_-]?key|secret|client_secret|密码|口令)'
    pattern = r'''(?i)(["']?\b''' + keys + r'''\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&,;\r\n}]+)'''
    return re.sub(pattern, r'\1[REDACTED]', text)

DASH_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = state_path(__file__, frozen=getattr(sys, 'frozen', False), executable=sys.executable)

C = {
    'bg': '#070b14', 'card_bg': '#0d1526', 'border': '#1b2a4a',
    'text': '#d6e4ff', 'text_dim': '#6b7f9e', 'accent': '#00d4ff',
    'green': '#00ff9d', 'red': '#ff4d6d', 'yellow': '#d29922', 'purple': '#bc8cff',
}

class DashboardTab:
    def __init__(self, parent, tabs_ref=None):
        self.parent = parent
        self.tabs = tabs_ref
        self._events = DashboardEvents()
        self._closed = False
        self._poll_id = self._clock_id = None
        self._save_warning = ''
        self._reported = set()
        self.state = self.load_state()
        self.build_ui()
        self.parent.bind('<Destroy>', self._on_destroy, add='+')
        if self._store.warning:
            self.log_activity(self._store.warning, 'alert')
        self.refresh_ui()
        self._drain_events()

    def load_state(self):
        self._store = DashboardState(STATE_FILE, os.path.join(DASH_DIR, 'dashboard_state.json'))
        return self._store.data

    def save_state(self):
        try:
            self._store.save()
            self._save_warning = ''
        except OSError as exc:
            message = f'统计保存失败，内存中的数据仍保留：{exc}'
            if message != self._save_warning:
                self._save_warning = message
                self.log_activity(message, 'alert')

    def update_stats(self, **kw):
        self._reported.update(valid_counters(kw))
        self._events.stats(kw)

    def _drain_events(self):
        if self._closed:
            return
        values, logs, dropped = self._events.drain()
        if self._store.update(values):
            self.save_state()
            self.refresh_ui()
        if dropped:
            self._append_activity(f'活动过多，已省略 {dropped} 条较早记录', 'alert')
        for message, tag in logs:
            self._append_activity(message, tag)
        self._poll_id = self.parent.after(100, self._drain_events)

    def _on_destroy(self, event):
        if event.widget == self.parent:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        values, _, _ = self._events.drain()
        if self._store.update(values):
            self.save_state()
        self._events.close()
        for callback in (self._poll_id, self._clock_id):
            if callback is not None:
                try:
                    self.parent.after_cancel(callback)
                except tk.TclError:
                    pass

    def refresh_ui(self):
        for kid, key in [('card_targets','total_targets'),('card_alive','alive_targets'),
                          ('card_flags','total_flags'),('card_alerts','total_alerts'),
                          ('card_waf','total_waf'),('card_shells','total_shells')]:
            if kid in self.cards: self.cards[kid].config(text=str(self.state.get(key, 0)) if key in self._reported else '—')
        last = self.state.get('history', [])
        if last and 'lbl_last' in dir(self):
            self.lbl_last.config(text=f'最后更新: {last[-1]["t"][:19]}')
        self.draw_trend()

    def build_ui(self):
        f = tk.Frame(self.parent, bg=C['bg'])
        f.pack(fill=tk.BOTH, expand=True)

        # ── 顶部霓虹指挥台横幅 ──
        head = tk.Frame(f, bg=C['bg'])
        head.pack(fill=tk.X, padx=12, pady=(10,0))
        self.banner = tk.Frame(head, bg='#082540', height=66)
        self.banner.pack(fill=tk.X)
        self.banner.pack_propagate(False)
        # 顶部青线
        tk.Frame(self.banner, bg='#00d4ff', height=2).pack(side=tk.TOP, fill=tk.X)
        # 主体行
        row = tk.Frame(self.banner, bg='#082540')
        row.pack(fill=tk.BOTH, expand=True)
        # 左侧 LOGO
        logo_frame = tk.Frame(row, bg='#082540')
        logo_frame.pack(side=tk.LEFT, padx=(14, 0))
        from AI中枢.branding import logo_photo
        self._logo = logo_photo(self.parent, 44)
        tk.Label(logo_frame, image=self._logo, bg='#082540').pack(side=tk.LEFT, padx=(0, 8), pady=8)
        # 左侧文字
        left = tk.Frame(row, bg='#082540')
        left.pack(side=tk.LEFT, fill=tk.Y, pady=10)
        tk.Label(left, text='GodHand', font=('微软雅黑', 18, 'bold'),
            fg='#e8f6ff', bg='#082540').pack(anchor=tk.W)
        tk.Label(left, text='作战指挥台  ·  SECURITY COMMAND CENTER',
            font=('微软雅黑', 9), fg=C['accent'], bg='#082540').pack(anchor=tk.W)
        # 右侧信息
        right = tk.Frame(row, bg='#082540')
        right.pack(side=tk.RIGHT, padx=(0, 14), pady=10)
        tk.Label(right, text=f'v{VERSION} · SYSTEM ONLINE', font=('微软雅黑', 8, 'bold'),
            fg='#00ff9d', bg='#082540').pack(anchor=tk.E)
        self.clock_lbl = tk.Label(right, text='', font=('微软雅黑', 12, 'bold'),
            fg=C['accent'], bg='#082540')
        self.clock_lbl.pack(anchor=tk.E)
        self.lbl_last = tk.Label(right, text='', font=('微软雅黑', 8),
            fg=C['text_dim'], bg='#082540')
        self.lbl_last.pack(anchor=tk.E)
        self._update_clock()
        # 底部暗线
        tk.Frame(self.banner, bg='#1b2a4a', height=1).pack(side=tk.BOTTOM, fill=tk.X)
        glow = tk.Frame(f, bg=C['accent'], height=2)
        glow.pack(fill=tk.X, padx=12, pady=(8,0))

        # ── 指标卡片 ──
        c1 = tk.Frame(f, bg=C['bg'])
        c1.pack(fill=tk.X, padx=12, pady=(4,6))
        self.cards = {}
        card_defs = [
            ('card_targets', '🌐 目标数', C['accent'], 'total_targets'),
            ('card_alive', '✅ 存活数', C['green'], 'alive_targets'),
            ('card_flags', '🏆 Flag数', C['yellow'], 'total_flags'),
            ('card_alerts', '🚨 告警数', C['red'], 'total_alerts'),
            ('card_waf', '🛡️ WAF部署', C['purple'], 'total_waf'),
            ('card_shells', '🕷️ 植入数（待采集）', '#ff7b72', 'total_shells'),
        ]
        for kid, lbl, col, key in card_defs:
            card = tk.Frame(c1, bg=C['card_bg'], bd=0, highlightthickness=1,
                highlightcolor=C['border'], highlightbackground=C['border'])
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
            # 顶部彩色装饰线
            tk.Frame(card, bg=col, height=3).pack(fill=tk.X)
            n = tk.Label(card, text=str(self.state.get(key, 0)),
                fg=col, bg=C['card_bg'], font=('微软雅黑', 22, 'bold'))
            n.pack(pady=(6,0))
            tk.Label(card, text=lbl, fg=C['text_dim'], bg=C['card_bg'],
                font=('微软雅黑', 9)).pack(pady=(0,6))
            self.cards[kid] = n

        # ── 快捷操作 ──
        c2 = tk.Frame(f, bg=C['bg'])
        c2.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(c2, text='⚡ 快捷操作', font=('微软雅黑', 9, 'bold'),
            fg=C['text'], bg=C['bg']).pack(anchor=tk.W, pady=(0,6))
        btn_f = tk.Frame(c2, bg=C['bg'])
        btn_f.pack(fill=tk.X)
        for txt, cmd, col in [('部署WAF',self.q_waf,'#7c5cff'),('Get Flag',self.q_flag,'#c084fc'),
            ('一键加固',self.q_harden,'#ff4d6d'),('备份网站',self.q_backup,'#00ff9d')]:
            b = tk.Button(btn_f, text=txt, command=cmd, bg=col, fg='white',
                relief=tk.FLAT, bd=0, padx=14, pady=4, cursor='hand2',
                font=('微软雅黑', 9, 'bold'), activebackground=C['text_dim'],
                highlightthickness=1, highlightcolor=col, highlightbackground=col)
            b.pack(side=tk.LEFT, padx=3)
            b.bind('<Enter>', lambda e, c=col, w=b: w.config(bg=C['bg'], fg=c))
            b.bind('<Leave>', lambda e, c=col, w=b: w.config(bg=c, fg='white'))

        # ── 趋势图 ──
        trend_f = tk.Frame(f, bg=C['bg'])
        trend_f.pack(fill=tk.X, padx=12, pady=(4,6))
        tk.Label(trend_f, text='📈 告警总数（最近 20 次采样）', font=('微软雅黑', 9, 'bold'),
            fg=C['text'], bg=C['bg']).pack(anchor=tk.W, pady=(0,4))
        self.trend_canvas = tk.Canvas(trend_f, bg=C['card_bg'], bd=0,
            highlightthickness=0, height=180)
        self.trend_canvas.pack(fill=tk.X)
        self.trend_canvas.bind('<Configure>', self.draw_trend)

        # ── 近期活动 ──
        log_f = tk.Frame(f, bg=C['bg'])
        log_f.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0,8))
        tk.Label(log_f, text='📋 近期活动', font=('微软雅黑', 9, 'bold'),
            fg=C['text'], bg=C['bg']).pack(anchor=tk.W, pady=(0,4))
        log_box = tk.Frame(log_f, bg=C['card_bg'], bd=0, highlightthickness=1,
            highlightcolor=C['border'], highlightbackground=C['border'])
        log_box.pack(fill=tk.BOTH, expand=True)
        self.activity_text = scrolledtext.ScrolledText(log_box, bg=C['card_bg'],
            fg=C['text_dim'], insertbackground=C['text'], font=('Consolas', 9),
            bd=0, highlightthickness=0, state=tk.DISABLED)
        self.activity_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        for t, c in [('flag',C['yellow']),('alert',C['red']),('ok',C['green']),
                      ('info',C['text_dim']),('cmd',C['accent'])]:
            self.activity_text.tag_config(t, foreground=c)

        self.show_welcome()

    def show_welcome(self):
        self.log_activity('✨ 安全监控面板已加载', 'ok')
        self.log_activity('\u2139\ufe0f 左侧Tab栏进入各功能模块', 'info')

    def _update_clock(self):
        if self._closed:
            return
        self.clock_lbl.config(text=datetime.now().strftime('%H:%M:%S'))
        self._clock_id = self.parent.after(1000, self._update_clock)

    def log_activity(self, msg, tag='info'):
        self._events.log(msg, tag)

    def _append_activity(self, msg, tag):
        if tag not in ('flag', 'alert', 'ok', 'info', 'cmd'):
            tag = 'info'
        self.activity_text.config(state=tk.NORMAL)
        ts = datetime.now().strftime('%H:%M:%S')
        self.activity_text.insert(tk.END, f'[{ts}] ', 'info')
        self.activity_text.insert(tk.END, msg + '\n', tag)
        line_count = int(self.activity_text.index('end-1c').split('.')[0])
        if line_count > 1001:
            self.activity_text.delete('1.0', f'{line_count - 1000}.0')
        self.activity_text.see(tk.END)
        self.activity_text.config(state=tk.DISABLED)

    def draw_trend(self, event=None):
        w = max(1, self.trend_canvas.winfo_width())
        h = max(1, self.trend_canvas.winfo_height())
        self.trend_canvas.delete('all')
        points = alert_samples(self.state.get('history', []))
        if not points:
            self.trend_canvas.create_text(w//2, h//2, text='数据不足，暂不显示趋势',
                fill=C['text_dim'], font=('微软雅黑', 9))
            return
        if w < 80 or h < 60:
            return
        vals = [value for _, value in points]
        max_v = max(vals) or 1
        slot = (w - 40) / len(vals)
        bar_w = max(1, slot * 0.65)
        baseline = h - 26
        self.trend_canvas.create_line(20, baseline, w - 20, baseline, fill=C['border'])
        for x, label, anchor in [(20, points[0][0][11:19], 'w'),
                                  (w - 20, points[-1][0][11:19], 'e')]:
            self.trend_canvas.create_text(x, h - 12, text=label, anchor=anchor,
                                          fill=C['text_dim'], font=('Consolas', 7))
        for i, v in enumerate(vals):
            bh = (v / max_v) * (h - 50)
            center = 20 + (i + 0.5) * slot
            if v:
                self.trend_canvas.create_rectangle(center - bar_w / 2, baseline - bh,
                    center + bar_w / 2, baseline, fill=C['red'], outline='', tags='bar')
            if slot >= 22:
                self.trend_canvas.create_text(
                    center, baseline - bh - 9,
                    text=str(v), fill=C['text_dim'], font=('Consolas', 7), tags='bar')

    # ── 快捷操作回调 ──
    def _switch_tab(self, idx):
        try:
            nb = self.parent.master
            if hasattr(nb, 'select'): nb.select(idx)
        except: pass

    def _get_ops_tab(self):
        if self.tabs and hasattr(self.tabs, 'ops'):
            return self.tabs.ops
        return None

    def q_ping(self):
        self.log_activity('快捷: 存活探测 — 请到 综合运维>存活探测 执行', 'cmd')
        self._switch_tab(5)

    def q_port(self):
        self.log_activity('快捷: 端口扫描 — 请到 综合运维>存活探测(端口模式)', 'cmd')
        self._switch_tab(5)

    def q_waf(self):
        self.log_activity('快捷: 部署WAF — 请到 WAF管理 输入目标后部署', 'cmd')
        self._switch_tab(1)

    def q_flag(self):
        self.log_activity('快捷: 获取Flag — 请到 Flag获取 配置目标后开始', 'cmd')
        self._switch_tab(2)

    def q_harden(self):
        self.log_activity('快捷: 一键加固 — 请到 综合运维>加固基线', 'cmd')
        self._switch_tab(5)

    def q_backup(self):
        self.log_activity('快捷: 备份网站 — 请到 综合运维>备份还原', 'cmd')
        self._switch_tab(5)
