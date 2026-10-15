# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Native receipt/file binding, independent of export-manifest coverage."""
import copy
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_geoscience_tools as t


class NativeArtifactQcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-artifact-qc-')
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        self.artifact = self.package / 'native_data/log/samples.csv'
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b'md,value\n0,1.25\n')
        self.receipt = self.package / '07_workflows_reports/native_recovery/native_recovery_report.json'
        self.receipt.parent.mkdir(parents=True)
        self.manifest = self.package / '00_manifest/export_manifest.csv'
        self.manifest.parent.mkdir()
        self.document = {'objects':[{'object_id':'test-log', 'status':'decoded',
            'numeric_round_trip':'exact', 'artifacts':[{'path':'log/samples.csv',
            'bytes':self.artifact.stat().st_size, 'sha256':t.sha256(self.artifact)}]}]}
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self, raw=None):
        self.receipt.write_text(raw if raw is not None else json.dumps(self.document), encoding='utf-8')
        with self.manifest.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=['export_file','sha256'])
            writer.writeheader()
            writer.writerow({'export_file':self.receipt.relative_to(self.package).as_posix(),
                             'sha256':t.sha256(self.receipt)})

    def run_qc(self, **kwargs):
        response = t.qc_data_package(dict(export_package=str(self.package),
            output_dir=str(self.root / 'qc'), petrel_version='synthetic', **kwargs))
        if kwargs.get('dry_run'):
            return response, response['plan']['native_artifact_qc']
        return response, t.read_json(Path(response['report_path']))['native_artifact_qc']

    def assert_failed(self, code, oid='test-log'):
        response, check = self.run_qc()
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(check['status'], 'failed')
        failure = next(f for f in check['failures'] if f['code'] == code)
        self.assertEqual(failure['object_id'], oid)
        self.assertEqual(failure['scope'], 'object' if oid else 'global')
        self.assertEqual(t.read_json(Path(response['receipt_path']))['status'], 'failed')
        self.assertEqual(t.verify_receipt(response)['status'], 'failed')
        return check

    def test_matching_and_missing_metadata_use_initial_hash_cache_only(self):
        self.document['objects'][0]['status'] = 'missing_metadata'
        self.save()
        paths = {os.path.normcase(str(p.resolve())) for p in t.safe_files(self.package)}
        counts = Counter()
        original = t.sha256
        def counted(path):
            key = os.path.normcase(str(path))
            if key in paths: counts[key] += 1
            return original(path)
        with patch.object(t, 'sha256', side_effect=counted):
            response, check = self.run_qc()
        self.assertEqual(response['status'], 'passed')
        self.assertEqual(check['checked_artifacts'], 1)
        self.assertEqual(check['matched_artifacts'], 1)
        self.assertEqual(counts, Counter({p:2 for p in paths}))  # Initial and finish only.
        self.assertEqual(t.verify_receipt(response)['status'], 'passed')

    def test_same_size_post_recovery_corruption_fails(self):
        self.artifact.write_bytes(b'md,value\n0,9.25\n')
        check = self.assert_failed('sha256_mismatch')
        self.assertEqual(check['failures'][0]['path'], 'native_data/log/samples.csv')
        self.assertEqual(check['matched_artifacts'], 0)

    def test_truncated_artifact_has_size_and_digest_failures(self):
        self.artifact.write_bytes(b'md,value\n')
        check = self.assert_failed('size_mismatch')
        self.assertIn('sha256_mismatch', [f['code'] for f in check['failures']])

    def test_missing_native_artifact_fails_even_when_not_in_manifest(self):
        self.artifact.unlink()
        check = self.assert_failed('artifact_missing_from_initial_snapshot')
        self.assertEqual(check['checked_artifacts'], 0)

    def test_invalid_digest_size_and_artifact_schema(self):
        for field, value, code in [('sha256','bad','invalid_sha256'),
                                   ('bytes',True,'invalid_bytes'),
                                   ('bytes',-1,'invalid_bytes')]:
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(self.document)
                document['objects'][0]['artifacts'][0][field] = value
                self.receipt.write_text(json.dumps(document))
                initial = {os.path.normcase(str(self.receipt)): {}}
                check = t.native_artifact_qc(self.package, initial)
                self.assertEqual(check['failures'][0]['code'], code)
        self.document['objects'][0]['artifacts'] = 'not a list'
        self.save()
        self.assert_failed('invalid_artifacts')

    def test_malformed_json_global_failure_does_not_use_legacy_fallback(self):
        (self.receipt.parent / 'native_recovery.json').write_text(json.dumps(self.document))
        self.save(raw='{bad JSON')
        self.assert_failed('invalid_receipt', None)

    def test_invalid_receipt_shapes_and_identity_are_global(self):
        for document in ([], {}, {'objects':{}}, {'objects':[None]},
                         {'objects':[{'status':'decoded','numeric_round_trip':'exact'}]}):
            with self.subTest(document=document):
                self.receipt.write_text(json.dumps(document))
                check = t.native_artifact_qc(self.package, {os.path.normcase(str(self.receipt)): {}})
                self.assertEqual(check['status'], 'failed')
                self.assertEqual(check['failures'][0]['scope'], 'global')

    def test_unsafe_paths_never_open_outside_package(self):
        outside = self.root / 'outside.txt'
        outside.write_text('not package evidence')
        for name in ('../../outside.txt', '../other.bin', str(outside),
                     'C:/elsewhere/data.bin', 'C:relative.bin', 'log/samples.csv:stream'):
            with self.subTest(name=name):
                self.document['objects'][0]['artifacts'][0]['path'] = name
                self.receipt.write_text(json.dumps(self.document))
                check = t.native_artifact_qc(self.package, {os.path.normcase(str(self.receipt)): {}})
                self.assertEqual(check['failures'][0]['code'], 'unsafe_path')
                self.assertIsNone(check['failures'][0]['path'])
        self.save()
        self.assert_failed('unsafe_path')

    def test_conflicting_declarations_reject_both_object_identities(self):
        other = copy.deepcopy(self.document['objects'][0])
        other['object_id'] = 'second-log'
        other['artifacts'][0]['sha256'] = '0' * 64
        self.document['objects'].append(other)
        later = copy.deepcopy(self.document['objects'][0])
        later['object_id'] = 'later-log'
        self.document['objects'].append(later)
        self.save()
        check = self.assert_failed('conflicting_declaration', 'second-log')
        self.assertEqual({f['object_id'] for f in check['failures']}, {'test-log','second-log','later-log'})
        self.assertEqual(check['unique_artifacts'], 1)

    def test_identical_duplicate_counts_one_checked_file(self):
        self.document['objects'][0]['artifacts'] *= 2
        self.save()
        response, check = self.run_qc()
        self.assertEqual(response['status'], 'passed')
        self.assertEqual(check['declared_artifacts'], 2)
        self.assertEqual(check['checked_artifacts'], 1)
        self.assertEqual(check['duplicate_declarations'], 1)

    def test_shared_identical_declaration_reports_corruption_for_every_owner(self):
        other = copy.deepcopy(self.document['objects'][0])
        other['object_id'] = 'second-log'
        self.document['objects'].append(other)
        self.save()
        self.artifact.write_bytes(b'md,value\n0,9.25\n')
        counts = Counter()
        original = t.sha256
        def counted(path):
            if Path(path) == self.artifact: counts['artifact'] += 1
            return original(path)
        with patch.object(t, 'sha256', side_effect=counted):
            response, check = self.run_qc()
        self.assertEqual(response['status'], 'failed')
        self.assertEqual({f['object_id'] for f in check['failures']
                          if f['code'] == 'sha256_mismatch'}, {'test-log', 'second-log'})
        self.assertEqual(check['checked_artifacts'], 1)
        self.assertEqual(check['matched_artifacts'], 0)
        self.assertEqual(check['duplicate_declarations'], 1)
        self.assertEqual(counts['artifact'], 2)

    def test_shared_identical_declaration_reports_missing_file_for_every_owner(self):
        other = copy.deepcopy(self.document['objects'][0])
        other['object_id'] = 'second-log'
        self.document['objects'].append(other)
        self.save()
        self.artifact.unlink()
        check = self.assert_failed('artifact_missing_from_initial_snapshot')
        self.assertEqual({f['object_id'] for f in check['failures']}, {'test-log', 'second-log'})
        self.assertEqual(check['checked_artifacts'], 0)
        self.assertEqual(check['duplicate_declarations'], 1)

    def test_legacy_receipt_fallback(self):
        self.receipt.unlink()
        self.receipt = self.receipt.parent / 'native_recovery.json'
        self.save()
        response, check = self.run_qc()
        self.assertEqual(response['status'], 'passed')
        self.assertTrue(check['receipt'].endswith('/native_recovery.json'))

    def test_unaccepted_objects_are_not_claimed_as_validated(self):
        self.document['objects'][0]['numeric_round_trip'] = 'failed'
        self.save()
        self.artifact.write_bytes(b'damaged')
        response, check = self.run_qc()
        self.assertEqual(response['status'], 'passed')
        self.assertEqual(check['accepted_objects'], 0)
        self.assertEqual(check['checked_artifacts'], 0)

    def test_dry_run_reports_corruption_without_creating_output(self):
        self.artifact.write_bytes(b'md,value\n0,9.25\n')
        before = {str(p):p.read_bytes() for p in t.safe_files(self.package)}
        response, check = self.run_qc(dry_run=True)
        self.assertEqual(response['status'], 'dry_run')
        self.assertEqual(check['status'], 'failed')
        self.assertFalse((self.root / 'qc').exists())
        self.assertEqual(before, {str(p):p.read_bytes() for p in t.safe_files(self.package)})


if __name__ == '__main__': unittest.main(verbosity=2)
