"""FV-ISSUE-0048: full standalone runs and independent finite lane clocks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import standalone_petrel_extract as standalone
import petrel_geoscience_tools as geo
import geoviewer_lanes as lanes


class DeadlineTests(unittest.TestCase):
    def test_default_standalone_and_explicit_bounded_policy(self):
        default=standalone.argument_parser().parse_args([])
        self.assertTrue(default.run_to_completion)
        self.assertEqual(standalone.elapsed_budgets(default),(None,None,None))
        finite=standalone.argument_parser().parse_args(['--bounded','--native-budget-seconds','900'])
        self.assertFalse(finite.run_to_completion)
        self.assertEqual(standalone.elapsed_budgets(finite),(900,600,7200))
        for bad in (0,-1):
            finite.native_budget_seconds=bad
            with self.assertRaises(ValueError):standalone.elapsed_budgets(finite)

    def test_explicit_outer_cap_still_validated_and_other_operations_unchanged(self):
        with patch.object(geo,'extract_portable_project',return_value={}) as operation:
            for args in ({},{'timeout_seconds':None},{'timeout_seconds':1},{'run_to_completion':True}):
                geo.dispatch('extract_portable_project',args)
            self.assertEqual(operation.call_count,4)
        for args in ({'timeout_seconds':0},{'timeout_seconds':True},{'timeout_seconds':7201},
                     {'run_to_completion':True,'timeout_seconds':1},
                     {'native_budget_seconds':float('nan')},{'companion_budget_seconds':None},
                     {'native_budget_seconds':True},{'native_budget_seconds':86401}):
            with self.assertRaises(geo.InputError):geo.dispatch('extract_portable_project',args)
        with self.assertRaises(geo.InputError):geo.dispatch('qc_data_package',{'timeout_seconds':None})

    def test_extract_dispatch_passes_no_outer_deadline_and_keeps_finite_lane_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pet=root/'demo.pet';pet.write_bytes(b'p');pet.with_suffix('.ptd').mkdir()
            class Run:
                dry_run=False;output=root/'output';version={'petrel_version':'unknown'}
                def __init__(self,*args):pass
            with patch.object(geo,'Run',Run),patch.object(geo.progress,'run_pipeline',return_value=0) as pipeline,\
                 patch.object(geo,'extraction_package',side_effect=RuntimeError('test stop after child')):
                with self.assertRaisesRegex(RuntimeError,'test stop'):
                    geo.dispatch('extract_portable_project',{'project_file':str(pet),'timeout_seconds':None})
            self.assertIsNone(pipeline.call_args.args[3])
            self.assertIn('-NativeBudgetSeconds',pipeline.call_args.args[0])
            self.assertNotIn('-RunToCompletion',pipeline.call_args.args[0])

    def test_preparation_elapsed_does_not_consume_child_lane_budget(self):
        # A real child simulates preparation before starting a separately timed lane.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state.json';log=root/'lane.log'
            code="import sys,time;sys.path.insert(0,"+repr(str(Path(lanes.__file__).parent))+ ");import geoviewer_lanes as l;time.sleep(.3);raise SystemExit(l.run_stage([sys.executable,'-c','import time;time.sleep(.2);print(42)'],lane='native',limit=3,state_path="+repr(str(state))+",log_path="+repr(str(log))+"))"
            result=geo.progress.run_pipeline([sys.executable,'-B','-c',code],root,root/'outer.log',None)
            self.assertEqual(result,0)
            row=json.loads(state.read_text())['attempts'][0]
            self.assertEqual(row['remaining_before'],3);self.assertEqual(row['status'],'completed')
            self.assertIn('42',log.read_text())

    def test_explicit_outer_deadline_still_interrupts_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'partial.txt'
            code="from pathlib import Path;import time;Path("+repr(str(data))+").write_text('preserved');time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                geo.progress.run_pipeline([sys.executable,'-B','-c',code],root,root/'outer.log',1)
            self.assertEqual(data.read_text(),'preserved')


if __name__=='__main__':unittest.main()
