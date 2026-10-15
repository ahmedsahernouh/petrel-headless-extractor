# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Focused regression and I/O-operation tests for shallow export delivery."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_delivery as delivery


ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT/'build/delivery_io_repair_20260921/geoviewer_delivery_baseline.py'


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')


def tree_hash(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class DeliveryIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.package=self.root/'package';self.package.mkdir()

    def fixture(self, native_count=2):
        objects=[];context=[]
        for number in range(native_count):
            identity=f'log-{number:03}'
            relative=f'logs/{identity}/samples_{number:03}.csv'
            source=self.package/'native_data'/relative;source.parent.mkdir(parents=True,exist_ok=True)
            source.write_text('sample,value\n0,1.25\n',encoding='utf-8')
            objects.append({'object_id':identity,'object_name':f'Log {number}','blob_type':'FloatWellLog',
                            'status':'decoded','numeric_round_trip':'exact',
                            'artifacts':[{'path':relative,'bytes':source.stat().st_size,
                                          'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]})
            context.append({'object_id':identity,'name':f'Log {number}'})
        for identity,name in [('poly-1','Polygon One'),('point-1','Point One'),('traj-1','Well One')]:
            context.append({'object_id':identity,'name':name})
        write_json(self.package/'01_project_metadata/project_context.json',{'objects':context})
        write_json(self.package/'07_workflows_reports/native_recovery/native_recovery_report.json',{'objects':objects})
        write_json(self.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
                   {'objects':[{'object_id':x,'status':'decoded'} for x in ('poly-1','point-1','traj-1')],
                    'model_well_head_decode':{'status':'passed'}})
        tables={
            '05_spatial/polygons/native_polygons_vertices.csv':
                'object_id,segment_id,vertex_index,x\npoly-1,seg-b,2,20\npoly-1,seg-a,0,10\n',
            '05_spatial/points/native_points_vertices.csv':
                'object_id,vertex_index,x\npoint-1,1,7\npoint-1,0,6\n',
            '02_wells/trajectories/native_well_trajectory_records.csv':
                'object_id,well_name,station_index,md\ntraj-1,Well One,1,200\ntraj-1,Well One,0,100\n',
            '02_wells/well_headers/native_well_heads.csv':'object_id,x\ntraj-1,1\n'}
        for relative,text in tables.items():
            path=self.package/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        workflow=self.package/'native_workflows/wf.json';workflow.parent.mkdir(parents=True);workflow.write_text('{"steps":[]}',encoding='utf-8')
        write_json(self.package/'01_project_metadata/workflow_recovery.json',
                   {'objects':[{'object_id':'wf-1','name':'Workflow One','status':'definition_recovered',
                                'artifacts':[{'path':'wf.json','sha256':hashlib.sha256(workflow.read_bytes()).hexdigest()}]}]})
        return {'objects':[]}

    def test_differential_fixture_matches_baseline_bytes_order_and_metadata(self):
        inventory=self.fixture();before=tree_hash(self.package)
        old_destination=self.root/'old';new_destination=self.root/'new'
        with patch.object(delivery,'event'):
            new=delivery.publish_exports(self.package,inventory,new_destination)
        polygon=next(x for x in new['files'] if x['object_id']=='poly-1')
        rows=(new_destination/polygon['path']).read_text(encoding='utf-8').splitlines()
        self.assertEqual(rows[1:],['poly-1,seg-b,2,20','poly-1,seg-a,0,10'])
        self.assertEqual(polygon['record_count'],2)
        self.assertEqual(tree_hash(self.package),before)
        self.assertEqual(len(new['files']),7);self.assertEqual(new['findings'],[])
        self.assertEqual(new['partial_files'], [])
        self.assertIs(new['partial'], False)
        self.assertEqual(new['status'], 'delivery_indexed')
        if BASELINE.is_file():
            spec=importlib.util.spec_from_file_location('delivery_baseline',BASELINE)
            baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
            old=baseline.publish_exports(self.package,inventory,old_destination)
            # Additive provenance fields intentionally extend the historical index.
            # All original metadata, order and artifact bytes must remain identical.
            def legacy_index(index):
                rows = []
                for row in index['files']:
                    row = {k:v for k,v in row.items() if k not in ('recovery_status', 'metadata_warning',
                        'source_lane','source_reference','source_format','derivative_reference','registration_scope',
                        'registration_format_warning')}
                    if row.get('storage') == 'object_table':
                        row.update(sha256='', integrity='derived_from_validated_shared_CSV')
                    if row.get('category') == 'well_headers':
                        # The old index omitted head identity. Verify the new
                        # digest before normalizing only those two old fields.
                        self.assertEqual(row['sha256'],hashlib.sha256(
                            (new_destination/row['path']).read_bytes()).hexdigest())
                        self.assertEqual(row['integrity'],'delivery_sha256_freshly_computed')
                        row.update(sha256='',integrity='file_identity_and_prior_numerical_QC')
                    rows.append(row)
                # The 21 September snapshot predates explicit checkpoint and
                # delivery-state fields. Their exact healthy values are checked
                # above; keep every historical field in the byte/order comparison.
                result={k:v for k,v in index.items() if k not in ('partial_files','partial','status')}
                return dict(result, files=rows)
            self.assertEqual(old,legacy_index(new))
            for row in new['files']:
                self.assertEqual(row['recovery_status'], 'decoded' if row['category']=='logs' else '')
                self.assertEqual(row['metadata_warning'], '')
            with (old_destination/'FILE_INDEX.csv').open(encoding='utf-8-sig', newline='') as handle:
                old_rows=list(csv.DictReader(handle))
            with (new_destination/'FILE_INDEX.csv').open(encoding='utf-8-sig', newline='') as handle:
                new_rows=list(csv.DictReader(handle))
            self.assertEqual(old_rows,legacy_index({'files':new_rows})['files'])
            self.assertEqual(json.loads((old_destination/'FILE_INDEX.json').read_text()),
                             legacy_index(json.loads((new_destination/'FILE_INDEX.json').read_text())))
            for item in new['files']:
                self.assertEqual((old_destination/item['path']).read_bytes(),(new_destination/item['path']).read_bytes())

    def test_traversal_and_symlink_escape_are_blocked(self):
        inventory=self.fixture(native_count=1)
        escaped=self.root/'escape.csv';escaped.write_text('outside',encoding='utf-8')
        report=self.package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        original=json.loads(report.read_text());bad=json.loads(report.read_text())
        bad['objects'][0]['artifacts'][0]['path']='../../escape.csv';write_json(report,bad)
        with self.assertRaises(ValueError):delivery.publish_exports(self.package,inventory,self.root/'traversal')
        write_json(report,original)

        symlink_destination=self.root/'symlink';symlink_destination.mkdir();outside=self.root/'outside';outside.mkdir()
        try:
            (symlink_destination/'logs').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError):delivery.publish_exports(self.package,inventory,symlink_destination)
        except OSError:
            original_is_symlink=Path.is_symlink
            def reports_link(path):
                return path==symlink_destination/'logs' or original_is_symlink(path)
            with patch.object(Path,'is_symlink',reports_link):
                with self.assertRaises(ValueError):delivery.publish_exports(self.package,inventory,symlink_destination)

    def test_collision_and_hardlink_fallback_preserve_files_and_report_truthfully(self):
        inventory=self.fixture(native_count=1);source=next((self.package/'native_data').rglob('*.csv'))
        destination=self.root/'collision';target=destination/'logs/Log_0_log-000_samples_000.csv'
        target.parent.mkdir(parents=True);target.write_text('keep me',encoding='utf-8')
        with patch.object(delivery,'event'):
            collision=delivery.publish_exports(self.package,inventory,destination)
        self.assertEqual(target.read_text(encoding='utf-8'),'keep me')
        item=next(x for x in collision['files'] if x['object_id']=='log-000')
        self.assertEqual(item['storage'],'original_path_filesystem_cannot_hardlink')
        self.assertTrue(any('collision' in x.lower() for x in collision['findings']))

        fallback_destination=self.root/'fallback';before=tree_hash(self.package)
        with patch.object(delivery.os,'link',side_effect=OSError(95,'Hard links unsupported')),patch.object(delivery,'event'):
            fallback=delivery.publish_exports(self.package,inventory,fallback_destination)
        item=next(x for x in fallback['files'] if x['object_id']=='log-000')
        self.assertEqual(item['storage'],'original_path_filesystem_cannot_hardlink')
        self.assertTrue((fallback_destination/item['path']).samefile(source))
        self.assertTrue(any('Hard links unsupported' in x for x in fallback['findings']))
        self.assertEqual(tree_hash(self.package),before)

    def test_category_mkdir_permission_falls_back_and_remaining_categories_continue(self):
        inventory=self.fixture(native_count=1);destination=self.root/'permission';original_mkdir=Path.mkdir
        def mkdir(path,*args,**kwargs):
            if path==destination/'logs':raise PermissionError(13,'Injected category denial')
            return original_mkdir(path,*args,**kwargs)
        with patch.object(Path,'mkdir',mkdir),patch.object(delivery,'event'):
            result=delivery.publish_exports(self.package,inventory,destination)
        log=next(x for x in result['files'] if x['object_id']=='log-000')
        workflow=next(x for x in result['files'] if x['object_id']=='wf-1')
        self.assertEqual(log['storage'],'original_path_filesystem_cannot_hardlink')
        self.assertTrue((destination/log['path']).samefile(next((self.package/'native_data').rglob('*.csv'))))
        self.assertEqual(workflow['storage'],'single_physical_copy_hardlink')
        self.assertTrue((destination/workflow['path']).is_file())
        self.assertTrue(any('Injected category denial' in x for x in result['findings']))

    def test_many_files_cache_metadata_operations_and_emit_exact_progress(self):
        inventory=self.fixture(native_count=32);destination=self.root/'exports';events=[]
        spatial_path=self.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        spatial=json.loads(spatial_path.read_text());spatial['objects'].append({'object_id':'poly-2','status':'decoded'});write_json(spatial_path,spatial)
        polygon=self.package/'05_spatial/polygons/native_polygons_vertices.csv'
        polygon.write_text('object_id,segment_id,vertex_index,x\npoly-1,seg-b,2,20\npoly-2,seg-c,0,30\npoly-1,seg-a,0,10\n',encoding='utf-8')
        original_resolve=Path.resolve;original_mkdir=Path.mkdir
        original_stat=os.stat;original_lstat=os.lstat;original_samefile=os.path.samefile
        original_islink=os.path.islink;original_isjunction=os.path.isjunction
        def measure(module,target,event_sink):
            operations={'resolve':0,'stat':0,'lstat':0,'islink':0,'isjunction':0,'samefile':0,
                        'package_resolve':0,'destination_resolve':0,'logs_mkdir':0}
            def resolve(path,*args,**kwargs):
                operations['resolve']+=1
                if path==self.package:operations['package_resolve']+=1
                if path==target:operations['destination_resolve']+=1
                return original_resolve(path,*args,**kwargs)
            def mkdir(path,*args,**kwargs):
                if path==target/'logs':operations['logs_mkdir']+=1
                return original_mkdir(path,*args,**kwargs)
            def stat(*args,**kwargs):operations['stat']+=1;return original_stat(*args,**kwargs)
            def lstat(*args,**kwargs):operations['lstat']+=1;return original_lstat(*args,**kwargs)
            def islink(*args,**kwargs):operations['islink']+=1;return original_islink(*args,**kwargs)
            def isjunction(*args,**kwargs):operations['isjunction']+=1;return original_isjunction(*args,**kwargs)
            def samefile(*args,**kwargs):operations['samefile']+=1;return original_samefile(*args,**kwargs)
            with patch.object(Path,'resolve',resolve),patch.object(Path,'mkdir',mkdir),\
                    patch.object(os,'stat',stat),patch.object(os,'lstat',lstat),\
                    patch.object(os.path,'islink',islink),patch.object(os.path,'isjunction',isjunction),\
                    patch.object(os.path,'samefile',samefile),\
                    patch.object(module,'event',side_effect=lambda kind,**fields:event_sink.append((kind,fields))):
                result=module.publish_exports(self.package,inventory,target)
            return result,operations
        old_operations=None
        if BASELINE.is_file():
            spec=importlib.util.spec_from_file_location('delivery_baseline_operations',BASELINE)
            baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
            _,old_operations=measure(baseline,self.root/'baseline_operations',[])
        result,operations=measure(delivery,destination,events)
        self.assertEqual({k:operations[k] for k in ('package_resolve','destination_resolve','logs_mkdir')},
                         {'package_resolve':1,'destination_resolve':1,'logs_mkdir':1})
        linked=sum(1 for x in result['files'] if x['storage']=='single_physical_copy_hardlink')
        self.assertEqual(operations['samefile'],linked)
        self.assertGreater(operations['stat'],0)
        self.assertGreater(operations['islink']+operations['isjunction'],0)
        if old_operations is not None:
            self.assertLess(operations['resolve'],old_operations['resolve'])
            if os.environ.get('GEOVIEWER_REPORT_IO_COUNTS')=='1':
                print('DELIVERY_IO_COUNTS '+json.dumps({'baseline':old_operations,'current':operations},sort_keys=True))
        self.assertEqual(len(result['files']),38)
        progress=[fields for kind,fields in events if kind=='delivery_progress']
        last_log=next(x for x in progress if x.get('current_object_id')=='log-031')
        self.assertEqual((last_log['completed_count'],last_log['attempted_count']),(31,32))
        polygon_progress=[x for x in progress if x['category']=='polygons']
        self.assertEqual([(x['completed_count'],x['attempted_count']) for x in polygon_progress],[(0,1),(0,2)])
        completed={fields['category']:fields for kind,fields in events if kind=='delivery_category_completed'}
        self.assertEqual(completed['logs']['attempted'],32);self.assertEqual(completed['logs']['exported'],32)
        final=[fields for kind,fields in events if kind=='delivery_completed'][-1]
        self.assertEqual(final['file_count'],38);self.assertEqual(final['finding_count'],0)
        self.assertIn('duration_seconds',final)


if __name__=='__main__':unittest.main()
