"""Binary-conversion regression controls; synthetic inputs only.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import hashlib
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import petrel_file_convert as converter
import petrel_progress as progress
import report_petrel_project_audit as audit_report
warnings.filterwarnings('ignore',message='seismic store access is not available:')
from openzgy.api import ZgyWriter,SampleDataType,UnitDimension,ZgyCompressFactory


class BinaryConversionTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='Petrel Binary Test '))
        self.inputs=self.root/'sources';self.inputs.mkdir()
        self.outputs=self.root/'outputs'

    def fixture(self,name='fixture',datatype=SampleDataType.float,compressed=False,**overrides):
        shape=(64,64,64) if compressed else (3,4,16)
        data=np.random.default_rng(420).normal(0,20,shape).astype(np.float32)
        args=dict(size=shape,datatype=datatype,zstart=120.,zinc=2.,annotstart=(101,201),annotinc=(2,3),
            zunitdim=UnitDimension.time,zunitname='ms',zunitfactor=.001,
            hunitdim=UnitDimension.length,hunitname='m',hunitfactor=1.,
            corners=[(451000.,6780000.),(451000.+15*(shape[0]-1),6780000.+20*(shape[0]-1)),
                     (451000.+20*(shape[1]-1),6780000.-15*(shape[1]-1)),
                     (451000.+15*(shape[0]-1)+20*(shape[1]-1),6780000.+20*(shape[0]-1)-15*(shape[1]-1))])
        if datatype!=SampleDataType.float:args['datarange']=(-128.,127.)
        if compressed:args['compressor']=ZgyCompressFactory('ZFP',snr=30)
        args.update(overrides)
        path=self.inputs/(name+'.zgy')
        with ZgyWriter(str(path),**args) as writer:writer.write((0,0,0),data)
        return path

    def run_conversion(self,path,**options):
        return converter.execute(path,self.outputs,'zgy-to-segy',{'full_hash':True,**options})

    def verify_raw(self,path,result):
        run=next(self.outputs.iterdir());out=run/'volume.segy'
        with converter.open_zgy(path) as source, out.open('rb') as f:
            raw=f.read(3600)
            self.assertEqual(struct.unpack_from('>H',raw,3216)[0],2000)
            self.assertEqual(struct.unpack_from('>H',raw,3224)[0],5)
            ns=source.size[2]
            for i in range(source.size[0]):
                block=np.empty((1,source.size[1],ns),dtype=np.float32);source.read((i,0,0),block)
                for j in range(source.size[1]):
                    header=f.read(240);samples=f.read(ns*4)
                    self.assertEqual(struct.unpack_from('>ii',header,188),(101+i*2,201+j*3))
                    self.assertEqual(struct.unpack_from('>h',header,70)[0],-100)
                    self.assertEqual(struct.unpack_from('>h',header,108)[0],120)
                    self.assertEqual(struct.unpack_from('>ii',header,180),tuple(round(v*100) for v in source.indexToWorld((i,j))))
                    np.testing.assert_array_equal(np.frombuffer(samples,dtype='>f4'),block[0,j])
            self.assertFalse(f.read(1))
        self.assertEqual(result['status'],'passed')
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(result['source_hashes_before'],result['source_hashes_after'])
        self.assertEqual(result['artifacts'][0]['path'],'conversion_metadata.json')
        self.assertFalse(list(run.glob('*.partial.*')))
        for row in result['artifacts']:
            self.assertEqual(hashlib.sha256((run/row['path']).read_bytes()).hexdigest(),row['sha256'])

    def test_float32_rotated_grid_raw_bytes(self):
        p=self.fixture();self.verify_raw(p,self.run_conversion(p))

    def test_scaled_int8(self):
        p=self.fixture(datatype=SampleDataType.int8);self.verify_raw(p,self.run_conversion(p))

    def test_scaled_int16(self):
        p=self.fixture(datatype=SampleDataType.int16);self.verify_raw(p,self.run_conversion(p))

    def test_compressed_zfp(self):
        p=self.fixture(compressed=True);self.verify_raw(p,self.run_conversion(p))

    def test_missing_units_rejected_before_hash_or_output(self):
        p=self.fixture(zunitdim=UnitDimension.unknown,zunitname='',hunitdim=UnitDimension.unknown,hunitname='')
        with patch.object(progress,'hash_file',side_effect=AssertionError('must not hash unresolved input')):
            with self.assertRaises(converter.InputError):self.run_conversion(p)
        self.assertFalse(self.outputs.exists())

    def test_explicit_metadata_for_unknown_source(self):
        p=self.fixture(zunitdim=UnitDimension.unknown,zunitname='',hunitdim=UnitDimension.unknown,hunitname='')
        r=self.run_conversion(p,domain='time',vertical_unit='ms',horizontal_unit='m',crs='unknown')
        self.assertFalse(r['summary']['profile']['crs_verified'])

    def test_depth_not_relabelled_time(self):
        p=self.fixture(zunitdim=UnitDimension.length,zunitname='m',zunitfactor=1.)
        with self.assertRaises(converter.InputError):self.run_conversion(p,domain='time',vertical_unit='ms')

    def test_fractional_sample_interval(self):
        p=self.fixture(zinc=2.0005)
        with self.assertRaises(converter.InputError):self.run_conversion(p)

    def test_fractional_origin(self):
        p=self.fixture(zstart=120.25)
        with self.assertRaises(converter.InputError):self.run_conversion(p)

    def test_conflicting_units(self):
        p=self.fixture()
        with self.assertRaises(converter.InputError):self.run_conversion(p,vertical_unit='s')
        with self.assertRaises(converter.InputError):self.run_conversion(p,horizontal_unit='ft')

    def test_degenerate_geometry(self):
        p=self.fixture()
        with converter.open_zgy(p) as reader:meta=converter.zgy_metadata(reader)
        meta['corners']=[[0,0]]*4
        with self.assertRaises(converter.InputError):converter.seismic_plan(meta,{})

    def test_output_inside_source_rejected(self):
        p=self.fixture()
        with self.assertRaises(converter.InputError):converter.execute(p,self.inputs/'bad','zgy-to-segy',{})
        self.assertFalse((self.inputs/'bad').exists())

    def test_truncated_source_rejected(self):
        p=self.inputs/'bad.zgy';p.write_bytes(b'not a ZGY file')
        with self.assertRaises(Exception):self.run_conversion(p)
        self.assertFalse(self.outputs.exists())

    def test_disk_full_retains_failure_receipt(self):
        p=self.fixture()
        with patch.object(converter.shutil,'disk_usage',return_value=type('Usage',(),{'free':0})()):
            with self.assertRaises(converter.InputError):self.run_conversion(p)
        r=json.loads(next(self.outputs.rglob('RUN_RESULT.json')).read_text())
        self.assertEqual(r['status'],'failed');self.assertFalse(list(self.outputs.rglob('volume.segy')))

    def test_cancelled_conversion_not_success(self):
        p=self.fixture()
        with patch.dict(converter.CONVERTERS,{'zgy-to-segy':lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())}):
            with self.assertRaises(KeyboardInterrupt):self.run_conversion(p)
        r=json.loads(next(self.outputs.rglob('RUN_RESULT.json')).read_text());self.assertEqual(r['status'],'cancelled')

    def test_source_change_rejects_output(self):
        p=self.fixture();original=converter.CONVERTERS['zgy-to-segy']
        def changed(*args):
            result=original(*args)
            with p.open('ab') as f:f.write(b'changed fixture')
            return result
        with patch.dict(converter.CONVERTERS,{'zgy-to-segy':changed}):
            with self.assertRaises((converter.InputError,OSError)):self.run_conversion(p)
        self.assertFalse(list(self.outputs.rglob('volume.segy')))
        self.assertEqual(json.loads(next(self.outputs.rglob('RUN_RESULT.json')).read_text())['status'],'failed')

    def test_no_universal_to_universal_features(self):
        self.assertEqual(set(converter.CONVERTERS),{'zgy-to-segy'})

    def test_native_counts_exclude_failed_empty_and_duplicate_objects(self):
        report=self.root/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        report.parent.mkdir(parents=True)
        def record(identity,status):
            return dict(object_id=identity,blob_type='Polygons3',status=status)
        converter.write_json(report,dict(object_type_counts={'Polygons3':4},objects=[
            record('a','decoded'),record('b','failed_closed'),record('c','empty_supported_object'),record('a','decoded')]))
        result=audit_report.gather_native_inventory(self.root)
        self.assertEqual(result['inspected_object_type_counts'],{'Polygons3':4})
        self.assertEqual(result['decoded_object_type_counts'],{'Polygons3':1})
        converter.write_json(report,dict(object_type_counts={'Polygons3':4}))
        self.assertEqual(audit_report.gather_native_inventory(self.root)['decoded_object_type_counts'],{})

    def test_report_does_not_count_companions_as_native_recovery(self):
        original=audit_report.render_html
        rendered=[]
        def render_with_companions(audit,title):
            audit['wells']['las_well_count']=99
            audit['well_tops'].update(available=True,pick_count=99)
            audit['surfaces'].update(available=True,summary={'exported':99})
            audit['seismic'].update(available=True,cube_count=99)
            result=original(audit,title);rendered.append(result);return result
        with patch.object(sys,'argv',['report','--export-package',str(self.root)]), \
             patch.object(audit_report,'render_html',side_effect=render_with_companions),redirect_stdout(io.StringIO()):
            self.assertEqual(audit_report.main(),0)
        for text in rendered:
            for name in ('Well logs','Regular-value grids','Seismic'):
                self.assertRegex(text,'<td>'+name+'</td><td>0</td><td>0</td>')
            self.assertIn('<td>Well tops (rows)</td><td>not enumerated</td><td>0</td>',text)

    def test_progress_units_and_completion(self):
        stream=io.StringIO();display=progress.ConsoleProgress(stages=5,stream=stream).start()
        try:
            progress.items('Conversion',50,100,display.started,'traces')
            self.assertIn('50/100 traces',display.render());self.assertNotIn('Hash ETA',display.render())
        finally:display.close(False)
        self.assertNotIn('5/5 stages complete',stream.getvalue())


if __name__=='__main__':unittest.main()
