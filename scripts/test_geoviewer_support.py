# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Support diagnostics acceptance. Website: https://saherlabs.dev/"""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from geoviewer_diagnostics import Diagnostics
import geoviewer_support as support


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gv_support_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root/'Private_Project_data'; self.run.mkdir()
        self.report = self.root/'Private_Project_REPORT.html'
        self.report.write_text('<html><body><main>Retained figure</main></body></html>', encoding='utf-8')
        self.logs = Diagnostics(self.root/'Private_Project_LOG.txt', self.root/'Private_Project_EVENTS.jsonl')
        self.addCleanup(self.logs.close)

    def build(self, **kwargs):
        self.logs.close()
        descriptor = dict(run=str(self.run), report=str(self.report), logs=self.logs.paths(),
                          status=self.logs.status, run_id=self.logs.run_id)
        with redirect_stdout(io.StringIO()): path = support.build_bundle(descriptor, **kwargs)
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            files = {n:archive.read(n).decode('utf-8') for n in archive.namelist()}
        return path, files

    def test_failure_bundle_keeps_debug_evidence_excludes_datasets(self):
        private = 'Confidential Well Alpha'
        object_id = '53e828db-49f1-49db-8c41-4bcc2aa8500e'
        error = dict(exception_type='PermissionError', winerror=32, errno=13,
                     traceback='Traceback: decoder.py, line 92\nPermissionError: sharing violation')
        self.logs.event('object_failed', object_id=object_id, name=private, **error)
        self.logs.event('completed', status='completed_with_gaps')
        (self.run/'RUN_RESULT.json').write_text(json.dumps(dict(status='completed_with_gaps',
            name=private, object_id=object_id, error=error, api_key='DO_NOT_SHARE_SECRET',
            project_context={'saved_by':'Private operator'})), encoding='utf-8')
        for name in ('volume.segy', 'test.pet', 'sample.csv', 'figure.png', 'workflow.json'):
            (self.run/name).write_text('DO_NOT_INCLUDE_DATA')
        source = self.run/'08_native_project'; source.mkdir()
        (source/'RUN_RESULT.json').write_text('DO_NOT_INCLUDE_NATIVE')
        before = self.logs.text_path.read_bytes()
        path, files = self.build()
        content = '\n'.join(files.values())
        for value in (private, object_id, 'DO_NOT_SHARE_SECRET', 'DO_NOT_INCLUDE_DATA', 'DO_NOT_INCLUDE_NATIVE', 'Private operator'):
            self.assertNotIn(value, content)
        self.assertIn('PermissionError', content); self.assertIn('sharing violation', content)
        self.assertIn('Traceback', content); self.assertIn('winerror', content)
        self.assertEqual(before, self.logs.text_path.read_bytes())
        self.assertIn(path.name, self.report.read_text()); self.assertIn('Retained figure', self.report.read_text())
        self.assertEqual(json.loads(files['ENVIRONMENT.json'])['status'], 'completed_with_gaps')

    def test_fallback_and_missing_logs_are_not_silently_lost(self):
        self.logs.event('stage', label='before fallback')
        original = self.logs.text_path
        self.logs.log.close()
        with redirect_stdout(io.StringIO()): self.logs.event('object_failed', reason='after fallback')
        self.logs.all_paths.append(self.root/'missing.log')
        _, files = self.build()
        content='\n'.join(files.values())
        self.assertIn('before fallback',content); self.assertIn('after fallback',content)
        manifest=json.loads(files['CONTENTS.json'])
        self.assertTrue(any(r['role']=='missing.log' for r in manifest['unavailable']))
        self.assertIn(str(original),self.logs.paths())

    def test_bootstrap_failure_has_exit_code_and_environment(self):
        session=self.root/'session';session.mkdir()
        (session/'BOOTSTRAP_LOG.txt').write_text('Repair failed: checksum mismatch')
        (session/'launcher_result.json').write_text(json.dumps({'exit_code':1}))
        self.logs.close()
        with redirect_stdout(io.StringIO()): path=support.build_bundle(session=session)
        with zipfile.ZipFile(path) as z:
            env=json.loads(z.read('ENVIRONMENT.json'))
            self.assertEqual(env['status'],'failed');self.assertEqual(env['launcher_exit_code'],1)
            self.assertIn('dependencies',env['environment'])
            self.assertNotIn('environ',env['environment'])
            self.assertTrue(any(b'checksum mismatch' in z.read(n) for n in z.namelist()))

    def test_rotated_child_logs_and_cancellation_are_collected(self):
        rotated=self.root/'run_LOG.001.txt';rotated.write_text('Rotated progress')
        child=self.root/'child.log';child.write_text('Child error traceback')
        self.logs.all_paths.append(rotated)
        self.logs.event('child_log_fallback',path=str(child))
        self.logs.event('cancelled',severity='error',reason='KeyboardInterrupt')
        path,files=self.build()
        self.assertIn('Rotated progress','\n'.join(files.values()))
        self.assertIn('Child error traceback','\n'.join(files.values()))
        self.assertEqual(json.loads(files['ENVIRONMENT.json'])['status'],'cancelled')

    def test_existing_bundle_never_overwritten(self):
        path,_=self.build();before=path.read_bytes()
        with self.assertRaises(FileExistsError):self.build()
        self.assertEqual(before,path.read_bytes())

    def test_archive_failure_preserves_extraction_and_falls_back(self):
        original=zipfile.ZipFile
        def fail_first(path,*args,**kwargs):
            if Path(path).parent==self.root:raise PermissionError('Output storage blocked')
            return original(path,*args,**kwargs)
        with patch.object(support.zipfile,'ZipFile',side_effect=fail_first):
            path,_=self.build()
        self.assertNotEqual(path.parent,self.root)
        self.assertTrue(path.is_file())

    def test_compression_and_json_events_remain_readable(self):
        for i in range(200): self.logs.event('child_output',line='Repeated stage diagnostic '+str(i))
        self.logs.event('completed',status='completed')
        _,files=self.build()
        structured=next(v for k,v in files.items() if k.endswith('.jsonl'))
        events=[json.loads(line) for line in structured.splitlines()]
        self.assertEqual(len(events),201)
        self.assertEqual(len({r['sequence'] for r in events}),201)
        path=self.report.with_name('Private_Project_SUPPORT.zip')
        with zipfile.ZipFile(path) as z:
            self.assertLess(sum(i.compress_size for i in z.infolist()),sum(i.file_size for i in z.infolist())/2)

    def test_report_link_supports_fallback_on_another_drive(self):
        bundle=self.root/'fallback.zip';bundle.write_bytes(b'test')
        with patch.object(support.os.path,'relpath',side_effect=ValueError('Different drives')):
            support.attach_link(self.report,bundle)
        self.assertIn(bundle.as_uri(),self.report.read_text())

    def test_redaction_preserves_schema_and_correlates_identifiers(self):
        scrub=support.Scrubber();scrub.learn({'name':'Depth','project_file':str(self.root/'Secret Project.pet')})
        value=scrub.data({'depth_range':[1,2], 'name':'Depth','reason':'Depth has unsupported geometry'})
        self.assertEqual(value['depth_range'],[1,2])
        self.assertIn(value['name'],value['reason'])
        self.assertNotIn('Secret Project',scrub.text(str(self.root/'Secret Project.pet')))


if __name__=='__main__':unittest.main()
