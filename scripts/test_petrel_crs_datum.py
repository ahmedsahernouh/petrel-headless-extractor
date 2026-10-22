# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Focused synthetic controls for stored Model.ptd physical declarations."""
import json
import unittest
from unittest.mock import patch
import numpy as np

import petrel_native_binary as native
import petrel_crs_datum as subject
import petrel_native_well_evidence as wells


def node(name, value=None, children=(), attrs=None):
    return native.Node(name=name, attrs=attrs or {}, children=list(children),
                       content=[] if value is None else [value])


XML = ('<?xml version="1.0"?><IEarlyBoundCoordinateReferenceSystem name="Early">'
       '<AuthorityCode>SIS,1</AuthorityCode>'
       '<ILateBoundCoordinateReferenceSystem name="Late"><AuthorityCode>EPSG,2</AuthorityCode>'
       '</ILateBoundCoordinateReferenceSystem><ISimpleTransform name="T">'
       '<AuthorityCode>SIS,3</AuthorityCode><WKT>GEOGTRAN["full",1]</WKT>'
       '</ISimpleTransform></IEarlyBoundCoordinateReferenceSystem>')


def project(projection='ESRI,PE_10_7_1: ' + XML, tag=0, used=False):
    return node('ProjectSubject', attrs={'xmlns':'urn:synthetic', 'Version':[1, 2]},
        children=[node('unique_tag', 'project-id'),
        node('version_string', '2024.5.0'), node('style', children=[
            node('units_style', children=[node('is_used', used), node('reference_datum', tag)]),
            node('projection', projection)])])


def datum(tag=0, elevation=0, velocity=subject.FLOAT32_MAX, uid='datum-id'):
    values = dict(unique_tag=uid, name='SRD', kid_tag=tag, datumInSI=elevation,
                  isPredefined=True, isWellSpecific=False, isTimeSpecific=True,
                  hasVelocity=False, velocityInSI=velocity)
    return node('DatumSubject', children=[node(k, v) for k, v in values.items()])


class TestCRSDatum(unittest.TestCase):
    def test_authority_only_transform_is_not_missing_crs(self):
        raw=XML.replace(' name="T"','').replace('<WKT>GEOGTRAN["full",1]</WKT>','')
        result=subject.inspect_documents([project('ESRI,PE_10_3_1: '+raw),datum()])
        p=result['project_records'][0]['projection']
        self.assertEqual(p['late_bound']['authority'],'EPSG,2')
        self.assertEqual(p['transform']['authority'],'SIS,3')
        self.assertIsNone(p['transform']['name'])
        self.assertIsNone(p['transform']['wkt'])
        self.assertFalse(p['transform']['wkt_present'])

    def test_projection_uuid_remains_reference_not_crs(self):
        for raw,state in [('00000000-0000-0000-0000-000000000000','serialized_null_reference'),
                          ('f58aba3f-82b2-481a-9926-dd5374bf75cf','serialized_reference_requires_resolution')]:
            p=subject.inspect_documents([project(raw),datum()])['project_records'][0]['projection']
            self.assertEqual(p['declaration_state'],state)
            self.assertNotIn('late_bound',p)

    def test_legacy_esri_wrapper_preserves_crs_and_transform_roles(self):
        result=subject.inspect_documents([project('ESRI: '+XML),datum()])
        p=result['project_records'][0]['projection']
        self.assertEqual(p['prefix'],'ESRI')
        self.assertEqual(p['late_bound']['authority'],'EPSG,2')
        self.assertEqual(p['transform']['authority'],'SIS,3')

    def test_older_well_reference_fields_do_not_require_head_profile(self):
        ref=lambda kind:node(kind,children=[node('name','KB'),node('offset',1147),node('description','Kelly bushing')])
        well=node('WellTraceSubject',attrs={'xmlns':wells.NAMESPACE,'Version':[23,2,13,1,2,19,0,0,1]},children=[
            node('unique_tag','1726f5ef-65d9-47cb-978a-454dd8b13742'),node('name','Y5-32'),
            ref('working_reference_level'),node('reference_levels',attrs={'Size':1},children=[ref('item')])])
        result=subject.inspect_documents([project(),datum(),well])
        row=result['well_reference_records'][0]
        self.assertEqual(row['working_reference_level']['offset'],1147)
        self.assertEqual(row['working_reference_level']['offset_unit_status'],'unresolved')
        self.assertEqual(row['metadata_status'],'source_recorded_reference_fields_only')
        self.assertNotIn('head_xy',row);self.assertNotIn('definite_provider_id',row)
        self.assertEqual(result['counts']['reference_field_only_records'],1)
        well.child('reference_levels').attrs['Size']=2
        result=subject.inspect_documents([project(),datum(),well])
        self.assertFalse(result['well_reference_records'])

    def test_legacy_datum_names_preserved_without_si_assumption(self):
        n=datum()
        n.child('datumInSI').name='datum';n.child('velocityInSI').name='velocity'
        result=subject.inspect_documents([project(),n]);row=result['datum_records'][0]
        self.assertEqual(row['datum'],0);self.assertTrue(row['datum_usable'])
        self.assertFalse(row['velocity_usable'])
        self.assertEqual(row['value_units_status'],'unresolved_legacy_field_units')
        self.assertNotIn('datumInSI',row)
        n.children.append(node('datumInSI',0))
        result=subject.inspect_documents([project(),n])
        self.assertFalse(result['datum_records'])
        self.assertTrue(any('ambiguous' in g['reason'] for g in result['gaps']))

    def test_declared_crs_and_zero_datum_stay_unqualified(self):
        result = subject.inspect_documents(iter([project(), datum()]))
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(result['saved_version'], '2024.5.0')
        projection = result['project_records'][0]['projection']
        self.assertEqual(projection['raw'], 'ESRI,PE_10_7_1: ' + XML)
        self.assertEqual(projection['early_bound']['authority'], 'SIS,1')
        self.assertEqual(projection['late_bound']['authority'], 'EPSG,2')
        self.assertEqual(projection['transform']['authority'], 'SIS,3')
        self.assertEqual(projection['transform']['wkt'], 'GEOGTRAN["full",1]')
        self.assertIs(result['coordinate_binding_verified'], False)
        self.assertIs(result['project_records'][0]['units_style']['is_used'], False)
        candidate = result['project_records'][0]['reference_datum_tag_candidate']
        self.assertTrue(candidate['unique_match'])
        self.assertIs(candidate['selection_verified'], False)
        self.assertEqual(result['datum_records'][0]['datumInSI'], 0)
        self.assertTrue(result['datum_records'][0]['datumInSI_usable'])
        self.assertFalse(result['datum_records'][0]['velocityInSI_usable'])
        json.dumps(result, allow_nan=False)

    def test_float_max_and_duplicate_datum_tag(self):
        result = subject.inspect_documents([project(), datum(elevation=subject.FLOAT32_MAX),
                                            datum(uid='another')])
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['datum_records'][0]['datumInSI_usable'])
        self.assertEqual(result['datum_records'][0]['datumInSI'], subject.FLOAT32_MAX)
        self.assertFalse(result['project_records'][0]['reference_datum_tag_candidate']['unique_match'])
        self.assertTrue(any('match_count=2' in gap['reason'] for gap in result['gaps']))

    def test_conflicting_projects_and_missing_path(self):
        result = subject.inspect_documents([project(), project(tag=5), datum()])
        self.assertIsNone(result['saved_version'])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(any('expected_one_valid' in gap['reason'] for gap in result['gaps']))
        missing = node('ProjectSubject', children=[node('version_string', '2024.5.0'),
                                                   node('style', children=[node('projection',
                                                   'ESRI,PE_10_7_1: ' + XML)])])
        result = subject.inspect_documents([missing])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['saved_version'], '2024.5.0')
        self.assertEqual(result['project_records'][0]['projection']['late_bound']['authority'], 'EPSG,2')
        self.assertTrue(any('units_style' in gap['reason'] for gap in result['gaps']))

    def test_projection_rejects_ambiguous_malformed_and_entity_xml(self):
        for xml in ('<IEarlyBoundCoordinateReferenceSystem>',
                    '<!DOCTYPE x [<!ENTITY e "expanded">]>' + XML,
                    XML.replace('<AuthorityCode>SIS,1</AuthorityCode>',
                                '<AuthorityCode>SIS,1</AuthorityCode><AuthorityCode>SIS,2</AuthorityCode>')):
            with self.subTest(xml=xml[:30]):
                result = subject.inspect_documents([project('ESRI,PE_10_7_1: ' + xml)])
                self.assertEqual(len(result['project_records']), 1)
                self.assertNotIn('projection', result['project_records'][0])
                self.assertTrue(result['gaps'])
        result = subject.inspect_documents([project('ESRI,PE_10_7_1: ' + XML + 'x')])
        self.assertNotIn('projection', result['project_records'][0])

    def test_projection_oversize_is_gap_without_truncation(self):
        result = subject.inspect_documents([project('ESRI,PE_10_7_1: ' + 'x' *
                                                    subject.MAX_PROJECTION_BYTES)])
        self.assertNotIn('projection', result['project_records'][0])
        self.assertTrue(any('2 MiB' in gap['reason'] for gap in result['gaps']))

    def test_project_fields_are_independent_and_profiles_raw(self):
        no_units = node('ProjectSubject', attrs={'xmlns': 'urn:other', 'Version': [9, 8]},
            children=[node('version_string', '2020.1'),
                      node('style', children=[node('projection', 'ESRI,PE_11_0_0: ' + XML)])])
        result = subject.inspect_documents([no_units])
        record = result['project_records'][0]
        self.assertEqual(result['saved_version'], '2020.1')
        self.assertEqual(record['projection']['prefix'], 'ESRI,PE_11_0_0')
        self.assertEqual(record['projection']['late_bound']['authority'], 'EPSG,2')
        self.assertEqual(record['source_record_profile'], {'xmlns': 'urn:other', 'Version': [9, 8]})
        self.assertNotIn('reference_datum_tag_candidate', record)
        self.assertTrue(any('units_style' in gap['locator'] for gap in result['gaps']))

        no_projection = node('ProjectSubject', children=[node('version_string', '2018.2'),
            node('style', children=[node('units_style', children=[
                node('is_used', False), node('reference_datum', 0)])])])
        result = subject.inspect_documents([no_projection, datum()])
        self.assertEqual(result['saved_version'], '2018.2')
        self.assertNotIn('projection', result['project_records'][0])
        self.assertTrue(result['project_records'][0]['reference_datum_tag_candidate']['unique_match'])

    def test_direct_late_crs_optional_transform_and_conflicts(self):
        late_xml = ('<ILateBoundCoordinateReferenceSystem name="Late">'
                    '<AuthorityCode>EPSG,9</AuthorityCode></ILateBoundCoordinateReferenceSystem>')
        result = subject.inspect_documents([project('ESRI,PE_9_3_0: ' + late_xml), datum()])
        projection = result['project_records'][0]['projection']
        self.assertEqual(projection['late_bound']['authority'], 'EPSG,9')
        self.assertNotIn('early_bound', projection)
        self.assertNotIn('transform', projection)
        self.assertEqual(projection['authority_records'],
                         [{'role': 'late_bound_crs', 'authority': 'EPSG,9'}])
        self.assertEqual(result['status'], 'parsed')
        for xml in (late_xml.replace('</ILateBoundCoordinateReferenceSystem>',
                     '<ILateBoundCoordinateReferenceSystem name="Other"/></ILateBoundCoordinateReferenceSystem>'),
                    XML.replace('</IEarlyBoundCoordinateReferenceSystem>',
                     '<ISimpleTransform name="Other"/></IEarlyBoundCoordinateReferenceSystem>'),
                    '<Unknown name="Other"><AuthorityCode>EPSG,9</AuthorityCode></Unknown>'):
            with self.subTest(xml=xml[-70:]):
                bad = subject.inspect_documents([project('ESRI,PE_9_3_0: ' + xml), datum()])
                self.assertNotIn('projection', bad['project_records'][0])
                self.assertEqual(bad['status'], 'partial')

    def test_negative_float_max_sentinel_unusable(self):
        result = subject.inspect_documents([project(), datum(elevation=-subject.FLOAT32_MAX,
                                                             velocity=-subject.FLOAT32_MAX)])
        row = result['datum_records'][0]
        self.assertEqual(row['datumInSI'], -subject.FLOAT32_MAX)
        self.assertFalse(row['datumInSI_usable'])
        self.assertFalse(row['velocityInSI_usable'])

    def test_partial_stream_keeps_prior_typed_records(self):
        def broken():
            yield project()
            yield datum()
            raise native.NativeError('synthetic tail failure')
        result = subject.inspect_documents(broken())
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['counts']['documents_seen'], 2)
        self.assertFalse(result['counts']['stream_complete'])
        self.assertEqual(len(result['datum_records']), 1)

    def test_inspect_model_uses_native_decompress_and_iterator(self):
        # An unrelated framed root has an intentionally unsupported opaque body;
        # a metadata audit must not imply it validated or decoded that body.
        opaque=b'\x40\x05Other\xff'
        with patch.object(subject.native, 'decompress', return_value=b'expanded') as dec, \
             patch.object(subject.native, 'documents', return_value=iter([([],opaque)])) as read:
            result = subject.inspect_model(b'exact-model')
        dec.assert_called_once_with(b'exact-model')
        read.assert_called_once_with(b'expanded')
        self.assertEqual(result['counts']['documents_seen'],1)
        self.assertTrue(result['counts']['stream_complete'])
        self.assertIn('opaque and unvalidated',result['body_validation_scope'])

    def test_relevant_document_body_is_still_fully_parsed(self):
        malformed=b'\x40\x0eProjectSubject\xff'
        with patch.object(subject.native,'decompress',return_value=b'expanded'), \
             patch.object(subject.native,'documents',return_value=iter([([],malformed)])):
            result=subject.inspect_model(b'exact-model')
        self.assertFalse(result['counts']['stream_complete'])
        self.assertFalse(result['project_records'])

    def test_well_decoder_rejection_and_raw_reference_offsets(self):
        bad = node('WellTraceSubject', attrs={'xmlns': 'unknown', 'Version': []})
        result = subject.inspect_documents([project(), datum(), bad])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['counts']['WellTraceSubject_seen'], 1)
        self.assertEqual(result['well_reference_records'], [])
        self.assertTrue(any('unsupported_well_reference_fields' in gap['reason'] for gap in result['gaps']))

        ref = lambda kind: node(kind, children=[node('name', 'KB'), node('offset', 12.5),
                                                node('description', 'raw')])
        well = node('WellTraceSubject', attrs={'xmlns': wells.NAMESPACE,
                                            'Version': list(wells.WELL_PROFILE)}, children=[
            node('unique_tag', 'c236a48c-cb49-4691-b2a5-a50a10a9cf4c'),
            node('name', 'Well A'),
            node('definite_survey_provider_tag_', 'f4fb11e1-492d-46f8-9032-179da3aa6e85'),
            node('well_head_', children=[native.Array('double', np.array([10., 20.], dtype='<f8'))]),
            ref('working_reference_level'),
            node('reference_levels', attrs={'Size': 1}, children=[ref('item')])])
        result = subject.inspect_documents([project(), datum(), well])
        row = result['well_reference_records'][0]
        self.assertEqual(row['document_ordinal'], 2)
        self.assertEqual(row['locator'], 'Model.ptd#document=2/WellTraceSubject')
        self.assertEqual(row['definite_provider_id'], 'f4fb11e1-492d-46f8-9032-179da3aa6e85')
        self.assertEqual(row['working_reference_level']['offset'], 12.5)
        self.assertEqual(row['reference_levels'][0]['offset'], 12.5)
        self.assertEqual(result['status'], 'parsed')
        self.assertNotIn('offset_unit', row)


if __name__ == '__main__':
    unittest.main()
