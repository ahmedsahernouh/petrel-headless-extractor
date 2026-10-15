# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Interrupted finalization must retain usable spatial files without final QC claims."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from geoviewer_incremental import IncrementalDelivery
from test_geoviewer_incremental import IncrementalDeliveryTests, write_json


class PartialSpatialTests(unittest.TestCase):
    def test_legacy_trajectory_is_published_with_unknown_angles_preserved(self):
        self.evidence['objects'][-1]['blob_type']='WellTrace3'
        write_json(self.receipt,self.evidence)
        table=self.package/'02_wells/trajectories/native_well_trajectory_records.csv'
        table.write_text('object_id,record_index,md,inclination_raw,angle_units_status\ntraj,0,7,0.5,unresolved\n')
        result=self.delivery.refresh(stopped_pipeline=True)
        item=next(x for x in result['files'] if x['object_id']=='traj')
        self.assertEqual(item['category'],'trajectories')
        with (self.destination/item['path']).open() as stream:
            row=next(csv.DictReader(stream))
        self.assertEqual(row['inclination_raw'],'0.5')
        self.assertEqual(row['angle_units_status'],'unresolved')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.run = self.root/'run'
        self.package = self.run/'extraction/package/Project'; self.package.mkdir(parents=True)
        self.destination = self.root/'EXPORTS'; self.report = self.root/'REPORT.html'
        self.report.write_text('<html><body><main>Inventory</main></body></html>')
        self.delivery = IncrementalDelivery(self.run, self.report, self.destination, {})
        self.receipt = self.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        self.tables = {
            '05_spatial/polygons/native_polygons_vertices.csv':
                'object_id,segment_id,vertex_index,x\npoly,9,2,20\npoly,3,0,10\nfailed,1,0,99\n',
            '05_spatial/points/native_points_vertices.csv':'object_id,point_index,x\npoint,0,4\n',
            '02_wells/trajectories/native_well_trajectory_records.csv':'object_id,record_index,md\ntraj,0,7\n',
            '02_wells/well_headers/native_well_heads.csv':'well_name,x\nA,4\n'}
        for relative, content in self.tables.items():
            path = self.package/relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.evidence = dict(tool='export_petrel_native_spatial_zero_gui.py',
            completed_at_utc='2026-09-26T00:00:00Z', polygon_vertex_rows=3,
            point_vertex_rows=1, trajectory_rows=1, native_well_head_rows=1,
            model_well_head_decode={'status':'decoded'}, objects=[
                dict(object_id='poly', status='decoded', blob_type='Polygons3'),
                dict(object_id='failed', status='failed_closed', blob_type='Polygons3'),
                dict(object_id='point', status='decoded', blob_type='Points3'),
                dict(object_id='traj', status='decoded', blob_type='MdInclAzimTrajectoryProviderData')])
        write_json(self.receipt, self.evidence)

    def test_stopped_pipeline_retains_committed_native_and_spatial_with_explicit_scope(self):
        IncrementalDeliveryTests.native(self)
        result = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(len(result['files']), 5)
        self.assertTrue(result['partial']); self.assertEqual(result['status'], 'stopped_partial')
        self.assertEqual({r['category'] for r in result['files']},
                         {'logs','polygons','points','trajectories','well_headers'})
        polygon = next(r for r in result['files'] if r['category']=='polygons')
        with (self.destination/polygon['path']).open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r['segment_id'], r['vertex_index']) for r in rows], [('9','2'),('3','0')])
        self.assertEqual(polygon['record_count'], 2)
        self.assertIn('package_QC_not_established', polygon['integrity'])
        self.assertNotIn('failed', {r['object_id'] for r in result['files']})
        for r in result['files']:
            self.assertTrue((self.destination/r['path']).is_file())
        html = (self.destination/'INCREMENTAL_INDEX.html').read_text()
        self.assertIn('QC are not established', html)
        self.assertIn('Open the live accepted-export index', self.report.read_text())
        saved = json.loads((self.destination/'FILE_INDEX.json').read_text())
        self.assertEqual(saved['files'], result['files'])

    def test_live_refresh_never_reads_spatial_tables(self):
        with patch('geoviewer_delivery.publish_exports', side_effect=AssertionError('not live')):
            self.assertEqual(self.delivery.refresh()['files'], [])

    def test_missing_terminal_receipt_cannot_publish_tables(self):
        self.evidence.pop('completed_at_utc'); write_json(self.receipt,self.evidence)
        result = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(result['files'], [])
        self.assertTrue(any('no completed decoder receipt' in s for s in result['findings']))

    def test_truncated_table_excludes_only_affected_category(self):
        (self.package/next(iter(self.tables))).write_text('object_id,x\npoly,2\n')
        result = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(len(result['files']), 3)
        self.assertNotIn('polygons', {r['category'] for r in result['files']})
        self.assertTrue(any('row count' in s for s in result['findings']))

    def test_missing_table_and_unproven_headers_are_not_published(self):
        (self.package/'05_spatial/points/native_points_vertices.csv').unlink()
        self.evidence.pop('model_well_head_decode'); write_json(self.receipt,self.evidence)
        result = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual({r['category'] for r in result['files']}, {'polygons','trajectories'})

    def test_accepted_header_rows_survive_other_failed_subjects(self):
        self.evidence['model_well_head_decode']['status']='decoded_with_failed_closed_subjects'
        write_json(self.receipt,self.evidence)
        result=self.delivery.refresh(stopped_pipeline=True)
        self.assertIn('well_headers',{r['category'] for r in result['files']})

    def test_malformed_receipt_does_not_lose_committed_logs(self):
        IncrementalDeliveryTests.native(self)
        self.receipt.write_text('{')
        result = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['files'][0]['category'], 'logs')
        self.assertTrue(any('Spatial checkpoint unavailable' in s for s in result['findings']))

    def test_repeat_is_idempotent_and_source_tables_unchanged(self):
        before = {p: (self.package/p).read_bytes() for p in self.tables}
        first = self.delivery.refresh(stopped_pipeline=True)
        second = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(first['files'], second['files'])
        self.assertEqual(before, {p: (self.package/p).read_bytes() for p in self.tables})

    def test_active_background_publisher_is_rejected(self):
        with patch.object(self.delivery, '_thread') as thread:
            thread.is_alive.return_value = True
            with self.assertRaisesRegex(RuntimeError,'stopped background'):
                self.delivery.refresh(stopped_pipeline=True)

    def test_later_corruption_withdraws_spatial_row_but_preserves_logs(self):
        IncrementalDeliveryTests.native(self)
        self.delivery.refresh(stopped_pipeline=True)
        (self.package/'05_spatial/points/native_points_vertices.csv').write_text('object_id,x\n')
        result = self.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(len(result['files']), 4)
        self.assertNotIn('points', {r['category'] for r in result['files']})
        self.assertIn('logs', {r['category'] for r in result['files']})


if __name__ == '__main__': unittest.main()
