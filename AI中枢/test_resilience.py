"""Desktop resilience regressions that require no external target."""
from pathlib import Path
from types import SimpleNamespace
import json
import time
import unittest
import urllib.error
from unittest.mock import patch

from .core import atomic_json
from .gui_bridge import Bridge


ROOT = Path(__file__).resolve().parent.parent


class Resilience(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / '.target-tests' / ('resilience-' + str(time.time_ns()))
        self.path.mkdir(parents=True)

    def test_atomic_json_falls_back_when_rename_is_forbidden(self):
        target = self.path / 'hub.json'
        with patch('AI中枢.core.os.replace', side_effect=PermissionError('rename denied')), \
                patch('AI中枢.core.time.sleep'):
            atomic_json(target, {'created': True})
            atomic_json(target, {'updated': True})
        self.assertEqual({'updated': True}, json.loads(target.read_text(encoding='utf-8')))
        self.assertEqual([], list(self.path.glob('*.tmp')))

    def test_bridge_close_never_contacts_a_stopped_backend(self):
        calls = []
        bridge = Bridge.__new__(Bridge)
        bridge.app = SimpleNamespace(log=lambda *_: None)
        bridge.connection = SimpleNamespace(call=lambda *a, **k: calls.append((a, k)))
        actor = object()
        bridge.pending = {'task': {'actor': actor, 'method': 'toggle'}}
        bridge.closed = False
        bridge.close()
        self.assertTrue(bridge.closed)
        self.assertEqual({}, bridge.pending)
        self.assertFalse(bridge.cancel_actor(actor))
        self.assertEqual([], calls)

    def test_bridge_discards_stale_task_after_connection_refusal(self):
        logs = []
        actor = object()
        bridge = Bridge.__new__(Bridge)
        bridge.app = SimpleNamespace(log=lambda *args: logs.append(args))
        bridge.connection = SimpleNamespace(
            call=lambda *a, **k: (_ for _ in ()).throw(
                urllib.error.URLError(ConnectionRefusedError(10061, 'refused'))),
            describe_error=lambda _: '后台服务未运行：本机端口拒绝连接。',
        )
        bridge.pending = {'task': {'actor': actor, 'method': 'toggle'}}
        bridge.closed = False
        self.assertTrue(bridge.cancel_actor(actor, 'toggle'))
        self.assertEqual({}, bridge.pending)
        self.assertIn('后台服务未运行', logs[0][0])

    def test_tools_tab_keeps_its_toplevel(self):
        import main_work
        top = object()
        parent = SimpleNamespace(winfo_toplevel=lambda: top)
        with patch.object(main_work.ToolsTab, 'build_ui'):
            tools = main_work.ToolsTab(parent, lambda *_: None)
        self.assertIs(top, tools.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
