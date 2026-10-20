"""Exercise the delivered stdio process, including worker and ownership cleanup."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time

from .core import Store, atomic_json
from .server import Connection
from .version import VERSION


async def exercise(command, args, data, attached_pid=None):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    # pywin32 imports can alter the parent's DLL directory. A frozen child
    # must load its own Python/runtime DLLs, not those of the test runner.
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    parameters = StdioServerParameters(command=command, args=args)
    async with Client(parameters) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ['godhand']
        async def call(action, arguments=None):
            response = await client.call_tool('godhand', {'action': action, 'arguments': arguments or {}})
            if response.is_error:
                raise AssertionError(response.content)
            return response.structured_content['result']
        assert (await call('echo', {'message': 'stdio-delivery'}))['message'] == 'stdio-delivery'
        health = await call('health')
        assert health['version'] == VERSION
        assert health['url'] == 'http://127.0.0.1:18765/mcp'
        if attached_pid:
            assert health['pid'] == attached_pid
        task = await call('run', {'operation': 'defense_report', 'target': 'local',
                                  'parameters': {'task_ids': []}})
        deadline = time.monotonic() + 40
        while True:
            row = await call('task', {'task_id': task['id']})
            if row['status'] not in ('queued', 'running'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('stdio worker task timeout')
            await asyncio.sleep(.25)
        assert row['status'] == 'succeeded', row
        events = await call('logs', {'task_id': task['id']})
        assert events
        return {'version': health['version'], 'tools': ['godhand'], 'pid': health['pid'],
                'task_status': row['status'], 'events': len(events)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--attached', action='store_true')
    options = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    data = Path(options.data_dir).resolve()
    command = str(Path(options.executable).resolve()) if options.executable else sys.executable
    prefix = [] if options.executable else [str(root / 'main_work.py')]
    store = Store(Path(options.executable).resolve().parent if options.executable else root, data)
    connection = Connection(store)
    owner = None
    log = None
    try:
        if options.attached:
            log = (data / 'owner.log').open('w', encoding='utf-8')
            owner = subprocess.Popen([command] + prefix + ['--mcp', '--data-dir', str(data)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 30
            while True:
                try:
                    connection.call()
                    break
                except Exception:
                    if owner.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('independent owner did not start')
                    time.sleep(.1)
        report = asyncio.run(exercise(command, prefix + ['--mcp-stdio', '--data-dir', str(data)],
                                      data, owner.pid if owner else None))
        if owner:
            assert owner.poll() is None
            assert connection.call()['pid'] == owner.pid
            report['independent_owner_preserved'] = True
        else:
            deadline = time.monotonic() + 20
            while (data / 'instance.json').exists() and time.monotonic() < deadline:
                time.sleep(.1)
            assert not (data / 'instance.json').exists(), 'owned service did not clean up'
            report['owned_service_cleaned'] = True
        atomic_json(data / 'stdio-report.json', report)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if owner and owner.poll() is None:
            try:
                connection.call('shutdown')
                owner.wait(timeout=20)
            except Exception:
                owner.kill()
                owner.wait(timeout=5)
        if log:
            log.close()


if __name__ == '__main__':
    main()
