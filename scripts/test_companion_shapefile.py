# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Companion geometry/attribute separation using independently packed SHP/DBF."""
import csv
import hashlib
import json
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import portable_petrel_companion_extract as companion


def shp(path, kind=1, bodies=None, bounds=(10., 20., 30., 40.)):
    """Write specified binary records directly; do not use the converter's reader/writer."""
    bodies = bodies if bodies is not None else [struct.pack('<i2d', 1, 10., 20.)]
    records = b''.join(struct.pack('>2i', n+1, len(body)//2)+body for n,body in enumerate(bodies))
    header = struct.pack('>7i',9994,0,0,0,0,0,(100+len(records))//2)
    header += struct.pack('<2i8d',1000,kind,*bounds,0.,0.,0.,0.)
    path.write_bytes(header+records)
    return path


def dbf(path, labels=('first',), deleted=()):
    header = struct.pack('<4BI2H20x',3,126,10,2,len(labels),65,13)
    field = struct.pack('<11sc4xBB14x',b'LABEL',b'C',12,0)
    path.write_bytes(header+field+b'\r'+b''.join((b'*' if n in deleted else b' ')+value.encode('ascii').ljust(12) for n,value in enumerate(labels))+b'\x1a')


class ShapefileCompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Companion SHP ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'input.shp'
        self.target = self.root/'new'/'converted.shp'

    def convert(self):
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        generated,summary = companion.convert_shapefile(self.source,self.target)
        after = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        self.assertEqual(before,after)
        with self.target.with_suffix('.csv').open(encoding='utf-8',newline='') as stream:
            rows = list(csv.DictReader(stream))
        features = json.loads(self.target.with_suffix('.geojson').read_text(encoding='utf-8'))['features']
        return generated,summary,rows,features

    def test_missing_dbf_preserves_point_and_declares_absent_attributes(self):
        shp(self.source)
        generated,summary,rows,features = self.convert()
        self.assertEqual(summary['attribute_status'],'missing_dbf_geometry_only')
        self.assertFalse(summary['attributes_complete'])
        self.assertEqual(summary['fields'],[])
        self.assertEqual((summary['records'],summary['total_vertices']),(1,1))
        self.assertEqual((float(rows[0]['geometry_x']),float(rows[0]['geometry_y'])),(10.,20.))
        self.assertEqual(features,[{'type':'Feature','properties':{},'geometry':{'type':'Point','coordinates':[10.,20.]}}])
        self.assertIn('missing',summary['validation_boundary'].lower())
        self.assertEqual(len(generated),3)

    def test_missing_dbf_preserves_multipart_lines_and_literal_crs(self):
        points=[(10.,20.),(11.,22.),(30.,40.),(32.,44.)]
        body=struct.pack('<i4d2i',3,10.,20.,32.,44.,2,4)+struct.pack('<2i',0,2)
        body += b''.join(struct.pack('<2d',*point) for point in points)
        shp(self.source,3,[body],(10.,20.,32.,44.))
        self.source.with_suffix('.prj').write_text('LOCAL_CS["literal unresolved CRS"]',encoding='utf-8')
        _,summary,rows,features=self.convert()
        self.assertEqual(features[0]['geometry'],{'type':'MultiLineString','coordinates':[[[10.,20.],[11.,22.]],[[30.,40.],[32.,44.]]]})
        self.assertEqual(rows[0]['geometry_vertex_count'],'4')
        self.assertEqual(summary['total_vertices'],4)
        self.assertEqual(summary['crs_status'],'source_sidecar_preserved_not_semantically_resolved')
        self.assertEqual(self.target.with_suffix('.prj').read_bytes(),self.source.with_suffix('.prj').read_bytes())

    def test_available_dbf_keeps_record_attributes(self):
        shp(self.source);dbf(self.source.with_suffix('.dbf'))
        _,summary,rows,features=self.convert()
        self.assertEqual(summary['attribute_status'],'dbf_records_read')
        self.assertTrue(summary['attributes_complete'])
        self.assertEqual(summary['fields'],['LABEL'])
        self.assertEqual(rows[0]['LABEL'],'first')
        self.assertEqual(features[0]['properties'],{'LABEL':'first'})

    def test_missing_dbf_point_z_preserves_explicit_csv_z_m(self):
        shp(self.source,11,[struct.pack('<i4d',11,10.,20.,-3.5,123.25)])
        _,summary,rows,_=self.convert()
        self.assertEqual((float(rows[0]['geometry_z']),float(rows[0]['geometry_m'])),(-3.5,123.25))
        self.assertFalse(summary['attributes_complete'])

    def test_missing_dbf_null_shape_remains_a_null_geometry(self):
        shp(self.source,0,[struct.pack('<i',0)],(0.,0.,0.,0.))
        _,summary,rows,features=self.convert()
        self.assertEqual(features[0]['geometry'],None)
        self.assertEqual((summary['records'],summary['total_vertices']),(1,0))
        self.assertEqual(rows[0]['geometry_vertex_count'],'0')

    def test_present_corrupt_dbf_never_falls_back_to_geometry_only(self):
        shp(self.source);self.source.with_suffix('.dbf').write_bytes(b'broken')
        with self.assertRaises(Exception):
            companion.convert_shapefile(self.source,self.target)
        self.assertFalse(self.target.with_suffix('.summary.json').exists())

    def test_present_dbf_count_mismatch_is_not_silent_geometry_loss(self):
        shp(self.source,bodies=[struct.pack('<i2d',1,10.,20.),struct.pack('<i2d',1,30.,40.)])
        dbf(self.source.with_suffix('.dbf'),('one',))
        with self.assertRaisesRegex(ValueError,'geometry/attribute record count'):
            companion.convert_shapefile(self.source,self.target)
        self.assertFalse(self.target.with_suffix('.summary.json').exists())
        self.assertFalse(self.target.with_suffix('.csv').exists())
        self.assertFalse(self.target.with_suffix('.geojson').exists())

    def test_deleted_attribute_slot_cannot_shift_values_to_another_geometry(self):
        shp(self.source,bodies=[struct.pack('<i2d',1,10.,20.),struct.pack('<i2d',1,30.,40.)])
        dbf(self.source.with_suffix('.dbf'),('deleted','second'),deleted=(0,))
        _,summary,rows,features=self.convert()
        self.assertEqual(summary['deleted_dbf_records'],1)
        self.assertFalse(summary['attributes_complete'])
        self.assertEqual([f['properties'] for f in features],[{}, {'LABEL':'second'}])
        self.assertEqual([r['source_shape_index'] for r in rows],['0','1'])
        self.assertEqual([r['source_attribute_status'] for r in rows],['dbf_record_marked_deleted','dbf_record_read'])
        self.assertEqual(features[1]['geometry']['coordinates'],[30.,40.])

    def test_existing_destination_is_not_overwritten(self):
        shp(self.source);self.target.parent.mkdir()
        keep=self.target.with_suffix('.csv');keep.write_bytes(b'unrelated')
        with self.assertRaisesRegex(ValueError,'already exists'):
            companion.convert_shapefile(self.source,self.target)
        self.assertEqual(keep.read_bytes(),b'unrelated')
        self.assertFalse(self.target.with_suffix('.summary.json').exists())

    def test_unreadable_dbf_is_not_treated_as_missing(self):
        shp(self.source);original=Path.open
        def guarded(path,*args,**kwargs):
            if path==self.source.with_suffix('.dbf'):
                raise PermissionError('DBF not readable')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',guarded),self.assertRaises(PermissionError):
            companion.convert_shapefile(self.source,self.target)
        self.assertFalse(self.target.with_suffix('.summary.json').exists())

    def test_failed_publication_cleans_its_new_files_and_releases_sources(self):
        shp(self.source);before=self.source.read_bytes()
        with patch.object(companion.shutil,'copyfileobj',side_effect=OSError('simulated output failure')):
            with self.assertRaisesRegex(OSError,'simulated output failure'):
                companion.convert_shapefile(self.source,self.target)
        self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(list(self.target.parent.iterdir()),[])
        # Windows permits this only after the source handles have been closed.
        self.source.rename(self.source.with_name('still-intact.shp'))

    def test_missing_dbf_pipeline_records_gap_and_indexes_geometry_files(self):
        from geoviewer_delivery import publish_exports
        powershell=shutil.which('powershell')
        if not powershell:
            self.skipTest('Windows PowerShell package validator unavailable')
        source=self.root/'source';source.mkdir()
        self.source=shp(source/'input.shp')
        self.source.with_suffix('.prj').write_text('LOCAL_CS["unresolved"]',encoding='utf-8')
        project=source/'Fixture.pet';project.write_bytes(b'fixture')
        (source/'Fixture.ptd').mkdir()
        package=self.root/'package';(package/'00_manifest').mkdir(parents=True)
        (package/'01_project_metadata').mkdir()
        (package/'01_project_metadata/project_summary.json').write_text('{}',encoding='utf-8')
        headers='project_name,petrel_version,source_object_type,source_object_path,export_file,export_format,export_status,validation_status,sha256,notes\n'
        (package/'00_manifest/export_manifest.csv').write_text(headers,encoding='utf-8')
        argv=['companion','--project-file',str(project),'--export-package',str(package),'--project-name','Fixture','--mode','convert']
        with patch.object(sys,'argv',argv):
            self.assertEqual(companion.main(),0)
        with (package/'00_manifest/companion_source_inventory.csv').open(encoding='utf-8',newline='') as stream:
            row=next(r for r in csv.DictReader(stream) if r['category']=='gis_shapefile_geometry')
        self.assertEqual(row['conversion_gap'],'missing_dbf_geometry_only')
        report=json.loads((package/'07_workflows_reports/portable_extractor/companion_capability_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['counts']['converted_sources_with_gaps'],1)
        with (package/'99_unexported_or_manual/portable_unsupported_inventory.csv').open(encoding='utf-8',newline='') as stream:
            self.assertEqual(next(csv.DictReader(stream))['conversion_gap'],'missing_dbf_geometry_only')
        validation=subprocess.run([powershell,'-NoProfile','-ExecutionPolicy','Bypass','-File',
            str(Path(__file__).with_name('validate_export_package.ps1')),'-ExportPackage',str(package),
            '-UpdateManifest','-FailOnMissingExports'],capture_output=True,text=True)
        self.assertEqual(validation.returncode,0,validation.stdout+validation.stderr)
        index=publish_exports(package,{'objects':[]},self.root/'EXPORTS')
        self.assertEqual(len(index['files']),4,index)
        self.assertEqual({r['category'] for r in index['files']},{'companion_gis'})
        self.assertEqual({Path(r['path']).suffix for r in index['files']},{'.csv','.geojson','.json','.prj'})
        self.assertTrue(all(r['source_lane']=='companion' for r in index['files']))


if __name__=='__main__':
    unittest.main()
