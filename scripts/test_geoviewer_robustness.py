"""Fault-injection checks with actual Windows handle locks and isolated fixtures.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout, closing
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import geoviewer_io as gio
from geoviewer_diagnostics import Diagnostics, PREFIX
import geoviewer_delivery as delivery
import geoviewer_stage as stage
import petrel_native_recovery as recovery
import petrel_geoscience_tools as geo
import petrel_progress as progress
import test_petrel_native_recovery as fixtures


@contextmanager
def windows_directory_lock(path):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    # Read/write sharing, deliberately without FILE_SHARE_DELETE.
    handle=kernel.CreateFileW(str(path),1,3,None,3,0x02000000,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:yield
    finally:kernel.CloseHandle(handle)


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='GeoViewer fault test ')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.old_budget=gio._retry_wait;gio._retry_wait=0
        self.addCleanup(setattr,gio,'_retry_wait',self.old_budget)

    @unittest.skipUnless(os.name=='nt','Windows handle semantics')
    def test_transient_real_windows_lock_recovers_identical_staging(self):
        pending=self.root/'data.partial';pending.mkdir();(pending/'samples.csv').write_text('1,2\n')
        before=gio.sha256(pending/'samples.csv');locked=threading.Event()
        def hold():
            with windows_directory_lock(pending):
                locked.set();threading.Event().wait(.35)
        thread=threading.Thread(target=hold);thread.start();locked.wait(3)
        log=Diagnostics(self.root/'run_LOG.txt',self.root/'events.jsonl')
        try:gio.finalize_directory(pending,self.root/'data')
        finally:thread.join();log.close()
        self.assertEqual(before,gio.sha256(self.root/'data/samples.csv'))
        events=[json.loads(line) for line in log.events_path.read_text().splitlines()]
        self.assertTrue(any(e['event']=='retry_scheduled' and e['winerror'] in (5,32,33) for e in events))
        self.assertTrue(any(e['event']=='io_recovered' for e in events))

    @unittest.skipUnless(os.name=='nt','Windows handle semantics')
    def test_persistent_real_windows_lock_is_bounded_and_retained(self):
        pending=self.root/'data.partial';pending.mkdir();(pending/'samples.csv').write_text('1,2\n')
        with windows_directory_lock(pending), patch.object(gio.time,'sleep') as sleep:
            with self.assertRaises(OSError) as error:gio.finalize_directory(pending,self.root/'data')
        self.assertIn(error.exception.winerror,(5,32,33));self.assertEqual(sleep.call_count,5)
        self.assertTrue((pending/'samples.csv').is_file());self.assertFalse((self.root/'data').exists())

    def test_collision_never_replaces_existing_destination(self):
        for name in ('data','data.partial'):
            (self.root/name).mkdir();(self.root/name/'test.txt').write_text(name)
        with self.assertRaises(FileExistsError):gio.finalize_directory(self.root/'data.partial',self.root/'data')
        self.assertEqual((self.root/'data/test.txt').read_text(),'data')

    def test_path_escape_and_systemic_errors_are_not_retried(self):
        with self.assertRaises(ValueError):gio.contained(self.root,self.root/'..'/'escape')
        for exc in (OSError(errno.ENOSPC,'Disk full'),OSError(errno.ENAMETOOLONG,'Long path')):
            with patch.object(gio.time,'sleep') as sleep:
                with self.assertRaises(OSError):gio.retry_io(lambda:(_ for _ in ()).throw(exc),operation='rename',source='a',destination='b')
                sleep.assert_not_called()

    def test_per_object_finalize_failure_continues_next_log_and_excludes_pending(self):
        fixture=fixtures.RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        first='00000000-0000-0000-0000-000000000001';second='00000000-0000-0000-0000-000000000002'
        fixture.ids['log']=first;package=fixture.fixture()
        with closing(sqlite3.connect(package/'08_native_project/ptd_store/Data.ptd')) as db,db:
            db.execute('INSERT INTO data VALUES(3,?,?,1,?)',('://Petrel/'+second,'','FloatWellLog'))
            db.execute('INSERT INTO blob_parts SELECT 3,part,blob_data FROM blob_parts WHERE data_fk=1')
        resolve=recovery.Metadata.resolve;finalize=gio.finalize_directory
        def resolve_two(meta,tag,kind):
            node,info=resolve(meta,first,kind);info=dict(info,object_id=tag,name='First' if tag==first else 'Second');return node,info
        def blocked(pending,destination,**kw):
            if 'First' in str(destination):
                exc=PermissionError(errno.EACCES,'Injected permanent access denial');exc.winerror=5
                raise exc
            return finalize(pending,destination,**kw)
        with patch.object(recovery.Metadata,'resolve',resolve_two),patch.object(gio,'finalize_directory',blocked),redirect_stdout(io.StringIO()):report=recovery.run(package)
        self.assertEqual([r['status'] for r in report['objects']],['finalization_failed','decoded'])
        self.assertTrue(report['source_unchanged']);self.assertTrue(report['has_gaps'])
        bad=report['objects'][0];self.assertEqual(bad['object_id'],first);self.assertEqual(bad['error']['operation'],'finalize_directory')
        self.assertEqual(bad['artifacts'],[])
        accepted=geo.safe_files(package,exclude_pending=True)
        self.assertFalse(any(gio.pending_output(p) for p in accepted))
        index=delivery.publish_exports(package,{'objects':[]},self.root/'EXPORTS')
        self.assertEqual({r['object_id'] for r in index['files']},{second})
        journal=package/'07_workflows_reports/native_recovery/objects.jsonl'
        self.assertEqual(len(journal.read_text().splitlines()),2)

    def test_optional_las_failure_keeps_csv_and_reports_gap(self):
        fixture=fixtures.RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups);package=fixture.fixture()
        with patch.object(recovery,'write_las',side_effect=OSError('Optional LAS write blocked')),redirect_stdout(io.StringIO()):report=recovery.run(package)
        item=report['objects'][0]
        self.assertEqual(item['numeric_round_trip'],'exact');self.assertEqual(item['las_status'],'failed')
        self.assertTrue(report['has_gaps']);self.assertTrue(item['artifacts'])
        self.assertEqual(stage.record(package,'logs',10,package/'07_workflows_reports/native_recovery/native_recovery_report.json'),0)

    def test_resume_checks_hashes_before_reuse(self):
        fixture=fixtures.RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        package,_,item=fixture.run_fixture()
        with patch.object(recovery,'recover_object',side_effect=AssertionError('Must reuse')),redirect_stdout(io.StringIO()):report=recovery.run(package,resume=True)
        self.assertTrue(report['objects'][0]['reused_committed_output'])
        artifact=package/'native_data'/item['artifacts'][0]['path'];artifact.write_text('tampered')
        with redirect_stdout(io.StringIO()):bad=recovery.run(package,resume=True)
        self.assertTrue(bad['has_gaps']);self.assertEqual(bad['objects'][0]['artifacts'],[])

    def test_shared_integrity_failure_is_fatal_but_category_crash_is_not(self):
        package=self.root/'package';package.mkdir();receipt=package/'receipt.json'
        gio.atomic_json(receipt,{'status':'failed','reason':'Decode failed'})
        self.assertEqual(stage.record(package,'logs',1,receipt),0)
        gio.atomic_json(receipt,{'status':'failed','source_unchanged':False})
        self.assertEqual(stage.record(package,'logs',1,receipt),1)
        gio.atomic_json(receipt,{'status':'failed','error':{'errno':errno.ENOSPC}})
        self.assertEqual(stage.record(package,'logs',1,receipt),1)

    def test_pipe_drains_unicode_stderr_and_malformed_events(self):
        script=self.root/'worker.py';script.write_text("import sys\nprint('GEOVIEWER_EVENT:bad')\nprint('GEOVIEWER_EVENT:[]')\nprint('OBJECT_RESULT broken')\nprint('\\u0639\\u0631\\u0628\\u064a')\nsys.stderr.write('x'*200000+'\\n')\nprint('finished',flush=True)\n")
        log=Diagnostics(self.root/'run_LOG.txt',self.root/'events.jsonl')
        try:code=progress.run_pipeline([sys.executable,'-B',str(script)],self.root,self.root/'raw.log',15)
        finally:log.close()
        self.assertEqual(code,0);self.assertIn('finished',(self.root/'raw.log').read_text(encoding='utf-8'))
        events=[json.loads(s) for s in log.events_path.read_text().splitlines()]
        self.assertEqual(sum(e['event']=='malformed_child_event' for e in events),3)
        self.assertEqual(len({e['event_id'] for e in events}),len(events))

    def test_diagnostic_sink_failure_falls_back(self):
        log=Diagnostics(self.root/'run_LOG.txt',self.root/'events.jsonl')
        log.log.close()
        try:
            log.event('object_failed',object_id='test',reason='Retained after sink failure')
            self.assertIsNotNone(log.fallback);self.assertIn('Retained after sink failure',log.text_path.read_text())
            self.assertTrue(log.summary_path.is_file())
        finally:log.close()

    def test_last_useful_report_is_preserved(self):
        report=self.root/'report.html';report.write_text('<html><body><main><h2>Accepted exports</h2><svg>Figure</svg></main></body></html>')
        delivery.partial_report(report,{},'Stopped','Failure <details>',preserve=True)
        text=report.read_text();self.assertIn('Accepted exports',text);self.assertIn('<svg>Figure</svg>',text);self.assertIn('&lt;details&gt;',text)

    def test_timeout_stops_owned_child_and_retains_exit_evidence(self):
        log=Diagnostics(self.root/'run_LOG.txt',self.root/'events.jsonl')
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                progress.run_pipeline([sys.executable,'-c','import time;print("started",flush=True);time.sleep(30)'],self.root,self.root/'raw.log',.3)
        finally:log.close()
        events=[json.loads(s) for s in log.events_path.read_text().splitlines()]
        self.assertTrue(any(e['event']=='child_interrupted' for e in events))
        ended=next(e for e in events if e['event']=='child_exit')
        self.assertIsNotNone(ended['exit_code']);self.assertNotEqual(ended['exit_code'],0)

    def test_optional_preview_failure_preserves_surface_exports(self):
        import petrel_surface_export as surfaces
        fixture=fixtures.RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        package=fixture.fixture(kind='RegValGrid2',surface=True)
        with patch.object(surfaces,'preview',side_effect=RuntimeError('Figure cache unavailable')),redirect_stdout(io.StringIO()):report=recovery.run(package)
        item=report['objects'][0];self.assertEqual(item['numeric_round_trip'],'exact')
        self.assertEqual(item['preview_status'],'failed')
        self.assertTrue(any(a['path'].endswith('surface.xyz') for a in item['artifacts']))
        self.assertFalse(any(a['path'].endswith('.npz') for a in item['artifacts']))

    def test_log_pointer_failure_does_not_abort_completed_pipeline(self):
        import standalone_petrel_extract as launcher
        import geoviewer_metadata as metadata
        source=self.root/'source/test.pet';source.parent.mkdir();source.write_text('fixture')
        source.with_suffix('.ptd').mkdir()
        package=self.root/'package';package.mkdir();(package/'PROJECT_REPORT.html').write_text('<html><head></head><body><main>Verified exports</main></body></html>')
        output=self.root/'output'
        def dispatch(operation,args):
            return {'summary':{'export_package':str(package)},'report_path':str(package/'qc.json')}
        original=gio.atomic_text
        def blocked(path,text):
            if Path(path).name=='RUN_LOG.txt':raise PermissionError('Injected pointer-file denial')
            return original(path,text)
        with patch.object(sys,'argv',['launcher','--project-file',str(source),'--output-root',str(output)]), \
             patch.object(launcher,'preflight',return_value={'version':'test'}), \
             patch.object(metadata,'inspect_project',return_value={'layout':'fixture','saved_version':{},'findings':[]}), \
             patch.object(launcher.g,'dispatch',side_effect=dispatch), \
             patch.object(launcher.g,'verify_receipt',return_value={'status':'passed'}), \
             patch.object(gio,'atomic_text',side_effect=blocked),redirect_stdout(io.StringIO()):
            code=launcher.main()
        self.assertEqual(code,10)
        result=json.loads(next(output.glob('*_data/RUN_RESULT.json')).read_text())
        self.assertEqual(result['status'],'completed_with_gaps')
        self.assertTrue(Path(result['file_index']).is_file())
        self.assertIn('run_log_pointer',Path(result['full_report']).read_text(encoding='utf-8'))
        self.assertTrue(Path(result['diagnostic_summary']).is_file())


if __name__=='__main__':unittest.main()
