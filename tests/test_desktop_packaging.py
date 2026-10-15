"""Archive isolation tests using a tiny synthetic engine; no extraction or UI."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC=importlib.util.spec_from_file_location('edition_builder',Path(__file__).resolve().parents[1]/'tools/build_desktop_editions.py')
builder=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(builder)

class Editions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';(self.source/'desktop').mkdir(parents=True)
        (self.source/'desktop/FieldViewer.Desktop.ps1').write_text('# gui only')
        (self.source/'desktop/FieldViewer.Desktop.Tests.ps1').write_text('# not distributed')
        (self.source/'desktop/smoke.png').write_bytes(b'not a shipped asset')
        (self.source/'desktop/.qa').mkdir()
        (self.source/'desktop/.qa/session.json').write_text('{}')
        (self.source/'FieldViewer_Desktop.bat').write_text('@echo off')
        (self.source/'docs').mkdir()
        (self.source/'docs/DESKTOP_GUI.md').write_text('Desktop instructions')
        self.engine=self.root/'base.zip'
        self.members={'FieldViewer/toolkit.json':b'{"version":"1.0.0"}',
          'FieldViewer_data_extractor.bat':b'original launcher',
          'FieldViewer/scripts/core.py':b'original engine\r\n',
          'FieldViewer/bootstrap/runtime.zip':b'opaque cached runtime'}
        self.write_engine()
    def write_engine(self,extra=None):
        with zipfile.ZipFile(self.engine,'w') as z:
            for n,b in {**self.members,**(extra or {})}.items():z.writestr(n,b)
        self.sha=hashlib.sha256(self.engine.read_bytes()).hexdigest()
    def build(self,**kw):
        return builder.build(self.engine,kw.get('sha',self.sha),self.root/'out',self.source,'preview-test','fixture-revision')
    def test_both_editions_keep_every_engine_byte(self):
        result=self.build()
        for edition in result['editions']:
            with zipfile.ZipFile(edition['path']) as z:
                for n,b in self.members.items():self.assertEqual(z.read(n),b)
                meta=json.loads(z.read('EDITION.json'))
                self.assertEqual(meta['engine_zip_sha256'],self.sha)
                self.assertNotIn('desktop/FieldViewer.Desktop.Tests.ps1',z.namelist())
                self.assertNotIn('desktop/smoke.png',z.namelist())
                self.assertNotIn('desktop/.qa/session.json',z.namelist())
                self.assertEqual('FieldViewer_Desktop.bat' in z.namelist(),edition['edition']=='Desktop')
                self.assertEqual('DESKTOP_README.md' in z.namelist(),edition['edition']=='Desktop')
                for row in json.loads(z.read('PACKAGE_MANIFEST.json'))['files']:
                    self.assertEqual(hashlib.sha256(z.read(row['path'])).hexdigest(),row['sha256'])
        self.assertEqual(result['editions'][0]['shared_engine_manifest_sha256'],result['editions'][1]['shared_engine_manifest_sha256'])
    def test_wrong_hash_fails_before_output(self):
        with self.assertRaisesRegex(ValueError,'SHA-256'):self.build(sha='0'*64)
        self.assertFalse((self.root/'out').exists())
    def test_existing_output_never_overwritten(self):
        self.build();p=self.root/'out/FieldViewer-Desktop-GUI-preview-win64.zip';before=p.read_bytes()
        with self.assertRaises(FileExistsError):self.build()
        self.assertEqual(before,p.read_bytes())
    def test_traversal_and_windows_stream_names_rejected(self):
        for name in ('../outside','/absolute','C:/absolute','FieldViewer/core.py:stream'):
            with self.subTest(name=name):
                self.write_engine({name:b'bad'})
                with self.assertRaises(ValueError):self.build()
        # Windows zipfile normalizes backslashes while writing; test the
        # validator directly rather than asserting that normalized input fails.
        with self.assertRaises(ValueError):builder.safe_name('FieldViewer\\escape')
    def test_case_collisions_rejected(self):
        self.write_engine({'FIELDVIEWER/TOOLKIT.JSON':b'bad'})
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.build()
    def test_gui_cannot_override_base(self):
        self.write_engine({'desktop/FieldViewer.Desktop.ps1':b'conflict'})
        with self.assertRaisesRegex(ValueError,'conflicting'):self.build()
    def test_missing_gui_entry_fails_before_output(self):
        (self.source/'desktop/FieldViewer.Desktop.ps1').unlink()
        with self.assertRaisesRegex(ValueError,'entrypoint'):self.build()
        self.assertFalse((self.root/'out').exists())

if __name__=='__main__':unittest.main()
