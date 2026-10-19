"""Verify frozen server protocol, child worker, and graceful shutdown."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import subprocess
import sys
import time

def main():
    executable=Path(sys.argv[1]).resolve();data=Path(sys.argv[2]).resolve()
    process=subprocess.Popen([str(executable),'--mcp','--data-dir',str(data)])
    from .core import Store,atomic_json
    from .server import Connection
    from .test_acceptance import wait_task
    deadline=time.monotonic()+15
    while not (data/'hub.json').exists() and time.monotonic()<deadline:time.sleep(.1)
    store=Store(executable.parent,data);connection=Connection(store)
    try:
        deadline=time.monotonic()+30
        while True:
            try:health=connection.call();break
            except Exception:
                if process.poll() is not None:raise RuntimeError('frozen server exit '+str(process.returncode))
                if time.monotonic()>deadline:raise TimeoutError('frozen startup')
                time.sleep(.2)
        async def check():
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
            async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+store.tokens()['mcp_token']}) as http:
                @asynccontextmanager
                async def transport():
                    async with streamable_http_client(health['url'],http_client=http) as streams:yield streams[:2]
                async with Client(transport()) as client:
                    tools=await client.list_tools();echo=await client.call_tool('godhand',{'action':'echo','arguments':{'message':'frozen-check'}})
                    assert not echo.is_error
                    return len(tools.tools)
        count=asyncio.run(check())
        script=data/'worker-test.py';script.write_text('print("frozen-worker-ok")\n',encoding='utf-8')
        task=connection.call('tool',identity='python_script',parameters={'file':str(script)},target_id='local')
        result=wait_task(store,task['id'],30)
        if result['status']!='succeeded':raise AssertionError(result)
        report={'health':health,'mcp_tools':count,'worker_status':result['status']}
        connection.call('shutdown');report['exit_code']=process.wait(timeout=20);atomic_json(data/'frozen-report.json',report);print(json.dumps(report,ensure_ascii=False))
    finally:
        if process.poll() is None:
            try:connection.call('shutdown');process.wait(timeout=15)
            except Exception:process.kill();process.wait(timeout=5)
if __name__=='__main__':main()
