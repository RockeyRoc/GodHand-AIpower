"""Local parser, boundary, report, and simulated SSH tests for defense adapters."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import stat
import time
import unittest

from 防御模块 import engine

ROOT = Path(__file__).resolve().parent.parent


def fixture_root():
    path = ROOT / ".acceptance" / ("defense-" + str(time.time_ns()))
    path.mkdir(parents=True)
    return path


class MemoryFile:
    def __init__(self, sftp, path, mode):
        self.sftp, self.path, self.mode = sftp, path, mode
        self.buffer = bytearray() if "w" in mode or "x" in mode else bytearray(sftp.files[path])

    def read(self, size=-1):
        return bytes(self.buffer if size < 0 else self.buffer[:size])

    def write(self, content):
        self.buffer.extend(content)

    def flush(self):
        pass

    def close(self):
        self.sftp.files[self.path] = bytes(self.buffer)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class MemorySFTP:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.modes = {path: 0o644 for path in self.files}

    def open(self, path, mode):
        if "x" in mode and path in self.files:
            raise FileExistsError(path)
        return MemoryFile(self, path, mode)

    def stat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return type("Attributes", (), {"st_mode": stat.S_IFREG | self.modes.get(path, 0o644)})()

    def chmod(self, path, mode):
        self.modes[path] = mode

    def remove(self, path):
        del self.files[path]
        self.modes.pop(path, None)

    def posix_rename(self, old, new):
        self.files[new] = self.files.pop(old)
        self.modes[new] = self.modes.pop(old, 0o644)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


class FakeChannel:
    def __init__(self, output=b"", error=b"", code=0, chunk=8192):
        self.output = output
        self.error = error
        self.code = code
        self.chunk = chunk
        self.closed = False

    def exit_status_ready(self):
        return not self.output and not self.error

    def recv_ready(self):
        return bool(self.output)

    def recv_stderr_ready(self):
        return bool(self.error)

    def recv(self, size):
        value, self.output = self.output[:min(size, self.chunk)], self.output[min(size, self.chunk):]
        return value

    def recv_stderr(self, size):
        value, self.error = self.error[:min(size, self.chunk)], self.error[min(size, self.chunk):]
        return value

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, channel):
        self.channel = channel


class FakeSSH:
    def __init__(self, output=b"", error=b"", code=0, files=None):
        self.channel = FakeChannel(output, error, code)
        self.commands = []
        self.sftp = MemorySFTP(files)

    def exec_command(self, command, **_kwargs):
        self.commands.append(command)
        return None, FakeStream(self.channel), FakeStream(self.channel)

    def open_sftp(self):
        return self.sftp


class FakeEnvironment:
    def __init__(self, path, data_dir=None):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.artifact_dirs = []
        self.request = {"data_dir": str(data_dir or self.path)}


class DefenseTests(unittest.TestCase):
    def test_catalogue_registers_all_defense_capabilities_and_side_effects(self):
        specs = engine.catalogue()
        self.assertIn("defense_probe", specs)
        self.assertIn("defense_fail2ban_rollback", specs)
        self.assertIn("defense_watchbird_stats", specs)
        self.assertIn("defense_aide_promote", specs)
        self.assertTrue(specs["defense_probe"].read_only)
        self.assertTrue(specs["defense_yara_scan"].read_only)
        self.assertFalse(specs["defense_fail2ban_ban"].read_only)
        self.assertFalse(specs["defense_aide_promote"].read_only)
        self.assertEqual(["godhand", "off", "watchbird"], sorted(specs["defense_waf_switch"].fields["engine"]["choices"]))

    def test_allowlist_and_paths_reject_broad_or_unsafe_inputs(self):
        normalized = engine.safe_allowlist("10.8.0.0/16, 192.168.1.15")
        self.assertIn("10.8.0.0/16", normalized)
        self.assertIn("127.0.0.1/8", normalized)
        with self.assertRaises(engine.DefenseError):
            engine.safe_allowlist("0.0.0.0/0")
        with self.assertRaises(engine.DefenseError):
            engine.safe_allowlist("not-an-ip")
        self.assertEqual("/var/www/site with spaces", engine.safe_remote_path("/var/www/site with spaces"))
        with self.assertRaises(engine.DefenseError):
            engine.safe_remote_path("/var/www/../etc")

    def test_watchbird_config_changes_one_flag_and_waf_path_is_scoped(self):
        original = b'O:13:"configmanager":2:{s:7:"waf_sql";i:1;s:7:"waf_rce";b:0;}'
        changed = engine.parse_waf_flags(original, {"waf_sql": False})
        self.assertIn(b's:7:"waf_sql";i:0;', changed)
        self.assertIn(b's:7:"waf_rce";b:0;', changed)
        with self.assertRaises(engine.DefenseError):
            engine.parse_waf_flags(b"O:13:\"configmanager\":1:{", {"waf_sql": True})
        switched = engine.replace_auto_prepend(b"date.timezone=UTC\n", "watchbird", "/var/www/site with spaces")
        self.assertIn(b"/var/www/site with spaces/watchbird.php", switched)
        self.assertEqual(b"date.timezone=UTC\n", engine.replace_auto_prepend(switched, "off", "/var/www/site with spaces"))
        with self.assertRaises(engine.DefenseError):
            engine.replace_auto_prepend(b"auto_prepend_file=/tmp/untrusted.php\n", "off")

    def test_parsers_produce_structured_lynis_yara_and_aide_findings(self):
        lynis = engine.parse_lynis("[WARNING] Disable an unused service\nSuggestions: Review SSH settings\n")
        self.assertEqual(2, len(lynis))
        yara = engine.parse_yara("suspicious_php /var/www/a.php:10\n")
        self.assertEqual("suspicious_php", yara[0]["title"])
        aide = engine.parse_aide("Added entries:\n  /var/www/new.php\nChanged entries:\n  /etc/ssh/sshd_config\nRemoved entries:\n  /tmp/old.bin\n")
        self.assertEqual(3, len(aide))
        self.assertIn("Removed file", aide[-1]["title"])

    def test_run_remote_drains_bounded_output_and_separate_stderr(self):
        output = b"A" * (engine.MAX_OUTPUT + 9000)
        ssh = FakeSSH(output, b"diagnostic", 3)
        code, stdout, stderr = engine.run_remote(ssh, "fixed-command", 4)
        self.assertEqual(3, code)
        self.assertEqual(engine.MAX_OUTPUT, len(stdout))
        self.assertEqual("diagnostic", stderr)

    def test_remote_snapshots_restore_previous_files_and_remove_new_files(self):
        sftp = MemorySFTP({"/etc/example.conf": b"old"})
        backup, existed = engine._remote_snapshot(sftp, "/etc/example.conf")
        self.assertTrue(existed)
        with sftp.open("/etc/example.conf", "wb") as handle:
            handle.write(b"new")
        self.assertEqual("restored", engine._restore_snapshot(sftp, "/etc/example.conf", backup))
        self.assertEqual(b"old", sftp.files["/etc/example.conf"])

        backup, existed = engine._remote_snapshot(sftp, "/etc/new.conf")
        self.assertFalse(existed)
        with sftp.open("/etc/new.conf", "wb") as handle:
            handle.write(b"created")
        self.assertEqual("removed_created_file", engine._restore_snapshot(sftp, "/etc/new.conf", backup))
        self.assertNotIn("/etc/new.conf", sftp.files)
        with self.assertRaises(engine.DefenseError):
            engine._restore_snapshot(sftp, "/etc/new.conf", "/etc/other.godhand-bak-012345abcdef")

    def test_fail2ban_disable_keeps_rollback_on_validation_failure(self):
        ssh = FakeSSH()
        result = engine._configure_fail2ban(ssh, {"enabled": False})
        self.assertEqual("disabled", result["status"])
        script = ssh.commands[-1]
        self.assertIn("fail2ban-client -t", script)
        self.assertIn("GODHAND-ABSENT-FILE", script)
        self.assertIn("cp -a", script)
        self.assertIn("fail2ban-client reload >/dev/null 2>&1 || true", script)

        engine._execute_one(ssh, "defense_fail2ban_rollback", {"backup": result["backup"]})
        rollback = ssh.commands[-1]
        self.assertIn("fail2ban-client -t && fail2ban-client reload", rollback)
        self.assertIn(result["backup"], rollback)

    def test_workflow_reports_partial_failure_and_evidence_lineage(self):
        directory = fixture_root()
        env = FakeEnvironment(directory / "task")
        class Client:
            def __enter__(self):
                return object()
            def __exit__(self, *_args):
                pass
        original = engine._execute_one
        def run(_ssh, operation, _parameters):
            if operation == "defense_probe":
                return {"components": {"yara": "4.5"}, "findings": [{"source": "fixture", "title": "sample", "evidence": "fixture line"}]}
            if operation == "defense_fail2ban_status":
                raise FileNotFoundError("Fail2Ban missing in local fixture")
            return {"version": "fixture", "findings": []}
        engine._execute_one = run
        try:
            result = engine.execute("defense_workflow_run", {"ip": "10.0.0.5"}, {"workflow": "host_audit"}, env, lambda _target: Client())
        finally:
            engine._execute_one = original
        data = result["return"]
        self.assertTrue(result["failed"])
        self.assertEqual("failed", data["steps"][1]["status"])
        finding = data["findings"][0]
        self.assertEqual("10.0.0.5", finding["target"])
        self.assertTrue(finding["evidence_id"].startswith("defense_probe:"))
        report = Path(result["outputs"]["report_markdown"])
        self.assertTrue(report.is_file())
        self.assertIn("fixture line", report.read_text(encoding="utf-8"))

    def test_report_action_exports_saved_defense_task_history(self):
        root = fixture_root()
        tasks = root / "tasks.sqlite3"
        with sqlite3.connect(tasks) as connection:
            connection.execute("CREATE TABLE tasks(id TEXT,operation TEXT,target TEXT,status TEXT,result TEXT,created REAL,updated REAL)")
            connection.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)", ("a" * 32, "defense_yara_scan", "10.0.0.5", "succeeded", json.dumps({"data": {"return": {
                "findings": [], "raw": "private scan output", "log": "private request log",
                "password": "fixture-password-value"}}}), 1, 2))
            connection.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)", ("b" * 32, "ops_ssh", "10.0.0.5", "succeeded", "{}", 1, 2))
        env = FakeEnvironment(root / "report-task", root)
        result = engine.execute("defense_report", {"ip": "local"}, {"task_ids": []}, env)
        self.assertEqual(1, len(result["return"]["tasks"]))
        self.assertEqual("1.6.0", result["return"]["product_version"])
        self.assertTrue(Path(result["outputs"]["json_report"]).is_file())
        self.assertTrue(Path(result["outputs"]["markdown_report"]).is_file())
        report = Path(result["outputs"]["json_report"]).read_text(encoding="utf-8")
        self.assertNotIn("private scan output", report)
        self.assertNotIn("private request log", report)
        self.assertNotIn("fixture-password-value", report)


if __name__ == "__main__":
    unittest.main()
