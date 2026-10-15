"""Retain an invocation failure without replacing historical spatial outcomes."""
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

import test_geoviewer_case_diagnostics as fixtures
import geoviewer_case_diagnostics as d
import geoviewer_support as support


class SpatialFailureDiagnosticsTests(unittest.TestCase):
    setUp = fixtures.DiagnosticsTests.setUp
    write = fixtures.DiagnosticsTests.write
    analyze = fixtures.DiagnosticsTests.analyze
    occurrences = fixtures.DiagnosticsTests.occurrences

    def test_actual_child_failure_reaches_summary_and_zip(self):
        old, sidecar = self.failure_fixture()
        sidecar.unlink()  # Only the fabricated fixture; the child must create its own receipt.
        before = old.read_bytes()
        self.write(self.package / '01_project_metadata/project_context.json',
                   dict(self.context, objects=[]))
        source = self.package / '08_native_project/ptd_store/Data.ptd'
        source.parent.mkdir(parents=True)
        connection = sqlite3.connect(source)
        try:
            connection.execute('CREATE TABLE data (data_pk INTEGER, droid TEXT, name TEXT, version INTEGER, blob_type TEXT)')
            connection.execute('CREATE TABLE blob_parts (data_fk INTEGER, part INTEGER, blob_data BLOB)')
            connection.commit()
        finally:
            connection.close()
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        script = Path(d.__file__).with_name('export_petrel_native_spatial_zero_gui.py').resolve()
        bootstrap = ('import importlib.util,pathlib,runpy,sys; '
                     'entry=pathlib.Path(sys.argv[1]).resolve(); '
                     'sys.argv=sys.argv[1:]; '
                     'sys.path.insert(0,str(entry.parent)); '
                     'assert pathlib.Path(importlib.util.find_spec("petrel_native_geometry_attributes").origin).resolve().parent == entry.parent; '
                     'runpy.run_path(str(entry),run_name="__main__")')
        child = subprocess.run([sys.executable, '-B', '-c', bootstrap, str(script), '--export-package', str(self.package),
            '--data-file', str(source)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PETREL_PROGRESS_EVENTS='1'))
        self.assertEqual(child.returncode, 2, child.stderr)
        self.assertIn('no such column: time_stamp', child.stderr)
        actual = list(old.parent.glob('native_spatial_decode_failure_*.json'))
        self.assertEqual(len(actual), 1)
        failure = json.loads(actual[0].read_text())
        case = self.analyze()
        self.assertEqual(len([r for r in case['category_reports']
            if r.get('evidence_kind') == 'invocation_failure']), 1)
        bundle = support.build_bundle({'run': str(self.output / 'trial_data'), 'status': 'failed'},
            destination=self.root / 'actual_child_SUPPORT.zip')
        with zipfile.ZipFile(bundle) as archive:
            self.assertIsNone(archive.testzip())
            contents = json.loads(archive.read('CONTENTS.json'))
            member = next(r['member'] for r in contents['files']
                if r['role'] == 'native_spatial_decode_failure')
            archived = json.loads(archive.read(member))
            self.assertEqual(archived['error']['exception_type'], failure['error']['exception_type'])
            self.assertIn('no such column: time_stamp', archived['error']['traceback'])
            self.assertFalse(any(n.endswith('.ptd') for n in archive.namelist()))
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)

    def failure_fixture(self):
        directory = self.package / '07_workflows_reports/native_spatial_zero_gui'
        old = directory / 'native_spatial_decode_report.json'
        self.write(old, {'status': 'completed', 'objects': [
            {'object_id': 'old-point', 'blob_type': 'Points3', 'status': 'decoded'}]})
        sidecar = directory / ('native_spatial_decode_failure_' + 'a' * 32 + '.json')
        self.write(sidecar, {'tool': 'export_petrel_native_spatial_zero_gui.py',
            'status': 'failed', 'preserved_report_path': str(old),
            'reason': 'fixture database access failed', 'outputs': [],
            'source_unchanged': None, 'source_integrity_status': 'unchecked',
            'error': {'exception_type': 'OperationalError', 'message': 'fixture database access failed',
                      'operation': 'native_spatial.run', 'traceback': 'fixture causal traceback'}})
        return old, sidecar

    def test_prior_counts_and_new_failure_are_separate(self):
        old, sidecar = self.failure_fixture()
        before = {p: p.read_bytes() for p in (old, sidecar)}
        case = self.analyze()
        self.assertEqual(sum(r['count'] for r in case['decoder_record_counts']), 1)
        failures = [r for r in case['category_reports'] if r.get('evidence_kind') == 'invocation_failure']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['summary']['source_integrity_status'], 'unchecked')
        self.assertTrue(any(sidecar.name in r['evidence'] and
            r['normalized']['exception_type'] == 'OperationalError' for r in self.occurrences()))
        receipts = json.loads((self.destination / 'DIAGNOSTIC_RECEIPTS.json').read_text())
        self.assertTrue(any(sidecar.name in key for key in receipts))
        self.assertTrue(any('do not prove' in r['reason'] for r in case['evidence_gaps']))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_lookalikes_and_other_locations_are_not_discovered(self):
        _, sidecar = self.failure_fixture()
        content = json.loads(sidecar.read_text())
        self.write(sidecar.with_name('native_spatial_decode_failure_arbitrary.json'), content)
        self.write(self.package / '08_native_project' / sidecar.name, content)
        found = list(d.diagnostic_documents(self.output))
        self.assertEqual([p for p in found if p.name.startswith('native_spatial_decode_failure_')], [sidecar])

    def test_bad_sidecar_status_does_not_create_an_object_or_success(self):
        _, sidecar = self.failure_fixture()
        self.write(sidecar, {'tool': 'export_petrel_native_spatial_zero_gui.py',
            'status': 'completed', 'objects': [{'object_id': 'invented', 'status': 'decoded'}]})
        case = self.analyze()
        self.assertEqual(sum(r['count'] for r in case['decoder_record_counts']), 1)
        self.assertFalse(any(r.get('evidence_kind') == 'invocation_failure' for r in case['category_reports']))
        self.assertTrue(any('Invalid spatial failure' in r['reason'] for r in case['evidence_gaps']))

    def test_rejected_sidecar_is_not_read(self):
        _, sidecar = self.failure_fixture()
        safe = d.safe_file
        with patch.object(d, 'safe_file', side_effect=lambda p, root: False if p == sidecar else safe(p, root)):
            case = self.analyze()
        self.assertFalse(any(r.get('evidence_kind') == 'invocation_failure' for r in case['category_reports']))
        self.assertTrue(any(sidecar.name in r.get('file', '') and 'Linked or missing' in r['reason']
                            for r in case['evidence_gaps']))


if __name__ == '__main__':
    unittest.main()
