# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Shared profile snapshots must not accept stale source metadata or cached files."""
import json
import copy
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import geoviewer_contract_publication as publication
import test_geoviewer_contract as fixtures
import validate_geoviewer_contract as validator
from geoviewer_contract import POINTER


class ProfileSnapshotTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    add_row = fixtures.ContractTests.add_row
    publish = fixtures.ContractTests.publish

    def prepare(self):
        return publication.publish_preparation(self.package, self.exports, self.run,
                                              {}, file_index=self.index)

    def test_late_receipt_mutation_blocks_publication(self):
        original = publication.publish_human_report
        receipt = self.package/'01_project_metadata/project_context.json'
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            stat = receipt.stat()
            raw = receipt.read_bytes()
            receipt.write_bytes(raw.replace(b'Same', b'Tame', 1))
            os.utime(receipt, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return result
        with patch.object(publication, 'publish_human_report', changed):
            with self.assertRaisesRegex(ValueError, 'changed.*snapshot|snapshot.*changed'):
                self.prepare()
        self.assertTrue((self.exports/POINTER).exists())
        self.assertFalse((self.run/'RUN_RESULT.json').exists())

    def test_publication_parses_tables_once_for_profiles_and_once_for_human_view(self):
        original = Path.open
        passes = {}
        class CountReads:
            def __init__(self, wrapped, path):
                self.wrapped, self.path, self.started = wrapped, path, False
            def __enter__(self):
                self.wrapped.__enter__()
                return self
            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)
            def readline(self, *args):
                if not self.started:
                    passes[self.path.name] = passes.get(self.path.name, 0) + 1
                    self.started = True
                return self.wrapped.readline(*args)
            def __getattr__(self, name):
                return getattr(self.wrapped, name)
        def opened(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            return CountReads(result, path) if path.suffix == '.jsonl' and args == ('rb',) else result
        with patch.object(Path, 'open', opened):
            self.prepare()
        self.assertEqual(set(passes), {'entities.jsonl', 'artifacts.jsonl', 'relationships.jsonl',
                                      'metadata_evidence.jsonl', 'gaps.jsonl'})
        self.assertEqual(set(passes.values()), {2}, passes)

    def test_shared_assessments_equal_independent_profiles_without_bulk_hashes(self):
        self.publish()
        with patch.object(validator, '_file_digest', side_effect=AssertionError('Unexpected bulk hash')):
            expected = {p: validator.validate_contract(self.exports/POINTER, p) for p in validator.PROFILES}
            with validator.verified_profile_assessments(self.exports/POINTER) as actual:
                self.assertEqual(actual, expected)
        self.assertEqual(actual, expected)

    def test_profiles_preserve_shared_metadata_and_isolate_artifact_state(self):
        self.publish()
        original = validator.assess_catalogue
        seen = []
        source_snapshot = None
        def assessed(**kwargs):
            nonlocal source_snapshot
            # Large immutable source tables are intentionally shared. Guard the
            # real assessor's read-only contract without requiring deep copies.
            source_tables = {k: v for k, v in kwargs['tables'].items() if k != 'artifacts'}
            if source_snapshot is None:
                source_snapshot = copy.deepcopy(source_tables)
            self.assertEqual(source_tables, source_snapshot)
            artifact = kwargs['tables']['artifacts'][0]
            self.assertNotIn('injected_availability', artifact)
            seen.append(kwargs['profile'])
            result = original(**kwargs)
            self.assertEqual(source_tables, source_snapshot)
            # Availability/retention/hash annotations belong to this profile's
            # artifact dictionary and must not leak into the next profile.
            artifact['injected_availability'] = 'profile-local test annotation'
            return result
        with patch.object(validator, 'assess_catalogue', assessed):
            with validator.verified_profile_assessments(self.exports/POINTER):
                pass
        self.assertEqual(seen, list(validator.PROFILES))

    def test_availability_is_fresh_for_each_profile(self):
        self.publish()
        original = validator.assess_catalogue
        def assessed(**kwargs):
            report = original(**kwargs)
            if kwargs['profile'] == 'source':
                (self.exports/'grids/surface.xyz').unlink()
            return report
        with patch.object(validator, 'assess_catalogue', assessed):
            with validator.verified_profile_assessments(self.exports/POINTER) as reports:
                counts = [sum(a['available'] for a in reports[p]['artifact_availability']) for p in validator.PROFILES]
        self.assertEqual(counts, [4, 3, 3])

    def bind_small_metadata(self, *, kind=None):
        pointer = self.publish()
        manifest_path = self.exports/pointer['manifest']
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        artifacts = [json.loads(line) for line in (manifest_path.parent/'artifacts.jsonl').read_text(encoding='utf-8').splitlines()]
        artifact = next(a for a in artifacts if a['relative_path'] == 'grids/metadata.json')
        manifest['metadata_bindings'] = [{**{k: artifact[k] for k in ('artifact_key', 'path_base', 'relative_path')},
                                         'kind': kind,
                                         'sha256': hashlib.sha256(b'{}').hexdigest()}]
        fixtures.write(manifest_path, manifest)
        pointer['manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        fixtures.write(self.exports/POINTER, pointer)

    def test_bound_small_sidecar_is_rechecked_per_profile_and_final_snapshot(self):
        self.bind_small_metadata()
        original = validator.assess_catalogue
        def assessed(**kwargs):
            report = original(**kwargs)
            if kwargs['profile'] == 'source':
                (self.exports/'grids/metadata.json').write_bytes(b'[]')
            return report
        with patch.object(validator, 'assess_catalogue', assessed), self.assertRaisesRegex(ValueError, 'snapshot changed'):
            with validator.verified_profile_assessments(self.exports/POINTER) as reports:
                stale = ['SEISMIC_METADATA_STALE' in {d['code'] for d in reports[p]['diagnostics']} for p in validator.PROFILES]
        self.assertEqual(stale, [False, True, True])

    def test_late_sidecar_mutation_after_all_profiles_is_rejected(self):
        self.bind_small_metadata()
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            with validator.verified_profile_assessments(self.exports/POINTER):
                (self.exports/'grids/metadata.json').write_bytes(b'[]')

    def test_existing_stale_or_missing_sidecar_remains_explicit_diagnostic(self):
        self.bind_small_metadata()
        path = self.exports/'grids/metadata.json'
        path.write_bytes(b'[]')
        for state in ('stale', 'missing'):
            if state == 'missing':
                path.unlink()
            with self.subTest(state=state):
                with validator.verified_profile_assessments(self.exports/POINTER) as reports:
                    self.assertTrue(all('SEISMIC_METADATA_STALE' in {d['code'] for d in r['diagnostics']} for r in reports.values()))

    def test_head_and_sidecar_size_bounds_remain_separate(self):
        self.bind_small_metadata(kind='native_head_rows')
        with patch.object(validator, 'MAX_HEAD_BYTES', 2), patch.object(validator, 'MAX_SIDECAR_BYTES', 1):
            with validator.verified_profile_assessments(self.exports/POINTER):
                pass
        with patch.object(validator, 'MAX_HEAD_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'metadata.*byte bound'):
                with validator.verified_profile_assessments(self.exports/POINTER):
                    self.fail('Oversized metadata copy should prevent shared publication')

    def test_final_check_rejects_changed_core_bindings_without_reparse(self):
        pointer = self.publish()
        manifest_path = self.exports/pointer['manifest']
        targets = [self.exports/POINTER, manifest_path, self.exports/'FILE_INDEX.json',
                   manifest_path.parent/'entities.jsonl',
                   self.package/'01_project_metadata/project_context.json']
        for path in targets:
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                try:
                    with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                        with validator.verified_profile_assessments(self.exports/POINTER):
                            path.write_bytes(raw+b' ')
                finally:
                    path.write_bytes(raw)

    def test_missing_receipt_cannot_appear_during_snapshot(self):
        pointer = self.publish()
        manifest = json.loads((self.exports/pointer['manifest']).read_text(encoding='utf-8'))
        missing = next(r for r in manifest['receipts'].values() if not r['sha256'])
        path = self.package/missing['path']
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            with validator.verified_profile_assessments(self.exports/POINTER):
                fixtures.write(path, {'objects': []})
        with self.assertRaisesRegex(ValueError, 'receipt appeared'):
            with validator.verified_profile_assessments(self.exports/POINTER):
                self.fail('An unbound receipt was accepted')

    def test_metadata_digest_has_byte_bound_without_restricting_payload_digest(self):
        path = self.exports/'large.bin'
        path.write_bytes(b'123456789')
        with patch.object(validator, 'MAX_TABLE_BYTES', 8):
            with self.assertRaisesRegex(ValueError, 'metadata.*byte bound'):
                validator._metadata_digest(path)
            self.assertEqual(validator._file_digest(path), hashlib.sha256(b'123456789').hexdigest())
            path.write_bytes(b'12345678')
            self.assertEqual(validator._metadata_digest(path), hashlib.sha256(b'12345678').hexdigest())

    def test_hash_opt_in_remains_fresh_between_profiles(self):
        self.publish()
        original = validator.assess_catalogue
        def assessed(**kwargs):
            report = original(**kwargs)
            if kwargs['profile'] == 'source':
                (self.exports/'grids/surface.xyz').write_bytes(b'4 5 6\n')
            return report
        with patch.object(validator, 'assess_catalogue', assessed):
            with validator.verified_profile_assessments(self.exports/POINTER, hash_artifacts=True) as reports:
                stale = ['HASH_MISMATCH' in {d['code'] for d in reports[p]['diagnostics']} for p in validator.PROFILES]
        self.assertEqual(stale, [False, True, True])

    def test_invalid_profile_list_fails_before_any_file_reads(self):
        for profiles in ((), ('source', 'source'), ('unsupported',)):
            with self.subTest(profiles=profiles), patch.object(validator, '_load_contract', side_effect=AssertionError('Read occurred')):
                with self.assertRaisesRegex(ValueError, 'unique supported profiles'):
                    with validator.verified_profile_assessments(self.exports/POINTER, profiles):
                        pass


if __name__ == '__main__':
    unittest.main()
