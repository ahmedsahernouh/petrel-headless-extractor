# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Attribute identity requires exact registry/Model/array agreement."""
from copy import deepcopy
import argparse, hashlib, json, unittest
from pathlib import Path
import numpy as np
import petrel_native_polygon_bindings as binding
import petrel_native_geometry_attributes as attrs
import petrel_native_binary as binary
import export_petrel_native_spatial_zero_gui as spatial
import geoviewer_delivery as delivery
import test_petrel_native_recovery as f
import test_native_geometry_attributes as raw
import test_spatial_missing_vertex_evidence as helper

OWNER=helper.OID;FOLDER='22222222-2222-2222-2222-222222222222'
ATTR='33333333-3333-3333-3333-333333333333';OTHER='44444444-4444-4444-4444-444444444444'
PARENT='55555555-5555-5555-5555-555555555555';TEMPLATE='66666666-6666-6666-6666-666666666666'

def fixtures():
    records=[('PolygonsSubject',OWNER,PARENT,'Polygon'),('PolygonsAttrFolder',FOLDER,OWNER,'Attributes'),
             ('PolygonsAttrSubject',ATTR,FOLDER,'Area_SqKm')]
    entries=[];models=[]
    for kind,tag,parent,name in records:
        stub=f.element('item',attrs={'Type':'SubjectStub','Version':[0,1,1]},children=[
            f.element('uniqueTag',tag),f.element('parentTag',parent),f.element('visualName',name)])
        entries.append(f.element('item',children=[f.element('key',kind),f.element('value',
            attrs={'Type':'ClassInfo','Version':[1,1]},children=[f.element('array',children=[stub])])]))
        children=[f.element('unique_tag',tag),f.element('name',name)]
        if kind=='PolygonsAttrSubject':children += [f.element('attr_index',0),f.element('attr_type',5),f.element('template',TEMPLATE)]
        models.append(f.element(kind,attrs={'xmlns':binding.NS,'Version':binding.PROFILES[kind]},children=children))
    pet=f.frame(f.element('node',attrs={'xmlns':binding.NS,'Type':'ProjectSerializer','Version':[153,1]},children=[
        f.element('class_infos',attrs={'Type':'ClassInfos','Version':[1,1]},children=[f.element('map',children=entries)])]))
    return pet,f.frame(*models)


def source():
    pet,model=fixtures()
    return dict(status='source_recorded',declarations=binding.declarations(pet,model),source_model_sha256='a'*64,source_project_sha256='b'*64)


def vectors():return [attrs.AttributeVector(0,5,('12.345',),b'\x00'*8)]


class PolygonBindingTests(unittest.TestCase):
    run_case=helper.MissingVertexEvidenceTests.run_case
    rows=helper.MissingVertexEvidenceTests.rows

    def test_exact_explicit_native_identity_is_not_a_unit_inference(self):
        result=binding.bind_vectors(OWNER,vectors(),source())
        self.assertEqual(result['status'],binding.STATUS)
        row=result['bindings'][0];self.assertEqual(row['attribute_id'],ATTR)
        self.assertEqual(row['attribute_name'],'Area_SqKm');self.assertEqual(row['attribute_template_id'],TEMPLATE)
        self.assertNotIn('unit',row);self.assertNotIn('part_index',row)

    def test_no_label_or_proximity_fallback(self):
        for change in ('wrong_parent','name_conflict','wrong_class','missing_model','duplicate_identity','missing_index','type_conflict'):
            s=source();d=s['declarations']
            if change=='wrong_parent':d['stubs'][ATTR]['parent_id']=OTHER
            elif change=='name_conflict':d['models'][ATTR]['name']='Different'
            elif change=='wrong_class':d['models'][ATTR]['category']='PointsAttrSubject'
            elif change=='missing_model':del d['models'][ATTR]
            elif change=='duplicate_identity':d['invalid'].append(ATTR)
            elif change=='missing_index':d['models'][ATTR]['index']=2
            else:d['models'][ATTR]['native_type']=2
            with self.subTest(change=change):
                result=binding.bind_vectors(OWNER,vectors(),s)
                self.assertEqual(result['status'],'unresolved');self.assertEqual(result['bindings'],[])

    def test_duplicate_index_or_folder_never_partially_maps(self):
        for mode in ('index','folder'):
            s=source();d=s['declarations']
            if mode=='index':
                d['stubs'][OTHER]=dict(d['stubs'][ATTR],object_id=OTHER)
                d['models'][OTHER]=dict(d['models'][ATTR],object_id=OTHER)
                vs=vectors()+[attrs.AttributeVector(1,5,('x',),b'0'*8)]
            else:
                d['stubs'][OTHER]=dict(d['stubs'][FOLDER],object_id=OTHER)
                d['models'][OTHER]=dict(d['models'][FOLDER],object_id=OTHER);vs=vectors()
            result=binding.bind_vectors(OWNER,vs,s)
            self.assertEqual(result['status'],'unresolved');self.assertEqual(result['bindings'],[])

    def test_unknown_native_profiles_fail_mapping(self):
        pet,model=fixtures()
        for old,new in ((f.text(binding.PROFILES['PolygonsAttrSubject']),f.text([9,9])),):
            changed=f.frame(*(body.replace(old,new) for _,body in binary.documents(model)))
            d=binding.declarations(pet,changed)
            s=source();s['declarations']=d
            self.assertEqual(binding.bind_vectors(OWNER,vectors(),s)['status'],'unresolved')
        with self.assertRaises(ValueError):binding.declarations(f.frame(*(body.replace(f.text([153,1]),f.text([154,1])) for _,body in binary.documents(pet))),model)

    def test_different_project_root_cannot_borrow_serializer_profile(self):
        pet,model=fixtures()
        changed=f.frame(*(body.replace(b'\x04node',b'\x05other',1) for _,body in binary.documents(pet)))
        self.assertNotEqual(pet,changed)
        with self.assertRaises(ValueError):binding.declarations(changed,model)

    def test_default_data_symlink_cannot_bind_unrelated_payload(self):
        root,p,_,_=self.run_case('Polygons3',raw.polygon([[(1.,2.,3.)]],[raw.vector(5,['x'])]))
        data=p/'08_native_project/ptd_store/Data.ptd'
        target=root/'other.ptd';data.replace(target)
        try:data.symlink_to(target)
        except OSError as exc:self.skipTest('Windows symlink privilege unavailable: '+str(exc))
        result=binding.load_package(p,data)
        self.assertEqual(result['status'],'unresolved');self.assertIn('Data.ptd path',result['reason'])

    def test_duplicate_model_uuid_is_detected_from_native_records(self):
        pet,model=fixtures();names,body=list(binary.documents(model))[-1]
        d=binding.declarations(pet,model+f.frame(body)[5:])
        self.assertIn(ATTR,d['invalid'])

    def test_custom_data_override_or_multiple_projects_do_not_map(self):
        root,p,_,_=self.run_case('Polygons3',raw.polygon([[(1.,2.,3.)]],[raw.vector(5,['x'])]))
        self.assertEqual(binding.load_package(p,root/'elsewhere.ptd')['status'],'unresolved')
        pr=p/'08_native_project/project_file';pr.mkdir();(pr/'a.pet').write_bytes(b'a');(pr/'b.pet').write_bytes(b'b')
        self.assertIn('one copied',binding.load_package(p,p/'08_native_project/ptd_store/Data.ptd')['reason'])

    def test_actual_export_rows_include_exact_names_and_input_hashes(self):
        root,p,_,_=self.run_case('Polygons3',raw.polygon([[(1.,2.,3.)]],[raw.vector(5,['12.345'])]))
        pet,model=fixtures();pr=p/'08_native_project/project_file';pr.mkdir()
        petpath=pr/'source.pet';petpath.write_bytes(b'\xff\xff\x01\x00\x11\x00ProjectSerializer'+bytes(9)+f.envelope(pet))
        mp=p/'08_native_project/ptd_store/Model.ptd';mp.write_bytes(f.envelope(model))
        before={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in (petpath,mp,p/'08_native_project/ptd_store/Data.ptd')}
        spatial.run(argparse.Namespace(export_package=str(p),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02))
        report=json.loads((p/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json').read_text(encoding='utf-8'))
        self.assertIn('exact native registry/Model names and IDs',report['objects'][0]['reason'])
        idx=delivery.publish_exports(p,{'objects':[]},root/'EXPORTS')
        item=next(x for x in idx['files'] if x['category']=='polygon_attributes')
        row=self.rows(root/'EXPORTS'/item['path'])[0]
        self.assertEqual(row['attribute_id'],ATTR);self.assertEqual(row['attribute_name'],'Area_SqKm')
        self.assertEqual(row['source_model_sha256'],before[str(mp)])
        self.assertEqual(row['source_project_sha256'],before[str(petpath)])
        self.assertEqual(json.loads(row['value_json']),'12.345')
        self.assertEqual(len(row['source_blob_sha256']),64)
        self.assertEqual(before,{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in (petpath,mp,p/'08_native_project/ptd_store/Data.ptd')})


if __name__=='__main__':unittest.main()
