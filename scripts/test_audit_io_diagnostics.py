# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bound metadata probes and preserve causal report-stage errors.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import report_petrel_project_audit as audit
from geoviewer_diagnostics import Diagnostics
from geoviewer_diagnostics import PREFIX

class AuditIOTests(unittest.TestCase):
    def test_image_scan_avoids_non_image_file_attribute_probes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for i in range(250): (root/f'{i}.bin').write_bytes(b'raw')
            image=root/'image.png'; image.write_bytes(b'not a real PNG')
            checked=[]; original=Path.is_file
            def check(path):
                checked.append(path)
                return original(path)
            with patch.object(Path,'is_file',check): result=audit.gather_media(root)
            self.assertEqual(result['image_count'],1)
            self.assertEqual(result['images'][0]['path'],'image.png')
            self.assertFalse(any(p.suffix=='.bin' for p in checked))

    def test_inventory_size_and_timestamp_share_one_explicit_stat(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); leaf=root/'dataset.csv';leaf.write_bytes(b'x,y\n1,2\n')
            checked=[];original=Path.stat
            def stat(path,*args,**kwargs):
                if path==leaf: checked.append(path)
                return original(path,*args,**kwargs)
            with patch.object(Path,'stat',stat): result=audit.gather_file_inventory(root)
            self.assertEqual(result['files'][0]['size_bytes'],8)
            self.assertLessEqual(len(checked),2) # is_file plus one shared metadata snapshot

    def test_stage_failure_is_logged_and_propagated(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); logs=Diagnostics(root/'RUN_LOG.txt',root/'RUN_EVENTS.jsonl');measurements=[]
            try:
                self.assertEqual(audit.measured_audit_step('small',lambda:42,measurements),42)
                def fail():raise OSError('report fixture read failed')
                with self.assertRaisesRegex(OSError,'fixture read failed'):
                    audit.measured_audit_step('image_read',fail,measurements)
            finally:logs.close()
            rows=[json.loads(x) for x in (root/'RUN_EVENTS.jsonl').read_text().splitlines()]
            self.assertEqual([x['event'] for x in rows],['audit_step_started','audit_step_finished']*2)
            self.assertEqual(rows[-1]['exception_type'],'OSError')
            self.assertEqual(rows[-1]['severity'],'error')
            self.assertEqual(measurements[-1]['status'],'failed')
            self.assertGreaterEqual(measurements[-1]['elapsed_seconds'],0)
            self.assertIn('report fixture read failed',(root/'RUN_LOG.txt').read_text())

    def test_child_report_failure_stream_reaches_parent_with_stage_and_cause(self):
        # Real child boundary: report workers have no in-process Diagnostics owner.
        with tempfile.TemporaryDirectory() as d:
            missing=Path(d)/'absent.json'
            code=(f'import sys;sys.path.insert(0,{str(Path(audit.__file__).parent)!r});'
                  'from pathlib import Path;'
                  'from report_petrel_project_audit import measured_audit_step;'
                  f'measured_audit_step("native_inventory",lambda:Path({str(missing)!r}).read_text(),[])')
            env=dict(os.environ, PETREL_PROGRESS_EVENTS='1')
            child=subprocess.run([sys.executable,'-B','-c',code],env=env,capture_output=True,text=True,timeout=20)
            self.assertNotEqual(child.returncode,0)
            rows=[json.loads(line[len(PREFIX):]) for line in child.stdout.splitlines() if line.startswith(PREFIX)]
            self.assertEqual([r['event'] for r in rows],['audit_step_started','audit_step_finished'])
            self.assertEqual(rows[0]['audit_step'],'native_inventory')
            self.assertEqual(rows[1]['step'],'native_inventory')
            self.assertEqual(rows[1]['exception_type'],'FileNotFoundError')
            self.assertEqual(rows[1]['status'],'failed')
            self.assertIn('absent.json',rows[1]['error'])
            self.assertIn('FileNotFoundError',child.stderr)
            self.assertEqual(rows[0]['pid'],rows[1]['pid'])
            self.assertGreater(rows[1]['child_sequence'],rows[0]['child_sequence'])

if __name__=='__main__':unittest.main()
