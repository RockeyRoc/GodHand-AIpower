"""Generate stdio/HTTP configurations and replace only the GodHand TOML table."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import tomllib

from .core import app_root, atomic_text
from .endpoint import MCP_URL, ensure_fixed_endpoint

ENV_VAR = 'GODHAND_MCP_TOKEN'
SECTION = 'mcp_servers.godhand'


def server_url(store=None):
    return MCP_URL


def codex_path(scope='user', root=None):
    if scope == 'user':
        return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'config.toml'
    if scope == 'project':
        return Path(root or app_root()) / '.codex' / 'config.toml'
    raise ValueError('配置位置应为 user 或 project')


def stdio_entry(store):
    if getattr(sys, 'frozen', False):
        command, args = str(Path(sys.executable).resolve()), ['--mcp-stdio']
    else:
        command = str(Path(sys.executable).resolve())
        args = [str((store.root / 'main_work.py').resolve()), '--mcp-stdio']
    return {'command': command, 'args': args + ['--data-dir', str(store.path.resolve())],
            'startup_timeout_sec': 30}


def render(store, transport='stdio'):
    if transport == 'stdio':
        entry = stdio_entry(store)
    elif transport == 'http':
        entry = {'url': MCP_URL, 'bearer_token_env_var': ENV_VAR}
    else:
        raise ValueError('传输方式应为 stdio 或 http')
    return '[mcp_servers.godhand]\n' + ''.join(
        key + ' = ' + json.dumps(value, ensure_ascii=False) + '\n' for key, value in entry.items())


def _strip_section(text, name):
    kept, dropping = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('['):
            try:
                table = tomllib.loads(line)
            except tomllib.TOMLDecodeError:
                pass
            else:
                dropping = isinstance(table.get('mcp_servers', {}).get('godhand'), dict)
        if not dropping:
            kept.append(line)
    return ''.join(kept)


def _without_godhand(data):
    data = copy.deepcopy(data)
    servers = data.get('mcp_servers', {})
    servers.pop('godhand', None)
    if not servers:
        data.pop('mcp_servers', None)
    return data


def install_codex(store, scope='user', path=None, transport='stdio'):
    ensure_fixed_endpoint(store)
    target = Path(path) if path else codex_path(scope, store.root)
    raw = target.read_bytes() if target.exists() else b''
    bom = raw.startswith(b'\xef\xbb\xbf')
    original = raw.decode('utf-8-sig')
    try:
        before = tomllib.loads(original)
        merged = _strip_section(original, SECTION)
        if merged and not merged.endswith('\n'):
            merged += '\n'
        if merged.strip():
            merged += '\n'
        candidate = merged + render(store, transport)
        parsed = tomllib.loads(candidate)
        if _without_godhand(before) != _without_godhand(parsed):
            raise ValueError('检测到其他配置将被改变')
        expected = tomllib.loads(render(store, transport))['mcp_servers']['godhand']
        if parsed['mcp_servers']['godhand'] != expected:
            raise ValueError('GodHand 配置校验失败')
    except (ValueError, KeyError) as exc:
        raise ValueError('配置未写入：' + str(exc)) from None
    backup = None
    if target.exists():
        backup = target.with_name(target.name + '.bak.' + str(time.time_ns()))
        backup.write_bytes(raw)
    atomic_text(target, ('\ufeff' if bom else '') + candidate)
    return {'path': str(target), 'backup': str(backup) if backup else '', 'created': not raw,
            'url': MCP_URL, 'transport': transport, 'env': ENV_VAR if transport == 'http' else '',
            'hint': ('在 Codex 的 MCP 设置中重启 godhand，或重新打开 Codex；stdio 无需设置凭据。'
                     if transport == 'stdio' else '在启动客户端的终端设置 GODHAND_MCP_TOKEN 后重新连接。')}


def token_command(store):
    """For copying to the HTTP client's launch terminal, never for export/logging."""
    value = store.tokens()['mcp_token'].replace("'", "''")
    return "$env:GODHAND_MCP_TOKEN = '" + value + "'"
