"""Exercise real launcher report-access calls with synthetic extraction/QC stages."""
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import standalone_petrel_extract as launcher
import geoviewer_result_access as access


class Display:
    elapsed = 0.01
    def __init__(self, *args, **kwargs): self.messages=[]
    def start(self): return self
    def message(self, value, **kwargs): self.messages.append(str(value))
    def close(self, **kwargs): pass


class ResultAccessLauncherTests(unittest.TestCase):
    def scenario(self, *, report_only=False, fail=False, gaps=False, desktop_error=False, empty=False):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'build') as tmp:
            root=Path(tmp)
            source=root/'source'/'Example.pet';source.parent.mkdir()
            source.write_bytes(b'synthetic input');source.with_suffix('.ptd').mkdir()
            output=root/'out';package=root/'fixture_package';package.mkdir()
            (package/'01_project_metadata').mkdir()
            (package/'PROJECT_REPORT.html').write_text('<html><body>Fixture</body></html>',encoding='utf-8')
            (package/'01_project_metadata/pipeline_stages.json').write_text(
                json.dumps([{'status':'partial','category':'synthetic','reason':'fixture gap'}] if gaps else []))
            wanted=not report_only and not empty
            calls=[]
            original=access.publish_result_access
            def publish(report, exports=None, **kwargs):
                calls.append((kwargs['status'], exports))
                if desktop_error: raise PermissionError('synthetic desktop refusal')
                return original(report,exports,desktop=False,**kwargs)
            def write_report(path,*args,**kwargs):
                Path(path).write_text('<html><body>Generated report</body></html>',encoding='utf-8')
            def files(destination):
                destination=Path(destination)
                if wanted:
                    destination.mkdir(exist_ok=True)
                    (destination/'values.csv').write_text('value\n1\n')
                return {'files':[{'path':'values.csv'}] if wanted else [],'findings':[]}
            class Incremental:
                def __init__(self,run,report,destination,*args,**kwargs):
                    self.destination=destination;self.current_index={'files':[]};self.findings=[];self.worker_errors=[]
                def start(self):return self
                def stop(self):pass
                def refresh(self,**kwargs):self.current_index=files(self.destination);return self.current_index
                def _html(self,index):return '<html>Index</html>'
            def dispatch(tool,args):
                if tool=='extract_portable_project':
                    if fail:raise RuntimeError('synthetic extraction failure')
                    return {'summary':{'export_package':str(package)}}
                return {'report_path':str(package/'QC.html')}
            display=Display()
            argv=['standalone','--project-file',str(source),'--output-root',str(output),'--label','Fixture']
            if report_only:argv.append('--report-only')
            with contextlib.ExitStack() as stack:
                changes=[
                    ('sys.argv',argv),
                    ('standalone_petrel_extract.preflight',lambda:{'version':'fixture'}),
                    ('standalone_petrel_extract.plan_run_paths',lambda *a,**kwargs:{'stem':'Fixture_000001','label_shortened':False}),
                    ('petrel_progress.ConsoleProgress',lambda **k:display),
                    ('petrel_progress.phase',lambda *a,**k:None),
                    ('petrel_progress.keep_stage',lambda *a:contextlib.nullcontext()),
                    ('geoviewer_metadata.inspect_project',lambda *a:{'layout':'fixture','saved_version':'unknown','findings':[]}),
                    ('geoviewer_support.register_run',lambda *a:None),
                    ('geoviewer_support.finish_run',lambda diagnostic,*a:diagnostic.close()),
                    ('geoviewer_support.environment_info',lambda *a:{}),
                    ('geoviewer_delivery.partial_report',write_report),
                    ('geoviewer_delivery.publish_exports',lambda p,i,d,**k:files(d)),
                    ('geoviewer_delivery.decorate',lambda *a:None),
                    ('geoviewer_delivery.diagnostics_section',lambda *a:None),
                    ('geoviewer_incremental.IncrementalDelivery',Incremental),
                    ('geoviewer_contract_publication.publish_preparation',lambda *a,**k:None),
                    ('petrel_project_seismic.deliver_report',lambda base,report,*a:write_report(report)),
                    ('petrel_project_seismic.convert_project',lambda *a,**k:None),
                    ('petrel_geoscience_tools.dispatch',dispatch),
                    ('petrel_geoscience_tools.verify_receipt',lambda *a:{'status':'passed'}),
                    ('geoviewer_result_access.publish_result_access',publish),
                ]
                for name,value in changes:stack.enter_context(patch(name,value))
                code=launcher.main()
            expected_status='failed' if fail else 'completed_with_gaps' if gaps else 'completed'
            self.assertEqual(code,1 if fail else 10 if gaps else 0)
            self.assertEqual([c[0] for c in calls],['in_progress',expected_status])
            self.assertIsNone(calls[0][1])
            self.assertEqual(calls[-1][1] is not None,wanted)
            result=json.loads((output/'Fixture_000001_data/RUN_RESULT.json').read_text('utf-8'))
            self.assertEqual(result['status'],expected_status)
            self.assertEqual(source.read_bytes(),b'synthetic input')
            if desktop_error:
                self.assertTrue(any('synthetic desktop refusal' in line for line in display.messages))
                self.assertIsNone(result['result_access']['entry_page'])
            else:
                entries=list((output/'Details').glob('OPEN_RESULTS_*.html'))
                self.assertEqual(len(list(output.glob('*.html'))),1)
                self.assertEqual(Path(result['full_report']).parent,output/'Details')
                self.assertEqual(len(entries),1)
                text=entries[0].read_text('utf-8')
                self.assertIn('Fixture_000001_REPORT.html',text)
                self.assertEqual('Fixture_000001_EXPORTS' in text,wanted)
                self.assertTrue(any('OPEN RESULTS:' in line for line in display.messages))

    def test_completed_with_exports(self):self.scenario()
    def test_completed_with_gaps_keeps_exit10(self):self.scenario(gaps=True)
    def test_report_only_has_no_export_shortcut(self):self.scenario(report_only=True)
    def test_zero_export_run_has_only_report_access(self):self.scenario(empty=True,gaps=True)
    def test_failure_report_keeps_retained_exports_and_failure_exit(self):self.scenario(fail=True)
    def test_access_failure_does_not_change_extraction_exit(self):self.scenario(desktop_error=True)


if __name__=='__main__':unittest.main()
