"""Optional real client smoke test; keeps client config and fixtures isolated."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import time
from .core import Store,atomic_json
from .server import Hub,client_configs
from .test_acceptance import free_port

def main():
    root=Path(__file__).resolve().parent.parent;path=root/'.client-test';path.mkdir(exist_ok=True)
    store=Store(data_dir=path/'data');store.save_config({'port':free_port()});hub=Hub(store);hub.start();report={}
    environment=os.environ.copy();environment['GODHAND_MCP_TOKEN']=store.tokens()['mcp_token'];environment['CLAUDE_CONFIG_DIR']=str(path/'claude-config')
    script=path/'sleep.py';script.write_text('import time\ntime.sleep(45)\n',encoding='utf-8')
    claude,codex=client_configs(store);(path/'.mcp.json').write_text(claude,encoding='utf-8')
    prompt='Validate this local MCP server only. Discover the single tool godhand. Use action=echo arguments.message= "client-check", call godhand action=run_tool arguments.tool=python_script arguments.target=local arguments.parameters={"file":'+json.dumps(str(script))+'}, cancel the returned task with godhand action=cancel, then query action=task until terminal and read action=logs. Do not read or modify other files, do not run shell commands, and do not call any other server. Report the actual statuses.'
    try:
        binary=shutil.which('codex')
        if binary and '--claude-only' not in sys.argv:
            command=[binary,'exec','--skip-git-repo-check','--ephemeral','-s','read-only','-C',str(path),'-c','mcp_servers={godhand={url="'+hub.url+'",bearer_token_env_var="GODHAND_MCP_TOKEN"}}','-o',str(path/'codex-final.txt'),prompt]
            with (path/'codex-run.log').open('w',encoding='utf-8') as out:
                try:
                    result=subprocess.run(command,env=environment,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,timeout=120);report['codex']={'exit_code':result.returncode}
                except subprocess.TimeoutExpired:report['codex']={'error':'client timeout'}
        elif '--claude-only' not in sys.argv:report['codex']={'error':'client not installed'}
        candidates=list((root/'.client-runtime2'/'node_modules'/'@anthropic-ai'/'claude-code-win32-x64').rglob('claude.exe'))
        if candidates:
            with (path/'claude-list.log').open('w',encoding='utf-8') as out:
                try:
                    # Register the test server through the client's normal local-scope setup.
                    subprocess.run([str(candidates[0]),'mcp','remove','--scope','local','godhand'],cwd=path,env=environment,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,timeout=15)
                    config=json.loads(claude)['mcpServers']['godhand']
                    added=subprocess.run([str(candidates[0]),'mcp','add-json','--scope','local','godhand',json.dumps(config)],cwd=path,env=environment,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,timeout=15)
                    result=subprocess.run([str(candidates[0]),'mcp','list'],cwd=path,env=environment,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,timeout=40);report['claude']={'discovery_exit':result.returncode}
                except (OSError,subprocess.TimeoutExpired) as exc:report['claude']={'error':str(exc)}
        else:report['claude']={'error':'client not installed'}
        report['tasks']=[{'operation':t['operation'],'status':t['status']} for t in store.tasks()]
    finally:hub.stop()
    atomic_json(path/'client-report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
