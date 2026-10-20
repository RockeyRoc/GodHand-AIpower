"""1.3.0 transport, client-config and import-authorization regressions."""
import asyncio
import json
from pathlib import Path
import socket
import sys
import time
import tomllib
import unittest

from .clientconfig import install_codex
from .core import Store
from .version import VERSION


class Transports(unittest.TestCase):
    def setUp(self):
        self.project = Path(__file__).resolve().parent.parent
        self.path = self.project / '.target-tests' / ('transports-' + str(time.time_ns()))
        self.root = self.path / 'application'
        self.root.mkdir(parents=True)
        self.store = Store(self.root, self.path / 'data')

    def free_port(self):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    def test_version_is_single_source(self):
        import AI中枢
        from . import version
        self.assertEqual('1.4.0', VERSION)
        self.assertEqual(VERSION, AI中枢.VERSION)
        self.assertEqual((1, 4, 0, 0), version.numeric())

    def test_codex_install_preserves_other_tables_and_is_idempotent(self):
        target = self.path / 'config.toml'
        target.write_text('[model]\nname = "keep-me"\n\n[mcp_servers.other]\nurl = "http://example.invalid/mcp"\n', encoding='utf-8')
        result = install_codex(self.store, 'user', path=target)
        data = tomllib.loads(target.read_text(encoding='utf-8'))
        self.assertEqual('keep-me', data['model']['name'])
        self.assertIn('other', data['mcp_servers'])
        self.assertTrue(data['mcp_servers']['godhand']['url'].endswith('/mcp'))
        self.assertEqual('GODHAND_MCP_TOKEN', data['mcp_servers']['godhand']['bearer_token_env_var'])
        self.assertTrue(Path(result['backup']).is_file())
        install_codex(self.store, 'user', path=target)
        text = target.read_text(encoding='utf-8')
        self.assertEqual(1, text.count('[mcp_servers.godhand]'))
        self.assertEqual(1, text.count('bearer_token_env_var'))

    def test_codex_install_replaces_stale_entry(self):
        target = self.path / 'config.toml'
        target.write_text('[mcp_servers.godhand]\nurl = "http://127.0.0.1:1/mcp"\nbearer_token_env_var = "OLD"\n\n[other]\nflag = true\n', encoding='utf-8')
        install_codex(self.store, 'project', path=target)
        data = tomllib.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(data['mcp_servers']['godhand']['url'].endswith(':' + str(self.store.config['port']) + '/mcp'))
        self.assertEqual('GODHAND_MCP_TOKEN', data['mcp_servers']['godhand']['bearer_token_env_var'])
        self.assertTrue(data['other']['flag'])

    def test_import_requires_selection_by_default(self):
        result = self.store.import_targets([{'name': 'manual', 'ip': '10.10.10.1'}])
        self.assertFalse(result['targets'][0]['allowed'])
        self.assertIn('导入不授予权限', result['authorization'])

    def test_codex_install_replaces_header_with_inline_comment(self):
        target = self.path / 'comment.toml'
        target.write_text('[mcp_servers.godhand]  # stale\nurl = "http://127.0.0.1:1/mcp"\n', encoding='utf-8')
        install_codex(self.store, 'user', path=target)
        text = target.read_text(encoding='utf-8')
        self.assertEqual(1, text.count('[mcp_servers.godhand]'))
        self.assertEqual(1, text.count('url ='))

    def test_auto_authorize_import_when_enabled(self):
        self.store.save_config({'auto_authorize_imports': True})
        result = self.store.import_targets([{'name': 'auto-a', 'ip': '10.10.10.2'}, {'name': 'auto-b', 'ip': '10.10.10.3'}])
        self.assertEqual(2, result['imported'])
        self.assertTrue(all(item['allowed'] for item in result['targets']))
        self.assertIn('自动授权', result['authorization'])
        rows = {row['name']: row for row in self.store.machine_records()}
        self.assertTrue(rows['auto-a']['allowed'])
        self.assertTrue(rows['auto-b']['allowed'])

    def test_stdio_bridge_serves_same_tool(self):
        port = self.free_port()
        self.store.save_config({'port': port})
        params = _stdio_params(self.project, self.store.path)

        async def check():
            from mcp import Client
            async with Client(params) as client:
                tools = await client.list_tools()
                names = [item.name for item in tools.tools]
                self.assertEqual(['godhand'], names)
                result = await client.call_tool('godhand', {'action': 'echo', 'arguments': {'message': 'stdio ok'}})
                self.assertFalse(result.is_error)
                payload = json.loads(result.content[0].text)
                self.assertEqual('stdio ok', payload['result']['message'])
                health = await client.call_tool('godhand', {'action': 'health', 'arguments': {}})
                self.assertEqual(VERSION, json.loads(health.content[0].text)['result']['version'])

        asyncio.run(check())


def _stdio_params(root, data_dir):
    from mcp.client.stdio import StdioServerParameters
    return StdioServerParameters(
        command=sys.executable,
        args=['-X', 'utf8', str(root / 'main_work.py'), '--mcp-stdio', '--data-dir', str(data_dir)],
        cwd=str(root),
    )


if __name__ == '__main__':
    unittest.main()
