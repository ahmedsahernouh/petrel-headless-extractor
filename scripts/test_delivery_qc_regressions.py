# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic final-delivery controls for checkpoint and companion evidence gaps."""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoviewer_delivery import exports_section, publish_exports
import petrel_geoscience_tools as qc_tools
import test_geoviewer_incremental as native_fixtures
import test_companion_delivery as companion_fixtures


class CheckpointFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native_fixtures.IncrementalDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.package = self.fixture.package
        self.destination = self.fixture.destination

    def _saved_accepted(self):
        with (self.destination/'FILE_INDEX.csv').open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    def test_same_size_mutation_is_withheld_from_every_accepted_view(self):
        commit, artifact = self.fixture.native(content='a,b\n0,1\n')
        checkpoint = self.fixture.delivery.refresh(package=self.package)
        artifact.write_text('a,b\n0,9\n', encoding='utf-8')
        manifest = self.package/'00_manifest/export_manifest.csv'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['source_object_type', 'export_file', 'sha256',
                                                         'validation_status'])
            writer.writeheader()
            writer.writerow(dict(source_object_type='native_commit',
                                 export_file=commit.relative_to(self.package).as_posix(),
                                 sha256=hashlib.sha256(commit.read_bytes()).hexdigest(),
                                 validation_status='validated'))
        qc = qc_tools.qc_data_package(dict(export_package=str(self.package),
                                           output_dir=str(self.fixture.run/'qc'), dry_run=False))
        self.assertEqual(qc['status'], 'passed')
        self.assertEqual(qc_tools.verify_receipt(qc)['status'], 'passed')
        self.assertEqual(json.loads(Path(qc['report_path']).read_text())['native_artifact_qc']['status'],
                         'not_present')
        result = publish_exports(self.package, {}, self.destination, checkpoint_index=checkpoint)
        self.assertEqual(result['files'], [])
        self.assertEqual(result['partial_files'], [])
        self.assertTrue(any('SHA-256 mismatch' in finding for finding in result['findings']))
        self.assertEqual(self._saved_accepted(), [])
        self.assertEqual(json.loads((self.destination/'FILE_INDEX.json').read_text())['files'], [])
        html = exports_section(result, self.destination, self.fixture.report)
        self.assertIn('Delivery partial:', html)
        self.assertIn('No accepted exports yet', html)
        self.assertNotIn('href="../EXPORTS/'+checkpoint['files'][0]['path']+'"', html)

    def test_fresh_checkpoint_without_aggregate_survives_only_as_partial(self):
        self.fixture.native()
        checkpoint = self.fixture.delivery.refresh(package=self.package)
        result = publish_exports(self.package, {}, self.destination, checkpoint_index=checkpoint)
        self.assertEqual(result['files'], [])
        self.assertEqual(self._saved_accepted(), [])
        self.assertTrue(result['partial'])
        self.assertEqual(len(result['partial_files']), 1)
        self.assertEqual(result['partial_files'][0]['status'], 'checkpoint_only_partial')
        self.assertIn('package_QC_not_established', result['partial_files'][0]['integrity'])
        self.assertIn('Checkpoint-only outputs', exports_section(result, self.destination, self.fixture.report))

    def test_aggregate_backed_native_row_remains_accepted(self):
        commit, _ = self.fixture.native()
        checkpoint = self.fixture.delivery.refresh(package=self.package)
        native_fixtures.write_json(self.package/'07_workflows_reports/native_recovery/native_recovery_report.json',
                                   {'objects': [json.loads(commit.read_text())['record']]})
        result = publish_exports(self.package, {}, self.destination, checkpoint_index=checkpoint)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['partial_files'], [])
        self.assertEqual(len(self._saved_accepted()), 1)

    def test_fresh_spatial_and_workflow_checkpoints_remain_visible_as_partial(self):
        self.package.mkdir(exist_ok=True)
        rows = []
        for category in ('polygons', 'workflows'):
            file = self.destination/category/(category+'.json')
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('{"synthetic":true}', encoding='utf-8')
            rows.append(dict(category=category, object_id=category, name=category,
                             path=file.relative_to(self.destination).as_posix(), bytes=file.stat().st_size,
                             sha256=hashlib.sha256(file.read_bytes()).hexdigest(), status='exported',
                             numerical_qc='checkpoint receipt', integrity='receipt_sha256'))
        result = publish_exports(self.package, {}, self.destination, checkpoint_index={'files': rows})
        self.assertEqual(result['files'], [])
        self.assertEqual({r['category'] for r in result['partial_files']}, {'polygons', 'workflows'})
        self.assertIn('polygons.json', exports_section(result, self.destination, self.fixture.report))

    def test_final_seismic_verifies_size_receipt_and_declared_hash(self):
        for mode in ('wrong_size', 'malformed_receipt', 'failed_receipt', 'same_size_damage'):
            with self.subTest(mode=mode):
                inventory = self.fixture.seismic()
                checkpoint = self.fixture.delivery.refresh(package=self.package, inventory=inventory)
                receipt = Path(inventory['objects'][0]['receipt_path'])
                artifact = receipt.parent/'volume.segy'
                if mode == 'wrong_size': artifact.write_bytes(b'BAD')
                elif mode == 'malformed_receipt': receipt.write_text('{', encoding='utf-8')
                elif mode == 'failed_receipt':
                    changed = json.loads(receipt.read_text()); changed['status'] = 'failed'
                    native_fixtures.write_json(receipt, changed)
                else: artifact.write_bytes(b'BADD')
                destination = self.fixture.root/('FINAL_'+mode)
                result = publish_exports(self.package, inventory, destination, checkpoint_index=checkpoint)
                self.assertEqual([row for row in result['files'] if row['category']=='seismic'], [])
                self.assertEqual(json.loads((destination/'FILE_INDEX.json').read_text())['files'], [])
                self.assertTrue(result['partial'])
                self.assertTrue(any('seismic' in finding.lower() for finding in result['findings']))
                # Recreate the independent fixture for the next subcase.
                artifact.write_bytes(b'SEGY')
                native_fixtures.write_json(receipt, inventory['objects'][0]['result'])

    def test_no_hash_known_bad_seismic_stays_out_of_final_accepted_index(self):
        inventory = self.fixture.seismic()
        row = inventory['objects'][0]
        receipt = Path(row['receipt_path'])
        row['result']['artifacts'][0]['sha256'] = None
        row['result']['full_seismic_hash'] = False
        native_fixtures.write_json(receipt, row['result'])
        checkpoint = self.fixture.delivery.refresh(package=self.package, inventory=inventory)
        artifact = receipt.parent/'volume.segy'
        artifact.unlink()
        withdrawn = self.fixture.delivery.refresh(package=self.package, inventory=inventory)
        self.assertEqual(withdrawn['files'], [])
        artifact.write_bytes(b'BADD')
        self.assertEqual(self.fixture.delivery.refresh(package=self.package, inventory=inventory)['files'], [])
        result = publish_exports(self.package, inventory, self.fixture.root/'FINAL_NOHASH',
                                 checkpoint_index=self.fixture.delivery.current_index)
        self.assertEqual([row for row in result['files'] if row['category']=='seismic'], [])
        self.assertTrue(any('Previously invalid seismic checkpoint' in finding for finding in result['findings']))

    def test_seismic_project_named_report_and_exact_zgy_layouts(self):
        inventory = self.fixture.seismic()
        project_named_report = self.fixture.run/'extraction/package/report'
        project_named_report.mkdir()
        exact_zgy_report = self.fixture.run/'report'
        exact_zgy_report.mkdir()
        for package in (project_named_report, exact_zgy_report):
            with self.subTest(package=str(package)):
                destination = self.fixture.root/('FINAL_'+str(len(list(self.fixture.root.glob('FINAL_*')))))
                result = publish_exports(package, inventory, destination)
                self.assertEqual(len([r for r in result['files'] if r['category']=='seismic']), 1)

    def test_final_seismic_hashes_shared_generated_file_once(self):
        inventory = self.fixture.seismic()
        second = json.loads(json.dumps(inventory['objects'][0]))
        second['id'] = 'cube-2'; second['name'] = 'Cube Two'
        inventory['objects'].append(second)
        import petrel_progress
        actual_hash = petrel_progress.hash_file
        observed = []
        def counted(path, *args, **kwargs):
            observed.append(Path(path))
            return actual_hash(path, *args, **kwargs)
        with patch.object(petrel_progress, 'hash_file', side_effect=counted):
            result = publish_exports(self.package, inventory, self.fixture.root/'FINAL_SHARED')
        self.assertEqual(len([r for r in result['files'] if r['category']=='seismic']), 2)
        self.assertEqual(len(observed), 1)

    def test_conflicting_shared_seismic_declarations_withhold_all_owners(self):
        inventory = self.fixture.seismic()
        first = inventory['objects'][0]
        first_receipt = Path(first['receipt_path'])
        second_folder = self.fixture.run/'seismic/cube_2'; second_folder.mkdir()
        os.link(first_receipt.parent/'volume.segy', second_folder/'volume.segy')
        second = json.loads(json.dumps(first))
        second['id'] = 'cube-2'; second['name'] = 'Cube Two'
        second['receipt_path'] = str(second_folder/'RUN_RESULT.json')
        second['result']['receipt_path'] = second['receipt_path']
        second['result']['artifacts'][0]['sha256'] = '0'*64
        native_fixtures.write_json(second_folder/'RUN_RESULT.json', second['result'])
        inventory['objects'].append(second)
        result = publish_exports(self.package, inventory, self.fixture.root/'FINAL_CONFLICT')
        self.assertEqual([r for r in result['files'] if r['category']=='seismic'], [])
        self.assertTrue(result['partial'])
        self.assertTrue(any('conflicting shared seismic artifact' in finding for finding in result['findings']))


class MissingCompanionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = companion_fixtures.CompanionDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_registered_derivative_without_inventory_is_explicit_partial(self):
        f = self.fixture
        f.add('well', 'las_well_data', '.las', 'las/well.csv')
        companion_fixtures.table(f.package/'00_manifest/export_manifest.csv', f.manifest)
        result = publish_exports(f.package, {}, f.destination)
        self.assertEqual(result['files'], [])
        self.assertTrue(result['partial'])
        self.assertTrue(any('Companion source inventory missing' in finding for finding in result['findings']))
        self.assertEqual(json.loads((f.destination/'FILE_INDEX.json').read_text())['status'], 'partial')
        self.assertIn('Delivery partial:', exports_section(result, f.destination, f.root/'report.html'))

    def test_no_companion_registration_without_inventory_is_clean(self):
        f = self.fixture
        native = [dict(source_object_type='native_data', export_file='native_data/object.csv')]
        companion_fixtures.table(f.package/'00_manifest/export_manifest.csv', native)
        result = publish_exports(f.package, {}, f.destination)
        self.assertEqual(result['files'], [])
        self.assertEqual(result['findings'], [])
        self.assertFalse(result['partial'])
        self.assertEqual(result['status'], 'delivery_indexed')

    def test_historical_well_top_registration_without_inventory_is_partial(self):
        f = self.fixture
        derivative = f.package/'02_wells/well_tops/tops.csv'
        derivative.parent.mkdir(parents=True)
        derivative.write_text('well,top\nA,T\n', encoding='utf-8')
        companion_fixtures.table(f.package/'00_manifest/export_manifest.csv', [
            dict(source_object_type='well_top', export_file='02_wells/well_tops/tops.csv',
                 sha256=hashlib.sha256(derivative.read_bytes()).hexdigest())])
        result = publish_exports(f.package, {}, f.destination)
        self.assertEqual(result['files'], [])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(any('Companion source inventory missing' in finding for finding in result['findings']))


if __name__ == '__main__':
    unittest.main()
