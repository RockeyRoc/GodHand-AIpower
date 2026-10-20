"""Regression tests for deployment reversibility and public release boundaries."""
import hashlib
from pathlib import Path
import shutil
import uuid
import unittest

from 防御模块.aoi import catalogue, inject_php, restore_php
from .release import verify, source_manifest


class AoiTests(unittest.TestCase):
    def test_namespaced_and_strict_php_preserves_declaration_order(self):
        for original in [b'<?php declare(strict_types=1); namespace A; echo 1;',
                         b'<?php /* comment */ namespace A { echo 1; }',
                         b'<?php declare(ticks=1); echo 1;', b'<html><?php echo 1; ?>']:
            installed, addition = inject_php(original, "/home/test path/quote'file.php")
            self.assertEqual(original, installed.replace(addition, b'', 1))
            if b'namespace' in original:
                self.assertLess(installed.index(b'namespace'), installed.index(b'GODHAND_AOI_BEGIN'))
            if b'declare(' in original:
                self.assertLess(installed.index(b'declare('), installed.index(b'GODHAND_AOI_BEGIN'))

    def test_rollback_restores_bytes_and_preserves_concurrent_edits(self):
        original = b'<?php echo "before";'
        installed, addition = inject_php(original, '/home/test/web_probe.php')
        digest = hashlib.sha256(installed).hexdigest()
        self.assertEqual(original, restore_php(installed, original, addition, digest))
        edited = installed.replace(b'before', b'after')
        self.assertEqual(original.replace(b'before', b'after'), restore_php(edited, original, addition, digest))
        with self.assertRaises(ValueError):
            restore_php(edited.replace(addition, b'changed probe'), original, addition, digest)
        with self.assertRaises(ValueError):
            inject_php(installed, '/another/path')

    def test_native_runtime_and_remote_deployment_have_distinct_permissions(self):
        specs = catalogue()
        self.assertTrue(specs['defense_aoi_start'].local)
        self.assertFalse(specs['defense_aoi_deploy'].local)
        self.assertFalse(specs['defense_aoi_deploy'].read_only)
        self.assertTrue(specs['defense_aoi_probe'].read_only)
        self.assertEqual('AoiHandler', specs['defense_aoi_open'].actor)

    def test_release_excludes_docx_folder_and_preserves_other_documents(self):
        root = Path(__file__).resolve().parent.parent
        sources = source_manifest(root)
        self.assertFalse(any(p.parts[0] == 'docx' for p in sources))
        self.assertIn(Path('docs/防御中心.md'), sources)
        for name in ['docx/接入.docx', 'docx/nested/evidence.json']:
            package = root / '.acceptance' / ('aoi-release-' + uuid.uuid4().hex)
            package.mkdir(parents=True)
            file = package / name
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'local')
                with self.assertRaises(ValueError):
                    verify(package)
            finally:
                shutil.rmtree(package)
        package = root / '.acceptance' / ('aoi-release-' + uuid.uuid4().hex)
        package.mkdir(parents=True)
        try:
            (package / 'docs').mkdir()
            for name in ['docs/验收.md', 'docs/参考.docx', '参考.docx']:
                (package / name).write_bytes(b'public')
            self.assertEqual(3, verify(package))
        finally:
            shutil.rmtree(package)


if __name__ == '__main__':
    unittest.main()
