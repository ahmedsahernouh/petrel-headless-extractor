"""Complete assessment member binding and fail-closed resource tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_assessment_shards as shards
import geoviewer_contract_publication as publication
from test_geoviewer_contract import ContractTests


class AssessmentShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'validation_source.json'
        self.report = dict(schema='geoviewer.contract-validation/2', generation='g',
                           manifest_sha256='a' * 64, profile='source', profile_revision='r',
                           counts=dict(entities=2, artifacts=1, source_ready=1, source_blocked=1,
                                       qualified_for_planned_mapping=0, receiving_qualified=0,
                                       receiving_imports_executed=0))
        self.report.update({field: [] for field in shards.LIST_FIELDS})
        self.report['assessments'] = [dict(entity_key='e1', source_state='source_prerequisites_satisfied',
                                           receiving_state='source_only'),
                                      dict(entity_key='e2', source_state='source_blocked',
                                           receiving_state='source_only')]
        self.report['artifact_availability'] = [dict(artifact_key='a1', available=True)]

    def write(self):
        with patch.object(shards, 'MAX_TABLE_BYTES', 125):
            result = shards.write_sharded_assessment(self.path, self.report)
        envelope = json.loads(self.path.read_text())
        return result, envelope

    def verify(self, envelope):
        with patch.object(shards, 'MAX_TABLE_BYTES', 125):
            return shards.verify_sharded_assessment(self.path, envelope,
                                                    entities={'e1', 'e2'}, artifacts={'a1'})

    def test_complete_ordered_round_trip(self):
        result, envelope = self.write()
        self.assertEqual(result['format'], shards.ENCODING)
        self.assertEqual(envelope['lists']['assessments']['rows'], 2)
        self.assertEqual(len(envelope['lists']['assessments']['members']), 2)
        self.assertEqual(self.verify(envelope)['status'], 'verified')

    def test_production_resource_limits(self):
        self.assertEqual(shards.MAX_TOTAL_BYTES, 4 * 1024**3)
        self.assertEqual(shards.MAX_TOTAL_ROWS, 2_000_000)
        self.assertEqual(shards.MAX_MEMBERS, 24)
        self.assertEqual(shards.MAX_TABLE_BYTES, 256 * 1024**2)
        self.assertEqual(shards.MAX_ROW_BYTES, 2 * 1024**2)
        self.assertEqual(shards.MAX_LOGICAL_BYTES, 32 * 1024**2)

    def test_aggregate_byte_exact_fit_and_one_byte_overflow(self):
        expected_bytes = sum(len(shards._canonical(['item', item]))
                             for field in shards.LIST_FIELDS for item in self.report[field])
        with patch.object(shards, 'MAX_TOTAL_BYTES', expected_bytes):
            _, envelope = self.write()
            verified = self.verify(envelope)
        self.assertEqual(verified['bytes'], expected_bytes)
        self.path = self.path.with_name('validation_overflow.json')
        with patch.object(shards, 'MAX_TOTAL_BYTES', expected_bytes - 1):
            with self.assertRaisesRegex(ValueError,
                    f'aggregate byte bound exceeded: {expected_bytes} > {expected_bytes - 1}'):
                self.write()
        self.assertFalse(self.path.exists())

    def test_readback_declared_and_streamed_aggregate_byte_bound(self):
        _, envelope = self.write()
        bound = envelope['aggregate']['bytes'] - 1
        with patch.object(shards, 'MAX_TOTAL_BYTES', bound):
            with self.assertRaisesRegex(ValueError, 'declared aggregate bound'):
                self.verify(envelope)
            # A forged in-bound total must also fail against actual member bytes.
            altered = copy.deepcopy(envelope)
            altered['aggregate']['bytes'] = bound
            with self.assertRaisesRegex(ValueError, 'member byte/row bound'):
                self.verify(altered)

    def test_scaled_row_member_and_physical_guards(self):
        with patch.object(shards, 'MAX_TOTAL_ROWS', 2):
            with self.assertRaisesRegex(ValueError, 'aggregate row bound exceeded: 3 > 2'):
                self.write()
        self.assertFalse(self.path.exists())
        self.path = self.path.with_name('validation_members.json')
        with patch.object(shards, 'MAX_MEMBERS', 2):
            with self.assertRaisesRegex(ValueError, 'member count bound'):
                self.write()
        self.assertFalse(self.path.exists())
        self.path = self.path.with_name('validation_physical.json')
        with patch.object(shards, 'MAX_ROW_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'physical row byte bound'):
                self.write()
        self.assertFalse(self.path.exists())

    def test_missing_tampered_reordered_duplicate_and_traversing_member(self):
        _, envelope = self.write()
        part = envelope['lists']['assessments']['members'][0]
        member_path = self.path.parent / part['path']
        original = member_path.read_bytes()
        member_path.write_bytes(original + b'{}\n')
        with self.assertRaises(ValueError):
            self.verify(envelope)
        member_path.write_bytes(original)
        altered = copy.deepcopy(envelope)
        altered['lists']['assessments']['members'][0]['path'] = 'missing.jsonl'
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.verify(altered)
        altered = copy.deepcopy(envelope)
        members = altered['lists']['assessments']['members']
        members[0], members[1] = members[1], members[0]
        with self.assertRaises(ValueError):
            self.verify(altered)
        altered = copy.deepcopy(envelope)
        altered['lists']['assessments']['members'][1]['path'] = part['path']
        with self.assertRaises(ValueError):
            self.verify(altered)
        altered = copy.deepcopy(envelope)
        altered['lists']['assessments']['members'][0]['path'] = '../outside.jsonl'
        with self.assertRaises(ValueError):
            self.verify(altered)
        altered = copy.deepcopy(envelope)
        altered['lists']['assessments']['members'][0]['index'] = True
        with self.assertRaises(ValueError):
            self.verify(altered)

    def test_count_coverage_and_aggregate_bounds(self):
        _, envelope = self.write()
        altered = copy.deepcopy(envelope)
        altered['base']['counts']['source_ready'] = 2
        altered['counts']['source_ready'] = 2
        with self.assertRaisesRegex(ValueError, 'counts'):
            self.verify(altered)
        altered = copy.deepcopy(envelope)
        altered['aggregate']['rows'] += 1
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            self.verify(altered)
        with patch.object(shards, 'MAX_TOTAL_BYTES', 1), self.assertRaisesRegex(ValueError, 'descriptor|aggregate'):
            self.verify(envelope)

    def test_nonfinite_number_fails_closed(self):
        self.report['diagnostics'] = [{'value': float('nan')}]
        with self.assertRaises(ValueError):
            shards.write_sharded_assessment(self.path, self.report)
        self.assertFalse(self.path.exists())

    def test_bounded_encoder_byte_parity_and_early_rejection(self):
        sample = {'z': ['é', '\u0000', '\n', '😀', 1.25, True, None],
                  'a': {'quote': '"' * 9000, 'slash': '\\' * 9000}}
        expected = shards._canonical(sample)
        self.assertEqual(shards._canonical_bounded(sample, len(expected)), expected)
        with self.assertRaisesRegex(ValueError, 'byte bound'):
            shards._canonical_bounded(sample, len(expected) - 1)
        self.report['diagnostics'] = [{'huge': 'x' * 200}]
        with patch.object(shards, 'MAX_LOGICAL_BYTES', 100), self.assertRaisesRegex(ValueError, 'byte bound'):
            shards.write_sharded_assessment(self.path, self.report)
        self.assertFalse(self.path.exists())

    def test_oversized_logical_item_round_trip_and_fragment_guard(self):
        self.report['assessments'][0]['metadata_resolution'] = {'large': 'x' * (2 * 1024 * 1024)}
        result = shards.write_sharded_assessment(self.path, self.report)
        self.assertEqual(result['format'], shards.ENCODING)
        envelope = json.loads(self.path.read_bytes())
        self.assertGreater(envelope['lists']['assessments']['rows'],
                           envelope['lists']['assessments']['items'])
        self.assertEqual(shards.verify_sharded_assessment(self.path, envelope,
                         entities={'e1', 'e2'}, artifacts={'a1'})['status'], 'verified')
        altered = copy.deepcopy(envelope)
        altered['lists']['assessments']['items'] += 1
        with self.assertRaises(ValueError):
            shards.verify_sharded_assessment(self.path, altered,
                                             entities={'e1', 'e2'}, artifacts={'a1'})

        part = envelope['lists']['assessments']['members'][0]
        member_path = self.path.parent / part['path']
        original = member_path.read_bytes()
        frames = original.splitlines(keepends=True)
        first = json.loads(frames[0])
        self.assertEqual(first[0], 'fragment')
        first[2] = 2
        frames[0] = shards._canonical(first)
        member_path.write_bytes(b''.join(frames))
        with self.assertRaisesRegex(ValueError, 'fragment order'):
            shards.verify_sharded_assessment(self.path, envelope,
                                             entities={'e1', 'e2'}, artifacts={'a1'})
        member_path.write_bytes(original)
        frames = original.splitlines(keepends=True)
        frames[0], frames[1] = frames[1], frames[0]
        member_path.write_bytes(b''.join(frames))
        with self.assertRaisesRegex(ValueError, 'fragment order'):
            shards.verify_sharded_assessment(self.path, envelope,
                                             entities={'e1', 'e2'}, artifacts={'a1'})


class AssessmentPublicationTests(unittest.TestCase):
    setUp = ContractTests.setUp
    add_row = ContractTests.add_row

    def test_small_generation_uses_bound_sharded_assessments_when_flat_cap_is_exceeded(self):
        with patch.object(publication, 'MAX_TABLE_BYTES', 1000):
            result = publication.publish_preparation(self.package, self.exports, self.run, {},
                                                     file_index=self.index, conversion_enabled=True)
        self.assertTrue(all(v['format'] == shards.ENCODING for v in result['assessments'].values()))
        self.assertEqual(publication.verify_publication_binding(result, self.run)['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
