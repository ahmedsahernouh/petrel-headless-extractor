# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Native LAS parsing, scoped rejection and existing diagnostic timings."""
import copy
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_geoscience_tools as tools
from geoviewer_operation_timing import operation_timing

LAS = '''~Version
VERS. 2.0 :
WRAP. NO :
~Well
STRT.M 100 :
STOP.M 102 :
STEP.M 1 :
NULL. -999.25 :
~Curve
DEPT.M : Depth
GR. : Unknown unit
~ASCII
100 1
101 -999.25
102 3
'''


class NativeLasQcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root/'package'
        self.receipt = self.package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        self.receipt.parent.mkdir(parents=True)
        self.objects = []

    def artifact(self, name, text=LAS, oid='well-log', status='missing_metadata'):
        path = self.package/'native_data'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.objects.append(dict(object_id=oid, status=status, numeric_round_trip='exact',
            artifacts=[dict(path=name, bytes=path.stat().st_size, sha256=tools.sha256(path))]))
        return path

    def run_qc(self, dry_run=False):
        self.receipt.write_text(json.dumps(dict(objects=self.objects)))
        manifest = self.package/'00_manifest/export_manifest.csv'
        manifest.parent.mkdir(exist_ok=True)
        with manifest.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['export_file', 'sha256'])
            writer.writeheader()
            writer.writerow(dict(export_file=self.receipt.relative_to(self.package).as_posix(),
                                 sha256=tools.sha256(self.receipt)))
        response = tools.qc_data_package(dict(export_package=str(self.package),
            output_dir=str(self.root/'qc'), dry_run=dry_run))
        result = response['plan'] if dry_run else tools.read_json(Path(response['report_path']))
        return response, result

    def test_native_las_outside_manifest_and_companion_uppercase_are_counted(self):
        self.artifact('log/curve.las')
        companion = self.package/'02_wells/well_logs_las/other.LAS'
        companion.parent.mkdir(parents=True); companion.write_text(LAS)
        response, result = self.run_qc()
        self.assertEqual(response['status'], 'passed')
        self.assertEqual(response['summary']['las_files'], 2)
        self.assertEqual(response['summary']['native_las_files'], 1)
        self.assertEqual(response['summary']['companion_las_files'], 1)
        native = next(r for r in result['las'] if r['origin'] == 'native')
        self.assertEqual(native['object_ids'], ['well-log'])
        self.assertEqual(native['curves'][1]['unit'], '')  # Unknown unit does not block parsing.
        self.assertEqual(native['curves'][1]['undefined'], 1)
        self.assertEqual(native['depth']['rows'], 3)
        self.assertEqual(tools.verify_receipt(response)['status'], 'passed')

    def test_unregistered_unaccepted_and_pending_files_are_not_parsed(self):
        self.artifact('accepted/curve.las')
        self.artifact('failed/curve.las', 'broken', oid='failed', status='failed_closed')
        path = self.package/'native_data/unregistered.las'; path.write_text('broken')
        # Prefix is the actual pending-output convention, not an arbitrary folder.
        from geoviewer_io import pending_output
        pending = self.package/'native_data/untrusted.partial'
        self.assertTrue(pending_output(pending))
        pending.mkdir(); (pending/'curve.las').write_text('broken')
        response, result = self.run_qc()
        self.assertEqual(response['status'], 'passed')
        self.assertEqual([r['object_ids'] for r in result['las']], [['well-log']])

    def test_shared_native_parse_failure_rejects_all_owners_preserving_good(self):
        self.artifact('bad/curve.las', 'broken', oid='bad')
        shared = copy.deepcopy(self.objects[0]); shared['object_id'] = 'shared'
        self.objects.append(shared)
        self.artifact('good/curve.las', oid='good')
        response, result = self.run_qc()
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['summary']['las_parse_failures'], 1)
        failures = result['native_artifact_qc']['failures']
        self.assertEqual({r['object_id'] for r in failures}, {'bad', 'shared'})
        self.assertTrue(all(r['code'] == 'las_parse_failed' and r['scope'] == 'object' for r in failures))
        self.assertEqual(result['native_artifact_qc']['matched_artifacts'], 2)
        self.assertTrue((self.package/'native_data/bad/curve.las').exists())
        self.assertEqual(next(r['status'] for r in result['las'] if r['object_ids'] == ['good']), 'parsed')

    def test_tampered_or_conflicting_native_las_is_not_parsed(self):
        path = self.artifact('bad/curve.las')
        path.write_text(LAS.replace('102 3', '102 9'))
        response, result = self.run_qc()
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(result['las'], [])
        self.assertEqual(result['native_artifact_qc']['failures'][0]['code'], 'sha256_mismatch')

    def test_invalid_companion_las_fails_format_qc(self):
        path = self.package/'02_wells/well_logs_las/bad.las'
        path.parent.mkdir(parents=True); path.write_text('broken')
        response, result = self.run_qc()
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['summary']['companion_las_files'], 1)
        self.assertEqual(result['native_artifact_qc']['status'], 'passed')

    def test_dry_run_counts_native_files_without_parsing_or_creating_output(self):
        self.artifact('log/curve.las', 'broken')
        response, result = self.run_qc(dry_run=True)
        self.assertEqual(result['las_files'], 1)
        self.assertEqual(response['status'], 'dry_run')
        self.assertFalse((self.root/'qc').exists())

    def test_compact_operation_spans_record_failure_and_preserve_exception(self):
        events = []
        with patch('geoviewer_diagnostics.event', side_effect=lambda kind, **fields: events.append(dict(event=kind, **fields))):
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with operation_timing('fixture', file_count=7):
                    raise RuntimeError('fixture')
        self.assertEqual([e['event'] for e in events], ['operation_started', 'operation_finished'])
        self.assertEqual(events[1]['status'], 'failed')
        self.assertEqual(events[1]['error_type'], 'RuntimeError')
        self.assertGreaterEqual(events[1]['elapsed_seconds'], 0)
        self.assertEqual(events[1]['file_count'], 7)
        self.assertEqual(events[1]['severity'], 'error')
        self.assertEqual(events[0]['operation_id'], events[1]['operation_id'])

    def test_qc_spans_identify_integrity_and_parsing_operations(self):
        self.artifact('log/curve.las')
        events = []
        with patch('geoviewer_diagnostics.event', side_effect=lambda kind, **fields: events.append(dict(event=kind, **fields))):
            self.run_qc()
        completed = {e['operation'] for e in events if e['event'] == 'operation_finished'}
        self.assertTrue({'package_file_inventory', 'input_hash_snapshot', 'manifest_path_validation',
                         'native_artifact_binding', 'las_format_qc', 'input_integrity_recheck',
                         'output_artifact_hashes', 'operation_receipt_finalization', 'package_qc'} <= completed)


if __name__ == '__main__': unittest.main(verbosity=2)
