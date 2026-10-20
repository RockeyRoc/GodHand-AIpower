"""Repeatable tests confined to local fixtures; no competition target access."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from .core import Store,atomic_json,protect
from .server import Hub,Connection,client_configs

ROOT=Path(__file__).resolve().parent.parent

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def wait_task(store,identity,timeout=25):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        row=store.task(identity)
        if row['status'] not in ('queued','running'):return row
        time.sleep(.1)
    raise AssertionError('task did not finish '+identity)

class Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'.acceptance'/str(time.time_ns());cls.path.mkdir(parents=True)
        cls.root=cls.path/'中文 比赛';cls.root.mkdir();(cls.root/'管理工具').mkdir()
        atomic_json(cls.root/'管理工具'/'machines.json',[{'name':'旧记录','ip':'127.0.0.8','port':'8080','pass':'LEGACY-SECRET'}])
        # Worker resolves the entrypoint via root: a forwarding entry keeps all data isolated.
        (cls.root/'main_work.py').write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT))+')\nfrom AI中枢.entry import main\nraise SystemExit(main())\n',encoding='utf-8')
        cls.store=Store(cls.root,cls.path/'data');cls.store.save_config({'port':free_port()})
        cls.hub=Hub(cls.store);cls.hub.start();cls.service=cls.hub.service
        cls.script=cls.path/'脚本 有空格.py';cls.script.write_text('import sys,time\nif "sleep" in sys.argv:time.sleep(60)\nprint("ARG="+repr(sys.argv[1:]))\nif "fail" in sys.argv:raise SystemExit(7)\n',encoding='utf-8')
    @classmethod
    def tearDownClass(cls):cls.hub.stop()
    def test_01_auth(self):
        with self.assertRaises(Exception):urllib.request.urlopen(self.hub.url,timeout=2)
        configs=client_configs(self.store)
        self.assertNotIn(self.store.tokens()['mcp_token'],''.join(configs))
    def test_02_script_argument_boundaries(self):
        row=self.service.submit_tool('python_script',parameters={'file':str(self.script),'args':['a b','x; echo INJECT','中文']})
        result=wait_task(self.store,row['id'])
        self.assertEqual('succeeded',result['status'],result)
        self.assertIn('x; echo INJECT',result['result']['data']['stdout'])
    def test_03_failure_and_dependency(self):
        row=self.service.submit_tool('python_script',parameters={'file':str(self.script),'args':['fail']})
        result=wait_task(self.store,row['id']);self.assertEqual('failed',result['status']);self.assertEqual(7,result['result']['exit_code'])
        with self.assertRaises(FileNotFoundError):self.service.submit_tool('php_script',parameters={'file':str(self.script)})
    def test_04_cancel_timeout(self):
        row=self.service.submit_tool('python_script',parameters={'file':str(self.script),'args':['sleep']})
        time.sleep(.4);self.service.cancel(row['id']);result=wait_task(self.store,row['id']);self.assertEqual('cancelled',result['status'],result)
        spec=self.service.registry.get('python_script');spec.id='short';spec.timeout=.5
        from dataclasses import asdict
        self.service.registry.save(asdict(spec))
        row=self.service.submit_tool('short',parameters={'file':str(self.script),'args':['sleep']});self.assertEqual('timed_out',wait_task(self.store,row['id'])['status'])
    def test_05_scope(self):
        self.store.add_target({'ip':'127.0.0.1','port':'8080','pass':'TEST-DO-NOT-LEAK'})
        with self.assertRaises(PermissionError):self.service.submit_operation('waf_check','127.0.0.1')
        self.assertNotIn('TEST-DO-NOT-LEAK',json.dumps(self.store.machine_records()))
    def test_05b_defense_catalogue_and_local_report(self):
        self.assertIn('defense_probe',self.service.specs)
        self.assertIn('defense_fail2ban_rollback',self.service.specs)
        self.assertTrue(self.service.specs['defense_probe'].read_only)
        self.assertFalse(self.service.specs['defense_fail2ban_ban'].read_only)
        row=self.service.submit_operation('defense_report','local',{'task_ids':[]})
        result=wait_task(self.store,row['id'])
        self.assertEqual('succeeded',result['status'],result)
        data=result['result']['data']['return']
        self.assertEqual('1.5.0',data['product_version'])
        self.assertTrue(Path(result['result']['data']['outputs']['markdown_report']).is_file())
    def test_06_legacy_audit_and_backup(self):
        source=self.path/'site';source.mkdir(exist_ok=True);(source/'index.php').write_text('<?php eval($_POST["x"]); ?>',encoding='utf-8')
        row=self.service.submit_operation('ops_audit_local','local',{'directory':str(source)})
        result=wait_task(self.store,row['id']);self.assertEqual('succeeded',result['status'],result)
        self.assertTrue(result['result']['data']['outputs']['_af_findings'],result)
        # Local backup uses the same original implementation as the GUI snapshot path.
        self.store.save_config({'allowed_targets':['local']})
        row=self.service.submit_operation('ops_backup','local',{'bak_mode':'local','bak_webdir':str(source),'bak_dir':str(self.path/'backups')})
        result=wait_task(self.store,row['id']);self.assertEqual('succeeded',result['status'],result)
        self.assertTrue(list((self.path/'backups').glob('*.tar.gz')))
    def test_07_idempotence(self):
        params={'file':str(self.script)}
        first=self.service.submit_tool('python_script',parameters=params,idempotency_key='same')
        second=self.service.submit_tool('python_script',parameters=params,idempotency_key='same');self.assertEqual(first['id'],second['id'])
        with self.assertRaises(ValueError):self.service.submit_tool('python_script',parameters={**params,'args':['x']},idempotency_key='same')
        wait_task(self.store,first['id'])
    def test_08_instance_and_port(self):
        connection=Connection(self.store);connection.start();self.assertTrue(connection.attached);connection.close();self.assertTrue(self.hub.alive)
        diagnosis=Connection(self.store).diagnose();self.assertEqual('ok',diagnosis['status'],diagnosis)
        self.assertEqual(['后台进程与管理鉴权','MCP 协议初始化','工具发现','健康调用'],[item['name'] for item in diagnosis['checks']])
        from .service import TaskService
        with self.assertRaises(RuntimeError):TaskService(self.store)
        other=Store(self.root,self.path/'other');other.save_config({'port':self.store.config['port']})
        with self.assertRaises(OSError):Hub(other).start()
    def test_08b_connection_error_categories(self):
        from urllib.error import HTTPError,URLError
        self.assertIn('HTTP 502',Connection.describe_error(HTTPError('http://127.0.0.1/mcp',502,'bad gateway',None,None)))
        self.assertIn('鉴权失败',Connection.describe_error(HTTPError('http://127.0.0.1/mcp',401,'unauthorized',None,None)))
        self.assertIn('后台服务未运行',Connection.describe_error(URLError(ConnectionRefusedError(10061,'refused'))))
    def test_09_protocol_two_clients(self):
        async def run():
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
            async def single(label):
                async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+self.store.tokens()['mcp_token']}) as http:
                    @asynccontextmanager
                    async def transport():
                        async with streamable_http_client(self.hub.url,http_client=http) as streams:yield streams[:2]
                    async with Client(transport()) as client:
                        tools=await client.list_tools();self.assertEqual(['godhand'],[t.name for t in tools.tools])
                        echo=await client.call_tool('godhand',{'action':'echo','arguments':{'message':label}});self.assertFalse(echo.is_error,echo)
                        task=await client.call_tool('godhand',{'action':'run_tool','arguments':{'tool':'python_script','parameters':{'file':str(self.script)}}})
                        self.assertFalse(task.is_error,task)
                        data=task.structured_content
                        if 'id' not in data:data=data['result']
                        identity=data['id']
                        await asyncio.to_thread(wait_task,self.store,identity)
                        result=await client.call_tool('godhand',{'action':'task','arguments':{'task_id':identity}});self.assertFalse(result.is_error)
                        resource=await client.read_resource('godhand://tasks/'+identity);self.assertTrue(resource.contents)
            await asyncio.gather(single('Claude-config-transport'),single('Codex-config-transport'))
        asyncio.run(run())
    def test_09b_unified_actions_validation_cancel(self):
        async def run():
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
            async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+self.store.tokens()['mcp_token']}) as http:
                @asynccontextmanager
                async def transport():
                    async with streamable_http_client(self.hub.url,http_client=http) as streams:yield streams[:2]
                async with Client(transport()) as client:
                    for action in ['health','targets','capabilities','tools','tasks']:
                        result=await client.call_tool('godhand',{'action':action})
                        self.assertFalse(result.is_error,result)
                        self.assertEqual(action,result.structured_content['action'])
                    selected=await client.call_tool('godhand',{'action':'capabilities','arguments':{'operation':'waf_check'}})
                    self.assertFalse(selected.is_error,selected)
                    self.assertEqual(['waf_check'],[item['id'] for item in selected.structured_content['result']])
                    defense=await client.call_tool('godhand',{'action':'capabilities','arguments':{'operation':'defense_probe'}})
                    self.assertFalse(defense.is_error,defense)
                    self.assertEqual(['defense_probe'],[item['id'] for item in defense.structured_content['result']])
                    self.assertIn('webdir',defense.structured_content['result'][0]['fields'])
                    report=await client.call_tool('godhand',{'action':'run','arguments':{'operation':'defense_report','target':'local','parameters':{'task_ids':[]}}})
                    self.assertFalse(report.is_error,report)
                    report_id=report.structured_content['result']['id']
                    report_row=await asyncio.to_thread(wait_task,self.store,report_id)
                    self.assertEqual('succeeded',report_row['status'],report_row)
                    for values in [{'action':'not_an_action'},{'action':'run','arguments':{'operation':'waf_check'}},{'action':'tasks','arguments':{'limit':'PRIVATE-INPUT-MARKER'}},{'action':'health','arguments':{'unknown':True}}]:
                        result=await client.call_tool('godhand',values)
                        self.assertTrue(result.is_error,result)
                        self.assertNotIn('PRIVATE-INPUT-MARKER',str(result))
                    task=await client.call_tool('godhand',{'action':'run_tool','arguments':{'tool':'python_script','parameters':{'file':str(self.script),'args':['sleep']}}})
                    identity=task.structured_content['result']['id']
                    cancelled=await client.call_tool('godhand',{'action':'cancel','arguments':{'task_id':identity}})
                    self.assertFalse(cancelled.is_error,cancelled)
                    row=await asyncio.to_thread(wait_task,self.store,identity)
                    self.assertEqual('cancelled',row['status'],row)
                    result=await client.call_tool('godhand',{'action':'logs','arguments':{'task_id':identity}})
                    self.assertFalse(result.is_error,result)
        asyncio.run(run())
    def test_10_http_business_and_platform(self):
        class Mock(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                if 'ajax=fm' in self.path:data=json.dumps({'content':'fixture-content','ok':True})
                else:data='WAF SIMULATED .waf.dat.php .user.ini waf_ban.php'
                self.send_response(200);self.end_headers();self.wfile.write(data.encode())
            def do_POST(self):
                from urllib.parse import parse_qs
                body=parse_qs(self.rfile.read(int(self.headers.get('Content-Length',0))).decode())
                if self.path=='/submit':
                    flag=body.get('flag',[''])[0];status=next((s for s in ('accepted','rejected','duplicate','rate_limited','unknown') if s in flag),'unknown')
                    self.send_response(429 if status=='rate_limited' else 200);self.end_headers();self.wfile.write(json.dumps({'status':status}).encode())
                else:
                    self.send_response(200);self.end_headers();self.wfile.write(b'waf_strong.php .waf.dat.php .user.ini waf_ban.php .htaccess [SHELL_OK] OK')
        http=ThreadingHTTPServer(('127.0.0.1',0),Mock);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            port=http.server_port;url=f'http://127.0.0.1:{port}/submit'
            fixture_id=self.store.add_target({'ip':'127.0.0.1','port':str(port),'pass':'fixture-secret'})['targets'][0]['id']
            self.store.save_config({'allowed_targets':[fixture_id,'local'],'submission_endpoints':[url]})
            for operation,parameters in [('waf_check',{}),('flag_test',{}),('monitor_file_read',{'remote_path':'/index.php'}),('channel_exec',{'e_cmd':'id'})]:
                row=self.service.submit_operation(operation,fixture_id,parameters)
                result=wait_task(self.store,row['id']);self.assertEqual('succeeded',result['status'],(operation,result))
            for status in ('accepted','rejected','duplicate','rate_limited','unknown'):
                row=self.service.submit_operation('flag_submit',fixture_id,{'manual_flag':'flag{'+status+'}','entry_submit':url})
                result=wait_task(self.store,row['id'])
                events=self.store.events(row['id'],0,500);outcomes=[e['payload']['status'] for e in events if e['kind']=='submission']
                self.assertEqual([status],outcomes,(status,result,events))
                self.assertEqual('succeeded' if status in ('accepted','duplicate') else 'failed',result['status'])
        finally:http.shutdown();http.server_close();thread.join(timeout=2)
    def test_11_recovery_isolation_serialization(self):
        from .service import TaskService
        other=Store(self.root,self.path/'recovery');service=TaskService(other);service.close()
        with other.connect() as c:
            for identity,write in [('read',0),('write',1)]:c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identity,'fixture','local','match-a','test','running',time.time(),time.time(),write,0,'{}',None))
        service=TaskService(other)
        try:
            self.assertEqual('interrupted',other.task('read')['status']);self.assertEqual('needs_review',other.task('write')['status'])
            self.assertEqual([],Store(self.root,self.path/'isolated').tasks())
        finally:service.close()
        first=self.service.submit_tool('python_script',parameters={'file':str(self.script),'args':['sleep']})
        second=self.service.submit_tool('python_script',parameters={'file':str(self.script)})
        time.sleep(.4);self.assertEqual('queued',self.store.task(second['id'])['status'])
        self.service.cancel(first['id']);wait_task(self.store,first['id']);self.assertEqual('succeeded',wait_task(self.store,second['id'])['status'])
    def test_12_real_tk_snapshot_same_backup_core(self):
        import tkinter as tk
        import main_work
        from .snapshot import freeze
        root=tk.Tk();root.withdraw()
        original_config_dir=main_work.CONFIG_DIR
        try:
            ops=main_work.OpsTab(root,None);ops.root=root
            config_dir=self.root/'管理工具';config_dir.mkdir(exist_ok=True)
            imported=self.store.import_targets([{'name':'MCP 记录','ip':'127.0.0.9','port':'8080','ssh_port':'2222','ssh_user':'ctf','shell':'/shell.php'}])['targets'][0]['id']
            main_work.CONFIG_DIR=str(config_dir)
            machines=main_work.MachinesTab(root,store=self.store)
            self.assertTrue(machines.tree.exists(imported))
            self.assertEqual('MCP 导入',machines.tree.item(imported,'values')[-1])
            legacy_id=self.store.legacy_machine_records()[0]['id']
            self.assertTrue(machines.tree.exists(legacy_id))
            self.assertEqual('历史登记',machines.tree.item(legacy_id,'values')[-1])
            from types import SimpleNamespace
            from unittest.mock import patch
            original_legacy=(config_dir/'machines.json').read_bytes()
            machines.tabs_ref=SimpleNamespace(**{name:tk.StringVar(value=value) for name,value in {
                'global_ip':'127.0.0.20','global_port':'8080','global_shell':'/health.php',
                'global_pass':'GUI-ASSET-SECRET','global_ssh_port':'22','global_ssh_user':'ctf'}.items()})
            with patch('main_work.simpledialog.askstring',return_value='GUI 新登记'):
                machines.save_current()
            created=next(row for row in self.store.machine_records() if row['name']=='GUI 新登记')
            self.assertEqual('desktop',created['source']);self.assertTrue(machines.tree.exists(created['id']))
            machines.tree.selection_set(created['id'])
            with patch('main_work.messagebox.askyesno',return_value=True):machines.delete_selected()
            self.assertFalse(any(row['id']==created['id'] for row in self.store.machine_records()))
            self.assertEqual(original_legacy,(config_dir/'machines.json').read_bytes())
            (self.path/'site').mkdir(exist_ok=True)
            (self.path/'site'/'fixture.txt').write_text('local fixture',encoding='utf-8')
            ops.bak_mode.set('local');ops.bak_webdir.delete(0,'end');ops.bak_webdir.insert(0,str(self.path/'site'))
            memo={};call=freeze(ops.do_backup,memo,'call')
            task=self.service.submit_call(call,parameters={'bak_dir':str(self.path/'tk-backups')})
            row=wait_task(self.store,task['id']);self.assertEqual('succeeded',row['status'],row)
            self.assertTrue(list((self.path/'tk-backups').glob('*.tar.gz')))
        finally:main_work.CONFIG_DIR=original_config_dir;root.destroy()

if __name__=='__main__':unittest.main(verbosity=2)
