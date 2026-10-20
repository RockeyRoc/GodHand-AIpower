"""Local-only target import, permission and MCP protocol checks."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
import unittest
from .core import Store,OperationSpec,atomic_json
from .server import Hub
from .test_acceptance import free_port


class Targets(unittest.TestCase):
    def setUp(self):
        self.path=Path(__file__).resolve().parent.parent/'.target-tests'/str(time.time_ns())
        self.root=self.path/'中文 比赛';self.root.mkdir(parents=True)
        self.store=Store(self.root,self.path/'data')
        self.spec=OperationSpec('fixture', '测试',local=False)

    def record(self,**extra):return dict(name='靶机 A',ip='127.0.0.1',port=8080,ssh_port=2222,ssh_user='ctf',shell='/shell.php',**extra)

    def test_01_persistence_secrets_no_auto_authorization(self):
        result=self.store.import_targets([self.record(**{'pass':'WEB-SECRET-MARKER','ssh_pass':'SSH-SECRET-MARKER'})])
        identity=result['targets'][0]['id'];self.assertEqual(1,result['imported']);self.assertFalse(result['targets'][0]['allowed'])
        fresh=Store(self.root,self.path/'data');target=fresh.resolve_target(identity)
        self.assertEqual('SSH-SECRET-MARKER',target['ssh_pass'])
        with self.assertRaises(PermissionError):fresh.allow(self.spec,target)
        public=json.dumps([result,fresh.machine_records()]);self.assertNotIn('SECRET-MARKER',public)
        for p in fresh.path.glob('tasks.sqlite3*'):
            self.assertNotIn(b'WEB-SECRET-MARKER',p.read_bytes());self.assertNotIn(b'SSH-SECRET-MARKER',p.read_bytes())
        self.assertEqual([],fresh.config['allowed_targets']);self.assertFalse((self.root/'管理工具/machines.json').exists())
        fresh.save_config({'allowed_targets':[identity]});fresh.allow(self.spec,fresh.resolve_target(identity))

    def test_02_alias_rebinding_never_authorizes_new_import(self):
        atomic_json(self.root/'管理工具/machines.json',[{'name':'已允许','ip':'127.0.0.1','port':'80'}])
        before=(self.root/'管理工具/machines.json').read_bytes()
        self.store.save_config({'allowed_targets':['127.0.0.1','已允许']})
        result=self.store.import_targets([dict(self.record(),name='已允许')]);identity=result['targets'][0]['id']
        self.assertFalse(result['targets'][0]['allowed'])
        with self.assertRaises(PermissionError):self.store.allow(self.spec,self.store.resolve_target(identity))
        with self.assertRaises(ValueError):self.store.resolve_target('已允许')
        with self.assertRaises(ValueError):self.store.resolve_target('127.0.0.1')
        self.assertEqual(before,(self.root/'管理工具/machines.json').read_bytes())

    def test_03_duplicate_does_not_replace_credentials(self):
        first=self.store.import_targets([self.record(**{'pass':'ORIGINAL-SECRET'})]);identity=first['targets'][0]['id']
        repeat=self.store.import_targets([dict(self.record(),name='新名称',**{'pass':'REPLACED-SECRET'})])
        self.assertEqual(1,repeat['skipped']);self.assertEqual(identity,repeat['targets'][0]['id'])
        self.assertEqual(['name','pass'],repeat['targets'][0]['differences'])
        self.assertNotIn('ORIGINAL-SECRET',json.dumps(repeat));self.assertNotIn('REPLACED-SECRET',json.dumps(repeat))
        self.assertEqual('ORIGINAL-SECRET',self.store.resolve_target(identity)['pass'])
        with self.assertRaises(ValueError):self.store.import_targets([dict(self.record(),ip='127.0.0.2'),self.record()],on_duplicate='error')
        self.assertEqual(1,len(self.store.machine_records()))

    def test_03b_delete_import_revokes_target_permission(self):
        identity=self.store.import_targets([self.record()])['targets'][0]['id']
        self.store.save_config({'allowed_targets':[identity]})
        result=self.store.delete_imported_target(identity)
        self.assertEqual({'deleted':True,'id':identity},result)
        self.assertEqual([],self.store.config['allowed_targets'])
        self.assertEqual([],self.store.machine_records())

    def test_03c_legacy_endpoint_conflicts_get_distinct_ids(self):
        atomic_json(self.root/'管理工具/machines.json',[
            {'name':'记录 A','ip':'127.0.0.1','port':'8080','pass':'FIRST'},
            {'name':'记录 B','ip':'127.0.0.1','port':'8080','pass':'SECOND'},
        ])
        rows=self.store.machine_records(private=True)
        self.assertNotEqual(rows[0]['id'],rows[1]['id'])
        self.assertTrue(rows[0]['conflict']);self.assertTrue(rows[1]['conflict'])

    def test_04_validation_atomic_and_namespace(self):
        invalid=[{},dict(self.record(),port=0),dict(self.record(),ip='http://127.0.0.1'),dict(self.record(),ip='local'),dict(self.record(),name='local'),dict(self.record(),id='local'),dict(self.record(),https='false'),dict(self.record(),ssh_pass=42)]
        for row in invalid:
            with self.assertRaises(ValueError):self.store.import_targets([dict(self.record(),ip='127.0.0.3'),row])
            self.assertEqual([],self.store.machine_records())
        self.store.import_targets([self.record(),dict(self.record(),ip='127.0.0.2',name='靶机 B')])
        other=Store(self.root,self.path/'other-data');self.assertEqual([],other.machine_records())
        with self.assertRaises(ValueError):other.import_targets([self.record()]*501)

    def test_05_concurrent_idempotent_import(self):
        def add(_):return Store(self.root,self.path/'data').import_targets([self.record()])
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(add,range(8)))
        self.assertEqual(1,sum(r['imported'] for r in results));self.assertEqual(1,len(self.store.machine_records()))

    def test_05b_config_updates_merge_across_store_instances(self):
        first=Store(self.root,self.path/'data');second=Store(self.root,self.path/'data')
        first.save_config({'allowed_targets':['preserve-this']})
        second.save_config({'submission_endpoints':['http://127.0.0.1/submit']})
        saved=json.loads((self.path/'data'/'hub.json').read_text(encoding='utf-8'))
        self.assertEqual(['preserve-this'],saved['allowed_targets'])
        self.assertEqual(['http://127.0.0.1/submit'],saved['submission_endpoints'])

    def test_06_mcp_single_entry_import(self):
        self.store.save_config({'port':free_port()});hub=Hub(self.store);hub.start()
        async def run():
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
            async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+self.store.tokens()['mcp_token']}) as http:
                @asynccontextmanager
                async def transport():
                    async with streamable_http_client(hub.url,http_client=http) as streams:yield streams[:2]
                async with Client(transport()) as client:
                    tools=await client.list_tools();self.assertEqual(['godhand'],[t.name for t in tools.tools])
                    imported=await client.call_tool('godhand',{'action':'targets_import','arguments':{'records':[self.record(**{'ssh_pass':'RPC-SECRET'})]}})
                    self.assertFalse(imported.is_error,imported);self.assertNotIn('RPC-SECRET',str(imported))
                    identity=imported.structured_content['result']['targets'][0]['id']
                    updated=await client.call_tool('godhand',{'action':'targets_update','arguments':{'id':identity,'changes':{'name':'RPC 更新资产'},'expected_revision':1}})
                    self.assertFalse(updated.is_error,updated);self.assertNotIn('RPC-SECRET',str(updated))
                    self.assertEqual(2,updated.structured_content['result']['revision'])
                    self.assertIs(False,updated.structured_content['result']['requires_local_selection'])
                    self.assertEqual('RPC-SECRET',self.store.resolve_target(identity)['ssh_pass'])
                    stale=await client.call_tool('godhand',{'action':'targets_update','arguments':{'id':identity,'changes':{'name':'过期修改'},'expected_revision':1}})
                    self.assertTrue(stale.is_error,stale)
                    targets=await client.call_tool('godhand',{'action':'targets'});self.assertFalse(targets.is_error,targets)
                    self.assertFalse(targets.structured_content['result'][0]['allowed'])
                    denied=await client.call_tool('godhand',{'action':'run','arguments':{'operation':'waf_check','target':identity}})
                    self.assertTrue(denied.is_error,denied)
                    invalid=await client.call_tool('godhand',{'action':'targets_import','arguments':{'records':[dict(self.record(),port='INVALID-SECRET')]}})
                    self.assertTrue(invalid.is_error,invalid);self.assertNotIn('INVALID-SECRET',str(invalid))
        try:asyncio.run(run())
        finally:hub.stop()

    def test_07_gui_selection_defers_authorization(self):
        import tkinter as tk
        from .targets_gui import TargetPicker
        imported=self.store.import_targets([self.record()]);identity=imported['targets'][0]['id']
        root=tk.Tk();root.withdraw();selected=[]
        try:
            picker=TargetPicker(root,self.store,['127.0.0.1'],selected.extend)
            self.assertTrue(picker.tree.exists(identity));self.assertNotIn(identity,picker.selected)
            picker.select_visible(True)
            another=self.store.import_targets([dict(self.record(),ip='127.0.0.2',name='靶机 B')])['targets'][0]['id']
            picker.reload();self.assertTrue(picker.tree.exists(another));self.assertNotIn(another,picker.selected)
            picker.search.set('靶机 B');picker.select_visible(True);picker.apply()
            self.assertIn(identity,selected);self.assertIn(another,selected)
            self.assertEqual([],self.store.config['allowed_targets'])
        finally:root.destroy()

    def test_07b_picker_requires_new_selection_after_connection_change(self):
        import tkinter as tk
        from .targets_gui import TargetPicker
        identity=self.store.import_targets([self.record()])['targets'][0]['id']
        self.store.save_config({'allowed_targets':[identity]})
        root=tk.Tk();root.withdraw();selected=[]
        try:
            picker=TargetPicker(root,self.store,[identity],selected.extend)
            self.assertIn(identity,picker.selected)
            self.store.update_target(identity,{'ip':'127.0.0.3'})
            picker.reload();self.assertNotIn(identity,picker.selected)
            picker.select_visible(True);picker.reload();self.assertIn(identity,picker.selected)
            picker.apply();self.store.save_config({'allowed_targets':selected})
            self.assertTrue(self.store.resolve_target(identity)['allowed'])
        finally:root.destroy()

    def test_08_authorized_worker_uses_imported_snapshot(self):
        import threading
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from urllib.parse import parse_qs
        from .service import TaskService
        from .test_acceptance import wait_task
        seen=[]
        class Mock(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'OK')
            def do_POST(self):
                body=parse_qs(self.rfile.read(int(self.headers.get('Content-Length',0))).decode())
                seen.append('IMPORTED-SECRET' in body)
                self.send_response(200);self.end_headers();self.wfile.write(b'OK')
        fixture=ThreadingHTTPServer(('127.0.0.1',0),Mock)
        thread=threading.Thread(target=fixture.serve_forever,daemon=True);thread.start()
        source=Path(__file__).resolve().parent.parent
        (self.root/'main_work.py').write_text('import sys\nsys.path.insert(0,'+repr(str(source))+')\nfrom AI中枢.entry import main\nraise SystemExit(main())\n',encoding='utf-8')
        service=TaskService(self.store)
        try:
            imported=self.store.import_targets([dict(self.record(),port=fixture.server_port,**{'pass':'IMPORTED-SECRET'})])
            identity=imported['targets'][0]['id'];self.store.save_config({'allowed_targets':[identity]})
            task=service.submit_operation('flag_test',identity);row=wait_task(self.store,task['id'])
            self.assertEqual('succeeded',row['status'],row);self.assertEqual([True],seen)
            self.assertNotIn('IMPORTED-SECRET',json.dumps([row,self.store.events(task['id'])]))
        finally:service.close();fixture.shutdown();fixture.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main(verbosity=2)
