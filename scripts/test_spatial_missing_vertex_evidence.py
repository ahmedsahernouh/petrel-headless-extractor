# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Missing native components must survive without being accepted as geometry."""
import argparse,csv,hashlib,json,sqlite3,struct,tempfile,unittest
from pathlib import Path
import export_petrel_native_spatial_zero_gui as spatial
import geoviewer_delivery as delivery
from geoviewer_contract import publish_contract,POINTER
from validate_geoviewer_contract import validate_contract
import test_petrel_native_recovery as fixture

OID='11111111-1111-1111-1111-111111111111'
M=spatial.NATIVE_FLOAT_MAX_SENTINEL

def points_doc(points):
    return fixture.element('data',attrs={'Type':'Points3','Version':[1,2,0,1,1],'xmlns':fixture.r.NAMESPACE},children=[
        fixture.element('user_data',attrs={'Size':0}),
        fixture.element('vertices',attrs={'Size':len(points)},children=[fixture.array('double',[v for p in points for v in p],'<f8')]),
        fixture.element('has_attr',False),fixture.element('has_object_ids',False)])

class MissingVertexEvidenceTests(unittest.TestCase):
    def run_case(self,kind,doc):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);root=Path(t.name)
        package=root/'data/package';store=package/'08_native_project/ptd_store';store.mkdir(parents=True)
        path=store/'Data.ptd'
        db=sqlite3.connect(path)
        try:
            db.executescript('CREATE TABLE data(data_pk INTEGER,droid TEXT,name TEXT,version INTEGER,blob_type TEXT,time_stamp TEXT); CREATE TABLE blob_parts(data_fk INTEGER,part INTEGER,blob_data BLOB);')
            db.execute('INSERT INTO data VALUES(1,?,"Native",1,?,"")',(OID,kind))
            db.execute('INSERT INTO blob_parts VALUES(1,0,?)',(fixture.envelope(fixture.frame(doc,split=True)),));db.commit()
        finally:db.close()
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        spatial.run(argparse.Namespace(export_package=str(package),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02))
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
        rp=package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        return root,package,json.loads(rp.read_text(encoding='utf-8-sig')),rp

    def rows(self,path):
        with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

    def test_polygon_gaps_preserve_raw_components_part_ordinals_and_signed_zero(self):
        parts=[[(1.,2.,3.),(M,-0.,42.)],[],[(7.,-M,9.)]]
        root,p,r,_=self.run_case('Polygons3',fixture.polygon_doc(parts))
        rows=self.rows(p/'05_spatial/polygons/native_polygon_missing_vertices.csv')
        self.assertEqual([(x['part_index'],x['vertex_index']) for x in rows],[('0','1'),('2','0')])
        for row,expected in zip(rows,[parts[0][1],parts[2][0]]):
            self.assertEqual([row[f'{axis}_raw_ieee754_le'] for axis in 'xyz'],[struct.pack('<d',v).hex() for v in expected])
        self.assertEqual(rows[0]['y_state'],'finite');self.assertEqual(rows[0]['x_state'],'native_missing_sentinel')
        self.assertEqual(r['polygon_missing_vertex_rows'],2);self.assertEqual(r['polygon_vertex_rows'],1)
        index=delivery.publish_exports(p,{'objects':[]},root/'EXPORTS')
        self.assertEqual({x['category'] for x in index['files']},{'polygons','polygon_parts','polygon_missing_vertices'})
        publish_contract(p,root/'EXPORTS',index)
        assessed=validate_contract(root/'EXPORTS'/POINTER,'opendtect')['assessments'][0]
        self.assertIn('NATIVE_VERTEX_GAPS_UNRESOLVED',assessed['reason_codes'])
        self.assertIn('NATIVE_VERTEX_GAPS_UNRESOLVED',assessed['receiving_assessment']['reason_codes'])

    def test_all_missing_points_have_evidence_without_full_geometry(self):
        root,p,r,_=self.run_case('Points3',points_doc([(M,10.,20.),(30.,M,40.)]))
        self.assertEqual(r['objects'][0]['status'],'empty_supported_object')
        rows=self.rows(p/'05_spatial/points/native_point_missing_vertices.csv')
        self.assertEqual([x['vertex_index'] for x in rows],['0','1'])
        self.assertEqual(rows[0]['y_raw'],'10.0');self.assertEqual(r['point_missing_vertex_rows'],2)
        ex=root/'EXPORTS';index=delivery.publish_exports(p,{'objects':[]},ex)
        self.assertEqual(len(index['files']),1);self.assertEqual(index['files'][0]['category'],'point_missing_vertices')
        pointer=publish_contract(p,ex,index);manifest=json.loads((ex/pointer['manifest']).read_text())
        artifact=json.loads(((ex/pointer['manifest']).parent/manifest['tables']['artifacts']['path']).read_text())
        self.assertEqual(artifact['role'],'missing_vertex_evidence')
        result=validate_contract(ex/POINTER)
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',result['assessments'][0]['reason_codes'])

    def test_failed_object_does_not_leak_partial_missing_slot_evidence(self):
        root,p,r,_=self.run_case('Polygons3',fixture.polygon_doc([[(M,2.,3.),(1.,float('nan'),3.)]]))
        self.assertEqual(r['objects'][0]['status'],'failed_closed')
        self.assertFalse((p/'05_spatial/polygons/native_polygon_missing_vertices.csv').exists())
        self.assertEqual(r['polygon_missing_vertex_rows'],0)

    def test_checkpoint_missing_slot_count_mismatch_is_excluded(self):
        root,p,r,rp=self.run_case('Points3',points_doc([(M,10.,20.)]))
        good=delivery.publish_exports(p,{'objects':[]},root/'good',spatial_checkpoint=True)
        self.assertEqual(good['files'][0]['record_count'],1)
        r['point_missing_vertex_rows']=2;rp.write_text(json.dumps(r))
        bad=delivery.publish_exports(p,{'objects':[]},root/'bad',spatial_checkpoint=True)
        self.assertEqual(bad['files'],[])

    def test_compact_slots_do_not_allocate_a_dictionary_per_vertex(self):
        raw=bytearray();points,meta=spatial.decode_points3(fixture.frame(points_doc([(M,2.,3.)]*5000)),missing_slot_bytes=raw)
        self.assertEqual(len(raw),32*5000);self.assertEqual(meta['missing_vertex_slots'],5000)
        rows=spatial.MissingVertexRows();rows.add(OID,1,raw)
        self.assertEqual(len(rows),5000);self.assertEqual([x['vertex_index'] for x in rows[2047:2049]],[2047,2048])

    def test_masked_nonfinite_component_retains_bits_not_a_complete_coordinate(self):
        # A missing X masks the entire native slot under the existing profile.
        nan=struct.unpack('<d',bytes.fromhex('210000000000f87f'))[0]
        root,p,r,_=self.run_case('Points3',points_doc([(M,nan,float('inf'))]))
        row=self.rows(p/'05_spatial/points/native_point_missing_vertices.csv')[0]
        self.assertEqual(row['y_raw_ieee754_le'],'210000000000f87f')
        self.assertEqual(row['z_state'],'nonfinite');self.assertEqual(row['geometry_status'],'missing_native_slot_not_geometry')
        self.assertEqual(r['point_vertex_rows'],0)

    def test_old_part_counts_also_block_silent_gap_filling(self):
        root,p,r,rp=self.run_case('Polygons3',fixture.polygon_doc([[(1.,2.,3.),(M,2.,3.)]]))
        del r['objects'][0]['missing_vertex_slots'];rp.write_text(json.dumps(r))
        ex=root/'EXPORTS';index=delivery.publish_exports(p,{'objects':[]},ex);publish_contract(p,ex,index)
        self.assertIn('NATIVE_VERTEX_GAPS_UNRESOLVED',validate_contract(ex/POINTER)['assessments'][0]['reason_codes'])

    def test_current_no_gap_run_removes_stale_generated_auxiliary_table(self):
        root,p,r,_=self.run_case('Points3',points_doc([(M,2.,3.)]))
        table=p/'05_spatial/points/native_point_missing_vertices.csv';self.assertTrue(table.exists())
        source=p/'08_native_project/ptd_store/Data.ptd'
        db=sqlite3.connect(source)
        try:
            db.execute('UPDATE blob_parts SET blob_data=?',(fixture.envelope(fixture.frame(points_doc([(1.,2.,3.)]))),));db.commit()
        finally:db.close()
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        spatial.run(argparse.Namespace(export_package=str(p),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02))
        self.assertFalse(table.exists());self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())

if __name__=='__main__':unittest.main()
