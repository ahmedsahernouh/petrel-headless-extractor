# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Keep numeric recovery, units, registry inventory and file counts distinct."""
import json
from pathlib import Path
import tempfile
import unittest

import report_petrel_project_audit as report
import petrel_visual_report as visual


class RecoveryOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.receipt = self.root / '07_workflows_reports/native_recovery/native_recovery_report.json'
        self.receipt.parent.mkdir(parents=True)
        metadata = self.root / '01_project_metadata'
        metadata.mkdir()
        (metadata / 'native_data_object_type_counts.csv').write_text(
            'object_type,unique_object_ids,version_records,occurrences,decode_status\n'
            'FloatWellLog,99,110,120,metadata_only\n', encoding='utf-8')

    def log(self, **overrides):
        return dict(dict(object_id='log', blob_type='FloatWellLog', status='missing_metadata',
                         numeric_round_trip='exact', sample_count=17, unit=None, depth_unit='m',
                         artifacts=[{'path': 'log/samples.csv'}]), **overrides)

    def gather(self, objects, **overrides):
        receipt = dict(status='partial', source_unchanged=True, objects=objects,
                       object_status_counts={'missing_metadata': 3})
        receipt.update(overrides)
        self.receipt.write_text(json.dumps(receipt), encoding='utf-8')
        return report.gather_native_inventory(self.root)

    def test_unknown_units_are_recovered_without_becoming_decoded(self):
        result = self.gather([self.log(), self.log(object_id='int', blob_type='IntWellLog'),
                              self.log(object_id='resolved', status='decoded', unit='API')])
        self.assertEqual(result['native_log_objects_recovered'], 3)
        self.assertEqual(result['native_log_samples_recovered'], 51)
        self.assertEqual(result['native_log_samples'], 51)
        self.assertEqual(result['native_recovery_category_counts']['numeric_recovered_units_unresolved'], 2)
        self.assertEqual(result['native_recovery_category_counts']['decoded_units_resolved'], 1)
        self.assertEqual(result['decoded_object_type_counts'], {'FloatWellLog': 1})
        self.assertEqual(result['registry_unique_objects'], 99)
        self.assertEqual(result['native_las_files'], 0)
        self.assertEqual(result['native_recovery_outcomes'][0]['status'], 'missing_metadata')
        self.assertEqual(result['native_recovery_status_counts'], {'missing_metadata': 3})

    def test_geometry_missing_preview_empty_and_failure_remain_separate(self):
        grid = self.log(blob_type='RegValGrid2', dataset_exported=True,
                        artifacts=[{'path': 'grid/surface.xyz'}], defined_nodes=8)
        objects = [grid, dict(grid, object_id='no_geometry', artifacts=[]),
                   dict(grid, object_id='preview', dataset_exported=False),
                   self.log(status='empty_supported_object', artifacts=[]),
                   self.log(status='conversion_failed'), self.log(status='write_failed'),
                   self.log(status='finalization_failed')]
        result = self.gather(objects)
        self.assertEqual(result['native_recovery_category_counts'], dict(
            decoded_units_resolved=0, numeric_recovered_units_unresolved=1,
            numeric_recovered_metadata_unresolved=0, no_usable_export=2, empty=1, failed=3))
        self.assertEqual(result['native_surface_objects_written'], 1)
        self.assertEqual(result['native_surface_nodes_written'], 8)
        self.assertEqual(result['native_surface_nodes'], 8)

    def test_artifact_and_exact_qc_and_unchanged_source_are_required(self):
        for item in (self.log(artifacts=[]), self.log(numeric_round_trip='failed'),
                     self.log(dataset_exported=False)):
            self.assertEqual(self.gather([item])['native_numeric_objects_recovered'], 0)
        self.assertEqual(self.gather([self.log()], source_unchanged=False)['native_numeric_objects_recovered'], 0)

    def test_visual_chart_counts_include_unknown_unit_logs(self):
        native = self.gather([self.log(), self.log(object_id='resolved', status='decoded', unit='API'),
                              self.log(object_id='failed', status='conversion_failed')])
        native['decoded_object_type_counts']['Polygons3'] = 4
        green, amber = visual.recovery_chart_counts(native, ['FloatWellLog', 'Polygons3'])
        self.assertEqual(green, [1, 4])
        self.assertEqual(amber, [1, 0])
        self.assertEqual(native['registry_unique_objects'], 99)

    def test_visual_chart_does_not_infer_recovery_from_missing_evidence(self):
        native = self.gather([self.log(artifacts=[]), self.log(numeric_round_trip='failed')])
        self.assertEqual(visual.recovery_chart_counts(native, ['FloatWellLog']), ([0], [0]))
        legacy = {'decoded_object_type_counts': {'RegValGrid2': 2},
                  'native_surface_written_by_type': {'RegValGrid2': 3}}
        self.assertEqual(visual.recovery_chart_counts(legacy, ['RegValGrid2', 'FloatWellLog']), ([2, 0], [1, 0]))

    def test_html_explains_counts_and_retains_raw_status(self):
        native = self.gather([self.log()])
        audit = {key: {} for key in ('project', 'wells', 'well_tops', 'surfaces', 'seismic',
                                    'native_semantic', 'spatial_overview', 'media', 'file_inventory',
                                    'domain_file_counts')}
        audit.update(native_inventory=native, manifest={'available': False}, qc_flags=[],
                     export_package=str(self.root), created_at_utc='test')
        audit['project']['native_model_type_counts'] = {'CheckShotSubject': 110}
        html = report.render_html(audit, 'Recovery test')
        self.assertIn('Numeric objects recovered; units unresolved', html)
        self.assertIn('17 native sample CSV records', html)
        self.assertIn('missing_metadata', html)
        self.assertNotIn('Only decoded objects from an unchanged source snapshot count as recovered', html)
        self.assertNotIn('project-linked SEG-Y conversion not integrated', html)
        self.assertIn('Native registry IDs are not physical seismic-file counts', html)
        self.assertIn('Checkshots (model subjects)</td><td>110</td><td>0</td>', html)
        self.assertIn('native checkshot export unsupported', html)
        self.assertIn('native horizon topology export unsupported', html)

    def test_model_faults_and_horizons_remain_visible_without_named_payloads(self):
        audit = {key: {} for key in ('project', 'wells', 'well_tops', 'surfaces', 'seismic',
                                    'native_semantic', 'spatial_overview', 'media', 'file_inventory',
                                    'domain_file_counts')}
        audit.update(native_inventory=self.gather([]), manifest={'available': False}, qc_flags=[],
                     export_package=str(self.root), created_at_utc='test')
        # Modern archived case: model subjects exist, neither named payload type is registered.
        audit['project']['native_model_type_counts'] = {
            'FaultSubject': 18, 'FaultFacesDataSubject': 18, 'PillarFaultSubject': 24,
            'SeismicHorizonSubject': 1, 'HorizonSubject': 13, 'HorizonSegSubject': 78}
        html = report.render_html(audit, 'Independent inventory bases')
        self.assertIn('Fault objects (model subjects)</td><td>18</td><td>0</td>', html)
        self.assertIn('Fault interpretation payloads</td><td>0</td><td>0</td>', html)
        self.assertIn('Seismic horizons (model subjects)</td><td>1</td><td>0</td>', html)
        self.assertIn('Seismic horizon payloads</td><td>0</td><td>0</td>', html)
        self.assertIn('not a count of all project faults', html)
        self.assertIn('Inventory count (basis named in row)', html)
        self.assertNotIn('<div class="tile-label">Faults</div>', html)
        # Legacy reports without model enumeration must not manufacture a zero.
        audit['project'].pop('native_model_type_counts')
        html = report.render_html(audit, 'Missing model evidence')
        self.assertIn('Fault objects (model subjects)</td><td>not enumerated</td>', html)
        self.assertIn('Seismic horizons (model subjects)</td><td>not enumerated</td>', html)

    def test_empty_horizon_parent_does_not_hide_child_tile_storage(self):
        metadata = self.root / '01_project_metadata/native_data_object_type_counts.csv'
        metadata.write_text('object_type,unique_object_ids,version_records,occurrences,decode_status\n'
            'SeismicHorizonShape,8,8,8,metadata_only\n'
            'Interpretation3Local,17,17,17,metadata_only\n', encoding='utf-8')
        audit = {key: {} for key in ('project', 'wells', 'well_tops', 'surfaces', 'seismic',
            'native_semantic', 'spatial_overview', 'media', 'file_inventory', 'domain_file_counts')}
        audit.update(native_inventory=self.gather([]), manifest={'available': False}, qc_flags=[],
                     export_package=str(self.root), created_at_utc='test')
        audit['project']['native_model_type_counts'] = {'SeismicHorizonSubject': 8}
        page = report.render_html(audit, 'Horizon storage boundaries')
        self.assertIn('Interpretation3Local payloads</td><td>17</td><td>0</td>', page)
        self.assertIn('Seismic horizons (model subjects)</td><td>8</td><td>0</td>', page)
        self.assertIn('empty parent payload does not prove an empty horizon', page)
        self.assertIn('.zhz/.zhz_msk', page)
        self.assertIn('not additional horizon objects or decoded grids', page)


if __name__ == '__main__':
    unittest.main()
