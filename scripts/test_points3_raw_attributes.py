# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded Points3 raw attributes remain auxiliary to independent geometry."""
import json
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import export_petrel_native_spatial_zero_gui as spatial
import geoviewer_delivery as delivery
from geoviewer_contract import POINTER, publish_contract
from validate_geoviewer_contract import validate_contract
import test_native_geometry_attributes as attr_fixture
import test_petrel_native_recovery as fixture
import test_spatial_missing_vertex_evidence as helper


def points_doc(points, vectors):
    return fixture.element('data', attrs={'Type':'Points3','Version':[1,2,0,1,1],
        'xmlns':fixture.r.NAMESPACE}, children=[
        fixture.element('user_data',attrs={'Size':0}),
        fixture.element('vertices',attrs={'Size':len(points)},children=[
            fixture.array('double',[v for point in points for v in point],'<f8')] if points else []),
        fixture.element('has_attr',True), attr_fixture.collection(vectors),
        fixture.element('has_object_ids',False)])


class PointsRawAttributeTests(unittest.TestCase):
    run_case = helper.MissingVertexEvidenceTests.run_case
    rows = helper.MissingVertexEvidenceTests.rows

    def test_receipt_hash_streams_without_path_read_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native_point_attributes.csv'
            content=b'value\n'+b'x'*(2*1024*1024+13)
            path.write_bytes(content)
            with patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded CSV read')):
                self.assertEqual(spatial.bounded_file_sha256(path),hashlib.sha256(content).hexdigest())

    def test_mixed_values_missing_geometry_and_final_checkpoint_delivery(self):
        nan = struct.unpack('<d', bytes.fromhex('210000000000f87f'))[0]
        points=[(1.,2.,3.),(helper.M,4.,5.)]
        doc=points_doc(points,[attr_fixture.vector(4,[nan,-0.],b'\x01'*8),
            attr_fixture.vector(5,['','a,\n"'],b'\x02'*8),
            attr_fixture.vector(1,[True,False],b'\x03'*8)])
        root,p,r,_=self.run_case('Points3',doc)
        item=r['objects'][0]
        self.assertEqual(item['status'],'decoded')
        self.assertEqual(item['attributes_status'],'raw_typed_values_decoded')
        self.assertEqual(item['attribute_value_count'],6)
        self.assertEqual(item['missing_vertex_slots'],1)
        rows=self.rows(p/'05_spatial/points/native_point_attributes.csv')
        self.assertEqual(len(rows),6)
        self.assertEqual(rows[0]['raw_ieee754_le'],'210000000000f87f')
        self.assertEqual(json.loads(rows[0]['value_json']),None)
        self.assertEqual(rows[1]['raw_ieee754_le'],struct.pack('<d',-0.).hex())
        self.assertEqual(rows[0]['native_default_hex'],'01'*8)
        self.assertEqual(json.loads(rows[3]['value_json']),'a,\n"')
        self.assertIn('point_binding_unresolved',rows[0]['association_status'])
        self.assertNotIn('attribute_binding_status',rows[0])
        for checkpoint in (False,True):
            with self.subTest(checkpoint=checkpoint):
                ex=root/('CHECKPOINT' if checkpoint else 'FINAL')
                index=delivery.publish_exports(p,{'objects':[]},ex,spatial_checkpoint=checkpoint)
                detail=next(x for x in index['files'] if x['category']=='point_attributes')
                self.assertEqual(detail['record_count'],6)
                self.assertEqual(self.rows(ex/detail['path']),rows)
                if not checkpoint:
                    pointer=publish_contract(p,ex,index)
                    result=validate_contract(ex/POINTER)
                    self.assertEqual(result['counts']['qualified_for_planned_mapping'],0)
                    manifest=json.loads((ex/pointer['manifest']).read_text())
                    artifacts=[json.loads(line) for line in
                        ((ex/pointer['manifest']).parent/manifest['tables']['artifacts']['path']).read_text().splitlines()]
                    self.assertIn('native_attribute_values',{a['role'] for a in artifacts})

    def test_bad_later_vector_and_count_keep_geometry_without_partial_values(self):
        for vectors in ([attr_fixture.vector(4,[1.]),attr_fixture.vector(5,['x'],version=6)],
                        [attr_fixture.vector(4,[1.,2.])]):
            with self.subTest(vectors=vectors):
                root,p,r,_=self.run_case('Points3',points_doc([(1.,2.,3.)],vectors))
                self.assertEqual(r['objects'][0]['status'],'decoded')
                self.assertEqual(r['objects'][0]['attributes_status'],'not_exported')
                self.assertTrue(r['objects'][0]['attribute_failure'])
                self.assertEqual(r['point_attribute_value_rows'],0)
                self.assertFalse((p/'05_spatial/points/native_point_attributes.csv').exists())

    def test_budget_rejection_keeps_geometry_and_zero_vectors(self):
        vectors=[]
        points,metadata=spatial.decode_points3(fixture.frame(points_doc([(1.,2.,3.)],
            [attr_fixture.vector(4,[1.]),attr_fixture.vector(1,[True])])),
            attribute_vectors=vectors,attribute_value_budget=1)
        self.assertEqual(points,[(1.,2.,3.)])
        self.assertEqual(vectors,[])
        self.assertEqual(metadata['attributes_status'],'not_exported')
        self.assertIn('bound',metadata['attribute_failure'])

    def test_numeric_shape_is_not_coerced_and_bad_geometry_leaks_no_values(self):
        # A scalar child is outside the observed packed numeric-array profile.
        malformed=fixture.element('type_vector',attrs={'Version':5},children=[
            fixture.element('type',4),
            fixture.element('doubles',attrs={'Size':1},children=[fixture.element('double',1.)]),
            fixture.element('default',b'\x00'*8,attrs={'Version':1})])
        root,p,r,_=self.run_case('Points3',points_doc([(1.,2.,3.)],[malformed]))
        self.assertEqual(r['objects'][0]['status'],'decoded')
        self.assertEqual(r['objects'][0]['attributes_status'],'not_exported')
        self.assertFalse((p/'05_spatial/points/native_point_attributes.csv').exists())
        root,p,r,_=self.run_case('Points3',points_doc([(1.,float('nan'),3.)],
            [attr_fixture.vector(4,[1.])]))
        self.assertEqual(r['objects'][0]['status'],'failed_closed')
        self.assertEqual(r['point_attribute_value_rows'],0)
        self.assertFalse((p/'05_spatial/points/native_point_attributes.csv').exists())

    def test_empty_geometry_detail_is_not_qualified_or_primary(self):
        root,p,r,_=self.run_case('Points3',points_doc([(helper.M,2.,3.)],
            [attr_fixture.vector(5,['raw'])]))
        self.assertEqual(r['objects'][0]['status'],'empty_supported_object')
        self.assertEqual(r['objects'][0]['attributes_status'],'raw_typed_values_decoded')
        self.assertEqual(r['point_attribute_value_rows'],1)
        ex=root/'FINAL';index=delivery.publish_exports(p,{'objects':[]},ex)
        self.assertEqual({x['category'] for x in index['files']},
                         {'point_attributes','point_missing_vertices'})
        publish_contract(p,ex,index)
        result=validate_contract(ex/POINTER)
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',result['assessments'][0]['reason_codes'])

    def test_stale_or_mismatched_detail_is_excluded_in_final_and_checkpoint(self):
        root,p,r,rp=self.run_case('Points3',points_doc([(1.,2.,3.)],[attr_fixture.vector(4,[2.])]))
        r['objects'][0]['attributes_status']='not_exported'
        rp.write_text(json.dumps(r))
        for checkpoint in (False,True):
            index=delivery.publish_exports(p,{'objects':[]},root/('badfinal' if not checkpoint else 'badcheckpoint'),
                                           spatial_checkpoint=checkpoint)
            self.assertNotIn('point_attributes',{x['category'] for x in index['files']})
            self.assertIn('points',{x['category'] for x in index['files']})
        r['objects'][0]['attributes_status']='raw_typed_values_decoded'
        r['point_attribute_value_rows']=2
        rp.write_text(json.dumps(r))
        index=delivery.publish_exports(p,{'objects':[]},root/'badcount')
        self.assertNotIn('point_attributes',{x['category'] for x in index['files']})
        r['point_attribute_value_rows']=1
        rp.write_text(json.dumps(r))
        table=p/'05_spatial/points/native_point_attributes.csv'
        table.write_text(table.read_text().replace('2.0','3.0'))
        index=delivery.publish_exports(p,{'objects':[]},root/'badhash',spatial_checkpoint=True)
        self.assertNotIn('point_attributes',{x['category'] for x in index['files']})


if __name__=='__main__':unittest.main()
