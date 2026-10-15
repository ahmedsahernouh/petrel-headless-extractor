# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic regression checks for the read-only pre-contract adapter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from geoviewer_contract import POINTER, build_catalogue, publish_contract
from geoviewer_legacy_contract import SCHEMA, assess_legacy, main
from test_geoviewer_contract import ContractTests, write


class LegacyContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ContractTests('test_multiple_representations_one_entity_and_roles')
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)
        self.package = self.fixture.package
        self.exports = self.fixture.exports
        self.run = self.fixture.run
        self.index = self.fixture.index

    def assess(self, **kwargs):
        write(self.exports / 'FILE_INDEX.json', self.index)
        return assess_legacy(self.package, self.exports, **kwargs)

    def test_normal_publication_uses_identical_normalized_tables(self):
        expected = build_catalogue(self.package, self.exports, self.index)['tables']
        pointer = publish_contract(self.package, self.exports, self.index)
        folder = (self.exports / pointer['manifest']).parent
        for name, rows in expected.items():
            actual = [json.loads(line) for line in (folder / (name + '.jsonl')).read_text().splitlines()]
            self.assertEqual(actual, rows)

    def test_accepted_relationships_identity_qc_and_no_commit(self):
        report = self.assess()
        self.assertEqual(report['schema'], SCHEMA)
        self.assertIsNone(report['committed_generation'])
        self.assertIsNone(report['generation'])
        self.assertIsNone(report['manifest_sha256'])
        self.assertIn('absent', report['publication_binding'])
        self.assertEqual(report['counts']['source_ready'], 0)
        self.assertEqual(report['counts']['qualified_for_planned_mapping'], 0)
        self.assertEqual(report['counts']['receiving_imports_executed'], 0)
        entities = report['normalized_catalogue']['entities']
        self.assertEqual(len(entities), 2)
        self.assertEqual({e['name'] for e in entities}, {'Same'})
        self.assertEqual(len({e['entity_key'] for e in entities}), 2)
        self.assertTrue(report['normalized_catalogue']['relationships'])
        self.assertTrue(all('PUBLICATION_NOT_FINAL' in a['reason_codes'] for a in report['assessments']))
        artifacts = report['normalized_catalogue']['artifacts']
        self.assertTrue(all(a['current_identity_scope'] == 'recorded_size_only' for a in artifacts))
        self.assertTrue(all(a['numerical_qc'] == 'exact_readback' for a in artifacts))
        self.assertFalse((self.exports / POINTER).exists())
        self.assertFalse((self.exports / 'INTEROPERABILITY').exists())

    def test_same_display_name_different_native_uuids_stay_separate(self):
        one = '11111111-1111-4111-8111-111111111111'
        two = '22222222-2222-4222-8222-222222222222'
        self.fixture.context['objects'] = [
            {'object_id': one, 'name': 'Repeated', 'category': 'Points3'},
            {'object_id': two, 'name': 'Repeated', 'category': 'Points3'}]
        write(self.package / '01_project_metadata/project_context.json', self.fixture.context)
        self.fixture.native['objects'] = []
        write(self.package / '07_workflows_reports/native_recovery/native_recovery_report.json', self.fixture.native)
        self.fixture.rows.clear()
        self.fixture.add_row(one, 'points/one.csv', b'x,y,z\n', 'CSV')
        self.fixture.add_row(two, 'points/two.csv', b'x,y,z\n', 'CSV')
        report = self.assess()
        entities = report['normalized_catalogue']['entities']
        self.assertEqual({e['native_uuid'] for e in entities}, {one, two})
        self.assertEqual({e['name'] for e in entities}, {'Repeated'})
        self.assertEqual(len({e['entity_key'] for e in entities}), 2)

    def test_receiving_profile_stays_unqualified_without_generation(self):
        report = self.assess(profile='opendtect')
        self.assertEqual(report['counts']['qualified_for_planned_mapping'], 0)
        self.assertEqual(report['counts']['receiving_qualified'], 0)
        self.assertEqual(report['counts']['receiving_imports_executed'], 0)
        self.assertTrue(all(a['receiving_state'] != 'qualified_for_planned_mapping' for a in report['assessments']))

    def test_partial_empty_and_context_only(self):
        self.index['partial_files'] = [dict(self.index['files'].pop(), status='checkpoint_only_partial')]
        self.index['status'] = 'partial'
        partial = self.assess()
        self.assertEqual(len(partial['normalized_catalogue']['artifacts']), 4)
        self.assertEqual(partial['counts']['source_ready'], 0)
        self.assertTrue(any(a['index_section'] == 'partial_files' for a in partial['normalized_catalogue']['artifacts']))
        self.index = dict(files=[], partial_files=[], status='delivery_indexed')
        empty = self.assess()
        self.assertEqual(empty['counts']['artifacts'], 0)
        self.assertEqual(empty['counts']['entities'], 2)
        self.index = dict(schema='geoviewer.inventory-index/1', files=[], partial_files=[],
                          status='inventory_only', dataset_conversion_enabled=False)
        inventory = self.assess()
        self.assertEqual(inventory['counts']['artifacts'], 0)
        self.assertTrue(all('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD' in a['reason_codes'] for a in inventory['assessments']))

    def test_saved_seismic_inventory_is_visible_without_following_source_path(self):
        self.index = dict(files=[], partial_files=[], status='delivery_indexed')
        write(self.package / '01_project_metadata/project_seismic_inventory.json',
              {'objects': [{'id': 'transport-a', 'name': 'Seismic', 'status': 'inventory_only',
                            'source': 'Z:/stale/nativeproject/Data.ptd',
                            'association': 'unresolved'}],
               'native_objects': [{'object_id': '33333333-3333-4333-8333-333333333333',
                                   'subject_type': 'SeismicSubject'}]})
        report = self.assess()
        entities = report['normalized_catalogue']['entities']
        self.assertEqual(len(entities), 4)
        self.assertTrue(any(e.get('source_reference_recorded_only') == 'Z:/stale/nativeproject/Data.ptd'
                            for e in entities))
        self.assertEqual(report['counts']['artifacts'], 0)

    def test_missing_bulk_keeps_historical_export_and_qc(self):
        (self.exports / 'grids/surface.xyz').unlink()
        report = self.assess()
        artifact = next(a for a in report['normalized_catalogue']['artifacts'] if a['relative_path'] == 'grids/surface.xyz')
        self.assertFalse(artifact['available'])
        self.assertEqual(artifact['original_status'], 'exported')
        self.assertEqual(artifact['numerical_qc'], 'exact_readback')
        self.assertEqual(artifact['current_identity_scope'], 'unavailable')
        self.assertIn('ARTIFACT_UNAVAILABLE', {d['code'] for d in report['diagnostics']})

    def test_relocation_and_input_bytes_unchanged(self):
        watched = [self.exports / 'FILE_INDEX.json',
                   self.package / '01_project_metadata/project_context.json',
                   self.package / '07_workflows_reports/native_recovery/native_recovery_report.json']
        original = {str(p.relative_to(self.run)): (p.read_bytes(), p.stat().st_mtime_ns) for p in watched}
        moved = Path(self.fixture.temp.name) / 'relocated'
        shutil.copytree(self.run, moved)
        report = assess_legacy(moved / 'data/package', moved / 'EXPORTS')
        self.assertTrue(any(a['available'] for a in report['normalized_catalogue']['artifacts']))
        for relative, value in original.items():
            source = self.run / relative
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), value)

    def test_unknown_malformed_and_stale_assertions_fail(self):
        for bad in (dict(self.index, schema='unknown/9'), dict(self.index, files='wrong'),
                    dict(self.index, status='success'), dict(self.index, files=[{'object_id': 'x'}])):
            self.index = bad
            with self.assertRaises(ValueError):
                self.assess()
        self.index = self.fixture.index
        write(self.exports / 'FILE_INDEX.json', self.index)
        with self.assertRaisesRegex(ValueError, 'Supplied FILE_INDEX differs'):
            assess_legacy(self.package, self.exports, file_index={'files': []})
        with self.assertRaisesRegex(ValueError, 'digest is stale'):
            assess_legacy(self.package, self.exports, expected_index_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'stale or missing'):
            assess_legacy(self.package, self.exports, expected_receipt_sha256={'native_recovery': '0' * 64})

    def test_status_free_historical_index_with_product_marker(self):
        self.index.pop('status')
        self.index['product'] = 'Field Viewer Data Extractor'
        report = self.assess()
        self.assertIsNone(report['assessments'][0]['state_axes']['original_run_publication'])
        self.assertEqual(report['counts']['source_ready'], 0)

    def test_path_escape_and_link_blocked(self):
        original = json.loads(json.dumps(self.index))
        self.index['files'][0]['path'] = '../../outside.csv'
        with self.assertRaises(ValueError):
            self.assess()
        self.index = original
        link = self.exports / 'grids/linked.csv'
        try:
            link.symlink_to(self.exports / 'grids/surface.xyz')
        except (OSError, NotImplementedError):
            self.skipTest('Local symlink creation unavailable')
        self.index['files'][0]['path'] = 'grids/linked.csv'
        with self.assertRaises(ValueError):
            self.assess()

    def test_same_size_change_requires_hash_opt_in(self):
        target = self.exports / 'grids/surface.xyz'
        target.write_bytes(b'9 8 7\n')
        unchecked = self.assess()
        row = next(a for a in unchecked['normalized_catalogue']['artifacts'] if a['relative_path'] == 'grids/surface.xyz')
        self.assertTrue(row['available'])
        self.assertEqual(row['current_identity_scope'], 'recorded_size_only')
        checked = self.assess(hash_artifacts=True)
        self.assertIn('HASH_MISMATCH', {d['code'] for d in checked['diagnostics']})

    def test_fingerprint_tracks_exact_index_receipt_and_adapter(self):
        first = self.assess()
        self.assertEqual(first['derived_local_fingerprint'], self.assess()['derived_local_fingerprint'])
        self.index['findings'] = ['new note']
        second = self.assess()
        self.assertNotEqual(first['derived_local_fingerprint'], second['derived_local_fingerprint'])
        native = self.package / '07_workflows_reports/native_recovery/native_recovery_report.json'
        value = json.loads(native.read_text())
        value['observation'] = 'changed receipt bytes'
        write(native, value)
        third = self.assess()
        self.assertNotEqual(second['derived_local_fingerprint'], third['derived_local_fingerprint'])
        self.assertEqual(len(third['fingerprint_inputs']['adapter_script_sha256']), 6)
        self.assertEqual(third['fingerprint_inputs']['adapter_script_sha256']['geoviewer_metadata_evidence.py'],
                         hashlib.sha256((Path(__file__).parent / 'geoviewer_metadata_evidence.py').read_bytes()).hexdigest())
        self.assertEqual(third['catalogue_input_fingerprint'], third['derived_local_fingerprint'])

    def test_bounded_seismic_sidecar_bytes_and_missing_state_are_fingerprinted(self):
        raw = b'{"axis":1}'
        self.fixture.add_row('transport-a', 'seismic/metadata.json', raw, 'JSON')
        self.fixture.rows[-1]['category'] = 'seismic'
        write(self.package / '01_project_metadata/project_seismic_inventory.json',
              {'objects': [{'id': 'transport-a', 'status': 'converted', 'source': 'Z:/stale.zgy',
                            'result': {'status': 'passed', 'source': 'Z:/stale.zgy',
                                       'artifacts': [{'path': 'conversion_metadata.json',
                                                      'sha256': hashlib.sha256(raw).hexdigest(),
                                                      'size_bytes': len(raw)}]}}]})
        first = self.assess()
        sidecar = self.exports / 'seismic/metadata.json'
        sidecar.write_bytes(b'{"axis":2}')
        second = self.assess()
        self.assertNotEqual(first['derived_local_fingerprint'], second['derived_local_fingerprint'])
        self.assertEqual(second['fingerprint_inputs']['consumed_metadata'][0]['state'], 'bounded')
        sidecar.unlink()
        third = self.assess()
        self.assertNotEqual(second['derived_local_fingerprint'], third['derived_local_fingerprint'])
        self.assertEqual(third['fingerprint_inputs']['consumed_metadata'][0]['state'], 'missing')

    def test_bound_receipt_snapshot_and_mid_assessment_change_rejected(self):
        import geoviewer_contract
        native = self.package / '07_workflows_reports/native_recovery/native_recovery_report.json'
        original_builder = geoviewer_contract.build_catalogue
        write(self.exports / 'FILE_INDEX.json', self.index)
        with patch('geoviewer_contract._read', side_effect=AssertionError('unexpected receipt reread')):
            assess_legacy(self.package, self.exports)

        def mutate_then_build(*args, **kwargs):
            value = json.loads(native.read_text())
            value['changed_during_assessment'] = True
            write(native, value)
            return original_builder(*args, **kwargs)

        with patch('geoviewer_legacy_contract.build_catalogue', side_effect=mutate_then_build):
            with self.assertRaisesRegex(ValueError, 'receipt changed during'):
                assess_legacy(self.package, self.exports)

    def test_cli_output_outside_and_never_overwritten(self):
        report = Path(self.fixture.temp.name) / 'review.json'
        self.assertEqual(main([str(self.package), str(self.exports), '--output', str(report)]), 0)
        raw = report.read_bytes()
        self.assertEqual(main([str(self.package), str(self.exports), '--output', str(report)]), 2)
        self.assertEqual(report.read_bytes(), raw)
        self.assertEqual(main([str(self.package), str(self.exports), '--output', str(self.run / 'report.json')]), 2)


if __name__ == '__main__':
    unittest.main()
