# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Resume cannot substitute older implementation, source or extraction scope."""
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import petrel_native_recovery as recovery
import test_petrel_native_recovery as fixtures


class NativeResumeBindingTests(unittest.TestCase):
    def setUp(self):
        self.writer = fixtures.RecoveryTests('runTest'); self.writer.setUp()
        self.addCleanup(self.writer.temp.cleanup)

    def run_native(self, package, **kwargs):
        with redirect_stdout(io.StringIO()):
            return recovery.run(package, **kwargs)

    def snapshot(self, package):
        return {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in package.rglob('*') if p.is_file()}

    def assert_rejected_without_rewriting_previous(self, package, before, result):
        self.assertEqual(result['status'], 'unsupported_layout')
        self.assertEqual(result['objects'], [])
        self.assertIn('Resume rejected', result['reason'])
        after = self.snapshot(package)
        self.assertEqual({p: after[p] for p in before}, before)
        added = set(after) - set(before)
        self.assertEqual(len(added), 1)
        added_path = Path(next(iter(added)))
        self.assertTrue(added_path.name.startswith('resume_rejected_'), added)
        saved = json.loads((package/added_path).read_text(encoding='utf-8'))
        self.assertEqual(saved['reason'], result['reason'])

    def test_same_source_engine_options_reuses_verified_commit(self):
        package = self.writer.fixture()
        first = self.run_native(package)
        binding = first['resume_binding']
        self.assertEqual(binding['options'], dict(grids_only=False, preview_only=False))
        self.assertEqual(binding['source_sha256'], first['source_hashes_before'])
        self.assertIn('petrel_native_log_evidence.py', binding['decoder']['module_sha256'])
        commit = next((package/'native_data').rglob('_commit.json'))
        self.assertEqual(json.loads(commit.read_text())['resume_binding'], binding)
        with patch.object(recovery, 'recover_object', side_effect=AssertionError('No re-decoding')):
            second = self.run_native(package, resume=True)
        self.assertTrue(second['objects'][0]['reused_committed_output'])
        self.assertEqual(second['resume_binding'], binding)
        self.assertTrue(second['source_unchanged'])
        self.assertIsNone(recovery._resume_binding.get())

    def test_changed_decoder_with_same_product_version_is_rejected(self):
        package = self.writer.fixture()
        first = self.run_native(package)
        before = self.snapshot(package)
        decoder = copy.deepcopy(first.get('resume_binding', {}).get('decoder', {}))
        decoder['module_sha256'] = {'petrel_native_recovery.py': '0'*64}
        with patch.object(recovery, 'decoder_identity', return_value=decoder, create=True):
            result = self.run_native(package, resume=True)
        self.assert_rejected_without_rewriting_previous(package, before, result)

    def test_preview_checkpoint_is_not_reused_as_full_dataset(self):
        package = self.writer.fixture(kind='RegValGrid2', surface=True)
        self.run_native(package, preview_only=True)
        before = self.snapshot(package)
        result = self.run_native(package, resume=True)
        self.assert_rejected_without_rewriting_previous(package, before, result)
        self.assertFalse(list(package.rglob('surface.xyz')))

    def test_changed_object_selection_is_not_reused(self):
        package = self.writer.fixture()
        self.run_native(package)
        before = self.snapshot(package)
        result = self.run_native(package, resume=True, grids_only=True)
        self.assert_rejected_without_rewriting_previous(package, before, result)

    def test_unbound_historical_report_rejected_preserving_its_evidence(self):
        package = self.writer.fixture()
        self.run_native(package)
        path = package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        old = json.loads(path.read_text()); old.pop('resume_binding', None)
        path.write_text(json.dumps(old), encoding='utf-8')
        before = self.snapshot(package)
        result = self.run_native(package, resume=True)
        self.assert_rejected_without_rewriting_previous(package, before, result)

    def test_changed_source_rejection_preserves_previous_receipts(self):
        package = self.writer.fixture()
        self.run_native(package)
        pet = package/'08_native_project/project_file/test.pet'
        pet.write_bytes(pet.read_bytes()+b'changed synthetic source')
        before = self.snapshot(package)
        result = self.run_native(package, resume=True)
        self.assert_rejected_without_rewriting_previous(package, before, result)
        self.assertIn('source identity changed', result['reason'])

    def test_relocation_does_not_change_decoder_or_source_binding(self):
        package = self.writer.fixture()
        first = self.run_native(package)
        moved = self.writer.root/'relocated-package'
        self.assertEqual(package.parent.resolve(), moved.parent.resolve())
        package.rename(moved)
        with patch.object(recovery, 'recover_object', side_effect=AssertionError('No re-decoding')):
            result = self.run_native(moved, resume=True)
        self.assertEqual(result['resume_binding'], first['resume_binding'])
        self.assertTrue(result['objects'][0]['reused_committed_output'])

    def test_valid_binding_does_not_waive_artifact_hash_checks(self):
        package = self.writer.fixture()
        first = self.run_native(package)
        artifact = next(a for a in first['objects'][0]['artifacts'] if a['path'].endswith('samples.csv'))
        path = package/'native_data'/artifact['path']
        original = path.read_bytes()
        changed = bytes([original[0] ^ 1]) + original[1:]
        path.write_bytes(changed)
        result = self.run_native(package, resume=True)
        record = result['objects'][0]
        self.assertEqual(record['artifacts'], [])
        self.assertNotIn('reused_committed_output', record)
        self.assertIn('artifact integrity mismatch', record['reason'])
        self.assertEqual(path.read_bytes(), changed)

    def test_commit_cannot_claim_a_different_source_or_mode(self):
        package = self.writer.fixture()
        self.run_native(package)
        commit = next((package/'native_data').rglob('_commit.json'))
        receipt = json.loads(commit.read_text())
        receipt['resume_binding'] = {'schema': 'different-source'}
        commit.write_text(json.dumps(receipt), encoding='utf-8')
        original = commit.read_bytes()
        result = self.run_native(package, resume=True)
        self.assertEqual(result['objects'][0]['artifacts'], [])
        self.assertNotIn('reused_committed_output', result['objects'][0])
        self.assertIn('Committed object', result['objects'][0]['reason'])
        self.assertEqual(commit.read_bytes(), original)
        import geoviewer_delivery
        index = geoviewer_delivery.publish_exports(package, {'objects': []}, self.writer.root/'exports')
        self.assertEqual(index['files'], [])
        self.assertIsNone(recovery._resume_binding.get())


if __name__ == '__main__':
    unittest.main()
