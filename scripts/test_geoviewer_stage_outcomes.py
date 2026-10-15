# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Focused outcome tests for native terminal partial receipts."""
import errno
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_stage as stage


class StageOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '01_project_metadata').mkdir()
        (self.root / '01_project_metadata/pipeline_stages.json').write_text('[]', encoding='utf-8')
        self.report = self.root / 'native_recovery_report.json'

    def tearDown(self):
        self.temp.cleanup()

    def write_report(self, value):
        self.report.write_text(json.dumps(value), encoding='utf-8')

    def read_row(self):
        return json.loads((self.root / '01_project_metadata/pipeline_stages.json').read_text())[-1]

    def partial(self):
        return {
            'status': 'partial', 'has_gaps': True, 'source_unchanged': True,
            'objects': [
                {'object_id': 'one', 'blob_type': 'well', 'status': 'decoded'},
                {'object_id': 'two', 'blob_type': 'well', 'status': 'missing_metadata'},
            ],
            'object_type_counts': {'well': 2},
            'object_status_counts': {'decoded': 1, 'missing_metadata': 1},
        }

    def test_nonzero_terminal_partial_is_recorded_as_completed_with_gaps(self):
        self.write_report(self.partial())
        self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 0)
        row = self.read_row()
        self.assertEqual(row['status'], 'partial')
        self.assertIn('completed with object-level gaps', row['reason'])
        self.assertEqual(row['exit_code'], 10)

    def test_truncated_object_list_or_count_disagreement_never_claims_completion(self):
        report = self.partial()
        report['objects'] = report['objects'][:1]
        self.write_report(report)
        self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 0)
        self.assertNotIn('completed with object-level gaps', self.read_row()['reason'])

    def test_type_identity_source_and_exit_code_are_required_for_completion(self):
        cases = []
        wrong_type = self.partial(); wrong_type['object_type_counts'] = {'grid': 2}; cases.append(wrong_type)
        duplicate_id = self.partial(); duplicate_id['objects'][1]['object_id'] = 'one'; cases.append(duplicate_id)
        missing_source = self.partial(); del missing_source['source_unchanged']; cases.append(missing_source)
        false_source = self.partial(); false_source['source_unchanged'] = False; cases.append(false_source)
        for report in cases:
            with self.subTest(report=report):
                self.write_report(report)
                stage.record(self.root, 'logs', 10, self.report)
                self.assertNotIn('completed with object-level gaps', self.read_row()['reason'])
        self.write_report(self.partial())
        stage.record(self.root, 'logs', 1, self.report)
        self.assertNotIn('completed with object-level gaps', self.read_row()['reason'])

    def test_fatal_partial_and_output_probe_failure_never_claim_completion(self):
        fatal = self.partial(); fatal['error'] = {'errno': errno.ENOSPC}
        self.write_report(fatal)
        self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 1)
        self.assertNotIn('completed with object-level gaps', self.read_row()['reason'])
        self.write_report(self.partial())
        original = stage.output_probe
        try:
            stage.output_probe = lambda package: (_ for _ in ()).throw(OSError('probe blocked'))
            self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 1)
        finally:
            stage.output_probe = original
        self.assertNotIn('completed with object-level gaps', self.read_row()['reason'])

    def test_missing_malformed_running_and_incomplete_receipts_do_not_complete(self):
        cases = [
            ('missing', None),
            ('malformed', '{'),
            ('running', {'status': 'running', 'has_gaps': True}),
            ('incomplete', {'status': 'partial', 'has_gaps': True, 'objects': [{'status': 'decoded'}]}),
        ]
        for name, value in cases:
            with self.subTest(name=name):
                if self.report.exists(): self.report.unlink()
                if value is not None:
                    if isinstance(value, str): self.report.write_text(value, encoding='utf-8')
                    else: self.write_report(value)
                stage.record(self.root, 'logs', 10, self.report)
                self.assertNotIn('completed with object-level gaps', self.read_row()['reason'])

    def test_explicit_reason_is_retained(self):
        report = self.partial(); report['reason'] = 'Native decoder completed selected objects'
        self.write_report(report)
        stage.record(self.root, 'logs', 10, self.report)
        self.assertEqual(self.read_row()['reason'], report['reason'])

    def test_legacy_semantic_gap_with_success_exit_stays_partial(self):
        self.write_report({'status': 'partial', 'has_gaps': False,
                           'object_status_counts': {'missing_metadata': 1328}})
        self.assertEqual(stage.record(self.root, 'logs_surfaces', 0, self.report), 0)
        self.assertEqual(self.read_row()['status'], 'partial')
        self.assertIn('1328 object(s): missing_metadata', self.read_row()['reason'])

    def test_spatial_subdecode_and_coverage_gaps_survive_good_objects(self):
        for field, value, reason in (
            ('model_well_head_decode', {'status': 'failed_closed', 'error': 'bad metadata'}, 'bad metadata'),
            ('coverage', {'status': 'partial', 'reason': '5 WellTrace3 payloads inventoried'}, 'WellTrace3'),
        ):
            with self.subTest(field=field):
                self.write_report({'status': 'completed', 'object_status_counts': {'decoded': 4}, field: value})
                self.assertEqual(stage.record(self.root, 'spatial', 0, self.report), 0)
                row = self.read_row()
                self.assertEqual(row['status'], 'partial')
                self.assertFalse(row['fatal'])
                self.assertIn(reason, row['reason'])
                self.assertEqual(row['object_status_counts'], {'decoded': 4})

    def test_absent_wells_and_empty_supported_objects_are_not_failures(self):
        for status in ('not_applicable', 'not_available'):
            self.write_report({'status': 'completed', 'object_status_counts': {'empty_supported_object': 1},
                               'model_well_head_decode': {'status': status},
                               'coverage': {'status': 'no_known_unsupported_trajectory_payloads'}})
            self.assertEqual(stage.record(self.root, 'spatial', 0, self.report), 0)
            self.assertEqual(self.read_row()['status'], 'completed')
            self.assertEqual(self.read_row()['gap_reasons'], [])

    def test_source_mutation_and_disk_failure_remain_fatal(self):
        for error_report in (
            dict(self.partial(), source_unchanged=False),
            dict(self.partial(), error={'errno': errno.ENOSPC}),
        ):
            with self.subTest(error_report=error_report):
                self.write_report(error_report)
                self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 1)
                self.assertEqual(self.read_row()['status'], 'failed')
                self.assertNotIn('will continue', self.read_row()['reason'])

    def test_malformed_object_status_cannot_crash_classification(self):
        report = self.partial()
        report['objects'][0]['status'] = ['decoded']
        self.write_report(report)
        self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 0)
        self.assertIn('could not be verified', self.read_row()['reason'])

    def test_partial_head_recovery_remains_a_visible_nonfatal_gap(self):
        self.write_report({'object_status_counts':{'decoded':2},
                           'model_well_head_decode':{'status':'decoded_with_failed_closed_subjects',
                                                     'well_head_rows':1,'failed_closed_subjects':[{'error':'bad vector'}]}})
        self.assertEqual(stage.record(self.root,'spatial',0,self.report),0)
        self.assertEqual(self.read_row()['status'],'partial')
        self.assertIn('decoded_with_failed_closed_subjects',self.read_row()['reason'])

    def test_terminal_failed_objects_are_gaps_not_unfinished_categories(self):
        for status in ('unsupported_layout', 'write_failed', 'finalization_failed'):
            with self.subTest(status=status):
                report = self.partial()
                report['objects'][1]['status'] = status
                report['object_status_counts'] = {'decoded': 1, status: 1}
                self.write_report(report)
                self.assertEqual(stage.record(self.root, 'logs', 10, self.report), 0)
                self.assertIn('completed with object-level gaps', self.read_row()['reason'])
                self.assertEqual(self.read_row()['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
