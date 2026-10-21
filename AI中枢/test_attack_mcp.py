"""Local-only tests for the first-class attack MCP adapters."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .attack import catalogue, execute


class FixtureEnvironment:
    def __init__(self, path):
        self.checked = []
        self.request = {
            'data_dir': str(path),
            'context': {'competition': 'fixture'},
        }

    def check_url(self, url):
        self.checked.append(url)


class AttackMCP(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.env = FixtureEnvironment(self.path)
        self.target = {
            'id': 'fixture-target',
            'ip': '127.0.0.1',
            'port': 18080,
            'shell': '/shell.php',
            'pass': 'fixture-pass',
            'https': False,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_catalogue_has_narrow_stable_contracts(self):
        specs = catalogue()
        self.assertEqual(
            {'attack_dfunc_check', 'attack_phpinfo_analyze', 'attack_phpggc_list',
             'attack_phpggc_search', 'attack_phpggc_generate', 'attack_mix_once'},
            set(specs),
        )
        self.assertEqual({'commands', 'mix', 'fake_count', 'request_timeout'},
                         set(specs['attack_mix_once'].fields))
        self.assertFalse(specs['attack_mix_once'].read_only)
        self.assertTrue(specs['attack_phpinfo_analyze'].local)
        self.assertEqual('AttackHandler', specs['attack_dfunc_check'].actor)

    def test_phpinfo_text_is_parsed_without_fetching_a_url(self):
        result = execute('attack_phpinfo_analyze', {'ip': 'local'}, {
            'input': 'disable_functions>exec,system< PHP Version </td><td>8.2.1<'
        }, self.env)
        self.assertEqual(['exec', 'system'], result['info']['disabled_functions'])
        self.assertEqual('8.2.1', result['info']['php_version'])
        self.assertEqual([], self.env.checked)
        with self.assertRaisesRegex(ValueError, '只接受文本'):
            execute('attack_phpinfo_analyze', {'ip': 'local'},
                    {'input': 'https://example.invalid/phpinfo.php'}, self.env)

    def test_dfunc_check_uses_only_the_resolved_target(self):
        calls = []
        fake = SimpleNamespace(check_remote=lambda url, password:
                               (calls.append((url, password)) or
                                ({'disabled_functions': []}, [{'method': 'system'}])))
        with patch('AI中枢.attack._load', return_value=fake):
            result = execute('attack_dfunc_check', self.target, {}, self.env)
        self.assertEqual([('http://127.0.0.1:18080/shell.php', 'fixture-pass')], calls)
        self.assertEqual(['http://127.0.0.1:18080/shell.php'], self.env.checked)
        self.assertEqual('fixture-target', result['target'])

    def test_dependency_failure_and_invalid_arguments_are_explicit(self):
        fake = SimpleNamespace(generate_payload=lambda *_args, **_kwargs: (None, 'missing phpggc'))
        with patch('AI中枢.attack._load', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'missing phpggc'):
                execute('attack_phpggc_generate', {'ip': 'local'}, {
                    'chain': 'Laravel/RCE1', 'arguments': ['id'], 'encoding': 'raw'
                }, self.env)
        with self.assertRaisesRegex(ValueError, 'commands'):
            execute('attack_mix_once', self.target, {'commands': 'id'}, self.env)
        with self.assertRaisesRegex(ValueError, 'fake_count'):
            execute('attack_mix_once', self.target,
                    {'commands': ['id'], 'fake_count': 0}, self.env)

    def test_mix_total_connection_failure_is_not_reported_as_success(self):
        class FailedRunner:
            def __init__(self, *_args, **_kwargs):
                self.known = set()
                self.last_results = {}
            def run_batch(self, _commands):
                self.last_results = {'127.0.0.1': {
                    'found': False, 'request_successes': 0, 'error': 'connection refused'}}
                return False
            def save_known(self):
                raise AssertionError('failed runs must not persist success state')
        with patch('AI中枢.attack._load', return_value=SimpleNamespace(MixAttack=FailedRunner)):
            with self.assertRaisesRegex(RuntimeError, 'connection refused'):
                execute('attack_mix_once', self.target, {'commands': ['id']}, self.env)

    def test_mix_once_returns_structured_result_and_never_submits(self):
        created = []
        class FakeRunner:
            def __init__(self, targets, shell_url, shell_pass, **kwargs):
                created.append((targets, shell_url, shell_pass, kwargs))
                self.known = {'old-flag'}
                self.args = None
            def run_batch(self, commands):
                self.commands = commands
                self.known.add('flag{fixture}')
                return True
            def save_known(self):
                self.saved = True
        with patch('AI中枢.attack._load', return_value=SimpleNamespace(MixAttack=FakeRunner)):
            result = execute('attack_mix_once', self.target, {
                'commands': ['id'], 'mix': True, 'fake_count': 3, 'request_timeout': 1.5
            }, self.env)
        self.assertTrue(result['found'])
        self.assertEqual(['flag{fixture}'], result['flags'])
        self.assertEqual(0, result['submitted'])
        self.assertTrue(created[0][3]['no_submit'])
        self.assertEqual(1, created[0][3]['threads'])

    def test_mix_known_flags_are_persisted_outside_the_source_tree(self):
        from 攻击模块.mix_attack import MixAttack
        known = self.path / 'state' / 'known.json'
        runner = MixAttack(['127.0.0.1'], no_submit=True, known_file=str(known))
        runner.known.update({'flag{b}', 'flag{a}'})
        runner.save_known()
        restored = MixAttack(['127.0.0.1'], no_submit=True, known_file=str(known))
        self.assertEqual({'flag{a}', 'flag{b}'}, restored.known)


if __name__ == '__main__':
    unittest.main()
