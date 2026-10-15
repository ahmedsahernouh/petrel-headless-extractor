"""Focused bounds, grammar, digest, and locator-order tests for native intake."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_native_receipt as native_receipt
from geoviewer_native_receipt import read_native_receipt


class NativeReceiptReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'native_recovery_report.json'

    def read(self, raw):
        self.path.write_bytes(raw)
        return read_native_receipt(self.path, frozenset(('object_id', 'name', 'native_unit_evidence')))

    def test_projection_keeps_positions_and_binds_whole_source(self):
        raw = (b'\xef\xbb\xbf{"objects":[{"object_id":"a","unused":{"nested":[1,2]},'
               b'"native_unit_evidence":{"unit":"m"}},null,{"object_id":"b","name":"B"}],'
               b'"source_unchanged":true}')
        data, sha, stats = self.read(raw)
        self.assertEqual(data['objects'], [{'object_id': 'a', 'native_unit_evidence': {'unit': 'm'}},
                                            None, {'object_id': 'b', 'name': 'B'}])
        self.assertEqual(sha, hashlib.sha256(raw).hexdigest())
        self.assertEqual(stats['objects'], 3)
        self.assertEqual(stats['omitted_field_counts'], {'unused': 1})

    def test_duplicate_keys_fail_closed_in_root_object_and_discarded_nested_value(self):
        for raw in (b'{"objects":[],"objects":[]}',
                    b'{"objects":[{"object_id":"a","object_id":"b"}]}',
                    b'{"objects":[{"object_id":"a","unused":{"x":1,"x":2}}]}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'Duplicate'):
                self.read(raw)

    def test_invalid_json_is_rejected_even_in_discarded_field(self):
        for raw in (b'{"objects":[],}', b'{"objects":[{"object_id":"a"},]}',
                    b'{"objects":[{"object_id":"a","unused":[1,]}]}',
                    b'{"objects":[{"object_id":"a","unused":{"x":,1}}]}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, json.JSONDecodeError)):
                self.read(raw)

    def test_explicit_input_value_and_nesting_bounds(self):
        with patch('geoviewer_native_receipt.MAX_NATIVE_RECEIPT_BYTES', 20):
            with self.assertRaisesRegex(ValueError, 'input byte bound'):
                self.read(b'{"objects":[],"extra":"long"}')
        with patch('geoviewer_native_receipt.MAX_NATIVE_VALUE_BYTES', 32):
            with self.assertRaisesRegex(ValueError, 'value exceeds'):
                self.read(b'{"objects":[{"object_id":"' + b'x' * 40 + b'"}]}')
        with patch('geoviewer_native_receipt.MAX_NATIVE_NESTING', 3):
            with self.assertRaisesRegex(ValueError, 'nesting exceeds'):
                self.read(b'{"objects":[{"object_id":"a","unused":[[[[0]]]]}]}')

    def test_production_limits_keep_other_guards(self):
        self.assertEqual(native_receipt.MAX_NATIVE_PROJECTION_BYTES, 2_147_483_648)
        self.assertEqual(native_receipt.MAX_NATIVE_RECEIPT_BYTES, 768 * 1024 * 1024)
        self.assertEqual(native_receipt.MAX_NATIVE_VALUE_BYTES, 32 * 1024 * 1024)
        self.assertEqual(native_receipt.MAX_NATIVE_NESTING, 128)
        self.assertEqual(native_receipt.MAX_NATIVE_OBJECTS, 1_000_000)
        self.assertEqual(native_receipt.MAX_INTERN_KEYS_BYTES, 64 * 1024 * 1024)

    def test_object_projection_exact_boundary_and_one_byte_over(self):
        raw = b'{"objects":[{"object_id":"a"},{"object_id":"b"}]}'
        expected = sum(len(json.dumps({'object_id': value}).encode('utf-8'))
                       for value in ('a', 'b'))
        with patch('geoviewer_native_receipt.MAX_NATIVE_PROJECTION_BYTES', expected):
            data, _, stats = self.read(raw)
            self.assertEqual([item['object_id'] for item in data['objects']], ['a', 'b'])
            self.assertEqual(stats['projected_bytes'], expected)
        with patch('geoviewer_native_receipt.MAX_NATIVE_PROJECTION_BYTES', expected - 1):
            with self.assertRaisesRegex(ValueError, 'consumed projection'):
                self.read(raw)

    def test_root_projection_aggregate_exact_boundary_and_one_byte_over(self):
        # Both root-before-objects and root-after-objects must share the same budget.
        for raw in (b'{"extra":"root","objects":[{"object_id":"a"}]}',
                    b'{"objects":[{"object_id":"a"}],"extra":"root"}'):
            expected = (len(json.dumps('root').encode('utf-8')) +
                        len(json.dumps({'object_id': 'a'}).encode('utf-8')))
            with self.subTest(raw=raw):
                with patch('geoviewer_native_receipt.MAX_NATIVE_PROJECTION_BYTES', expected):
                    _, _, stats = self.read(raw)
                    self.assertEqual(stats['projected_bytes'], expected)
                with patch('geoviewer_native_receipt.MAX_NATIVE_PROJECTION_BYTES', expected - 1):
                    with self.assertRaisesRegex(ValueError, 'consumed projection'):
                        self.read(raw)


if __name__ == '__main__':
    unittest.main()
