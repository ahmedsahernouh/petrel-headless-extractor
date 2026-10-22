"""Behavioral checks for exact native top binding and fail-closed boundaries."""
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import uuid

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import petrel_native_binary as binary
import petrel_native_tops as tops


def uid(n):
    return str(uuid.UUID(int=n+1))


def node(name,value=None,attrs=None,children=None):
    return binary.Node(name,attrs or {},children or [],[] if value is None else [value])


class BindingTests(unittest.TestCase):
    def fixture(self):
        ids={name:uid(i+100) for i,name in enumerate(('top','attr_folder','well_folder','surface_folder',
            'md','surface_attr','well_attr','other','well','surface','trace','checkshot','checkshot_surface'))}
        context={'objects':[]}
        docs={}; ordinals={}
        def subject(key,category,name,parent=None,**extra):
            tag=ids[key]
            context['objects'].append(dict(object_id=tag,category=category,name=name,parent_id=ids[parent] if parent else None))
            attrs={'xmlns':tops.NS,'Version':tops.VERSIONS.get(category,[1])}
            children=[node('unique_tag',tag),node('name',name)]
            children.extend(node(k,v) for k,v in extra.items())
            docs[tag]=node(category,attrs=attrs,children=children)
            ordinals[tag]=len(ordinals)
        subject('top','WellTopsSubject','Tops')
        subject('attr_folder','PointsAttrFolder','Attributes','top')
        subject('well_folder','WellTopsWellFolder','Wells','top')
        subject('surface_folder','WellTopsSurfaceFolder','Surfaces','top')
        # Deliberately permute vector indices and UUIDs from the demo fixture.
        subject('well_attr','PointsAttrSubject','Well','attr_folder',attr_index=0,attr_type=6)
        subject('other','PointsAttrSubject','Other','attr_folder',attr_index=1,attr_type=2)
        subject('md','PointsAttrSubject','MD','attr_folder',attr_index=2,attr_type=4)
        subject('surface_attr','PointsAttrSubject','Surface','attr_folder',attr_index=3,attr_type=6)
        subject('well','WellTopsWellSubject','A','well_folder',kid_tag=41,well_link=ids['trace'])
        subject('surface','WellTopsSurfaceSubject','Top A','surface_folder',kid_tag=73)
        subject('trace','WellTraceSubject','A')
        subject('checkshot','WellTopsSubject','Unrelated')
        subject('checkshot_surface','WellTopsSurfaceSubject','Wrong','checkshot',kid_tag=73)
        def vec(kind, values):
            name,scalar,dtype={2:('ints','int','<i4'),4:('doubles','double','<f8'),6:('kidtags','kidtag','<i4')}[kind]
            a=binary.Array(scalar,np.asarray(values,dtype=dtype))
            return node('type_vector',attrs={'Version':5},children=[node('type',kind),
                node(name,attrs={'Size':2},children=[a]),node('default',b'\0'*8,{'Version':1})])
        vectors=[vec(6,[41,41]),vec(2,[0,0]),vec(4,[120.,125.]),vec(6,[73,73])]
        root=node('data',attrs={'Type':'Points3','Version':[1,2,0,1,1]},children=[
            node('has_attr',True),node('vertices',attrs={'Size':2}),
            node('attributes',attrs={'Size':4},children=vectors)])
        return ids,context,docs,ordinals,root,[(1.,2.,3.),(4.,5.,6.)]

    def bind(self, fixture):
        ids,context,docs,ordinals,root,points=fixture
        with patch.object(tops,'model_index',return_value=(docs,set(),ordinals)),\
             patch.object(binary,'read_points3_documents',return_value=iter([root])):
            return tops.bind(ids['top'],77,points,b'fake',context,b'fake')

    def test_permuted_indices_and_unrelated_reused_kidtag(self):
        rows,receipt=self.bind(self.fixture())
        self.assertEqual(receipt['status'],tops.STATUS)
        self.assertEqual([(r['well_name'],r['surface'],r['measured_depth']) for r in rows],
                         [('A','Top A',120.),('A','Top A',125.)])
        self.assertEqual(rows[0]['well_kid_tag'],41)
        self.assertEqual(rows[0]['surface_kid_tag'],73)
        self.assertEqual(rows[0]['z_semantic_status'],'native_xyz_z; not_TVD_or_TVDSS')

    def test_conflicting_link_name_fails(self):
        f=self.fixture();ids,context,docs,*_=f
        docs[ids['trace']].child('name').content=['Different']
        with self.assertRaisesRegex(tops.TopBindingError,'name conflict'):
            self.bind(f)

    def test_duplicate_surface_tag_under_top_fails(self):
        f=self.fixture();ids,context,docs,ordinals,*_=f
        new=uid(999); context['objects'].append(dict(object_id=new,category='WellTopsSurfaceSubject',
            name='Conflicting',parent_id=ids['surface_folder']))
        docs[new]=node('WellTopsSurfaceSubject',attrs={'xmlns':tops.NS,'Version':tops.VERSIONS['WellTopsSurfaceSubject']},
            children=[node('unique_tag',new),node('name','Conflicting'),node('kid_tag',73)])
        with self.assertRaisesRegex(tops.TopBindingError,'Duplicate Well Tops surface kid_tag'):
            self.bind(f)

    def test_count_and_profile_fail_closed(self):
        f=self.fixture(); f[4].child('attributes').children[2].child('doubles').attrs['Size']=1
        with self.assertRaisesRegex(tops.TopBindingError,'count'):
            self.bind(f)
        f=self.fixture(); f[4].attrs['Version']=[9]
        with self.assertRaisesRegex(tops.TopBindingError,'profile'):
            self.bind(f)

    def test_demo_read_only_against_prior_independent_proof(self):
        source=Path(r'D:\FV_QA\B3R12_DEMO2018_SOURCE\Petrel2010 demo project_2018.ptd')
        package=Path(r'D:\FV_QA\B3R12_DEMO2018_OUT\Demo2018_20261022_111253_771349_data\extraction\package\p_20261022_111300_b84aad')
        prior=Path(r'D:\Computer\Code\Petrel_project\build\tops_native_investigation_20261022\worker\native_tops_84_candidate.csv')
        if not all(p.exists() for p in (source/'Data.ptd',source/'Model.ptd',
                  package/'01_project_metadata/project_context.json',prior)):
            self.skipTest('demo native proof files unavailable')
        import export_petrel_native_spatial_zero_gui as spatial
        with spatial.open_read_only_sqlite(source/'Data.ptd') as db:
            obj=next(o for o in spatial.live_native_objects(db) if o.data_pk==1253)
            blob=spatial.object_blob(db,obj)
            payload,_=spatial.decompress_lz4_block(blob)
        points,_=spatial.decode_points3(payload)
        model=binary.decompress((source/'Model.ptd').read_bytes())
        context=json.loads((package/'01_project_metadata/project_context.json').read_text())
        rows,receipt=tops.bind(obj.object_id,obj.data_pk,points,payload,context,model)
        with prior.open(newline='',encoding='utf-8') as stream:
            reference=list(csv.DictReader(stream))
        self.assertEqual((receipt['status'],len(rows)),(tops.STATUS,84))
        self.assertEqual([(r['well_name'],r['surface'],r['measured_depth']) for r in rows],
            [(r['well_name'],r['surface_name'],float(r['md_native'])) for r in reference])

    def test_delivery_accepts_only_receipt_hash_bound_table(self):
        import geoviewer_delivery as delivery
        ids,context,*_=self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            package=Path(folder)/'package'; package.mkdir()
            context_file=package/'01_project_metadata/project_context.json'
            context_file.parent.mkdir();context_file.write_text(json.dumps(context),encoding='utf-8')
            relative='02_wells/well_tops/native_well_top_picks_bound.csv'
            path=package/relative;path.parent.mkdir(parents=True)
            with path.open('w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=tops.FIELDS);writer.writeheader()
                for index in range(2):
                    row={field:'' for field in tops.FIELDS}
                    row.update(object_id=ids['top'],data_pk=77,point_index=index,well_name='A',surface='Top A',
                               measured_depth=120+index,x=1,y=2,z=3,well_link_uuid=ids['trace'],
                               binding_status=tops.STATUS)
                    writer.writerow(row)
            receipt={'tool':'export_petrel_native_spatial_zero_gui.py','completed_at_utc':'now',
                'objects':[{'object_id':ids['top'],'status':'decoded','blob_type':'Points3'}],
                'point_vertex_rows':0,'polygon_vertex_rows':0,'polygon_part_rows':0,
                'polygon_attribute_value_rows':0,'point_attribute_value_rows':0,
                'polygon_missing_vertex_rows':0,'point_missing_vertex_rows':0,
                'point_identity_rows':0,'trajectory_rows':0,'native_well_head_rows':0,
                'well_top_binding':{'status':tops.STATUS,'row_count':2,'output_path':relative,
                    'output_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'objects':[{'object_id':ids['top'],'status':tops.STATUS,'row_count':2,'point_count':2}]}}
            report=package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
            report.parent.mkdir(parents=True);report.write_text(json.dumps(receipt),encoding='utf-8')
            self.assertIn(relative,delivery._completed_spatial_tables(package,receipt,[]))
            for checkpoint in (True,False):
                output=delivery.publish_exports(package,{},Path(folder)/('partial' if checkpoint else 'final'),
                    spatial_checkpoint=checkpoint)
                self.assertEqual([(r['category'],r['object_id'],r['status']) for r in output['files']
                                  if r['category']=='well_tops'],[('well_tops',ids['top'],'exported')])
            path.write_text(path.read_text(encoding='utf-8')+'tamper\n',encoding='utf-8')
            self.assertNotIn(relative,delivery._completed_spatial_tables(package,receipt,[]))


if __name__=='__main__':unittest.main()
