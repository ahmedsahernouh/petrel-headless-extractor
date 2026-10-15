# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Part metadata must survive geometry delivery without becoming geometry itself."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import export_petrel_native_spatial_zero_gui as spatial
import geoviewer_delivery as delivery
from geoviewer_contract import POINTER,publish_contract
from validate_geoviewer_contract import validate_contract
import test_petrel_native_recovery as bf

TAG='11111111-1111-1111-1111-111111111111'

class PolygonPartEvidenceTests(unittest.TestCase):
    def run_case(self,parts,closed=None,attributes=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);package=root/'data/package';store=package/'08_native_project/ptd_store';store.mkdir(parents=True)
        data=store/'Data.ptd'
        with sqlite3.connect(data) as db:
            db.executescript('CREATE TABLE data(data_pk INTEGER,droid TEXT,name TEXT,version INTEGER,blob_type TEXT,time_stamp TEXT);CREATE TABLE blob_parts(data_fk INTEGER,part INTEGER,blob_data BLOB);')
            db.execute('INSERT INTO data VALUES(1,?,"Segments",1,"Polygons3","")',(TAG,))
            db.execute('INSERT INTO blob_parts VALUES(1,0,?)',(bf.envelope(bf.frame(bf.polygon_doc(parts,closed,attributes=attributes),split=True)),))
        db.close()
        before=hashlib.sha256(data.read_bytes()).hexdigest()
        spatial.run(argparse.Namespace(export_package=str(package),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02))
        self.assertEqual(before,hashlib.sha256(data.read_bytes()).hexdigest())
        report_path=package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        report=json.loads(report_path.read_text());return root,package,report,report_path

    def rows(self,path):
        with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

    def catalogue(self,package,exports,index):
        pointer=publish_contract(package,exports,index);base=(exports/pointer['manifest']).parent
        manifest=json.loads((exports/pointer['manifest']).read_text())
        return {k:[json.loads(line) for line in (base/v['path']).read_text().splitlines()] for k,v in manifest['tables'].items()}

    def test_native_flag_repeated_endpoint_and_unassessable_endpoints_are_separate(self):
        missing=spatial.NATIVE_FLOAT_MAX_SENTINEL
        parts=[[(1.,2.,3.),(4.,5.,6.)],[(1.,2.,3.),(1.,2.,3.)],[],[(missing,0.,0.),(4.,5.,6.)]]
        root,package,report,_=self.run_case(parts,[True,False,False,True])
        segments=report['objects'][0]['segments']
        self.assertEqual([r['is_closed_by_repeated_xyz'] for r in segments],[False,True,None,None])
        table=self.rows(package/'05_spatial/polygons/native_polygon_parts.csv')
        self.assertEqual([r['part_index'] for r in table],['0','1','2','3'])
        self.assertEqual([r['is_closed_native'] for r in table],['yes','no','no','yes'])
        self.assertEqual([r['is_closed_by_repeated_xyz'] for r in table],['no','yes','unknown','unknown'])
        self.assertEqual([r['missing_vertex_slots'] for r in table],['0','0','0','1'])
        self.assertEqual(report['polygon_part_rows'],4)
        finite=self.rows(package/'05_spatial/polygons/native_polygons_vertices.csv')
        self.assertEqual([(r['part_index'],r['vertex_index']) for r in finite],[('0','0'),('0','1'),('1','0'),('1','1'),('3','1')])
        self.assertEqual(report['polygon_vertex_rows'],5)

    def test_empty_parts_are_indexed_metadata_not_accepted_full_geometry(self):
        root,package,report,_=self.run_case([[],[]],[True,False])
        self.assertEqual(report['objects'][0]['status'],'empty_supported_object')
        exports=root/'EXPORTS';index=delivery.publish_exports(package,{'objects':[]},exports)
        self.assertEqual(len(index['files']),1);self.assertEqual(index['files'][0]['category'],'polygon_parts')
        self.assertEqual(index['files'][0]['record_count'],2)
        tables=self.catalogue(package,exports,index)
        self.assertEqual(tables['artifacts'][0]['role'],'polygon_part_metadata')
        result=validate_contract(exports/POINTER)
        self.assertEqual(result['assessments'][0]['category'],'culture_polygon')
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',result['assessments'][0]['reason_codes'])

    def test_attributes_and_part_evidence_reach_catalogue_without_topology_guess(self):
        root,package,report,_=self.run_case([[(0.,0.,0.),(1.,1.,1.)],[]],[False,True],attributes=True)
        exports=root/'EXPORTS';index=delivery.publish_exports(package,{'objects':[]},exports)
        self.assertEqual({r['category'] for r in index['files']},{'polygons','polygon_parts'})
        tables=self.catalogue(package,exports,index)
        evidence=next(r for r in tables['metadata_evidence'] if r['field']=='polygon_geometry_evidence')
        part_rows=sorted([r for r in tables['metadata_evidence'] if r['field'].startswith('polygon_part:')],
                         key=lambda r:r['raw_value']['part_index'])
        self.assertEqual([r['raw_value'] for r in part_rows],report['objects'][0]['segments'])
        self.assertEqual(evidence['raw_value']['part_count'],2)
        self.assertEqual(evidence['raw_value']['attributes_status'],'not_exported')
        self.assertFalse(any(r['field']=='polygon_topology' for r in tables['metadata_evidence']))
        self.assertTrue(any(r['code']=='POLYGON_ATTRIBUTES_NOT_EXPORTED' for r in tables['gaps']))
        self.assertTrue(any(r['code']=='POLYGON_RING_ROLES_UNRESOLVED' for r in tables['gaps']))

    def test_checkpoint_part_table_count_mismatch_is_excluded(self):
        root,package,report,rp=self.run_case([[],[]])
        good=delivery.publish_exports(package,{'objects':[]},root/'good',spatial_checkpoint=True)
        self.assertEqual(good['files'][0]['record_count'],2)
        report['polygon_part_rows']=3;rp.write_text(json.dumps(report))
        bad=delivery.publish_exports(package,{'objects':[]},root/'bad',spatial_checkpoint=True)
        self.assertEqual(bad['files'],[])

    def test_duplicate_serialization_ids_do_not_merge_collection_parts(self):
        doc=bf.polygon_doc([[(1.,2.,3.)],[(4.,5.,6.)]])
        # Exact typed fixture serialization ID replacement, independently located.
        before=bf.element('item',attrs={'Id':3});after=bf.element('item',attrs={'Id':2})
        # Prefix ends before the item's closure; actual item has further attributes.
        token_before=before[:-1];token_after=after[:-1]
        self.assertEqual(doc.count(token_before),1)
        doc=doc.replace(token_before,token_after,1)
        parts,meta=spatial.decode_polygons3(bf.frame(doc))
        self.assertEqual(len(parts),2)
        self.assertEqual([s['native_serialization_id'] for s in meta['segments']],[2,2])
        self.assertEqual([s['part_index'] for s in meta['segments']],[0,1])

    def test_many_parts_stay_separate_bounded_jsonl_records(self):
        root,package,report,_=self.run_case([[]]*5000)
        exports=root/'EXPORTS';index=delivery.publish_exports(package,{'objects':[]},exports)
        tables=self.catalogue(package,exports,index)
        parts=[r for r in tables['metadata_evidence'] if r['field'].startswith('polygon_part:')]
        self.assertEqual(len(parts),5000)
        self.assertLess(max(len(json.dumps(r).encode('utf-8')) for r in tables['metadata_evidence']),8192)
        result=validate_contract(exports/POINTER)
        self.assertEqual(len(result['assessments']),1)
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',result['assessments'][0]['reason_codes'])

    def test_real_polygons_subject_category_normalizes_without_losing_native_type(self):
        root,package,report,_=self.run_case([[(0.,0.,0.),(1.,1.,1.)]])
        context=package/'01_project_metadata/project_context.json';context.parent.mkdir(parents=True,exist_ok=True)
        context.write_text(json.dumps({'objects':[{'object_id':TAG,'name':'Native polygon','category':'PolygonsSubject'}]}))
        exports=root/'EXPORTS';index=delivery.publish_exports(package,{'objects':[]},exports)
        tables=self.catalogue(package,exports,index)
        node=next(n for n in tables['entities'] if n['source_id']==TAG)
        self.assertEqual(node['category'],'culture_polygon')
        self.assertEqual(node['native_subject_type'],'PolygonsSubject')

if __name__=='__main__':unittest.main()
