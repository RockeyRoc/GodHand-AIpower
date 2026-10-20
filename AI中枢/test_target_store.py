"""Asset migration/CRUD regressions using temporary local data only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from .core import Store,OperationSpec,atomic_json,protect
from .target_store import backup_info
from .targets import endpoint_key,normalize


class TargetStorage(unittest.TestCase):
    def setUp(self):
        self.path=Path(__file__).resolve().parent.parent/'.target-tests'/('unified-'+str(time.time_ns()))
        self.root=self.path/'application';self.root.mkdir(parents=True)
        self.store=Store(self.root,self.path/'match-a')
        self.legacy=self.root/'管理工具'/'machines.json'
        self.record={'name':'资产 A','ip':'127.0.0.1','port':'8080','ssh_port':'22','ssh_user':'ctf','shell':'/shell.php','pass':'ASSET-SECRET'}

    def seed_mcp_v1(self,record=None,identity='mcp_historical'):
        row=normalize(record or self.record,1)
        with self.store.connect() as c:
            c.execute('INSERT INTO mcp_targets VALUES(?,?,?,?)',
                      (identity,endpoint_key(row),protect(json.dumps(row).encode()),time.time()))
        return identity

    def legacy_id(self,record):
        return hashlib.sha256('|'.join(str(record.get(k,'')) for k in ['ip','port','ssh_port','ssh_user','shell']).encode()).hexdigest()[:16]

    def test_01_migration_preserves_ids_credentials_permissions_and_backup(self):
        old=dict(self.record,name='历史资产',**{'pass':'LEGACY-SECRET'})
        old['authorization_ref']='keep-reference'
        atomic_json(self.legacy,[old]);original=self.legacy.read_bytes()
        mcp_id=self.seed_mcp_v1();legacy_id=self.legacy_id(old)
        config=dict(self.store.config,allowed_targets=[mcp_id,legacy_id]);atomic_json(self.store.config_path,config)
        rows={row['id']:row for row in self.store.machine_records(private=True)}
        self.assertEqual({mcp_id,legacy_id},set(rows))
        self.assertEqual('ASSET-SECRET',rows[mcp_id]['pass'])
        self.assertEqual('LEGACY-SECRET',rows[legacy_id]['pass'])
        self.assertEqual('keep-reference',rows[legacy_id]['authorization_ref'])
        self.assertTrue(all(row['allowed'] and row['conflict'] for row in rows.values()))
        state=self.store.target_migration_status();backup=self.store.path/state['mcp_v1']['backup']
        self.assertEqual({'version':1,'records':2,'sources':['legacy_v1','mcp_v1']},backup_info(self.store,backup))
        self.assertNotIn(b'LEGACY-SECRET',backup.read_bytes())
        self.assertEqual(original,self.legacy.read_bytes())
        fresh=Store(self.root,self.path/'match-a');self.assertEqual(set(rows),{r['id'] for r in fresh.machine_records()})
        self.assertEqual(1,len(list((self.store.path/'backups').glob('*.dpapi'))))
        for file in self.store.path.glob('tasks.sqlite3*'):
            self.assertNotIn(b'ASSET-SECRET',file.read_bytes());self.assertNotIn(b'LEGACY-SECRET',file.read_bytes())

    def test_02_corrupt_legacy_rolls_back_both_sources(self):
        self.legacy.parent.mkdir(exist_ok=True);self.legacy.write_bytes(b'{broken-secret')
        identity=self.seed_mcp_v1()
        with self.assertRaisesRegex(ValueError,'原文件已保留'):self.store.machine_records()
        with self.store.connect() as c:
            self.assertEqual(0,c.execute('SELECT COUNT(*) FROM targets').fetchone()[0])
            self.assertEqual(0,c.execute('SELECT COUNT(*) FROM target_migrations').fetchone()[0])
            self.assertEqual(identity,c.execute('SELECT id FROM mcp_targets').fetchone()[0])
        self.assertEqual(b'{broken-secret',self.legacy.read_bytes())
        self.assertFalse(self.legacy.with_name('.godhand-targets-owner.json').exists())

    def test_03_backup_failure_and_retry(self):
        atomic_json(self.legacy,[self.record]);before=self.legacy.read_bytes()
        with patch('AI中枢.target_store.atomic_bytes',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):self.store.machine_records()
        with self.store.connect() as c:self.assertEqual(0,c.execute('SELECT COUNT(*) FROM targets').fetchone()[0])
        self.assertEqual(before,self.legacy.read_bytes())
        self.assertEqual(1,len(self.store.machine_records()))

    def test_04_competition_isolation_and_no_resurrection(self):
        atomic_json(self.legacy,[self.record]);first=self.store.machine_records()[0]['id']
        other=Store(self.root,self.path/'match-b');self.assertEqual([],other.machine_records())
        self.assertEqual('another_competition',other.target_migration_status()['legacy_v1']['status'])
        self.store.delete_target(first)
        # The original rollback file is never consulted after successful migration.
        self.legacy.write_bytes(b'changed after migration')
        self.assertEqual([],Store(self.root,self.path/'match-a').machine_records())

    def test_05_explicit_update_preserves_id_and_omitted_credentials(self):
        identity=self.store.import_targets([self.record])['targets'][0]['id']
        result=self.store.update_target(identity,{'name':'改名'},expected_revision=1)
        self.assertEqual(identity,result['id']);self.assertEqual(2,result['revision'])
        self.assertEqual(['name'],result['changed_fields']);self.assertNotIn('ASSET-SECRET',json.dumps(result))
        self.assertEqual('ASSET-SECRET',self.store.resolve_target(identity)['pass'])
        with self.assertRaisesRegex(ValueError,'其他客户端'):self.store.update_target(identity,{'name':'过期编辑'},1)
        with self.assertRaises(ValueError):self.store.update_target(identity,{'pass':'[REDACTED]'})
        with self.assertRaises(ValueError):self.store.update_target(identity,{'name':'不应保存','port':0})
        self.assertEqual('改名',self.store.resolve_target(identity)['name'])

    def test_06_connection_update_requires_local_reselection_and_live_config(self):
        identity=self.store.import_targets([self.record])['targets'][0]['id'];spec=OperationSpec('fixture','本机测试')
        self.store.save_config({'allowed_targets':[identity]});stale=Store(self.root,self.path/'match-a')
        stale.allow(spec,stale.resolve_target(identity))
        update=self.store.update_target(identity,{'ip':'127.0.0.2'})
        self.assertTrue(update['requires_local_selection']);self.assertFalse(update['allowed'])
        with self.assertRaises(PermissionError):stale.allow(spec,stale.resolve_target(identity))
        self.store.save_config({'allowed_targets':[identity]});stale.allow(spec,stale.resolve_target(identity))
        self.store.save_config({'enabled_operations':[]})
        with self.assertRaises(PermissionError):stale.allow(spec,stale.resolve_target(identity))

    def test_07_deleted_id_is_never_reused(self):
        first=self.store.import_targets([self.record])['targets'][0]['id']
        self.store.save_config({'allowed_targets':[first]})
        stale=Store(self.root,self.path/'match-a');snapshot=stale.resolve_target(first)
        self.store.delete_target(first)
        second=self.store.import_targets([self.record])['targets'][0]['id']
        self.assertNotEqual(first,second);self.assertFalse(self.store.resolve_target(second)['allowed'])
        with self.assertRaises(PermissionError):stale.allow(OperationSpec('fixture','测试'),snapshot)

    def test_08_concurrent_revision_and_import(self):
        def add(_):return Store(self.root,self.path/'match-a').import_targets([self.record])
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(add,range(8)))
        self.assertEqual(1,sum(r['imported'] for r in results));identity=self.store.machine_records()[0]['id']
        def edit(index):
            try:Store(self.root,self.path/'match-a').update_target(identity,{'name':f'edit-{index}'},1);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=4) as pool:edits=list(pool.map(edit,range(4)))
        self.assertEqual(1,sum(edits));self.assertEqual(2,self.store.resolve_target(identity)['revision'])

    def test_09_delete_only_one_historical_conflict_keeps_other_id(self):
        atomic_json(self.legacy,[self.record,dict(self.record,name='另一记录',**{'pass':'SECOND-SECRET'})])
        rows=self.store.machine_records();first,second=[row['id'] for row in rows]
        self.store.delete_target(first)
        fresh=Store(self.root,self.path/'match-a');remaining=fresh.machine_records()
        self.assertEqual([second],[row['id'] for row in remaining]);self.assertFalse(remaining[0]['conflict'])
        self.assertEqual('SECOND-SECRET',fresh.resolve_target(second)['pass'])

    def test_10_corrupt_mcp_and_managed_records_are_retained(self):
        with self.store.connect() as c:c.execute('INSERT INTO mcp_targets VALUES(?,?,?,?)',('mcp_broken','fixture',b'broken',time.time()))
        with self.assertRaisesRegex(ValueError,'原数据已保留'):self.store.machine_records()
        with self.store.connect() as c:
            self.assertEqual(b'broken',bytes(c.execute('SELECT record FROM mcp_targets').fetchone()[0]))
            c.execute('DELETE FROM mcp_targets')
        identity=self.store.import_targets([self.record])['targets'][0]['id']
        with self.store.connect() as c:c.execute('UPDATE targets SET record=? WHERE id=?',(b'broken',identity))
        with self.assertRaisesRegex(ValueError,'原数据已保留'):self.store.machine_records()
        with self.assertRaises(ValueError):self.store.import_targets([dict(self.record,ip='127.0.0.2')])
        with self.store.connect() as c:self.assertEqual(1,c.execute('SELECT COUNT(*) FROM targets').fetchone()[0])

    def test_11_duplicate_update_validation_and_batch_rollback(self):
        first=self.store.import_targets([self.record])['targets'][0]['id']
        second=self.store.import_targets([dict(self.record,ip='127.0.0.2')])['targets'][0]['id']
        with self.assertRaisesRegex(ValueError,'已存在'):self.store.update_target(second,{'ip':'127.0.0.1'})
        self.assertEqual('127.0.0.2',self.store.resolve_target(second)['ip'])
        with self.assertRaises(ValueError):self.store.import_targets([dict(self.record,ip='127.0.0.3'),self.record],'error')
        self.assertEqual({first,second},{row['id'] for row in self.store.machine_records()})

    def test_12_restore_backup_to_empty_repository(self):
        atomic_json(self.legacy,[self.record]);identity=self.store.machine_records()[0]['id']
        backup=self.store.path/self.store.target_migration_status()['legacy_v1']['backup']
        recovered=Store(self.root,self.path/'recovered')
        result=recovered.restore_target_backup(backup);self.assertEqual(1,result['restored'])
        row=recovered.resolve_target(identity);self.assertEqual('ASSET-SECRET',row['pass'])
        self.assertTrue(row['requires_local_selection']);self.assertFalse(row['allowed'])
        with self.assertRaises(ValueError):recovered.restore_target_backup(backup)
        self.assertEqual([identity],[item['id'] for item in recovered.machine_records()])
        recovered.save_config({'allowed_targets':[identity]})
        self.assertTrue(recovered.resolve_target(identity)['allowed'])

    def test_13_original_aliases_survive_normalization_but_not_endpoint_changes(self):
        old=dict(self.record,ip='EXAMPLE.TEST.',name='  历史名称  ')
        atomic_json(self.legacy,[old])
        config=dict(self.store.config,allowed_targets=['EXAMPLE.TEST.']);atomic_json(self.store.config_path,config)
        row=self.store.machine_records()[0];identity=row['id']
        self.assertEqual('example.test',row['ip']);self.assertTrue(row['allowed'])
        self.store.update_target(identity,{'ip':'127.0.0.5'})
        self.store.save_config({'allowed_targets':['EXAMPLE.TEST.']})
        self.assertFalse(self.store.resolve_target(identity)['allowed'])
        self.store.save_config({'allowed_targets':[identity]});self.assertTrue(self.store.resolve_target(identity)['allowed'])
        self.store.save_config({'allowed_targets':['EXAMPLE.TEST.']});self.assertFalse(self.store.resolve_target(identity)['allowed'])

    def test_14_delete_reports_scope_cleanup_failure_without_overwriting_config(self):
        identity=self.store.import_targets([self.record])['targets'][0]['id']
        self.store.config_path.write_bytes(b'{broken config')
        result=self.store.delete_target(identity)
        self.assertTrue(result['deleted']);self.assertIn('warning',result)
        self.assertEqual(b'{broken config',self.store.config_path.read_bytes())
        with self.store.connect() as c:self.assertEqual(0,c.execute('SELECT COUNT(*) FROM targets').fetchone()[0])


if __name__=='__main__':unittest.main(verbosity=2)
