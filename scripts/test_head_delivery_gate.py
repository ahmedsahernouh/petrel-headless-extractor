# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Well-head delivery must be independent of point-attribute validation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from geoviewer_delivery import publish_exports


class HeadDeliveryGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        self.heads = self.package / '02_wells/well_headers/native_well_heads.csv'
        self.heads.parent.mkdir(parents=True)
        self.heads.write_text('well_name,x\nA,4\n', encoding='utf-8')
        self.attributes = self.package / '05_spatial/points/native_point_attributes.csv'
        self.receipt = self.package / '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        self.receipt.parent.mkdir(parents=True)

    def publish(self, attribute_rows, *, valid_hash=True, checkpoint=False, head_rows=1):
        receipt = {
            'tool': 'export_petrel_native_spatial_zero_gui.py',
            'completed_at_utc': '2026-10-04T00:00:00Z',
            'objects': [],
            'native_well_head_rows': head_rows,
            'model_well_head_decode': {'status': 'decoded'},
            'point_attribute_value_rows': attribute_rows,
        }
        if attribute_rows:
            self.attributes.parent.mkdir(parents=True, exist_ok=True)
            self.attributes.write_text('object_id,vector_index,value\npoint-1,0,2\n', encoding='utf-8')
            receipt['point_attribute_csv_sha256'] = hashlib.sha256(self.attributes.read_bytes()).hexdigest()
            if not valid_hash:
                receipt['point_attribute_csv_sha256'] = '0' * 64
            receipt['objects'].append({
                'object_id': 'point-1', 'blob_type': 'Points3',
                'status': 'empty_supported_object',
                'attributes_status': 'raw_typed_values_decoded',
                'attribute_value_count': 1,
            })
        self.receipt.write_text(json.dumps(receipt), encoding='utf-8')
        return publish_exports(self.package, {'objects': []}, self.root / 'exports',
                               spatial_checkpoint=checkpoint)

    def test_final_heads_survive_zero_point_attributes(self):
        result = self.publish(0)
        self.assertEqual([row['category'] for row in result['files']], ['well_headers'])

    def test_final_heads_survive_valid_point_attributes(self):
        result = self.publish(1)
        self.assertEqual({row['category'] for row in result['files']},
                         {'well_headers', 'point_attributes'})

    def test_final_heads_survive_rejected_point_attributes(self):
        result = self.publish(1, valid_hash=False)
        self.assertEqual([row['category'] for row in result['files']], ['well_headers'])
        self.assertTrue(any('point attribute CSV SHA-256' in finding
                            for finding in result['findings']))

    def test_checkpoint_rejects_unproven_heads_independently(self):
        result = self.publish(1, checkpoint=True, head_rows=2)
        self.assertEqual([row['category'] for row in result['files']], ['point_attributes'])
        self.assertTrue(any('native_well_heads.csv' in finding
                            for finding in result['findings']))

    def test_final_and_checkpoint_heads_record_fresh_byte_identity(self):
        original = self.heads.read_bytes()
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint):
                row = next(r for r in self.publish(0, checkpoint=checkpoint)['files']
                           if r['category'] == 'well_headers')
                delivered = self.root / 'exports' / row['path']
                self.assertEqual(row['sha256'], hashlib.sha256(original).hexdigest())
                expected_integrity = 'delivery_sha256_freshly_computed'
                if checkpoint:
                    expected_integrity += '; completed_decoder_receipt_and_table_row_count; package_QC_not_established'
                self.assertEqual(row['integrity'], expected_integrity)
                expected_qc = 'native_model_cross_checks'
                if checkpoint:
                    expected_qc += '; recorded_decoder_checks_only; package_QC_not_established'
                self.assertEqual(row['numerical_qc'], expected_qc)
                self.assertEqual(row['units'], 'unknown')
                self.assertEqual(delivered.read_bytes(), original)
                self.assertEqual(self.heads.read_bytes(), original)

    def test_head_collision_preserves_other_file_and_hashes_original_fallback(self):
        target = self.root / 'exports/well_headers/All_well_headers_well_headers_native_well_heads.csv'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'unrelated file')
        row = self.publish(0)['files'][0]
        self.assertEqual(target.read_bytes(), b'unrelated file')
        self.assertEqual(row['storage'], 'original_path_filesystem_cannot_hardlink')
        self.assertTrue((self.root / 'exports' / row['path']).samefile(self.heads))
        self.assertEqual(row['sha256'], hashlib.sha256(self.heads.read_bytes()).hexdigest())

    def test_failed_head_hash_does_not_publish_an_index(self):
        with patch('geoviewer_delivery.sha256', side_effect=OSError('head read failed')):
            with self.assertRaisesRegex(OSError, 'head read failed'):
                self.publish(0)
        self.assertFalse((self.root / 'exports/FILE_INDEX.json').exists())

    def test_linked_head_path_is_rejected_before_hashing(self):
        original_is_symlink = Path.is_symlink
        with patch.object(Path, 'is_symlink',
                          new=lambda p: p == self.heads or original_is_symlink(p)):
            with patch('geoviewer_delivery.sha256') as digest:
                with self.assertRaisesRegex(ValueError, 'Linked output'):
                    self.publish(0)
                digest.assert_not_called()

    def test_failed_closed_heads_stay_withheld_without_hashing(self):
        self.publish(0)
        receipt = json.loads(self.receipt.read_text())
        receipt['model_well_head_decode']['status'] = 'failed_closed'
        self.receipt.write_text(json.dumps(receipt), encoding='utf-8')
        with patch('geoviewer_delivery.sha256') as digest:
            result = publish_exports(self.package, {'objects': []}, self.root / 'failed_closed')
            self.assertEqual(result['files'], [])
            digest.assert_not_called()


if __name__ == '__main__':
    unittest.main()
