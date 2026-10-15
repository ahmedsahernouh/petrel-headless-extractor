# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Independent literal-name fixtures for bounded legacy trace preservation."""
import unittest
import tempfile, sqlite3, json, io
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout, redirect_stderr
from test_petrel_native_recovery import element, array, frame, envelope
import export_petrel_native_spatial_zero_gui as d

def fixture(version=None, count=2, xyz=None, md=None, incl=None, dtype='<f8', duplicate=False, attr=False):
    def arr(name, seq, size=None):
        return element(name, children=[array('double',seq,dtype)] if seq else [],
                       attrs={'Size': len(seq) if size is None else size})
    xyz=[10.,20.,-30.,11.,21.,-40.] if xyz is None else xyz
    md=[-3.,7.] if md is None else md
    points=element('trace_points',children=[arr('vertices',xyz,count),element('has_attr',attr),element('has_object_ids',False)])
    children=[points,arr('md_array',md),arr('incl_array',[0.,0.2] if incl is None else incl),arr('azim_array',[0.,1.] if count else [])]
    if duplicate:children.append(points)
    return frame(element('data',children=children,attrs={'Type':'WellTrace3','Version':version or [25,2,0,1,1]}))

class LegacyWellTraceTests(unittest.TestCase):
    def test_values_and_unknown_angles_preserved(self):
        rows,evidence=d.decode_trajectory(fixture(),'WellTrace3')
        self.assertEqual(rows[1],dict(record_index=1,x=11.,y=21.,z=-40.,md=7.,inclination_raw=0.2,azimuth_raw=1.))
        self.assertNotIn('inclination',rows[1])
        self.assertIn('unresolved',evidence['angle_units_status'])
        self.assertIn('No well head',evidence['well_head_boundary'])
    def test_second_observed_version(self):
        self.assertEqual(len(d.decode_legacy_welltrace(fixture([26,2,0,1,1]))[0]),2)
    def test_unknown_version_rejected(self):
        with self.assertRaisesRegex(d.DecodeError,'version/type'):d.decode_legacy_welltrace(fixture([27]))
    def test_array_length_mismatch_rejected(self):
        with self.assertRaises(d.DecodeError):d.decode_legacy_welltrace(fixture(xyz=[1.,2.,3.]))
    def test_station_alignment_rejected(self):
        with self.assertRaises(d.DecodeError):d.decode_legacy_welltrace(fixture(md=[1.]))
    def test_decreasing_md_rejected(self):
        with self.assertRaisesRegex(d.DecodeError,'MD decreases'):d.decode_legacy_welltrace(fixture(md=[2.,1.]))
    def test_null_or_nonfinite_rejected(self):
        for bad in (float('nan'),float('inf'),d.NATIVE_FLOAT_MAX_SENTINEL):
            with self.subTest(bad=bad),self.assertRaises(d.DecodeError):
                d.decode_legacy_welltrace(fixture(xyz=[bad,2.,3.,4.,5.,6.]))
    def test_ambiguous_duplicate_rejected(self):
        with self.assertRaises(d.DecodeError):d.decode_legacy_welltrace(fixture(duplicate=True))
    def test_unvalidated_attributes_rejected(self):
        with self.assertRaises(d.DecodeError):d.decode_legacy_welltrace(fixture(attr=True))
    def test_wrong_scalar_width_rejected(self):
        with self.assertRaises(d.DecodeError):d.decode_legacy_welltrace(fixture(dtype='<f4'))
    def test_empty_arrays_not_failure(self):
        rows,evidence=d.decode_legacy_welltrace(fixture(count=0,xyz=[],md=[],incl=[]))
        self.assertEqual(rows,[]);self.assertEqual(evidence['record_count'],0)
    def test_extra_document_rejected(self):
        raw=fixture()+fixture()[5:]
        with self.assertRaises(d.DecodeError):d.decode_legacy_welltrace(raw)
    def test_missing_or_ambiguous_model_identity_excludes_export(self):
        for objects in ([], [dict(object_id='legacy',category='SurfaceSubject',name='wrong')],
                        [dict(object_id='legacy',category='WellTraceSubject',name='A')]*2):
            with self.subTest(objects=objects),tempfile.TemporaryDirectory() as temp:
                root=Path(temp); store=root/'08_native_project/ptd_store';store.mkdir(parents=True)
                meta=root/'01_project_metadata';meta.mkdir()
                (meta/'project_context.json').write_text(json.dumps(dict(objects=objects,payload_type_counts={'WellTrace3':1})))
                with sqlite3.connect(store/'Data.ptd') as conn:
                    conn.execute('CREATE TABLE data(data_pk,droid,name,version,blob_type,time_stamp)')
                    conn.execute('CREATE TABLE blob_parts(data_fk,part,blob_data)')
                    conn.execute('INSERT INTO data VALUES(1,?,?,?,?,?)',('legacy','A',1,'WellTrace3',''))
                    conn.execute('INSERT INTO blob_parts VALUES(1,0,?)',(envelope(fixture()),))
                conn.close()
                args=SimpleNamespace(export_package=root,data_file=None,skip_well_top_calibration=True,
                                     well_tops_validation_csv=None,validation_tolerance=1e-6)
                with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):d.run(args)
                receipt=json.loads((root/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json').read_text())
                self.assertEqual(receipt['trajectory_rows'],0)
                self.assertEqual(receipt['coverage']['status'],'partial')
                self.assertIn('exact named WellTraceSubject',receipt['objects'][0]['error'])

if __name__=='__main__':unittest.main()
