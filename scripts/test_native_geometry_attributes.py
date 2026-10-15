# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Native typed attributes survive decoding, delivery and failure boundaries."""
import json
import struct
import unittest
from unittest.mock import patch

import numpy as np
import petrel_native_binary as binary
import petrel_native_geometry_attributes as attrs
import export_petrel_native_spatial_zero_gui as spatial
import geoviewer_delivery as delivery
import test_petrel_native_recovery as fixture
import test_spatial_missing_vertex_evidence as helper
from geoviewer_contract import POINTER, publish_contract
from validate_geoviewer_contract import validate_contract


def vector(kind, values, default=b'\x00'*8, version=5):
    collection, item, _ = attrs.TYPES[kind]
    children = ([fixture.element('item', v) for v in values] if kind == 5 else
                [fixture.array(item, values, {1:'u1', 2:'<i4', 4:'<f8'}[kind])] if len(values) else [])
    return fixture.element('type_vector', attrs={'Version':version}, children=[
        fixture.element('type', kind), fixture.element(collection, attrs={'Size':len(values)}, children=children),
        fixture.element('default', default, attrs={'Version':1})])


def collection(vectors):
    return fixture.element('attributes', attrs={'Size':len(vectors)}, children=vectors)


def node(vectors):
    return binary.NBFX(collection(vectors), []).parse()


def polygon(parts, vectors):
    old = fixture.element('attributes', children=[fixture.element('unresolved_property', 42.)])
    return fixture.polygon_doc(parts, attributes=True).replace(old, collection(vectors))


class GeometryAttributeTests(unittest.TestCase):
    run_case = helper.MissingVertexEvidenceTests.run_case
    rows = helper.MissingVertexEvidenceTests.rows

    def test_all_observed_types_and_defaults_are_preserved(self):
        vectors = attrs.decode_attribute_vectors(node([
            vector(1,[True,False]), vector(2,[-2147483648,2147483647]),
            vector(4,[-0.,3.4028234663852886e38],bytes.fromhex('000000e0ffffef47')),
            vector(5,['','a,\n\"ÃƒÆ’Ã…Â¸'])]), 2)
        rows=attrs.AttributeRows();rows.add(helper.OID,1,vectors)
        self.assertEqual(len(rows),8)
        self.assertEqual([r['value_json'] for r in rows[:4]],['true','false','-2147483648','2147483647'])
        self.assertEqual(rows[4]['raw_ieee754_le'],struct.pack('<d',-0.).hex())
        self.assertEqual(rows[5]['native_default_hex'],'000000e0ffffef47')
        self.assertEqual(json.loads(rows[-1]['value_json']),'a,\n\"ÃƒÆ’Ã…Â¸')
        self.assertEqual(rows[6]['value_json'],'""')
        self.assertNotIn('part_index', rows[0])
        self.assertEqual(vectors[2].descriptor()['default_application'],'not_inferred')

    def test_nonfinite_payload_bits_survive_without_invalid_json(self):
        value=struct.unpack('<d',bytes.fromhex('210000000000f87f'))[0]
        vectors=attrs.decode_attribute_vectors(node([vector(4,[value,float('inf')])]),2)
        self.assertEqual(vectors[0].row(0)['raw_ieee754_le'],'210000000000f87f')
        self.assertIsNone(json.loads(vectors[0].row(0)['value_json']))
        self.assertEqual(vectors[0].row(1)['value_state'],'nonfinite_raw_value')

    def test_profiles_and_duplicates_fail_closed(self):
        mutations=[lambda n:n.attrs.update(Size=True),
            lambda n:n.attrs.update(xmlns='unrecognized'),
            lambda n:n.children[0].attrs.update(Version=6),
            lambda n:n.children[0].attrs.update(xmlns='unrecognized'),
            lambda n:n.children[0].children.append(n.children[0].children[0]),
            lambda n:n.children[0].child('default').attrs.update(Version=2),
            lambda n:n.children[0].child('default').attrs.update(Version=True),
            lambda n:n.children[0].child('default').content.__setitem__(0,b'bad'),
            lambda n:n.children[0].child('type').content.__setitem__(0,99),
            lambda n:n.children[0].child('type').content.__setitem__(0,True),
            lambda n:n.children[0].child('ints').attrs.update(Size=3)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                n=node([vector(2,[1,2])]);mutate(n)
                with self.assertRaises(binary.NativeError):attrs.decode_attribute_vectors(n,2)

    def test_numeric_arrays_do_not_coerce_wrong_types(self):
        n=node([vector(2,[1,2])]);n.children[0].child('ints').children[0].values=np.array([1.,2.])
        with self.assertRaises(binary.NativeError):attrs.decode_attribute_vectors(n,2)
        n=node([vector(1,[1,0])]);n.children[0].child('bools').children[0].values=np.array([1,2],dtype='u1')
        with self.assertRaises(binary.NativeError):attrs.decode_attribute_vectors(n,2)

    def test_text_items_cannot_be_mixed_scalar_types(self):
        n=node([vector(5,['x'])]);n.children[0].child('strings').children[0].content=[1]
        with self.assertRaises(binary.NativeError):attrs.decode_attribute_vectors(n,1)

    def test_count_and_budget_checks_are_before_partial_acceptance(self):
        n=node([vector(2,[1,2]),vector(4,[1.,2.])])
        with self.assertRaises(binary.NativeError):attrs.decode_attribute_vectors(n,2,max_values=3)
        n.children[1].child('doubles').children[0].values=np.array([1.])
        with self.assertRaises(binary.NativeError):attrs.decode_attribute_vectors(n,2)
        rows=attrs.AttributeRows()
        with patch.object(attrs,'MAX_ATTRIBUTE_VALUES',1):
            with self.assertRaises(binary.NativeError):rows.add('oid',1,[attrs.AttributeVector(0,2,np.array([1,2]),b'0'*8)])
        self.assertEqual(len(rows),0)

    def test_array_in_a_scalar_field_is_a_scoped_profile_error(self):
        for target in ('type','ints','default'):
            n=node([vector(2,[1])]);v=n.children[0]
            v.children=[binary.Array(target,np.array([1])) if c.name==target else c for c in v.children]
            with self.subTest(field=target),self.assertRaises(binary.NativeError):
                attrs.decode_attribute_vectors(n,1)

    def test_empty_vector_preserves_its_descriptor_and_default(self):
        v=attrs.decode_attribute_vectors(node([vector(5,[],b'1'*8)]),0)[0]
        self.assertEqual(v.descriptor()['value_count'],0)
        self.assertEqual(v.descriptor()['native_default_hex'],(b'1'*8).hex())

    def test_delivery_keeps_values_separate_and_does_not_claim_semantics(self):
        root,p,r,_=self.run_case('Polygons3',polygon([[(1.,2.,3.)],[]],
            [vector(4,[1.,-0.]),vector(5,['','label'])]))
        self.assertEqual(r['polygon_attribute_value_rows'],4)
        self.assertEqual(r['objects'][0]['attributes_status'],'raw_typed_values_decoded')
        rows=self.rows(p/'05_spatial/polygons/native_polygon_attributes.csv')
        self.assertEqual([x['value_index'] for x in rows],['0','1','0','1'])
        ex=root/'EXPORTS';idx=delivery.publish_exports(p,{'objects':[]},ex)
        self.assertEqual({x['category'] for x in idx['files']},{'polygons','polygon_parts','polygon_attributes'})
        ar=next(x for x in idx['files'] if x['category']=='polygon_attributes')
        self.assertEqual(self.rows(ex/ar['path']),rows)
        pointer=publish_contract(p,ex,idx)
        result=validate_contract(ex/POINTER,'opendtect')
        self.assertEqual(result['counts']['qualified_for_planned_mapping'],0)
        mp=ex/pointer['manifest'];manifest=json.loads(mp.read_text())
        artifacts=[json.loads(line) for line in (mp.parent/manifest['tables']['artifacts']['path']).read_text().splitlines()]
        self.assertIn('native_attribute_values',{a['role'] for a in artifacts})

    def test_bad_later_vector_retains_geometry_but_no_partial_attributes(self):
        root,p,r,_=self.run_case('Polygons3',polygon([[(1.,2.,3.)]],
            [vector(2,[42]),vector(4,[1.],version=9)]))
        self.assertEqual(r['objects'][0]['status'],'decoded')
        self.assertIn('type_vector',r['objects'][0]['attribute_failure'])
        self.assertEqual(r['polygon_attribute_value_rows'],0)
        self.assertFalse((p/'05_spatial/polygons/native_polygon_attributes.csv').exists())

    def test_bad_geometry_does_not_leak_attribute_values(self):
        root,p,r,_=self.run_case('Polygons3',polygon([[(1.,float('nan'),3.)]], [vector(2,[42])]))
        self.assertEqual(r['objects'][0]['status'],'failed_closed')
        self.assertEqual(r['polygon_attribute_value_rows'],0)

    def test_attributes_of_empty_geometry_are_not_a_full_payload(self):
        root,p,r,_=self.run_case('Polygons3',polygon([[]],[vector(2,[42])]))
        ex=root/'EXPORTS';idx=delivery.publish_exports(p,{'objects':[]},ex,spatial_checkpoint=True)
        self.assertNotIn('polygons',{x['category'] for x in idx['files']})
        self.assertIn('polygon_attributes',{x['category'] for x in idx['files']})
        ex=root/'FINAL';idx=delivery.publish_exports(p,{'objects':[]},ex)
        publish_contract(p,ex,idx)
        result=validate_contract(ex/POINTER)
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',result['assessments'][0]['reason_codes'])

    def test_checkpoint_count_mismatch_excludes_attribute_table_only(self):
        root,p,r,rp=self.run_case('Polygons3',polygon([[(1.,2.,3.)]],[vector(2,[42])]))
        r['polygon_attribute_value_rows']=2;rp.write_text(json.dumps(r))
        idx=delivery.publish_exports(p,{'objects':[]},root/'EXPORTS',spatial_checkpoint=True)
        self.assertIn('polygons',{x['category'] for x in idx['files']})
        self.assertNotIn('polygon_attributes',{x['category'] for x in idx['files']})

    def test_attribute_budget_does_not_discard_independent_geometry(self):
        captured=[]
        polygons,report=spatial.decode_polygons3(fixture.frame(polygon([[(1.,2.,3.)]],
            [vector(2,[42]),vector(4,[1.])])),attribute_vectors=captured,attribute_value_budget=1)
        self.assertEqual(polygons,[[(1.,2.,3.)]])
        self.assertEqual(captured,[])
        self.assertEqual(report['attributes_status'],'not_exported')
        self.assertIn('bound',report['attribute_failure'])

    def test_contract_splits_each_descriptor_into_a_bounded_evidence_row(self):
        root,p,r,_=self.run_case('Polygons3',polygon([[]],[vector(2,[n]) for n in range(120)]))
        ex=root/'EXPORTS';idx=delivery.publish_exports(p,{'objects':[]},ex)
        pointer=publish_contract(p,ex,idx);mp=ex/pointer['manifest'];manifest=json.loads(mp.read_text())
        evidence=[json.loads(line) for line in (mp.parent/manifest['tables']['metadata_evidence']['path']).read_text().splitlines()]
        vectors=[e for e in evidence if e['field'].startswith('polygon_attribute_vector:')]
        self.assertEqual(len(vectors),120)
        self.assertEqual(sorted(v['raw_value']['attribute_vector_index'] for v in vectors),list(range(120)))
        self.assertTrue(all(v['field']==f"polygon_attribute_vector:{v['raw_value']['attribute_vector_index']}" for v in vectors))
        self.assertTrue(all(len(json.dumps(v))<2048 for v in vectors))


if __name__=='__main__':unittest.main()
