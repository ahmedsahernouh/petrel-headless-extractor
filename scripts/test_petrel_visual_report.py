"""Numeric, provenance, inventory completeness and offline-link report controls.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

import numpy as np
import petrel_visual_report as v
import report_petrel_project_audit as audit


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'native_data/log/samples.csv';self.path.parent.mkdir(parents=True)
        self.path.write_text('sample_index,md,raw_value,is_null\n0,100,10,0\n1,101,999999,1\n2,102,30,0\n',encoding='utf-8')
        self.item=dict(object_id='log-id',name='<GR & test>',well='W1',blob_type='FloatWellLog',unit='API',depth_unit='m',status='decoded',
                       ancestry=[dict(object_id='log-id',name='GR',kind='WellLogSubject',parent='well-id'),dict(object_id='well-id',name='W1',kind='WellTraceSubject',parent='')],
                       artifacts=[dict(path='log/samples.csv',sha256=v.sha(self.path))])
        self.receipt=dict(status='completed',source_unchanged=True,objects=[self.item])
        self.receipt_path=self.root/'07_workflows_reports/native_recovery/native_recovery_report.json'
        self.receipt_path.parent.mkdir(parents=True);self.receipt_path.write_text(json.dumps(self.receipt))
        self.min_audit=dict(native_inventory={})

    def tearDown(self):self.temp.cleanup()

    def build(self):return v.build_visuals(self.root,self.min_audit)

    def test_stats_exclude_null_and_preserve_depth(self):
        result=self.build();obj=result['objects'][0]
        self.assertEqual((obj['valid_count'],obj['minimum'],obj['maximum'],obj['mean'],obj['null_count']),(2,10,30,20,1))
        self.assertEqual(obj['depth_range'],[100,102])
        self.assertEqual(v.sha(self.path),self.item['artifacts'][0]['sha256'])
        self.assertTrue((self.root/result['figures'][0]['svg']).is_file())

    def test_model_metadata_failures_remain_visible_in_report(self):
        path=self.root/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        path.parent.mkdir(parents=True)
        failure=dict(status='failed_closed',error='LZ4 envelope length mismatch <unvalidated>')
        path.write_text(json.dumps(dict(model_well_head_decode=failure,trajectory_name_linkage=dict(candidate_fallback_report=failure),objects=[])))
        with patch.object(sys,'argv',['report','--export-package',str(self.root)]),redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(),0)
        text=(self.root/'PROJECT_REPORT.html').read_text(encoding='utf-8')
        self.assertIn('Data and preview limitations',text)
        self.assertIn('Native well-head metadata',text)
        self.assertIn('Trajectory name candidates',text)
        self.assertIn('LZ4 envelope length mismatch &lt;unvalidated&gt;',text)
        self.assertNotIn('<unvalidated>',text)
        self.assertIn('Complete data inventory tree',text)

    def test_tampered_csv_never_plotted(self):
        self.path.write_text(self.path.read_text().replace('30','300'))
        result=self.build();self.assertEqual(result['figures'],[])
        self.assertIn('hash',result['issues'][0]['reason'])

    def test_untrusted_receipt_does_not_plot_or_count_decoded(self):
        self.receipt['source_unchanged']=False;self.receipt_path.write_text(json.dumps(self.receipt))
        result=self.build();self.assertEqual(result['figures'],[])
        self.assertEqual(result['objects'][0]['status'],'untrusted_receipt')

    def test_external_artifact_rejected(self):
        with self.assertRaises(ValueError):v.contained(self.root,'../outside.csv')

    def test_partial_artifact_rejected(self):
        with self.assertRaises(ValueError):v.contained(self.root,'native_data/log.partial/samples.csv')

    def test_unknown_units_stay_unknown(self):
        self.item.update(unit=None,depth_unit=None,status='missing_metadata')
        self.receipt_path.write_text(json.dumps(self.receipt));result=self.build()
        self.assertEqual(result['objects'][0]['status'],'missing_metadata')
        self.assertIsNone(result['objects'][0]['unit'])

    def test_inventory_union_no_truncation_and_cycles(self):
        registry=self.root/'01_project_metadata/native_data_object_registry.csv';registry.parent.mkdir()
        with registry.open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['object_id','object_type'])
            for i in range(1500):writer.writerow([str(i),'FaultInterpretation'])
        receipt=dict(objects=[dict(ancestry=[dict(object_id='a',parent='b',kind='Folder',name='A'),dict(object_id='b',parent='a',kind='Folder',name='B')])])
        inventory=v.data_inventory(self.root,receipt,[])
        self.assertEqual(inventory['node_count'],1502)
        self.assertEqual(inventory['registry_ids'],1500)
        self.assertEqual([n['display_parent'] for n in inventory['nodes'][:2]],['',''])
        page=v.render_inventory(dict(visual_report=dict(inventory=inventory)),lambda a,p:p)
        self.assertEqual(page.count('class="object-node"'),1502)
        self.assertIn('Expand all',page)

    def test_budget_keeps_every_object(self):
        with patch.object(v,'MAX_LOG_FIGURES',0):result=self.build()
        self.assertEqual(len(result['objects']),1);self.assertEqual(result['figures'],[])
        self.assertEqual(result['objects'][0]['preview_status'],'not_selected_budget')

    def test_empty_package_renders_offline_and_escapes_names(self):
        with patch.object(sys,'argv',['report','--export-package',str(self.root)]),redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(),0)
        text=(self.root/'PROJECT_REPORT.html').read_text(encoding='utf-8')
        self.assertIn('Complete data inventory tree',text)
        self.assertIn('&lt;GR &amp; test&gt;',text)
        self.assertNotIn('<script src=',text)
        self.assertIn('data:image/png;base64,',text)

    def test_surface_undefined_nodes_excluded(self):
        self.path=self.root/'native_data/log/nodes.csv'
        self.path.write_text('node_index,i,j,x,y,raw_value,defined\n0,0,0,10,20,-10,1\n1,1,0,11,20,99999,0\n2,0,1,10,21,-30,1\n')
        self.item.update(blob_type='RegValGrid2',unit='ms',horizontal_unit='m',measurement='Time',artifacts=[dict(path='log/nodes.csv',sha256=v.sha(self.path))])
        self.receipt_path.write_text(json.dumps(self.receipt));obj=self.build()['objects'][0]
        self.assertEqual((obj['minimum'],obj['maximum'],obj['mean']),(-30,-10,-20))

    def test_zgy_patch_does_not_claim_segy_conversion(self):
        from petrel_file_convert import open_zgy
        from openzgy.api import ZgyWriter, SampleDataType
        path=self.root/'08_native_project/ptd_store/cube.zgy';path.parent.mkdir(parents=True)
        data=np.arange(3*4*16,dtype=np.float32).reshape(3,4,16)
        with ZgyWriter(str(path),size=data.shape,datatype=SampleDataType.float) as writer:writer.write((0,0,0),data)
        before=v.sha(path);result=self.build();obj=next(o for o in result['objects'] if o['category']=='Seismic')
        self.assertEqual(obj['preview_shape'],[1,4,16])
        self.assertEqual(obj['mean'],float(data[1].mean()))
        self.assertEqual(obj['status'],'preserved_only');self.assertEqual(v.sha(path),before)
        self.assertEqual(list(self.root.rglob('*.segy')),[])

    def test_clipping_spatial_preview_does_not_join_disjoint_segments(self):
        path=self.root/'05_spatial/polygons/native_polygons_vertices.csv';path.parent.mkdir(parents=True)
        path.write_text('object_id,part_index,x,y\np,0,0,0\np,0,1,1\np,0,100,100\np,0,2,2\np,0,3,3\n')
        overview=audit.gather_spatial_overview(self.root,dict(native_well_heads=[dict(well_name='A',x=0,y=0),dict(well_name='B',x=3,y=3)]))
        self.assertEqual(len(overview['polylines']),2)

    def test_polygon_segments_and_vertex_order_are_preserved(self):
        path=self.root/'05_spatial/polygons/native_polygons_vertices.csv';path.parent.mkdir(parents=True)
        path.write_text('object_id,part_index,segment_id,vertex_index,x,y\np,0,a,1,1,1\np,0,b,1,11,11\np,0,a,0,0,0\np,0,b,0,10,10\n')
        overview=audit.gather_spatial_overview(self.root,{})
        segments={item['segment_id']:item['points'] for item in overview['polylines']}
        self.assertEqual(segments,{'a':[(0.,0.),(1.,1.)],'b':[(10.,10.),(11.,11.)]})

    def test_polygon_missing_vertices_split_lines_and_ambiguous_order_is_rejected(self):
        path=self.root/'05_spatial/polygons/native_polygons_vertices.csv';path.parent.mkdir(parents=True)
        path.write_text('object_id,part_index,vertex_index,x,y\np,0,0,0,0\np,0,1,1,1\np,0,4,4,4\np,0,5,5,5\np,0,6,nan,6\np,0,7,7,7\np,0,8,8,8\np,1,0,10,10\np,1,0,11,11\n')
        overview=audit.gather_spatial_overview(self.root,{})
        self.assertEqual([p['points'] for p in overview['polylines']], [[(0.,0.),(1.,1.)],[(4.,4.),(5.,5.)],[(7.,7.),(8.,8.)]])
        self.assertEqual(overview['ambiguous_polygon_segments'],1)

    def test_polygon_clipping_precedes_decimation(self):
        path=self.root/'05_spatial/polygons/native_polygons_vertices.csv';path.parent.mkdir(parents=True)
        path.write_text('object_id,part_index,vertex_index,x,y\n'+''.join(f'p,0,{i},{1000000 if i==101 else i},{i}\n' for i in range(300)))
        overview=audit.gather_spatial_overview(self.root,dict(native_well_heads=[dict(well_name='A',x=0,y=0),dict(well_name='B',x=299,y=299)]))
        self.assertEqual(len(overview['polylines']),2)
        self.assertEqual(overview['polylines'][0]['points'][-1],(100.,100.))
        self.assertEqual(overview['polylines'][1]['points'][0],(102.,102.))

    def test_report_only_retains_figures_but_no_dataset_exports(self):
        from test_petrel_native_recovery import RecoveryTests
        fixture=RecoveryTests();fixture.setUp()
        try:
            package=fixture.fixture();original={p:v.sha(p) for p in package.rglob('*') if p.is_file()}
            with patch.object(sys,'argv',['report','--export-package',str(package),'--report-only']),redirect_stdout(io.StringIO()):
                self.assertEqual(audit.main(),0)
            receipt=json.loads(next(package.rglob('visual_report.json')).read_text())
            self.assertTrue(receipt['figures']);self.assertTrue(receipt['report_only'])
            self.assertFalse((package/'native_data').exists())
            for suffix in ('*.las','*.xyz','*.segy'):self.assertEqual(list(package.rglob(suffix)),[])
            self.assertFalse(any(p.name in ('samples.csv','nodes.csv') for p in package.rglob('*')))
            self.assertTrue(all(v.sha(p)==h for p,h in original.items()))
            objects=receipt['objects'];self.assertTrue(any(o['status']=='preview_only' for o in objects))
            self.assertFalse(any(o['status']=='decoded' for o in objects))
            self.assertIn('Dataset conversion OFF',(package/'PROJECT_REPORT.html').read_text(encoding='utf-8'))
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
