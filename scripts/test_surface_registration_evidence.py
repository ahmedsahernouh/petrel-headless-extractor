# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Exact native lattice evidence and current delivery gates, without original projects."""
from copy import deepcopy
import math
import unittest

import test_geoviewer_contract as contract_tests
from geoviewer_contract import POINTER
from geoviewer_surface_registration import derive_surface_evidence, METHOD, PROFILES, RECEIPT
from validate_geoviewer_contract import validate_contract

PARENT = 'fc88cfe8-9d89-4a6e-900d-09fa3fa20ed2'
PROP = '00c2f435-4a99-4c3a-95f5-2ab6676d4e74'
OTHER = '011d5b27-7f46-4894-b9ad-8db0b95a9645'


def grid(tag=PARENT, prop=False, angle=0.0):
    c, s = math.cos(angle), math.sin(angle)
    item = dict(object_id=tag, name='Same name is not identity', subject_type='SurfaceAttrSubject' if prop else 'SurfaceSubject',
                blob_type='RegValGrid2', object_version=list(PROFILES[0]), status='missing_metadata',
                numeric_round_trip='exact', dataset_exported=True, cell_mask_preserved=True,
                node_size=[3, 2], node_count=6, defined_nodes=5, origin=[100., 200.],
                increments=[25., 50.], maximum=[150., 250.], rotation_radians=angle,
                i_basis=[25*c, 25*s], j_basis=[-50*s, 50*c], ordering='i/x fastest, then j/y')
    if prop:
        item.update(ancestry=[dict(object_id=tag, parent=PARENT)], defined_nodes=4)
    return item


def report(angle=0.0):
    return dict(source_unchanged=True, objects=[grid(angle=angle), grid(PROP, True, angle)])


def node_row(doc):
    return next(r for r in derive_surface_evidence(doc, 'revision') if r['entity_key'].endswith(PROP) and r['field'] == 'node_registration')


class SurfaceEvidenceTests(unittest.TestCase):
    @staticmethod
    def large_doc(dims=(4087, 7427), chunked=True):
        doc = report()
        for item in doc['objects']:
            item.update(node_size=list(dims), node_count=math.prod(dims),
                        maximum=[o+(n-1)*d for o,n,d in zip(item['origin'],dims,item['increments'])])
            if chunked:
                item['coordinate_storage_profile'] = 'packed_regular_chunked/1'
        return doc

    def test_large_recorded_chunked_profile_preserves_full_integer_lattice(self):
        for dims in ((4087, 7427), (6400, 5000)):
            doc = self.large_doc(dims)
            rows = derive_surface_evidence(doc, 'revision')
            grids = [r['raw_value'] for r in rows if r['field']=='grid_registration']
            self.assertEqual(len(grids),2)
            self.assertEqual(grids[0]['dimensions'],list(dims))
            self.assertEqual(grids[0],grids[1])
            self.assertTrue(node_row(doc)['raw_value']['validated'])
            self.assertEqual(node_row(doc)['raw_value']['count'],math.prod(dims))
            self.assertEqual(node_row(doc)['raw_value']['definition_policy'],'require_both_node_masks')

    def test_large_v0_requires_its_own_resource_profile(self):
        doc=self.large_doc()
        for item in doc['objects']:
            item['object_version']=list(PROFILES[1])
            item['coordinate_storage_profile']='packed_regular_v0_chunked/1'
        self.assertTrue(node_row(doc)['raw_value']['validated'])
        self.assertEqual(node_row(doc)['raw_value']['count'],30354149)
        for item in doc['objects']:item['object_version']=list(PROFILES[0])
        self.assertFalse(node_row(doc)['raw_value']['validated'])

    def test_large_profile_requires_exact_version_name_and_resource_bounds(self):
        for mode in ('absent','unknown','legacy','too_many','axis','failed_numeric','partial'):
            with self.subTest(mode=mode):
                doc = self.large_doc()
                for item in doc['objects']:
                    if mode=='absent':item.pop('coordinate_storage_profile')
                    if mode=='unknown':item['coordinate_storage_profile']='packed_regular_chunked/2'
                    if mode=='legacy':item['object_version']=list(PROFILES[1])
                    if mode=='failed_numeric':item['numeric_round_trip']='failed'
                    if mode=='partial':item['dataset_exported']=False
                if mode=='too_many':doc=self.large_doc((6400,5001))
                if mode=='axis':doc=self.large_doc((65537,160))
                self.assertFalse(node_row(doc)['raw_value']['validated'])
                self.assertFalse(any(r['field']=='grid_registration' for r in derive_surface_evidence(doc,'revision')))

    def test_ordinary_bound_is_unchanged_and_resource_constants_match_decoder(self):
        import geoviewer_surface_registration as registration
        import petrel_regular_grid_stream as stream
        import petrel_native_recovery as recovery
        self.assertEqual(registration.CHUNKED_MAX_NODES,stream.MAX_NODES)
        self.assertEqual(registration.CHUNKED_MAX_AXIS,stream.MAX_AXIS)
        self.assertEqual(registration.ORDINARY_MAX_NODES,recovery.MAX_SURFACE_NODES)
        self.assertTrue(node_row(self.large_doc((4000,2500),False))['raw_value']['validated'])
        self.assertFalse(node_row(self.large_doc((4000,2501),False))['raw_value']['validated'])

    def test_exact_parent_and_rotation_formula_with_separate_masks(self):
        for angle in (0.0, 0.67, -1.0):
            with self.subTest(angle=angle):
                rows = derive_surface_evidence(report(angle), 'revision')
                joined = node_row(report(angle))
                value = joined['raw_value']
                self.assertTrue(value['validated'])
                self.assertEqual(value['parent_entity_key'], 'revision:native:'+PARENT)
                self.assertEqual((value['count'], value['property_defined_nodes'], value['parent_defined_nodes']), (6, 4, 5))
                self.assertEqual(value['definition_policy'], 'require_both_node_masks')
                self.assertEqual(value['method'], METHOD)
                self.assertEqual(joined['dependencies'], [RECEIPT+'#/objects/0', RECEIPT+'#/objects/1'])
                self.assertEqual({r['field'] for r in rows}, {'node_registration', 'grid_registration'})
                self.assertTrue(all('historical' in (r['scope'].lower()) for r in rows))

    def test_direct_and_ancestry_parent_must_agree(self):
        doc = report(); doc['objects'][1]['parent_id'] = PARENT
        self.assertTrue(node_row(doc)['raw_value']['validated'])
        doc['objects'][1]['parent_id'] = OTHER
        self.assertFalse(node_row(doc)['raw_value']['validated'])

    def test_missing_or_wrong_identity_never_inferred_from_equal_names_bounds(self):
        for update in ({'ancestry': []}, {'ancestry': [{'object_id': OTHER, 'parent': PARENT}]},
                       {'ancestry': [{'object_id': PROP, 'parent': OTHER}]}, {'parent_id': 'not-a-uuid'}):
            with self.subTest(update=update):
                doc = report(); doc['objects'][1].update(update)
                self.assertFalse(node_row(doc)['raw_value']['validated'])

    def test_parent_type_duplicate_or_failed_numeric_blocks(self):
        for mode in ('well', 'property', 'duplicate', 'numeric', 'preview'):
            with self.subTest(mode=mode):
                doc = report()
                if mode == 'well': doc['objects'][0]['subject_type'] = 'WellSubject'
                if mode == 'property': doc['objects'][0]['subject_type'] = 'SurfaceAttrSubject'
                if mode == 'duplicate': doc['objects'].append(deepcopy(doc['objects'][0]))
                if mode == 'numeric': doc['objects'][0]['numeric_round_trip'] = 'failed'
                if mode == 'preview': doc['objects'][0]['dataset_exported'] = False
                self.assertFalse(node_row(doc)['raw_value']['validated'])

    def test_lattice_difference_blocks_even_when_each_lattice_valid(self):
        for mode in ('shift', 'rotate', 'dimensions'):
            with self.subTest(mode=mode):
                doc = report(); item = doc['objects'][1]
                if mode == 'shift': item.update(origin=[101., 200.], maximum=[151., 250.])
                if mode == 'rotate': doc['objects'][1] = grid(PROP, True, .25)
                if mode == 'dimensions': item.update(node_size=[2, 3], maximum=[125., 300.])
                self.assertFalse(node_row(doc)['raw_value']['validated'])
                self.assertIn('lattices differ', node_row(doc)['raw_value']['reason'])

    def test_invalid_numeric_descriptors_cannot_qualify(self):
        cases = [('node_size', [True, 6]), ('node_size', [0, 6]), ('node_size', [10_000_001, 1]),
                 ('node_count', True), ('node_count', 7), ('defined_nodes', True), ('defined_nodes', 7),
                 ('origin', [float('nan'), 200.]), ('origin', [float('inf'), 200.]),
                 ('increments', [0., 50.]), ('rotation_radians', True), ('rotation_radians', 9.),
                 ('i_basis', [25., 1.]), ('maximum', [151., 250.]), ('ordering', 'j first'),
                 ('object_version', [True]+list(PROFILES[0][1:])), ('object_version', [2]*11),
                 ('cell_mask_preserved', False), ('dataset_exported', False), ('status', 'failed')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                doc = report(); doc['objects'][1][field] = value
                self.assertFalse(node_row(doc)['raw_value']['validated'])

    def test_source_integrity_required_and_duplicate_property_not_merged(self):
        for value in (None, False, 1):
            doc = report(); doc['source_unchanged'] = value
            self.assertFalse(node_row(doc)['raw_value']['validated'])
        doc = report(); doc['objects'].append(deepcopy(doc['objects'][1]))
        self.assertFalse(any(r['entity_key'].endswith(PROP) for r in derive_surface_evidence(doc, 'revision')))

    def test_native_profile_variants_and_ordinal_locator_preserved(self):
        doc = report(); doc['objects'][0]['object_version'] = list(PROFILES[1])
        doc['objects'].insert(0, None)
        self.assertEqual(node_row(doc)['locator'], RECEIPT+'#/objects/2')
        self.assertTrue(node_row(doc)['raw_value']['validated'])


class SurfaceContractIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fx = contract_tests.ContractTests(); self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.native = report()
        self.fx.context['objects'] = [dict(object_id=PARENT, category='SurfaceSubject'),
                                      dict(object_id=PROP, category='SurfaceAttrSubject', parent_id=PARENT)]
        for row in self.fx.rows:
            row['object_id'] = {'grid-a': PARENT, 'grid-b': PROP}[row['object_id']]
        self.fx.add_row(PROP, 'grids/property_node_definitions.csv', b'i,j,defined\n', 'CSV')
        contract_tests.write(self.fx.package/'01_project_metadata/project_context.json', self.fx.context)
        contract_tests.write(self.fx.package/RECEIPT, self.fx.native)
        self.fx.publish()

    def assessment(self, profile='source'):
        return next(x for x in validate_contract(self.fx.exports/POINTER, profile)['assessments']
                    if x['category'] == 'surface_property')

    def test_large_registration_reaches_contract_without_qualifying_unknown_units(self):
        self.fx.native = SurfaceEvidenceTests.large_doc()
        contract_tests.write(self.fx.package/RECEIPT,self.fx.native)
        self.fx.publish()
        rows = self.fx.table('metadata_evidence')
        self.assertEqual(len([x for x in rows if x['field']=='grid_registration']),2)
        joined = next(x for x in rows if x['field']=='node_registration')
        self.assertEqual(joined['raw_value']['count'],30_354_149)
        for profile in ('opendtect','paleoscan'):
            assessment=self.assessment(profile)
            self.assertNotIn('NODE_REGISTRATION_UNVERIFIED',assessment['reason_codes'])
            self.assertNotEqual(assessment['receiving_state'],'qualified_for_planned_mapping')

    def test_derived_registration_removes_only_proven_blocker(self):
        a = self.assessment()
        self.assertNotIn('NODE_REGISTRATION_UNVERIFIED', a['reason_codes'])
        self.assertNotIn('NODE_MASK_UNAVAILABLE', a['reason_codes'])
        self.assertIn('UNRESOLVED_CRS', a['reason_codes'])
        row = next(x for x in self.fx.table('metadata_evidence') if x['field'] == 'node_registration')
        self.assertTrue(row['raw_value']['validated'])
        for profile in ('paleoscan', 'opendtect'):
            self.assertNotEqual(self.assessment(profile)['receiving_state'], 'qualified_for_planned_mapping')

    def test_both_masks_required_and_historical_evidence_preserved(self):
        for path in ('grids/node_definitions.csv', 'grids/property_node_definitions.csv'):
            with self.subTest(path=path):
                target = self.fx.exports/path; original = target.read_bytes(); target.unlink()
                try:
                    a = self.assessment('paleoscan')
                    self.assertIn('NODE_MASK_UNAVAILABLE', a['reason_codes'])
                    self.assertNotIn('NODE_REGISTRATION_UNVERIFIED', a['reason_codes'])
                    self.assertNotEqual(a['receiving_state'], 'qualified_for_planned_mapping')
                    self.assertTrue(next(x for x in self.fx.table('metadata_evidence') if x['field'] == 'node_registration')['raw_value']['validated'])
                finally: target.write_bytes(original)

    def test_partial_mask_is_not_accepted_delivery(self):
        row = self.fx.rows.pop()
        self.fx.index['partial_files'].append(row)
        self.fx.publish()
        self.assertIn('NODE_MASK_UNAVAILABLE', self.assessment()['reason_codes'])

    def test_legacy_receipt_name_uses_actual_evidence_locator(self):
        original = self.fx.package/RECEIPT
        alternate = original.with_name('native_recovery.json')
        original.rename(alternate)
        self.fx.publish()
        rows = [x for x in self.fx.table('metadata_evidence') if x.get('resolution') == 'derived_from_bound_native_receipt']
        self.assertTrue(rows)
        self.assertTrue(all('native_recovery.json#/objects/' in x['locator'] for x in rows))
        self.assertTrue(all('native_recovery.json#/objects/' in path for x in rows for path in x.get('dependencies', [])))
        self.assertNotIn('NODE_REGISTRATION_UNVERIFIED', self.assessment()['reason_codes'])

    def test_conflicting_context_parent_is_not_silently_replaced(self):
        self.fx.context['objects'][1]['parent_id'] = OTHER
        contract_tests.write(self.fx.package/'01_project_metadata/project_context.json', self.fx.context)
        self.fx.publish()
        self.assertIn('NODE_REGISTRATION_UNVERIFIED', self.assessment()['reason_codes'])


if __name__ == '__main__':
    unittest.main()
