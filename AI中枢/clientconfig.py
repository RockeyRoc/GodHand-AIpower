"""Write the GodHand MCP entry into a Codex config file.

Only the ``mcp_servers.godhand`` table is replaced. Everything else in the
file is preserved byte for byte, and the previous content is backed up before
the new file is validated and swapped in.
"""
from pathlib import Path
import os
import time
import tomllib

from .core import app_root, atomic_text

ENV_VAR = 'GODHAND_MCP_TOKEN'
SECTION = 'mcp_servers.godhand'


def server_url(store):
    return 'http://127.0.0.1:' + str(store.reload_config()['port']) + '/mcp'


def codex_path(scope='user', root=None):
    if scope == 'user':
        return Path.home() / '.codex' / 'config.toml'
    if scope == 'project':
        return Path(root or app_root()) / '.codex' / 'config.toml'
    raise ValueError('scope 应为 user 或 project')


def _strip_section(text, name):
    """Drop the exact table ``name`` and any of its subtables."""
    kept = []
    dropping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('['):
            header_line = stripped.split('#', 1)[0].strip()
            if header_line.endswith(']'):
                header = header_line.strip('[]').strip()
                dropping = header == name or header.startswith(name + '.')
                if dropping:
                    continue
        if not dropping:
            kept.append(line)
    return ''.join(kept)


def render(store):
    return ('[mcp_servers.godhand]\n'
            'url = "' + server_url(store) + '"\n'
            'bearer_token_env_var = "' + ENV_VAR + '"\n')


def install_codex(store, scope='user', path=None):
    target = Path(path) if path else codex_path(scope)
    original = ''
    if target.exists():
        original = target.read_text(encoding='utf-8-sig')
    merged = _strip_section(original, SECTION)
    if merged and not merged.endswith('\n'):
        merged += '\n'
    if merged.strip():
        merged += '\n'
    candidate = merged + render(store)
    try:
        parsed = tomllib.loads(candidate)
        url = parsed['mcp_servers']['godhand']['url']
    except Exception as exc:
        raise ValueError('生成的 Codex 配置无法解析，未写入：' + str(exc)) from None
    if url != server_url(store):
        raise ValueError('Codex 配置校验失败，未写入')
    backup = None
    if target.exists():
        backup = target.with_name(target.name + '.bak.' + time.strftime('%Y%m%d-%H%M%S'))
        backup.write_bytes(target.read_bytes())
    atomic_text(target, candidate)
    return {'path': str(target), 'backup': str(backup) if backup else '', 'created': not original,
            'url': url, 'env': ENV_VAR,
            'hint': '在启动 Codex 的环境中设置 ' + ENV_VAR + ' 后重新打开会话'}
