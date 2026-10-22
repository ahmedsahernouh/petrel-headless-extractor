"""Meaningful offline summary checks against mixed and incomplete catalogues."""
import json
import base64
import hashlib
import struct
import zlib
import os
from pathlib import Path
import re
import tempfile
import unittest

from geoviewer_summary import publish_summary


def payload(path):
    html = path.read_text(encoding='utf-8')
    return json.loads(re.search(r'const project=(.*?);\nconst ', html, re.S).group(1))


def flatten(categories):
    result = {}
    def walk(node):
        result[node['id']] = node
        for child in node.get('children', []):
            walk(child)
    for category in categories:
        walk(category)
    return result


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'
        (self.package / '01_project_metadata').mkdir(parents=True)
        self.exports = self.root / 'case_EXPORTS'; self.exports.mkdir()
        self.report = self.root / 'case_SUMMARY.html'

    def publish(self, **kw):
        return publish_summary(self.report, package=self.package, destination=self.exports, **kw)

    def test_mixed_tree_exact_relationships_and_downloads(self):
        context = {'project_name': '<script>alert(1)</script>', 'objects': [
            {'object_id': 'well', 'name': 'Alpha', 'category': 'WellTraceSubject'},
            {'object_id': 'log', 'name': 'GR', 'category': 'WellLogSubject', 'parent_id': 'well'},
            {'object_id': 'orphan', 'name': 'Alpha lookalike', 'category': 'WellLogSubject'},
            {'object_id': 'model', 'name': 'Reservoir A', 'category': 'PillarGrid2'},
            {'object_id': 'property', 'name': 'Porosity', 'category': 'FloatProperty', 'parent_id': 'model'},
            {'object_id': 'surface', 'name': 'Top A', 'category': 'SurfaceSubject'},
            {'object_id': 'mystery', 'name': 'Odd geometry', 'category': 'InterpretationMeshData'},
            {'object_id': 'config', 'name': 'UI palette', 'category': 'ContColorTableSubject'},
        ], 'seismic_objects': [{'object_id': 'seis', 'name': 'Volume A', 'category': 'seismic_volume', 'status': 'metadata_only'}]}
        (self.package/'01_project_metadata/project_context.json').write_text(json.dumps(context), encoding='utf-8')
        (self.exports/'logs').mkdir()
        (self.exports/'logs/GR.csv').write_text('md,raw_value\n1,5\n', encoding='utf-8')
        (self.exports/'logs/track.csv').write_text('well_name,md,x,y,z\nAlpha,1,1,2,3\n', encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'status': 'partial', 'files': [
            {'object_id': 'log', 'name': 'GR', 'category': 'logs', 'format': 'CSV', 'path': 'logs/GR.csv', 'status': 'exported'},
            {'object_id': 'log', 'name': 'GR', 'category': 'logs', 'format': 'JSON', 'path': 'logs/no.json', 'status': 'exported'},
            {'object_id': 'track', 'name': 'Track', 'category': 'trajectories', 'format': 'CSV', 'path': 'logs/track.csv', 'status': 'exported'},
            {'object_id': 'seis', 'name': 'Volume A', 'category': 'seismic', 'format': 'SEGY', 'path': '../escape.sgy', 'status': 'exported'}
        ]}), encoding='utf-8')
        result = self.publish(status='running')
        data = payload(self.report); nodes = flatten(data['categories'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual({x['id'] for x in nodes['well']['children']}, {'log', 'track'})
        self.assertNotIn('orphan', [x['id'] for x in nodes['well']['children']])
        self.assertEqual([x['id'] for x in nodes['model']['children']], ['property'])
        self.assertEqual(data['counts']['Seismic'], 1)
        self.assertEqual(len(nodes['log']['files']), 1)
        self.assertEqual(nodes['seis']['files'], [])
        self.assertNotIn('config', nodes)
        self.assertIn('mystery', nodes)
        self.assertNotIn('<script>alert(1)</script>', self.report.read_text(encoding='utf-8'))

    def test_no_index_and_relocation(self):
        (self.package/'01_project_metadata/project_context.json').write_text(json.dumps({'objects': [
            {'object_id': 'model', 'name': 'Model', 'category': 'PillarGrid2'}]}), encoding='utf-8')
        result = self.publish(status='running')
        self.assertEqual(result['counts']['Models'], 1)
        self.assertTrue(result['warnings'])
        self.report.unlink()
        (self.exports/'faults').mkdir()
        (self.exports/'faults/F.csv').write_text('x,y,z\n1,2,3\n', encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': [
            {'object_id': 'f', 'name': 'Fault', 'category': 'polygons', 'format': 'CSV',
             'path': 'faults/F.csv', 'status': 'exported'}]}), encoding='utf-8')
        self.publish()
        nodes = flatten(payload(self.report)['categories'])
        href = nodes['f']['files'][0]['href']
        self.assertFalse(href.startswith('/'))
        moved = self.root / 'moved'; moved.mkdir()
        os.replace(self.report, moved/self.report.name)
        os.replace(self.exports, moved/self.exports.name)
        self.assertTrue((moved / href).is_file())

    def test_large_csv_preview_is_bounded(self):
        (self.exports/'large.csv').write_text('md,raw_value\n' + '1,2\n'*100000, encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': [
            {'object_id': 'l', 'name': 'Large log', 'category': 'logs', 'format': 'CSV',
             'path': 'large.csv', 'status': 'exported'}]}), encoding='utf-8')
        self.publish()
        nodes = flatten(payload(self.report)['categories'])
        self.assertLessEqual(len(nodes['l']['preview']), 256)
        self.assertLess(self.report.stat().st_size, 150000)
        self.assertEqual(self.publish(status='failed')['report_path'], str(self.report))
        self.assertEqual(payload(self.report)['status'], 'failed')

    def test_null_flags_missing_file_and_primary_formats(self):
        (self.exports/'log_samples.csv').write_text(
            'md,raw_value,is_null,defined\n1,10,0,1\n2,-999.25,1,1\n3,30,0,1\n4,40,0,0\n5,1e309,0,1\n',
            encoding='utf-8')
        (self.exports/'log_audit.csv').write_text('sample,value\n1,1\n', encoding='utf-8')
        (self.exports/'log.las').write_text('~Version\n', encoding='utf-8')
        (self.exports/'grid.zmap').write_text('grid', encoding='utf-8')
        (self.exports/'grid.xyz').write_text('grid', encoding='utf-8')
        (self.exports/'grid_nodes.csv').write_text('x,y,raw_value,defined\n1,2,3,1\n4,5,-999,0\n', encoding='utf-8')
        (self.exports/'grid_cells.csv').write_text('x,y,raw_value\n0,0,0\n', encoding='utf-8')
        rows = [
            ('log', 'logs', 'LAS', 'log.las'), ('log', 'logs', 'CSV', 'log_audit.csv'),
            ('log', 'logs', 'CSV', 'log_samples.csv'), ('grid', 'grids', 'ZMAP', 'grid.zmap'),
            ('grid', 'grids', 'XYZ', 'grid.xyz'), ('grid', 'grids', 'CSV', 'grid_cells.csv'),
            ('grid', 'grids', 'CSV', 'grid_nodes.csv'),
            ('missing', 'seismic', 'SEGY', '../outside.sgy')]
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': [
            {'object_id': identity, 'name': identity, 'category': category, 'format': fmt,
             'path': path, 'status': 'exported'} for identity, category, fmt, path in rows]}), encoding='utf-8')
        self.publish()
        nodes = flatten(payload(self.report)['categories'])
        self.assertEqual([x['_valid'] for x in nodes['log']['preview']], [True, False, True, False, False])
        self.assertEqual(nodes['log']['preview'][1]['is_null'], '1')
        self.assertEqual([x['label'] for x in nodes['log']['files']], ['Download LAS', 'Download CSV'])
        self.assertTrue(nodes['log']['files'][1]['href'].endswith('log_samples.csv'))
        self.assertEqual([x['label'] for x in nodes['grid']['files']],
                         ['Download ZMAP', 'Download XYZ', 'Download CSV'])
        self.assertTrue(nodes['grid']['files'][2]['href'].endswith('grid_nodes.csv'))
        self.assertEqual(nodes['missing']['status'], 'unavailable')
        self.assertEqual(nodes['missing']['files'], [])
        html = self.report.read_text(encoding='utf-8')
        self.assertIn('<details class="panel">', html)
        self.assertIn('if(!p){started=false;continue}', html)

    def test_well_tops_have_their_own_inventory_category_without_invented_picks(self):
        self.publish(context={'objects':[{'object_id':'tops','name':'Well Tops','category':'WellTopsSubject'},{'object_id':'formation','name':'BCU','category':'WellTopsSurfaceSubject','parent_id':'tops'},{'object_id':'welltops','name':'A10 picks','category':'WellTopsWellSubject','parent_id':'tops'},{'object_id':'point','name':'Points','category':'PointSubject'}]})
        data=payload(self.report);nodes=flatten(data['categories'])
        self.assertEqual(data['counts']['Well tops'],3)
        self.assertEqual(data['counts']['Points'],1)
        self.assertEqual(nodes['formation']['kind'],'Well tops')
        self.assertEqual(nodes['welltops']['files'],[])
        self.assertEqual(nodes['welltops']['status'],'metadata_only')

    def test_verified_post_run_tops_bind_to_exact_wells_and_fail_closed_on_tampering(self):
        run=self.root/'case_data/RUN_RESULT.json';run.parent.mkdir();run.write_text('{"status":"completed_with_gaps"}')
        table=self.exports/'tops.csv';table.write_text('object_id,point_index,well_name,surface,measured_depth,x,y,z,well_link_uuid\nt,0,Alpha,BCU,1234,1,2,-1200,w\n')
        receipt=self.root/'Details/case_TOPS/RECOVERY.json';receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({'schema':'fieldviewer.native-tops/1','status':'verified','object_id':'t','row_count':1,'run_receipt_path':'case_data/RUN_RESULT.json','run_receipt_sha256':hashlib.sha256(run.read_bytes()).hexdigest(),'csv_path':'case_EXPORTS/tops.csv','csv_sha256':hashlib.sha256(table.read_bytes()).hexdigest(),'units':{'md':'m','z':'m'}}))
        context={'objects':[{'object_id':'t','name':'Tops','category':'WellTopsSubject'},{'object_id':'w','name':'Alpha','category':'WellTraceSubject'}]}
        self.publish(context=context);data=payload(self.report);nodes=flatten(data['categories'])
        self.assertEqual(nodes['w']['top_picks'][0]['surface'],'BCU')
        self.assertEqual(nodes['t']['top_picks'][0]['measured_depth'],'1234')
        self.assertEqual(data['well_top_status']['post_run_recovery']['rows'],1)
        self.assertEqual(nodes['t']['files'][0]['label'],'Download well tops (CSV)')
        table.write_text(table.read_text().replace('1234','9999'))
        result=self.publish(context=context);nodes=flatten(payload(self.report)['categories'])
        self.assertEqual(nodes['t']['top_picks'],[])
        self.assertTrue(any('checksum mismatch' in s for s in result['warnings']))

    def test_complete_polygon_preview_retains_parts_closure_and_late_vertices(self):
        table=self.exports/'linework.csv'
        table.write_text('part_index,vertex_index,is_closed_native,x,y\n'+''.join(f'0,{i},no,{i},0\n' for i in range(500))+'1,0,yes,0,10\n1,1,yes,10,10\n1,2,yes,10,20\n')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'object_id':'p','category':'polygons','format':'CSV','path':'linework.csv','status':'exported'}]}))
        self.publish(context={'objects':[{'object_id':'p','name':'Multipart','category':'PolygonsSubject'}]})
        n=flatten(payload(self.report)['categories'])['p'];g=n['polygon_preview']
        self.assertEqual(g['vertices'],503)
        self.assertEqual([len(p['points']) for p in g['parts']],[500,3])
        self.assertEqual(g['parts'][0]['points'][-1],[499,0])
        self.assertFalse(g['parts'][0]['closed']);self.assertTrue(g['parts'][1]['closed'])
        self.assertLess(len(n['preview']),g['vertices'])

    def test_native_bound_tops_require_receipt_hash_and_accepted_delivery(self):
        folder=self.package/'02_wells/well_tops';folder.mkdir(parents=True)
        table=folder/'native_well_top_picks_bound.csv'
        body='object_id,point_index,well_name,surface,measured_depth,x,y,z,well_link_uuid\nt,0,Alpha,BCU,1234,1,2,-1200,w\n'
        table.write_text(body);(self.exports/'picks.csv').write_text(body)
        (self.exports/'xyz.csv').write_text('x,y,z\n1,2,-1200\n')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'object_id':'t','category':'points','format':'CSV','path':'xyz.csv','status':'exported'}, {'object_id':'t','category':'well_tops','format':'CSV','path':'picks.csv','status':'exported'}]}))
        receipt=self.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json';receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({'well_top_binding':{'status':'native_schema_bound','row_count':1,'output_path':'02_wells/well_tops/native_well_top_picks_bound.csv','output_sha256':hashlib.sha256(table.read_bytes()).hexdigest()}}))
        context={'objects':[{'object_id':'t','name':'Tops','category':'WellTopsSubject'},{'object_id':'w','name':'Alpha','category':'WellTraceSubject'}]}
        self.publish(context=context);data=payload(self.report);nodes=flatten(data['categories'])
        self.assertEqual(nodes['w']['top_picks'][0]['surface'],'BCU')
        self.assertEqual(data['well_top_status']['native_binding']['rows'],1)
        self.assertEqual(nodes['t']['files'][0]['label'],'Download well tops (CSV)')
        self.assertTrue(nodes['t']['files'][0]['href'].endswith('/picks.csv'))
        self.assertEqual(len(nodes['t']['files']),2)
        self.assertNotIn('post_run_recovery',data['well_top_status'])
        self.assertEqual(data['well_top_status']['native_binding']['units'],{})
        (self.exports/'FILE_INDEX.json').write_text('{"files":[]}')
        self.publish(context=context);nodes=flatten(payload(self.report)['categories'])
        self.assertEqual(nodes['w']['top_picks'],[])
        table.write_text(body.replace('1234','8888'))
        self.assertTrue(any('checksum mismatch' in n for n in self.publish(context=context)['warnings']))

    def test_polygon_geometry_rejects_disordered_or_oversize_data(self):
        from geoviewer_summary import _polygon_geometry
        table=self.exports/'linework.csv'
        table.write_text('part_index,vertex_index,is_closed_native,x,y\n0,0,no,0,0\n0,2,no,1,1\n')
        self.assertIsNone(_polygon_geometry(table,{'bytes':0,'points':0}))
        table.write_text('part_index,vertex_index,is_closed_native,x,y\n'+''.join(f'0,{i},no,{i},0\n' for i in range(20001)))
        self.assertIsNone(_polygon_geometry(table,{'bytes':0,'points':0}))

    def test_top_point_geometry_is_not_promoted_to_validated_picks(self):
        (self.exports/'points.csv').write_text('x,y,z\n1,2,-3\n',encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'object_id':'tops','category':'points','format':'CSV','path':'points.csv','status':'exported'}]}))
        self.publish(context={'objects':[{'object_id':'tops','name':'Well Tops','category':'WellTopsSubject'}]})
        n=flatten(payload(self.report)['categories'])['tops']
        self.assertEqual(n['kind'],'Well tops')
        self.assertTrue(n['tops_geometry_only'])
        self.assertEqual(n['files'][0]['label'],'Download recovered XYZ points (CSV)')
        self.assertEqual(n['preview'][0]['z'],'-3')
        self.assertNotIn('md',n['preview'][0])

    def test_grid_image_uses_verified_bounded_preview_and_preserves_children(self):
        import numpy as np
        folder=self.package/'native_data/g';folder.mkdir(parents=True)
        path=folder/'grid_preview.npz'
        np.savez_compressed(path,value=np.array([[1.,2.,3.],[4.,5.,6.]]),valid=np.array([[True,False,True],[True,True,True]]),x=np.array([[10.,20.,30.],[10.,20.,30.]]),y=np.array([[100.,100.,100.],[200.,200.,200.]]))
        recovery=self.package/'07_workflows_reports/native_recovery';recovery.mkdir(parents=True)
        data={'objects':[{'object_id':'g','artifacts':[{'path':'g/grid_preview.npz','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}]}
        (recovery/'native_recovery_report.json').write_text(json.dumps(data))
        (self.exports/'surface.xyz').write_text('1 2 3\n')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'object_id':'g','category':'grids','format':'XYZ','path':'surface.xyz','status':'exported'}]}))
        context={'objects':[{'object_id':'g','name':'Grid','category':'SurfaceSubject'},{'object_id':'a','name':'Attribute','category':'SurfaceAttrSubject','parent_id':'g'}]}
        self.publish(context=context);nodes=flatten(payload(self.report)['categories'])
        self.assertEqual(nodes['g']['children'][0]['id'],'a')
        self.assertTrue(nodes['g']['files'])
        image=nodes['g']['grid_preview'];self.assertEqual((image['rows'],image['columns']),(2,3))
        self.assertTrue(base64.b64decode(image['image'].split(',')[1]).startswith(b'\x89PNG'))
        self.assertEqual(image['orientation'],{'coordinates_available':True,'rows_reversed':True,'columns_reversed':False})
        def pixels(image):
            raw=base64.b64decode(image['image'].split(',')[1]);offset=8;packed=b''
            while offset<len(raw):
                length=struct.unpack('>I',raw[offset:offset+4])[0]
                if raw[offset+4:offset+8]==b'IDAT':packed+=raw[offset+8:offset+8+length]
                offset+=length+12
            return zlib.decompress(packed)
        scan=pixels(image)
        self.assertEqual(scan[1:5],bytes((162,158,91,255))) # Highest Y: source value 4.
        self.assertEqual(scan[21],0) # Undefined node stays blank after the flip.
        np.savez_compressed(path,value=np.array([[1.,2.,3.],[4.,5.,6.]]),valid=np.ones((2,3),dtype=bool),x=np.array([[30.,20.,10.],[30.,20.,10.]]),y=np.array([[200.,200.,200.],[100.,100.,100.]]))
        data['objects'][0]['artifacts'][0]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (recovery/'native_recovery_report.json').write_text(json.dumps(data))
        self.publish(context=context)
        image=flatten(payload(self.report)['categories'])['g']['grid_preview']
        self.assertEqual(image['orientation'],{'coordinates_available':True,'rows_reversed':False,'columns_reversed':True})
        self.assertEqual(pixels(image)[1:5],bytes((118,132,114,255))) # Increasing X goes right.
        x,y=np.meshgrid(np.arange(4,dtype=float)*10,np.arange(4,dtype=float)*100)
        mask=np.zeros((4,4),dtype=bool);mask[1:3,1:3]=True
        x[~mask]=0;y[~mask]=0
        np.savez_compressed(path,value=np.arange(16,dtype=float).reshape(4,4),valid=mask,x=x,y=y)
        data['objects'][0]['artifacts'][0]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (recovery/'native_recovery_report.json').write_text(json.dumps(data))
        self.publish(context=context)
        image=flatten(payload(self.report)['categories'])['g']['grid_preview']
        self.assertEqual(image['orientation'],{'coordinates_available':True,'rows_reversed':True,'columns_reversed':False})
        data['objects'][0]['artifacts'][0]['sha256']='0'*64
        (recovery/'native_recovery_report.json').write_text(json.dumps(data))
        self.publish(context=context)
        self.assertIsNone(flatten(payload(self.report)['categories'])['g']['grid_preview'])

    def test_well_header_fields_and_missing_kb_td_are_not_inferred(self):
        (self.exports/'headers.csv').write_text('well_name,x,y,z,z_coordinate_status,KB,TD\nAlpha,123,456,-1200,observed_native_trajectory_start_z_not_resolved_as_datum_elevation,,\nBeta,0,0,2,,0,2400\n',encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'category':'well_headers','format':'CSV','path':'headers.csv','status':'exported'}]}))
        self.publish(context={'objects':[{'object_id':'a','name':'Alpha','category':'WellTraceSubject'},{'object_id':'b','name':'Beta','category':'WellTraceSubject'},{'object_id':'l','name':'GR','category':'WellLogSubject','parent_id':'a'}]})
        nodes=flatten(payload(self.report)['categories'])
        self.assertEqual(nodes['a']['header']['x'],'123')
        self.assertEqual(nodes['a']['header']['z'],'-1200')
        self.assertIsNone(nodes['a']['header']['kb'])
        self.assertIsNone(nodes['a']['header']['td'])
        self.assertEqual(nodes['b']['header']['kb'],'0')
        self.assertEqual(nodes['b']['header']['td'],'2400')
        self.assertEqual(nodes['a']['children'][0]['id'],'l')
        self.assertEqual(nodes['a']['header']['download'],'case_EXPORTS/headers.csv')

    def test_ambiguous_well_names_do_not_receive_each_others_headers(self):
        (self.exports/'headers.csv').write_text('well_name,x,y\nSame,1,2\n',encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'category':'well_headers','format':'CSV','path':'headers.csv','status':'exported'}]}))
        result=self.publish(context={'objects':[{'object_id':'a','name':'Same','category':'WellTraceSubject'},{'object_id':'b','name':'Same','category':'WellTraceSubject'}]})
        nodes=flatten(payload(self.report)['categories'])
        self.assertIsNone(nodes['a']['header']);self.assertIsNone(nodes['b']['header'])
        self.assertEqual(result['counts']['Wells'],2)
        self.assertTrue(any('ambiguous' in w for w in result['warnings']))

    def test_horizon_metadata_does_not_inflate_surface_grid_count(self):
        context={'objects':[
            {'object_id':'surface','name':'Top grid','category':'SurfaceSubject'},
            {'object_id':'attribute','name':'TWT','category':'SurfaceAttrSubject','parent_id':'surface'},
            {'object_id':'horizon','name':'Interpretation','category':'HorizonSubject'},
            {'object_id':'segment','name':'Segment','category':'HorizonSegSubject','parent_id':'horizon'},
            {'object_id':'filter','name':'Filter','category':'HorizonFilterSubject'},
            {'object_id':'cache','name':'Cache','category':'HorizonWellIntersectCache'},
            {'object_id':'tops','name':'Top picks','category':'WellTopsSurfaceSubject'}]}
        self.publish(context=context)
        data=payload(self.report);nodes=flatten(data['categories'])
        self.assertEqual(data['counts']['Surfaces / Grids'],2)
        self.assertEqual(data['counts']['Horizons / Interpretations'],2)
        self.assertEqual(nodes['segment']['kind'],'Horizons / Interpretations')
        self.assertEqual(nodes['horizon']['children'][0]['id'],'segment')
        self.assertEqual(nodes['tops']['kind'],'Well tops')
        self.assertNotIn('filter',nodes);self.assertNotIn('cache',nodes)

    def test_exact_seismic_transport_identity_is_one_dataset(self):
        (self.exports/'volume.segy').write_bytes(b'fixture')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'object_id':'transport','name':'Volume','category':'seismic','format':'SEGY','path':'volume.segy','status':'exported'}]}))
        context={'objects':[{'object_id':'native','name':'Volume','category':'SeismicVolume'}]}
        inventory={'objects':[{'id':'transport','name':'Volume','association':'exact_native_UUID_in_selected_store','native_objects':[{'object_id':'native'}]}]}
        self.publish(context=context,inventory=inventory)
        nodes=flatten(payload(self.report)['categories'])
        self.assertNotIn('transport',nodes)
        self.assertEqual(nodes['native']['files'][0]['label'],'Download SEGY')
        self.assertEqual(payload(self.report)['counts']['Seismic'],1)

    def test_preview_budget_reaches_later_logs_and_grids(self):
        (self.exports/'stations.csv').write_text('md,x,y,z\n'+('1,'+'2'*100+',3,4\n')*2000)
        (self.exports/'samples.csv').write_text('md,raw_value\n1,2\n2,3\n')
        (self.exports/'nodes.csv').write_text('x,y,z\n1,2,3\n2,3,4\n')
        rows=[{'object_id':f't{i}','category':'trajectories','format':'CSV','path':'stations.csv','status':'exported'} for i in range(24)]
        rows += [{'object_id':'late_log','category':'logs','format':'CSV','path':'samples.csv','status':'exported'},
                 {'object_id':'late_grid','category':'grids','format':'CSV','path':'nodes.csv','status':'exported'}]
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':rows}))
        self.publish();nodes=flatten(payload(self.report)['categories'])
        self.assertTrue(nodes['late_log']['preview'])
        self.assertTrue(nodes['late_grid']['preview'])
        self.assertLessEqual(sum(len(n.get('preview',[])) for n in nodes.values()),20000)

    def test_unknown_native_payload_and_total_preview_budget(self):
        registry = self.package/'01_project_metadata/native_data_object_registry.csv'
        registry.write_text('object_id,object_type,decode_status\n'
                            'novel,BrandNewNativePayload,not_decoded\n'
                            'ui,LayoutServiceData,not_decoded\n', encoding='utf-8')
        (self.package/'01_project_metadata/project_context.json').write_text(json.dumps({'objects': [
            {'object_id': 'p', 'name': 'Generic polygon', 'category': 'PolygonsSubject'},
            {'object_id': 'f', 'name': 'Fault interpretation', 'category': 'FaultInterpretation'},
            {'object_id': 'cfg', 'name': 'Palette', 'category': 'ContColorTableSubject'}]}), encoding='utf-8')
        sample = 'md,raw_value\n' + '1,2\n'*256
        rows = []
        for number in range(100):
            path = f'log{number}.csv'
            (self.exports/path).write_text(sample, encoding='utf-8')
            rows.append({'object_id': f'l{number}', 'name': f'Log {number}', 'category': 'logs',
                         'format': 'CSV', 'path': path, 'status': 'exported'})
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': rows}), encoding='utf-8')
        result = self.publish()
        data = payload(self.report); nodes = flatten(data['categories'])
        self.assertIn('novel', nodes)
        self.assertNotIn('ui', nodes)
        self.assertNotIn('cfg', nodes)
        self.assertEqual(nodes['p']['kind'], 'Polygons / Faults')
        self.assertEqual(nodes['f']['kind'], 'Polygons / Faults')
        total = sum(len(node.get('preview', [])) for node in nodes.values())
        self.assertLessEqual(total, 20000)
        self.assertTrue(any('preview budget' in warning for warning in result['warnings']))


if __name__ == '__main__':
    unittest.main()
