"""Fault injection for independent budgets and interrupted-stage preservation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import geoviewer_lanes as lanes
import petrel_project_seismic as seismic
import standalone_petrel_extract as standalone


class LaneTests(unittest.TestCase):
    def test_native_child_can_create_its_owned_report_directory(self):
        from test_petrel_native_recovery import RecoveryTests
        fixture=RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        package=fixture.fixture(kind='RegValGrid2',surface=True)
        command=[sys.executable,str(Path(__file__).parent/'petrel_native_recovery.py'),'--export-package',str(package)]
        code=lanes.run_stage(command,lane='native',limit=30,state_path=package/'01_project_metadata/stage_budgets.json',
                             log_path=package/'07_workflows_reports/stage_logs/native_recovery.log')
        self.assertEqual(code,0)
        result=json.loads((package/'07_workflows_reports/native_recovery/native_recovery_report.json').read_text())
        self.assertEqual(result['objects'][0]['numeric_round_trip'],'exact')

    def test_budget_validation_and_independence(self):
        for invalid in (False,0,-1,float('nan'),float('inf'),'1'):
            with self.assertRaises(ValueError):lanes.seconds(invalid)
        state={'attempts':[{'lane':'companions','elapsed_seconds':900},{'lane':'native','elapsed_seconds':12}]}
        self.assertEqual(lanes.remaining(state,'native',30),18)
        self.assertEqual(lanes.remaining(state,'seismic',30),30)

    def test_standalone_maps_only_explicit_completion_mode_to_unlimited(self):
        finite=SimpleNamespace(native_budget_seconds=7200,companion_budget_seconds=600,
                               seismic_budget_seconds=7200,run_to_completion=False)
        self.assertEqual(standalone.elapsed_budgets(finite),(7200,600,7200))
        finite.run_to_completion=True
        self.assertEqual(standalone.elapsed_budgets(finite),(None,None,None))

    def test_actual_timeout_retains_first_file_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'completed.csv'
            command=[sys.executable,'-c',"from pathlib import Path;import time;Path("+repr(str(data))+").write_text('complete');print('retained',flush=True);time.sleep(30)"]
            code=lanes.run_stage(command,lane='native',limit=.5,state_path=root/'budgets.json',log_path=root/'child.log')
            self.assertEqual(code,124);self.assertEqual(data.read_text(),'complete')
            row=json.loads((root/'budgets.json').read_text())['attempts'][0]
            self.assertEqual(row['status'],'interrupted_budget');self.assertIn('retained',(root/'child.log').read_text())

    def test_exhausted_lane_does_not_start_child_other_lane_can(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'budgets.json';p.write_text(json.dumps({'attempts':[{'lane':'native','elapsed_seconds':10}]}))
            with patch.object(lanes.progress,'run_pipeline',return_value=0) as child:
                self.assertEqual(lanes.run_stage(['unused'],lane='native',limit=1,state_path=p,log_path=root/'n.log'),124)
                child.assert_not_called()
                self.assertEqual(lanes.run_stage(['unused'],lane='companions',limit=1,state_path=p,log_path=root/'c.log'),0)
                child.assert_called_once()

    def test_run_to_completion_lane_passes_unlimited_wait_and_records_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'budgets.json'
            with patch.object(lanes.progress,'run_pipeline',return_value=0) as child:
                self.assertEqual(lanes.run_stage(['synthetic'],lane='native',limit=None,
                    state_path=state,log_path=root/'child.log'),0)
            self.assertIsNone(child.call_args.args[3])
            row=json.loads(state.read_text())['attempts'][0]
            self.assertIsNone(row['limit_seconds']);self.assertIsNone(row['remaining_before'])

    def test_companion_quarantine_restores_manifest_and_preserves_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'00_manifest').mkdir();manifest=root/'00_manifest/export_manifest.csv';manifest.write_bytes(b'original')
            native=root/'native_data';native.mkdir();(native/'valid.csv').write_bytes(b'native')
            snapshot=lanes.companion_snapshot(root)
            (root/'09_source_companions').mkdir();(root/'09_source_companions/broken.xlsx').write_bytes(b'partial')
            manifest.write_bytes(b'incomplete-additions')
            dest=Path(lanes.quarantine_companions(root,snapshot))
            self.assertEqual(manifest.read_bytes(),b'original');self.assertFalse((root/'09_source_companions').exists())
            retained=json.loads((dest/'QUARANTINE.json').read_text())['retained_paths']
            source_tree=next(x['retained'] for x in retained if x['original']=='09_source_companions')
            self.assertEqual((root/source_tree/'broken.xlsx').read_bytes(),b'partial')
            self.assertLessEqual(len(str(root/source_tree)),len(str(root/'09_source_companions')))
            self.assertEqual((native/'valid.csv').read_bytes(),b'native');self.assertTrue(dest.name.endswith('.partial'))

    def test_companion_quarantine_rejects_existing_deliverables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'09_source_companions').mkdir();(root/'09_source_companions/old.csv').write_text('old')
            with self.assertRaisesRegex(ValueError,'fresh output'):lanes.companion_snapshot(root)

    def test_seismic_timeout_preserves_completed_and_skips_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[dict(id=str(n),source=str(Path(tmp)/f'{n}.zgy'),name=str(n),format='.zgy',association='project',status='inventoried') for n in range(3)]
            result={'status':'passed','receipt_path':str(Path(tmp)/'first/RUN_RESULT.json')}
            times=iter([0,0,0,0,0,0,0,0,0,2,2,2,2,2,2,2,2,2,2,2])
            def tick():return next(times,2)
            calls=[]
            def worker(*a,**kw):
                calls.append(a)
                if len(calls)==1:return result
                raise subprocess.TimeoutExpired('child',1)
            with patch('time.monotonic',side_effect=tick),patch.object(lanes,'seismic_job',side_effect=worker):
                inventory=seismic.convert_project({'objects':rows},Path(tmp)/'out',True,False,budget_seconds=1)
            self.assertEqual(rows[0]['status'],'converted');self.assertEqual(rows[1]['status'],'interrupted_budget')
            self.assertEqual(rows[2]['status'],'not_attempted_budget')
            self.assertEqual(sum(inventory['counts'].values()),3)

    def test_seismic_run_to_completion_keeps_child_isolation_without_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            row=dict(id='1',source=str(Path(tmp)/'one.zgy'),name='one',format='.zgy',association='project',status='inventoried')
            result={'status':'passed','receipt_path':str(Path(tmp)/'one/RUN_RESULT.json')}
            with patch.object(lanes,'seismic_job',return_value=result) as child:
                seismic.convert_project({'objects':[row]},Path(tmp)/'out',True,False,
                                        budget_seconds=None,run_to_completion=True)
            self.assertIsNone(child.call_args.kwargs['timeout'])
            self.assertEqual(row['status'],'converted')

    def test_native_integrity_list_does_not_scan_companions(self):
        import petrel_geoscience_tools as geo
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pet=root/'a.pet';pet.write_text('native');store=root/'a.ptd';store.mkdir();(store/'Data.ptd').write_text('data')
            companion=root/'huge.zip';companion.write_text('unrelated')
            with patch.object(geo,'ROOT',root),patch.object(geo.Run,'__init__',return_value=None) as init,patch.object(geo.Run,'dry_run',True,create=True),patch.object(geo.Run,'plan',return_value={}):
                geo.extract_portable_project({'project_file':str(pet),'output_dir':str(root.parent/'new')})
            self.assertEqual(set(init.call_args.args[2]),{pet,store/'Data.ptd'})

    def test_dispatch_accepts_null_extraction_timeout_with_independent_stage_budgets(self):
        import petrel_geoscience_tools as geo
        with patch.object(geo,'extract_portable_project',return_value={'status':'planned'}) as operation:
            result=geo.dispatch('extract_portable_project',{'run_to_completion':True,'timeout_seconds':None})
        self.assertEqual(result['status'],'planned');operation.assert_called_once()
        with patch.object(geo,'extract_portable_project',return_value={'status':'planned'}):
            self.assertEqual(geo.dispatch('extract_portable_project',{'timeout_seconds':None})['status'],'planned')
        with self.assertRaisesRegex(geo.InputError,'integer between'):
            geo.dispatch('qc_data_package',{'timeout_seconds':None})

if __name__=='__main__':unittest.main()
