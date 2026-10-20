"""Endpoint migration, portable configuration and preservation regressions."""
import json
from pathlib import Path
import socket
import sys
import time
import tomllib
import unittest
from unittest.mock import patch

from .core import Store
from .clientconfig import codex_path, install_codex, render, token_command
from .endpoint import MCP_URL, PORT, ensure_fixed_endpoint
from .test_acceptance import free_port


class Version16(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.path = self.root / '.target-tests' / ('v16-' + str(time.time_ns()))
        self.store = Store(self.root, self.path / '比赛 数据')

    def test_idle_legacy_port_is_backed_up_and_migration_is_idempotent(self):
        self.store.save_config({'port': free_port(), 'allowed_targets': ['preserve-id']})
        before = self.store.config_path.read_bytes()
        backup = ensure_fixed_endpoint(self.store)
        self.assertEqual(before, Path(backup).read_bytes())
        self.assertEqual(PORT, self.store.config['port'])
        self.assertEqual(['preserve-id'], self.store.config['allowed_targets'])
        self.assertEqual('', ensure_fixed_endpoint(self.store))

    def test_active_legacy_listener_preserves_original_configuration(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            self.store.save_config({'port': listener.getsockname()[1]})
            before = self.store.config_path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, '先停止旧服务'):
                ensure_fixed_endpoint(self.store)
            self.assertEqual(before, self.store.config_path.read_bytes())
            self.assertEqual([], list(self.store.path.glob('*.bak')))

    def test_frozen_config_uses_exact_executable_and_data_directory(self):
        executable = self.path / '便携 程序' / 'GodHand.exe'
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)):
            entry = tomllib.loads(render(self.store))['mcp_servers']['godhand']
        self.assertEqual(str(executable.resolve()), entry['command'])
        self.assertEqual(['--mcp-stdio', '--data-dir', str(self.store.path)], entry['args'])
        self.assertNotIn('bearer_token_env_var', entry)

    def test_http_remains_fixed_and_export_contains_no_token(self):
        entry = tomllib.loads(render(self.store, 'http'))['mcp_servers']['godhand']
        self.assertEqual(MCP_URL, entry['url'])
        self.assertEqual('GODHAND_MCP_TOKEN', entry['bearer_token_env_var'])
        self.assertNotIn(self.store.tokens()['mcp_token'], render(self.store, 'http'))
        self.assertIn(self.store.tokens()['mcp_token'], token_command(self.store))

    def test_config_preserves_bom_comments_crlf_and_other_tables(self):
        target = self.path / 'client.toml'
        raw = b'\xef\xbb\xbf# keep\r\nmodel = "keep"\r\n[mcp_servers.other]\r\nurl = "https://example.invalid"\r\n["mcp_servers"."godhand"] # old\r\nurl = "http://old.invalid"\r\n'
        target.write_bytes(raw)
        result = install_codex(self.store, path=target)
        self.assertEqual(raw, Path(result['backup']).read_bytes())
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf# keep\r\nmodel = "keep"\r\n'))
        self.assertEqual('https://example.invalid', tomllib.loads(target.read_text(encoding='utf-8-sig'))['mcp_servers']['other']['url'])
        install_codex(self.store, path=target)
        self.assertEqual(1, target.read_text(encoding='utf-8-sig').count('[mcp_servers.godhand]'))

    def test_invalid_existing_toml_is_not_replaced(self):
        target = self.path / 'broken.toml'
        target.write_bytes(b'[broken\n')
        with self.assertRaises(ValueError):
            install_codex(self.store, path=target)
        self.assertEqual(b'[broken\n', target.read_bytes())
        self.assertEqual([], list(self.path.glob('broken.toml.bak.*')))

    def test_codex_home_and_project_scope(self):
        with patch.dict('os.environ', {'CODEX_HOME': str(self.path)}):
            self.assertEqual(self.path / 'config.toml', codex_path('user'))
            self.assertEqual(self.root / '.codex' / 'config.toml', codex_path('project', self.root))

    def test_configuration_controls_and_scrollable_layout_at_common_scales(self):
        import tkinter as tk
        from tkinter import ttk
        from types import SimpleNamespace
        from .hub_gui import HubPanel
        from .server import Connection
        root = tk.Tk()
        root.attributes('-alpha', 0)
        root.geometry('1000x550')
        root.update()
        app = SimpleNamespace(root=root)
        try:
            for scaling in (96 / 72, 144 / 72, 192 / 72):
                root.tk.call('tk', 'scaling', scaling)
                host = ttk.Frame(root)
                host.pack(fill='both', expand=True)
                panel = HubPanel(host, app, self.store, Connection(self.store))
                panel.set_state('running')
                self.assertEqual('disabled', str(panel.choose_button['state']))
                self.assertEqual('disabled', str(panel.save_button['state']))
                self.assertEqual('disabled', str(panel.preview['state']))
                panel.set_state('stopped')
                self.assertEqual('normal', str(panel.save_button['state']))
                panel.advanced_visible.set(True)
                panel.toggle_advanced()
                root.update()
                def buttons(widget):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Button):
                            yield child
                        yield from buttons(child)
                for button in buttons(panel.parent):
                    self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                         panel.canvas.winfo_rootx() + panel.canvas.winfo_width(),
                                         (scaling, button['text'], panel.canvas.winfo_width()))
                    y = button.winfo_rooty() - panel.parent.winfo_rooty()
                    panel.canvas.yview_moveto(max(0, (y - panel.canvas.winfo_height() / 2) / panel.parent.winfo_height()))
                    root.update_idletasks()
                    self.assertGreaterEqual(button.winfo_rooty(), panel.canvas.winfo_rooty())
                    self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                         panel.canvas.winfo_rooty() + panel.canvas.winfo_height())
                panel.close()
                host.destroy()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
