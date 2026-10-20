"""Windows-owned AoiAWD services and reversible Linux probe deployment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
import socket
import stat
import struct
import subprocess
import threading
import time
import urllib.request
import uuid
from functools import partial

from AI中枢.core import OperationSpec, atomic_json, protect
from .engine import ssh_session as legacy_ssh_session, run_remote, safe_remote_path

ssh_session = partial(legacy_ssh_session, preserve_errors=True)

ASSETS = Path(__file__).with_name('aoi_assets')
BEGIN = b'/*GODHAND_AOI_BEGIN*/'
END = b'/*GODHAND_AOI_END*/'


def catalogue():
    field = lambda default='': {'kind': 'value', 'default': default}
    rows = {
        'defense_aoi_start': ('启动 Windows 监测服务', True, False, {}),
        'defense_aoi_stop': ('停止 Windows 监测服务及回传隧道', True, False, {}),
        'defense_aoi_status': ('检查本地监测服务', True, True, {}),
        'defense_aoi_probe': ('检查靶机探针部署条件', False, True, {}),
        'defense_aoi_deploy': ('部署 AoiAWD 探针', False, False, {
            'web': field(True), 'webdir': field(''), 'watch_paths': {'kind': 'list', 'default': []},
            'php_files': {'kind': 'list', 'default': []},
            'elf_path': field(''), 'process_interval': field(250)}),
        'defense_aoi_remove': ('卸载探针并恢复 PHP 文件', False, False, {}),
        'defense_aoi_open': ('连接目标的本地监测页面', False, True, {}),
    }
    return {name: OperationSpec(name, title, 'AoiHandler', name, [], readonly, local,
            'Windows 原生 AoiAWD Core、Web 和 MongoDB；靶机仅部署探针。监测页按目标隔离，SSH 回传不开放公网监听。', fields)
            for name, (title, local, readonly, fields) in rows.items()}


def inject_php(content: bytes, monitor_path: str) -> tuple[bytes, bytes]:
    """Insert after opening tag, declare and namespace, preserving original bytes."""
    if BEGIN in content:
        raise ValueError('PHP 文件已有 GodHand 探针，但缺少匹配的部署记录')
    if b'TAPEWORMINSTALLED' in content:
        raise ValueError('检测到已有 TapeWorm，请先恢复该部署')
    path = monitor_path.replace('\\', '\\\\').replace("'", "\\'")
    statement = ("\n/*GODHAND_AOI_BEGIN*/ if (is_file('" + path + "')) { include_once '" + path + "'; } /*GODHAND_AOI_END*/\n").encode()
    opening = re.match(rb'(?:\xef\xbb\xbf)?\s*<\?php\b', content)
    if not opening:
        addition = b'<?php' + statement + b'?>'
        return addition + content, addition
    offset = opening.end()
    trivia = re.compile(rb'(?:\s+|/\*.*?\*/|//[^\r\n]*(?:\r?\n|$)|\#[^\r\n]*(?:\r?\n|$))*', re.S)
    while True:
        pos = trivia.match(content, offset).end()
        declaration = re.match(rb'declare\s*\([^)]*\)\s*;', content[pos:])
        if not declaration:
            break
        offset = pos + declaration.end()
    pos = trivia.match(content, offset).end()
    namespace = re.match(rb'namespace\s*(?:[a-zA-Z_\\][a-zA-Z_0-9\\]*\s*)?[;{]', content[pos:])
    if namespace:
        offset = pos + namespace.end()
    return content[:offset] + statement + content[offset:], statement


def restore_php(current: bytes, original: bytes, addition: bytes, installed_hash: str) -> bytes:
    if hashlib.sha256(current).hexdigest() == installed_hash:
        return original
    if current.count(addition) != 1:
        raise ValueError('探针标记已被修改，保留当前文件及备份供人工恢复')
    return current.replace(addition, b'', 1)


class Cancelled(RuntimeError):
    pass


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ReverseTunnel:
    """The target listens on loopback; only its saved SSH connection is used."""
    def __init__(self, target, remote_port, local_port):
        self.target, self.remote_port, self.local_port = target, remote_port, local_port
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.error = ''
        self.ssh = None
        self.thread = threading.Thread(target=self._run, daemon=True, name='GodHand-Aoi-SSH')
        self.thread.start()

    def _run(self):
        while not self.closed.is_set():
            try:
                with ssh_session(self.target) as ssh:
                    self.ssh = ssh
                    transport = ssh.get_transport()
                    transport.set_keepalive(15)
                    assigned = transport.request_port_forward('127.0.0.1', self.remote_port)
                    if assigned != self.remote_port:
                        raise RuntimeError('SSH 未创建预期的探针回传端口')
                    self.error = ''
                    self.ready.set()
                    while not self.closed.is_set() and transport.is_active():
                        channel = transport.accept(.5)
                        if channel:
                            threading.Thread(target=self._relay, args=(channel,), daemon=True).start()
            except Exception as exc:
                self.error = str(exc)[:400]
            finally:
                self.ready.clear()
                self.ssh = None
            self.closed.wait(2)

    def _relay(self, channel):
        try:
            with channel, socket.create_connection(('127.0.0.1', self.local_port), 5) as local:
                while not self.closed.is_set():
                    readable, _, _ = select.select([channel, local], [], [], 1)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (local if source is channel else channel).sendall(data)
        except (OSError, EOFError):
            pass

    def close(self):
        self.closed.set()
        if self.ssh:
            self.ssh.close()
        self.thread.join(timeout=8)


class AoiRuntime:
    def __init__(self, store):
        self.store = store
        self.bundle = store.root / 'tools' / 'AoiAWD'
        self.state = store.path / 'aoi'
        self.state.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.processes = {}
        self.tunnels = {}
        self.records = {}
        self.config_file = self.state / 'runtime.dpapi'
        if self.config_file.exists():
            self.records = json.loads(protect(self.config_file.read_bytes(), True))
        self._saved_snapshot = json.dumps(self.records, sort_keys=True)
        self.mongo_port = _free_port()

    def _save(self):
        from AI中枢.core import _replace
        snapshot = json.dumps(self.records, sort_keys=True)
        if snapshot == self._saved_snapshot and self.config_file.exists():
            return
        temporary = self.config_file.with_name(self.config_file.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(protect(snapshot.encode()))
            _replace(temporary, self.config_file)
            self._saved_snapshot = snapshot
        finally:
            temporary.unlink(missing_ok=True)

    def _spawn(self, key, command, cwd, environment=None):
        from AI中枢.winjob import Job, resume
        log = (self.state / (key + '.log')).open('ab')
        try:
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                stderr=log, env=environment, creationflags=subprocess.CREATE_NO_WINDOW | 0x4)
            try:
                job = Job(process)
                resume(process)
            except Exception:
                process.kill()
                process.wait(timeout=5)
                raise
            self.processes[key] = (process, job)
        finally:
            log.close()

    def _alive(self, key):
        item = self.processes.get(key)
        return bool(item and item[0].poll() is None)

    def _wait(self, key, port, check):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            check()
            if not self._alive(key):
                raise RuntimeError('监测组件启动失败：' + key + '，请查看本地监测服务日志')
            try:
                with socket.create_connection(('127.0.0.1', port), .3):
                    return
            except OSError:
                time.sleep(.1)
        raise TimeoutError('监测组件未就绪：' + key)

    def start(self, check=lambda: None):
        if self._alive('mongodb'):
            return self.status()
        manifest_path = self.bundle / 'bundle.json'
        if not manifest_path.is_file():
            raise FileNotFoundError('缺少 Windows AoiAWD 配套运行环境，请运行防御模块/aoi_bundle.py 构建或使用完整便携版')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for relative, digest in manifest['files'].items():
            check()
            path = (self.bundle / relative).resolve()
            path.relative_to(self.bundle.resolve())
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('AoiAWD 配套文件校验失败：' + relative)
        database = self.state / 'database'
        database.mkdir(exist_ok=True)
        self._spawn('mongodb', [str(self.bundle / 'mongodb' / 'bin' / 'mongod.exe'),
            '--dbpath', str(database), '--bind_ip', '127.0.0.1', '--port', str(self.mongo_port),
            '--wiredTigerCacheSizeGB', '0.25', '--quiet'], self.state)
        try:
            self._wait('mongodb', self.mongo_port, check)
        except Exception:
            self.stop()
            raise
        return self.status()

    def _target_key(self, target):
        identity = target.get('id') or '|'.join(str(target.get(k, '')) for k in ('ip', 'ssh_port', 'ssh_user'))
        return hashlib.sha256(identity.encode()).hexdigest()[:16]

    def _backend(self, target, check):
        self.start(check)
        key = self._target_key(target)
        record = self.records.setdefault(key, {'web_port': _free_port(), 'log_port': _free_port(),
            'remote_port': 0, 'access_token': os.urandom(24).hex(), 'name': target.get('name', target['ip'])})
        self._save()
        process_key = 'core-' + key
        if not self._alive(process_key):
            directory = self.state / key
            directory.mkdir(exist_ok=True)
            plugins = directory / 'plugins'
            plugins.mkdir(exist_ok=True)
            (plugins / 'Observe.php').write_bytes((ASSETS / 'Observe.php').read_bytes())
            public = directory / 'public'
            public.mkdir(exist_ok=True)
            html = (ASSETS / 'index.html').read_text(encoding='utf-8')
            html = html.replace('__AOI_ACCESS_TOKEN__', record['access_token'])
            # Encode names as JavaScript string contents, including closing-script sequences.
            name = json.dumps(record['name'], ensure_ascii=True)[1:-1].replace("'", '\\u0027').replace('<', '\\u003c')
            (public / 'index.html').write_text(html.replace('__AOI_TARGET_NAME__', name), encoding='utf-8')
            environment = dict(os.environ, GODHAND_AOI_PUBLIC=str(public))
            self._spawn(process_key, [str(self.bundle / 'php' / 'php.exe'), '-c', str(self.bundle / 'php' / 'php.ini'),
                '-d', 'extension_dir=' + str(self.bundle / 'php'),
                str(self.bundle / 'core' / 'main.php'), '-w', 'tcp://127.0.0.1:' + str(record['web_port']),
                '-l', 'tcp://127.0.0.1:' + str(record['log_port']), '-m', 'mongodb://127.0.0.1:' + str(self.mongo_port),
                '-t', record['access_token']], directory, environment)
            try:
                self._wait(process_key, record['web_port'], check)
                self._wait(process_key, record['log_port'], check)
            except Exception:
                self._stop_process(process_key)
                raise
        return key, record

    def connect(self, target, check=lambda: None):
        key, record = self._backend(target, check)
        current = self.tunnels.get(key)
        if not current or current.closed.is_set():
            if not record['remote_port']:
                with ssh_session(target) as ssh:
                    # Let SSH select a free target port, then release it before the durable tunnel.
                    transport = ssh.get_transport()
                    record['remote_port'] = transport.request_port_forward('127.0.0.1', 0)
                    transport.cancel_port_forward('127.0.0.1', record['remote_port'])
                self._save()
            current = ReverseTunnel(target, record['remote_port'], record['log_port'])
            self.tunnels[key] = current
        deadline = time.monotonic() + 18
        while not current.ready.wait(.1):
            check()
            if time.monotonic() > deadline:
                current.close()
                self.tunnels.pop(key, None)
                raise RuntimeError('SSH 回传隧道不可用：' + current.error)
        return key, record

    def page(self, target, check=lambda: None):
        _, record = self.connect(target, check)
        return {'url': 'http://127.0.0.1:' + str(record['web_port']) + '/',
            'name': record['name'], 'remote_port': record['remote_port'], 'connected': True}

    def status(self):
        return {'platform': 'windows-native', 'mongodb': self._alive('mongodb'),
            'bundle_present': (self.bundle / 'bundle.json').is_file(),
            'targets': [{'name': row['name'], 'core': self._alive('core-' + key),
                'connected': bool(self.tunnels.get(key) and self.tunnels[key].ready.is_set()),
                'url': 'http://127.0.0.1:' + str(row['web_port']) + '/'} for key, row in self.records.items()]}

    def _stop_process(self, key):
        item = self.processes.pop(key, None)
        if item:
            process, job = item
            if key == 'mongodb' and process.poll() is None:
                code = 'try{$m=new MongoDB\\Driver\\Manager("mongodb://127.0.0.1:".$argv[1]."/?serverSelectionTimeoutMS=2000");$m->executeCommand("admin",new MongoDB\\Driver\\Command(["shutdown"=>1]));}catch(Throwable $e){}'
                try:
                    subprocess.run([str(self.bundle / 'php/php.exe'), '-c', str(self.bundle / 'php/php.ini'), '-d',
                        'extension_dir=' + str(self.bundle / 'php'), '-r', code, str(self.mongo_port)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            job.close()
            process.wait(timeout=10)

    def stop(self):
        for tunnel in list(self.tunnels.values()):
            tunnel.close()
        self.tunnels.clear()
        for key in list(self.processes):
            if key != 'mongodb':
                self._stop_process(key)
        self._stop_process('mongodb')
        return {'stopped': True, 'owned_processes_stopped': not self.processes, 'tunnels_closed': not self.tunnels}

    def execute(self, identity, target, parameters, check=lambda: None):
        with self.lock:
            check()
            if identity == 'defense_aoi_status':
                return self.status()
            if identity == 'defense_aoi_start':
                return self.start(check)
            if identity == 'defense_aoi_stop':
                return self.stop()
            if identity == 'defense_aoi_probe':
                return probe(target)
            if identity == 'defense_aoi_open':
                return self.page(target, check)
            if identity == 'defense_aoi_deploy':
                key, record = self.connect(target, check)
                result = deploy(target, parameters, self.bundle, record['remote_port'], check)
                result.update({'url': 'http://127.0.0.1:' + str(record['web_port']) + '/',
                    'name': record['name'], 'remote_port': record['remote_port'], 'connected': True})
                return result
            if identity == 'defense_aoi_remove':
                result = remove(target)
                key = self._target_key(target)
                tunnel = self.tunnels.pop(key, None)
                if tunnel:
                    tunnel.close()
                self._stop_process('core-' + key)
                return result
            raise ValueError('未知 AoiAWD 操作')


def _remote(ssh, command, timeout=30):
    code, output, error = run_remote(ssh, command, timeout)
    if code:
        raise RuntimeError('靶机操作失败：' + (error or output)[-1200:])
    return output.strip()


def probe(target):
    with ssh_session(target) as ssh, ssh.open_sftp() as sftp:
        home = sftp.normalize('.')
        webdir = target.get('webdir') or '/var/www/html'
        output = _remote(ssh, "uname -m; getconf GNU_LIBC_VERSION; command -v php || true; test -r /proc/self/stat && echo proc=readable; test -w /tmp && echo tmp=writable")
        exists = False
        try:
            exists = stat.S_ISDIR(sftp.stat(webdir).st_mode)
        except OSError:
            pass
        return {'home': home, 'webdir': webdir, 'webdir_exists': exists,
            'php': '/php' in output, 'details': output, 'guardian': '需提供 ELF 路径；生成独立 .guardianed 文件'}


def _write(sftp, path, data, mode=0o644):
    with sftp.open(path, 'wb') as stream:
        stream.write(data)
    sftp.chmod(path, mode)


def _read(sftp, path, maximum=16 * 1024 * 1024):
    if sftp.stat(path).st_size > maximum:
        raise ValueError('文件超过部署大小限制：' + path)
    with sftp.open(path, 'rb') as stream:
        return stream.read(maximum + 1)


def _walk(sftp, directory):
    for item in sftp.listdir_attr(directory):
        path = directory.rstrip('/') + '/' + item.filename
        if stat.S_ISDIR(item.st_mode):
            if item.filename not in ('.git', 'runtime', '.godhand-aoi'):
                yield from _walk(sftp, path)
        elif stat.S_ISREG(item.st_mode) and item.filename.lower().endswith('.php'):
            yield path, item.st_mode & 0o777


def _pid_stop(ssh, sftp, base, manifest):
    pid = manifest.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        return
    expected = base + '/roundworm'
    # Match executable path as well as PID, so a reused PID cannot stop an unrelated process.
    _remote(ssh, "if [ \"$(readlink /proc/" + str(pid) + "/exe 2>/dev/null)\" = " + shlex.quote(expected) + " ]; then kill " + str(pid) + "; fi; "
        "for attempt in 1 2 3 4 5 6 7 8 9 10; do "
        "[ \"$(readlink /proc/" + str(pid) + "/exe 2>/dev/null)\" != " + shlex.quote(expected) + " ] && exit 0; sleep 0.1; done; exit 1")


def deploy(target, parameters, bundle, remote_port, check=lambda: None):
    interval = int(parameters.get('process_interval', 250))
    if not 100 <= interval <= 10000:
        raise ValueError('进程采样间隔应为 100–10000 毫秒')
    with ssh_session(target) as ssh, ssh.open_sftp() as sftp:
        if _remote(ssh, 'uname -m') != 'x86_64':
            raise ValueError('当前配套探针支持 Linux x86_64')
        home = sftp.normalize('.')
        base = home + '/.godhand-aoi'
        manifest_path = base + '/private/manifest.json'
        try:
            previous = json.loads(_read(sftp, manifest_path))
        except FileNotFoundError:
            previous = None
        if previous:
            if previous['remote_port'] != remote_port:
                raise ValueError('已部署探针的回传端口不同，请先卸载恢复再部署')
            return {'installed': True, 'already_installed': True, 'web_files': len(previous['files']),
                'guardian_path': previous.get('guardian_path', ''), 'remote_dir': base}
        _remote(ssh, 'mkdir -p ' + shlex.quote(base + '/private') + '; chmod 755 ' + shlex.quote(base) + '; chmod 700 ' + shlex.quote(base + '/private'))
        manifest = {'version': 1, 'remote_port': remote_port, 'files': [], 'pid': None, 'guardian_path': ''}
        _write(sftp, manifest_path, json.dumps(manifest).encode(), 0o600)
        try:
            check()
            binary = bundle / 'probes' / 'roundworm'
            _write(sftp, base + '/roundworm', binary.read_bytes(), 0o755)
            webdir = safe_remote_path(parameters.get('webdir') or target.get('webdir') or '/var/www/html')
            paths = parameters.get('watch_paths') or ['/tmp', webdir]
            if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
                raise ValueError('监测目录应为 1–20 项列表')
            paths = [safe_remote_path(path) for path in paths]
            if any(';' in path for path in paths):
                raise ValueError('监测路径不能包含分号')
            for path in paths:
                if not stat.S_ISDIR(sftp.stat(path).st_mode):
                    raise ValueError('监测目录不存在：' + path)
            php = _remote(ssh, 'command -v php || true')
            web_enabled = parameters.get('web', True)
            if not isinstance(web_enabled, bool):
                raise ValueError('web 应为布尔值')
            web_status = '未选择 PHP 探针' if not web_enabled else '目标没有 PHP，已跳过 Web 探针'
            if web_enabled and php:
                monitor = (ASSETS / 'web_probe.php').read_bytes().replace(b'SERVER_PORT', str(remote_port).encode())
                _write(sftp, base + '/web_probe.php', monitor)
                _remote(ssh, shlex.quote(php) + ' -l ' + shlex.quote(base + '/web_probe.php'))
                selected = parameters.get('php_files') or []
                if not isinstance(selected, list) or len(selected) > 5000:
                    raise ValueError('php_files 应为最多 5000 项的路径列表')
                candidates = []
                for item in selected:
                    path = safe_remote_path(item)
                    if not path.startswith(webdir.rstrip('/') + '/') or not path.lower().endswith('.php'):
                        raise ValueError('选定 PHP 文件必须位于 Web 根目录内')
                    attributes = sftp.lstat(path)
                    if not stat.S_ISREG(attributes.st_mode):
                        raise ValueError('选定 PHP 文件必须是普通文件')
                    candidates.append((path, attributes.st_mode & 0o777))
                for path, mode in (candidates if selected else _walk(sftp, webdir)):
                    check()
                    if len(manifest['files']) >= 5000:
                        raise ValueError('PHP 文件超过 5000 个，请缩小部署目录')
                    original = _read(sftp, path, 4 * 1024 * 1024)
                    installed, addition = inject_php(original, base + '/web_probe.php')
                    backup = base + '/private/' + hashlib.sha256(path.encode()).hexdigest() + '.bak'
                    _write(sftp, backup, original, 0o600)
                    row = {'path': path, 'backup': backup, 'mode': mode, 'addition': addition.decode(),
                        'installed_hash': hashlib.sha256(installed).hexdigest()}
                    manifest['files'].append(row)
                    _write(sftp, manifest_path, json.dumps(manifest).encode(), 0o600)
                    _write(sftp, path, installed, mode)
                    _remote(ssh, shlex.quote(php) + ' -l ' + shlex.quote(path))
                web_status = '已部署 PHP Web 探针'
            elf = parameters.get('elf_path', '')
            if elf:
                elf = safe_remote_path(elf)
                raw = _read(sftp, elf, 32 * 1024 * 1024)
                if raw[:6] != b'\x7fELF\x02\x01' or len(raw) < 64 or raw[18:20] != b'\x3e\x00':
                    raise ValueError('Guardian 需要 Linux x86_64 ELF 文件')
                guardian = (bundle / 'probes' / 'guardian').read_bytes()
                output = elf + '.guardianed'
                try:
                    sftp.lstat(output)
                    raise ValueError('Guardian 输出已存在，请先恢复原部署')
                except FileNotFoundError:
                    pass
                manifest['guardian_path'] = output
                _write(sftp, manifest_path, json.dumps(manifest).encode(), 0o600)
                _write(sftp, output, guardian + raw + struct.pack('<I', len(guardian)) + socket.inet_aton('127.0.0.1') + struct.pack('!H', remote_port), 0o755)
            check()
            command = 'nohup ' + shlex.quote(base + '/roundworm') + ' -s 127.0.0.1 -p ' + str(remote_port) + ' -i ' + str(interval) + ' -w ' + shlex.quote(';'.join(paths)) + ' >' + shlex.quote(base + '/private/roundworm.log') + ' 2>&1 </dev/null & echo $!'
            manifest['pid'] = int(_remote(ssh, command))
            _write(sftp, manifest_path, json.dumps(manifest).encode(), 0o600)
            time.sleep(.3)
            _remote(ssh, 'kill -0 ' + str(manifest['pid']))
            check()
            return {'installed': True, 'web_status': web_status, 'web_files': len(manifest['files']),
                'pid': manifest['pid'], 'guardian_path': manifest['guardian_path'], 'remote_dir': base, 'watch_paths': paths}
        except Exception:
            _restore(ssh, sftp, base, manifest)
            raise


def _restore(ssh, sftp, base, manifest):
    conflicts = []
    _pid_stop(ssh, sftp, base, manifest)
    for row in reversed(manifest['files']):
        try:
            current = _read(sftp, row['path'])
            original = _read(sftp, row['backup'])
            if current == original:
                continue
            restored = restore_php(current, original, row['addition'].encode(), row['installed_hash'])
            _write(sftp, row['path'], restored, row['mode'])
        except Exception as exc:
            conflicts.append({'path': row['path'], 'error': str(exc)})
    if conflicts:
        raise RuntimeError('部分文件需人工恢复，已保留备份：' + json.dumps(conflicts, ensure_ascii=False))
    guardian = manifest.get('guardian_path')
    if guardian:
        try:
            sftp.remove(guardian)
        except FileNotFoundError:
            pass
    # Only delete files recorded by this deployment, never recursively remove a computed path.
    for name in ['roundworm', 'web_probe.php']:
        try:
            sftp.remove(base + '/' + name)
        except FileNotFoundError:
            pass
    for name in sftp.listdir(base + '/private'):
        if name == 'manifest.json' or name == 'roundworm.log' or re.fullmatch(r'[0-9a-f]{64}\.bak', name):
            sftp.remove(base + '/private/' + name)
    sftp.rmdir(base + '/private')
    try:
        sftp.rmdir(base)
    except OSError:
        pass
    return {'removed': True, 'restored_files': len(manifest['files']), 'cleanup_verified': True}


def remove(target):
    with ssh_session(target) as ssh, ssh.open_sftp() as sftp:
        base = sftp.normalize('.') + '/.godhand-aoi'
        try:
            manifest = json.loads(_read(sftp, base + '/private/manifest.json'))
        except FileNotFoundError:
            return {'removed': True, 'already_absent': True}
        return _restore(ssh, sftp, base, manifest)
