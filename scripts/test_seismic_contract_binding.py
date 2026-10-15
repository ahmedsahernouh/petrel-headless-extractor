# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Independent small fixtures for transport/native identity and metadata bindings."""
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from geoviewer_contract import POINTER, canonical, publish_contract
from validate_geoviewer_contract import validate_contract
import test_geoviewer_contract as fixtures
write = fixtures.write


class SeismicBindingTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    add_row = fixtures.ContractTests.add_row
    table = fixtures.ContractTests.table

    def fixture(self, owners=None):
        self.owners = owners or ['11111111-1111-1111-1111-111111111111']
        self.plan = dict(size=[2, 3, 4], native_axis=dict(origin=-2.0, increment=0.5, count=4, domain='unknown', unit='unknown'),
                         domain='unknown', source_vertical_unit='unknown', horizontal_unit='unknown', crs_verified=False,
                         axis_header_policy='unspecified_headers_exact_native_axis_in_text_and_JSON', interval_us=0,
                         segy_header_profile={'schema': 'fieldviewer.generated-segy/1'})
        self.source = dict(size=[2, 3, 4], zstart=-2.0, zinc=0.5, corners=[[1,2],[3,4],[5,6],[7,8]])
        declarations = []
        for name, value in [('conversion_metadata.json', self.plan), ('source_metadata.json', self.source)]:
            # Deliberately opaque filenames and labels: only receipt/hash/owner binding works.
            raw = canonical(value)
            path = 'seismic/opaque-' + str(len(declarations)) + '.json'
            self.add_row('transport-a', path, raw, 'JSON')
            self.rows[-1]['category'] = 'seismic'
            declarations.append(dict(path=name, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)))
        self.add_row('transport-a', 'seismic/data.segy', b'not a real cube; metadata-only test', 'SEGY')
        self.rows[-1].update(category='seismic', numerical_qc='every_amplitude_and_trace_geometry')
        self.item = dict(id='transport-a', name='same', association='exact_native_UUID_in_selected_store',
                         source='Z:/old/original.zgy', status='converted', preserved_copy=False,
                         native_objects=[dict(object_id=x, name='same') for x in self.owners],
                         result=dict(status='passed', source='Z:/old/original.zgy', receipt_path='Z:/never/follow/RUN_RESULT.json',
                                     summary=dict(all_decoded_samples_exact=True, headers_verified=True, traces=6, samples_verified=24),
                                     artifacts=declarations, full_seismic_hash=False, source_stat_unchanged=True,
                                     source_hashes_before={}, source_hashes_after={}, source_unchanged=None))
        self.inventory = dict(objects=[self.item], native_objects=[dict(object_id=x, virtual=False, category='SeismicSubject') for x in self.owners])

    def publish(self):
        write(self.exports / 'FILE_INDEX.json', self.index)
        return publish_contract(self.package, self.exports, self.index, self.inventory)

    def test_exact_native_identity_shares_artifacts_and_no_abs_path_reads(self):
        self.fixture(['11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'])
        self.publish()
        nodes = self.table('entities')
        owners = [e for e in nodes if e['native_uuid'] in self.owners]
        self.assertEqual(len(owners), 2)
        self.assertEqual(owners[0]['artifact_keys'], owners[1]['artifact_keys'])
        self.assertEqual(len(owners[0]['artifact_keys']), 3)
        self.assertEqual(len([a for a in self.table('artifacts') if a['format'] == 'SEGY']), 1)
        self.assertIsNone(next(e for e in nodes if e['source_lane'] == 'seismic_transport')['native_uuid'])
        report = validate_contract(self.exports / POINTER)
        self.assertFalse(report['dependency_cycles'])
        native = next(a for a in report['assessments'] if a['entity_key'] == owners[0]['entity_key'])
        self.assertNotIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD', native['reason_codes'])
        self.assertIn('SEISMIC_DOMAIN_UNRESOLVED', native['reason_codes'])
        self.assertNotIn('UNRESOLVED_VALUE_UNIT', native['reason_codes'])
        stored = next(e['raw_value'] for e in self.table('metadata_evidence') if e['field'] == 'seismic_conversion')
        self.assertEqual(stored['native_axis'], self.plan['native_axis'])
        self.assertEqual(stored['interval_us'], 0)

    def test_nonexact_reference_never_acquires_native_ownership(self):
        self.fixture(); self.item['association'] = 'native_model_external_reference'; self.publish()
        self.assertTrue(all(len(a['owner_keys']) == 1 for a in self.table('artifacts')))
        links = [r for r in self.table('relationships') if r['relation'] == 'materialized_payload']
        self.assertEqual(links[0]['status'], 'unresolved_reference')

    def test_missing_seismic_qc_never_reports_recorded_validation(self):
        self.fixture()
        self.item['result']['summary']['all_decoded_samples_exact'] = False
        self.publish()
        seismic = [a for a in validate_contract(self.exports / POINTER)['assessments'] if a['category'] == 'seismic']
        self.assertTrue(seismic)
        for a in seismic:
            self.assertIn('SEISMIC_NUMERICAL_QC_UNVALIDATED', a['reason_codes'])
            self.assertEqual(a['state_axes']['numerical_qc'], 'recorded_unvalidated')

    def test_relocated_and_new_transport_id_preserve_exact_native_scoped_keys(self):
        self.fixture(); self.publish()
        before = {x['entity_key'] for x in self.table('entities')}
        self.item['id'] = 'path-relocated-id'
        for row in self.rows:
            if row['object_id'] == 'transport-a': row['object_id'] = self.item['id']
        self.publish()
        self.assertEqual(before, {x['entity_key'] for x in self.table('entities')})
        moved = Path(self.temp.name) / 'moved'
        shutil.move(str(self.run), moved)
        report = validate_contract(moved / 'EXPORTS' / POINTER)
        self.assertFalse(any(d['code'] == 'SEISMIC_METADATA_STALE' for d in report['diagnostics']))

    def test_equal_length_sidecar_tamper_blocks_affected_objects_without_bulk_hash(self):
        self.fixture(); self.publish()
        path = self.exports / 'seismic/opaque-0.json'
        path.write_bytes(path.read_bytes().replace(b'-2.0', b'-9.0'))
        with patch('validate_geoviewer_contract._file_digest', wraps=__import__('validate_geoviewer_contract')._file_digest) as hashed:
            report = validate_contract(self.exports / POINTER)
        self.assertFalse(any(str(c.args[0]).endswith('.segy') for c in hashed.call_args_list))
        seismic = [a for a in report['assessments'] if a['category'] == 'seismic']
        self.assertTrue(all('SEISMIC_METADATA_STALE' in a['reason_codes'] for a in seismic))
        self.assertTrue(all('SEISMIC_METADATA_STALE' not in a['reason_codes'] for a in report['assessments'] if a['category'] != 'seismic'))

    def test_missing_pruned_sidecar_keeps_original_export_state(self):
        self.fixture(); self.publish(); (self.exports / 'seismic/opaque-0.json').unlink()
        result = validate_contract(self.exports / POINTER)
        self.assertTrue(any(d['code'] == 'SEISMIC_METADATA_STALE' for d in result['diagnostics']))
        self.assertTrue(all(a['original_status'] == 'exported' for a in self.table('artifacts')))

    def test_wrong_index_hash_partial_and_wrong_receipt_do_not_supply_metadata(self):
        for mode in ('wrong_hash', 'partial', 'wrong_receipt'):
            with self.subTest(mode=mode):
                self.fixture()
                if mode == 'wrong_hash': self.rows[-3]['sha256'] = '0' * 64
                elif mode == 'partial': self.rows[-3]['status'] = 'checkpoint_only_partial'
                else: self.item['result']['artifacts'][0]['sha256'] = '0' * 64
                self.publish()
                self.assertTrue(any(g['code'] == 'SEISMIC_METADATA_UNBOUND' for g in self.table('gaps')))
                del self.rows[-3:]

    def test_oversize_metadata_does_not_expand_contract(self):
        self.fixture(); raw = b' ' * (1024*1024+1)
        row = self.rows[-3]; (self.exports / row['path']).write_bytes(raw)
        row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.item['result']['artifacts'][0].update(size_bytes=len(raw), sha256=row['sha256'])
        self.publish()
        self.assertTrue(any('1 MiB' in g['reason'] for g in self.table('gaps')))

    def test_changed_axis_same_shape_is_a_conflict(self):
        self.fixture(); self.plan['native_axis']['increment'] = 2.0
        raw = canonical(self.plan); row = self.rows[-3]
        (self.exports / row['path']).write_bytes(raw)
        row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.item['result']['artifacts'][0].update(size_bytes=len(raw), sha256=row['sha256'])
        self.publish(); report = validate_contract(self.exports / POINTER)
        self.assertTrue(any('SEISMIC_AXIS_CONFLICT' in a['reason_codes'] for a in report['assessments']))

    def test_inventory_only_virtual_definition_never_becomes_a_cube(self):
        self.fixture(); self.inventory['native_objects'].append(dict(object_id='virtual-one', virtual=True, category='SeismicSubject'))
        self.publish(); key = next(x['entity_key'] for x in self.table('entities') if x['source_id'] == 'virtual-one')
        report = validate_contract(self.exports / POINTER)
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD', next(a for a in report['assessments'] if a['entity_key'] == key)['reason_codes'])

    def test_wrong_source_receipt_not_adopted_even_if_metadata_hashes_match(self):
        self.fixture();self.item['result']['source']='Z:/other/original.zgy';self.publish()
        self.assertTrue(any(g['code']=='SEISMIC_SOURCE_BINDING_CONFLICT' for g in self.table('gaps')))
        self.assertFalse(any(e['field']=='seismic_conversion' for e in self.table('metadata_evidence')))
        report=validate_contract(self.exports/POINTER)
        self.assertTrue(all('SEISMIC_METADATA_UNBOUND' in a['reason_codes'] for a in report['assessments'] if a['category']=='seismic'))


if __name__ == '__main__':
    unittest.main()
