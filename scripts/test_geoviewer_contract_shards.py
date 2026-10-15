"""Lossless ordered shard bounds and tamper checks; old tables stay single files."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_contract as contract
import validate_geoviewer_contract as validator
import geoviewer_contract_publication as publication
from geoviewer_contract_publication import publish_preparation
from test_geoviewer_contract import ContractTests


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [dict(entity_key='e', field='f', locator=f'#/objects/{i}', raw_value='x' * 20)
                     for i in range(19)]
        self.bounds = [patch.object(contract, 'MAX_TABLE_BYTES', 500),
                       patch.object(contract, 'MAX_TABLE_ROWS', 7),
                       patch.object(validator, 'MAX_TABLE_BYTES', 500),
                       patch.object(validator, 'MAX_TABLE_ROWS', 7)]
        for bound in self.bounds:
            bound.start()
            self.addCleanup(bound.stop)

    def write(self):
        return contract._write_sharded_metadata(self.root, self.rows)

    def test_ordered_round_trip_and_legacy_rejection(self):
        spec = self.write()
        self.assertGreater(len(spec['shards']), 1)
        self.assertEqual(spec['sha256'], hashlib.sha256(b''.join(map(contract.canonical, self.rows))).hexdigest())
        loaded, bindings, count = validator._table_rows(self.root, 'metadata_evidence', spec,
                                                         contract.SHARDED_SCHEMA)
        self.assertEqual(loaded, self.rows)
        self.assertEqual(count, len(self.rows))
        self.assertEqual(len(bindings), len(spec['shards']))
        with self.assertRaisesRegex(ValueError, 'Unsupported sharded'):
            validator._table_rows(self.root, 'metadata_evidence', spec, contract.SCHEMA)

    def test_sharding_triggered_by_byte_or_row_bound(self):
        with patch.object(contract, 'MAX_TABLE_ROWS', 100):
            self.assertTrue(contract._metadata_requires_shards(self.rows))
        with patch.object(contract, 'MAX_TABLE_BYTES', 100_000):
            self.assertTrue(contract._metadata_requires_shards(self.rows))
        with patch.object(contract, 'MAX_TABLE_BYTES', 100_000), \
             patch.object(contract, 'MAX_TABLE_ROWS', 100):
            self.assertFalse(contract._metadata_requires_shards(self.rows))

    def test_tamper_missing_reorder_duplicate_and_traversal_fail_closed(self):
        spec = self.write()
        first = self.root / spec['shards'][0]['path']
        original = first.read_bytes()
        first.write_bytes(original.replace(b'objects/0', b'objects/z'))
        with self.assertRaisesRegex(ValueError, 'hash'):
            validator._table_rows(self.root, 'metadata_evidence', spec, contract.SHARDED_SCHEMA)
        first.write_bytes(original)
        altered = copy.deepcopy(spec)
        altered['shards'][0]['path'] = 'missing.jsonl'
        with self.assertRaises((ValueError, FileNotFoundError)):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)
        altered = copy.deepcopy(spec)
        altered['shards'][0], altered['shards'][1] = altered['shards'][1], altered['shards'][0]
        with self.assertRaisesRegex(ValueError, 'bound'):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)
        altered = copy.deepcopy(spec)
        altered['shards'][1]['path'] = altered['shards'][0]['path']
        with self.assertRaisesRegex(ValueError, 'bound|Duplicate'):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)
        altered = copy.deepcopy(spec)
        altered['shards'][0]['path'] = '../outside.jsonl'
        with self.assertRaises(ValueError):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)
        altered = copy.deepcopy(spec)
        altered['rows'] += 1
        with self.assertRaisesRegex(ValueError, 'aggregate identity'):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)
        altered = copy.deepcopy(spec)
        altered['shards'][0]['index'] = True
        with self.assertRaisesRegex(ValueError, 'bound'):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)
        altered = copy.deepcopy(spec)
        altered['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'aggregate identity'):
            validator._table_rows(self.root, 'metadata_evidence', altered, contract.SHARDED_SCHEMA)


class ShardedPublicationTests(unittest.TestCase):
    setUp = ContractTests.setUp
    add_row = ContractTests.add_row
    publish = ContractTests.publish

    def test_selective_profile_copy_matches_full_copy_and_isolates_mutation(self):
        pointer = self.publish()
        path = self.exports / contract.POINTER
        catalogue, _ = validator._load_contract(path)
        expected = {profile: validator.assess_catalogue(**copy.deepcopy(catalogue), profile=profile)
                    for profile in ('source', 'opendtect', 'paleoscan')}
        with validator.verified_profile_assessments(path) as reports:
            self.assertEqual(reports, expected)
            self.assertNotIn('available', catalogue['tables']['artifacts'][0])
            reports['source']['artifact_availability'][0]['current_retention']['status'] = 'changed_in_test'
            self.assertNotEqual(reports['opendtect']['artifact_availability'][0]['current_retention']['status'],
                                'changed_in_test')
    def test_full_small_sharded_publication_and_summary(self):
        normalized = contract.build_catalogue(self.package, self.exports, self.index)
        seed = normalized['tables']['metadata_evidence'][0]
        normalized['tables']['metadata_evidence'] = [dict(seed, locator=f'#/objects/{i}') for i in range(13)]
        with patch.object(contract, 'build_catalogue', return_value=normalized), \
             patch.object(contract, 'MAX_TABLE_ROWS', 5), \
             patch.object(validator, 'MAX_TABLE_ROWS', 5):
            result = publish_preparation(self.package, self.exports, self.run, {},
                                         file_index=self.index, conversion_enabled=True)
            pointer = json.loads((self.exports / contract.POINTER).read_text())
            self.assertEqual(pointer['schema'], contract.SHARDED_SCHEMA)
            manifest = json.loads((self.exports / pointer['manifest']).read_text())
            self.assertEqual(manifest['tables']['metadata_evidence']['rows'], 13)
            self.assertEqual(len(manifest['tables']['metadata_evidence']['shards']), 3)
            self.assertEqual(validator.validate_contract(self.exports / contract.POINTER)['generation'],
                             pointer['generation'])
            html = Path(result['human_report']['path']).read_text()
            self.assertIn('bounded summary', html)
            self.assertIn('metadata_evidence.0001.jsonl', html)
            self.assertIn('validation_source.json', html)

    def test_sharded_contract_and_assessments_publish_together(self):
        normalized = contract.build_catalogue(self.package, self.exports, self.index)
        seed = normalized['tables']['metadata_evidence'][0]
        normalized['tables']['metadata_evidence'] = [dict(seed, locator=f'#/objects/{i}') for i in range(13)]
        with patch.object(contract, 'build_catalogue', return_value=normalized), \
             patch.object(contract, 'MAX_TABLE_ROWS', 5), \
             patch.object(validator, 'MAX_TABLE_ROWS', 5), \
             patch.object(publication, 'MAX_TABLE_BYTES', 1000):
            result = publish_preparation(self.package, self.exports, self.run, {},
                                         file_index=self.index, conversion_enabled=True)
        self.assertTrue(all(item['format'] == publication.SHARDED_ASSESSMENT_ENCODING
                            for item in result['assessments'].values()))
        self.assertEqual(publication.verify_publication_binding(result, self.run)['status'], 'verified')
        html = Path(result['human_report']['path']).read_text()
        self.assertIn('complete assessment members', html)

    def test_existing_generation_can_publish_in_new_run_without_rewriting_tables(self):
        pointer = self.publish()
        root = (self.exports / pointer['manifest']).parent
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
        new_run = Path(self.temp.name) / 'new_run'
        new_run.mkdir()
        result = publication.publish_current_preparation(self.exports, new_run,
                                                         expected_pointer=pointer)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result['generation'], pointer['generation'])

    def test_assessment_writer_exact_bytes_and_bound(self):
        value = {'é': [1, True, {'x': None}]}
        path = self.run / 'bounded.json'
        result = publication._atomic_bounded_assessment(path, value)
        expected = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, default=str) + '\n').encode()
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(result['sha256'], hashlib.sha256(expected).hexdigest())
        oversized = self.run / 'oversized.json'
        with patch.object(publication, 'MAX_TABLE_BYTES', 100):
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                publication._atomic_bounded_assessment(oversized, {'text': 'x' * 200})
        self.assertFalse(oversized.exists())


if __name__ == '__main__':
    unittest.main()
