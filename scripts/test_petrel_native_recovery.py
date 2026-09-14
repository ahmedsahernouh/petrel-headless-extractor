"""Synthetic end-to-end controls for native log/surface recovery.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
The fixture writer emits literal-name NBFX independently of the production reader.
"""
from pathlib import Path
import hashlib
import io
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
from contextlib import redirect_stdout, closing

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import lasio
import petrel_native_binary as b
import petrel_native_recovery as r
import report_petrel_project_audit as audit


def vint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128); value >>= 7
    out.append(value)
    return bytes(out)


def name(text):
    raw = text.encode('utf-8'); return vint(len(raw))+raw


def text(value):
    if isinstance(value, bool): return b'\x86' if value else b'\x84'
    if isinstance(value, int): return b'\x8c'+struct.pack('<i', value)
    if isinstance(value, float): return b'\x92'+struct.pack('<d', value)
    if isinstance(value, bytes): return b'\xa0'+struct.pack('<H', len(value))+value
    if isinstance(value, list): return b'\xa4'+b''.join(text(x) for x in value)+b'\xa6'
    raw = str(value).encode('utf-8'); return b'\x9a'+struct.pack('<H', len(raw))+raw


def element(label, value=None, *, children=(), attrs=None):
    result = b'\x40'+name(label)
    for key, item in (attrs or {}).items():
        result += b'\x08'+name(item) if key == 'xmlns' else b'\x04'+name(key)+text(item)
    result += b''.join(children)
    if value is not None: result += text(value)
    return result+b'\x01'


def array(label, values, dtype):
    codes = {'<f4': 0x91, '<f8': 0x93, '<i4': 0x8d, 'u1': 0xb5}
    data = np.asarray(values, dtype=dtype)
    return b'\x03'+element(label)+bytes([codes[dtype]])+vint(len(data))+data.tobytes()


def frame(*docs, split=False):
    result = b'BXML\x01'
    for doc in docs:
        blocks = [doc[:len(doc)//2], doc[len(doc)//2:]] if split else [doc]
        for block in blocks: result += b'\xa0'+vint(len(block))+block
        result += b'\xa2'
    return result


def envelope(payload):
    size = len(payload); block = bytes([min(size, 15) << 4])
    if size >= 15:
        n = size-15
        while n >= 255: block += b'\xff'; n -= 255
        block += bytes([n])
    block += payload
    return b'LZ4\x01'+struct.pack('<II', len(block), 0)+block


def log_doc(kind='FloatWellLog', md=None, values=None, char=False, intervals=False, version=None, base=0):
    md = [10.25, 11.25, 13.25] if md is None else md
    values = [1.25, r.FLOAT_NULL, 2.5] if values is None else values
    md_element = element('md', children=[array('double', md, '<f8')] if len(md) else [], attrs={'Size': len(md)})
    val = element('char_values', bytes(values), attrs={'Size': len(values)}) if char else element('float_values', children=[array('float', values, '<f4')] if len(values) else [], attrs={'Size': len(values)})
    return element('data', children=[md_element, element('log_values', children=[element('is_char', char), val]),
                   element('min_index', base), element('has_discrete_intervals', intervals)],
                   attrs={'xmlns': r.NAMESPACE, 'Type': kind, 'Version': version or r.PROFILES[kind]})


def surface_doc(kind='RegValGrid2', rotation=0, context=True, dims=(3, 2), mask=b'\x1f', connections=False):
    n = dims[0]*dims[1]
    vals = list(range(1, n+1))
    children = [element('user_data', attrs={'Size': 0}), element('node_size', children=[array('int', dims, '<i4')]),
                element('has_node_defs', True), element('has_cell_defs', False),
                element('has_connections', connections), element('has_segments', False),
                element('node_defs', children=[element('bool_count', n), element('bitmask', mask)])]
    if kind == 'RegValGrid2':
        for field, val in [('original_inc', [20., 30.]), ('original_min', [100., 200.]),
                           ('original_max', [100.+20*(dims[0]-1), 200.+30*(dims[1]-1)])]:
            children.append(element(field, children=[array('double', val, '<f8')]))
        children += [element('has_coordinate_context', context), element('axis_flip_state', 0),
                     element('original_rotation', children=[element('radians', rotation)]),
                     element('dip', children=[element('radians', 0)])]
        children.append(element('grid', children=[array('node', vals, '<f4')], attrs={'Size': -1}))
    else:
        xyz = [[100.+i*20, 200.+j*30, vals[j*dims[0]+i]] for j in range(dims[1]) for i in range(dims[0])]
        children.append(element('grid', children=[array('node', np.ravel(xyz), '<f8')], attrs={'Size': -1}))
    return element('data', children=children, attrs={'xmlns': r.NAMESPACE, 'Type': kind, 'Version': r.PROFILES[kind]})


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Petrel native fixture ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ids = {key: str(uuid.uuid4()) for key in ('well', 'log', 'surface', 'template')}

    def fixture(self, *, kind='FloatWellLog', doc=None, metric=True, surface=False):
        package = self.root/'package'; native = package/'08_native_project'
        (native/'project_file').mkdir(parents=True); (native/'ptd_store').mkdir()
        tag = self.ids['surface' if surface else 'log']
        subject = 'SurfaceSubject' if surface else 'WellLogSubject'
        stub = element('item', children=[element('uniqueTag', tag), element('parentTag', '' if surface else self.ids['well']), element('visualName', 'Surface' if surface else 'Gamma')], attrs={'Type': 'SubjectStub'})
        wellstub = element('item', children=[element('uniqueTag', self.ids['well']), element('parentTag', ''), element('visualName', 'Synthetic Well')], attrs={'Type': 'SubjectStub'})
        def entry(key, child): return element('entry', children=[element('key', key), element('value', children=[child])])
        project = element('ProjectSerializer', children=[element('class_infos', children=[element('map', children=[entry(subject, stub), entry('WellTraceSubject', wellstub)])])])
        (native/'project_file/test.pet').write_bytes(b'\xff\xff\x01\x00\x11\x00ProjectSerializer'+bytes(9)+envelope(frame(project)))
        attrs = {'xmlns': r.NAMESPACE}
        meta = [element('unique_tag', tag), element('template', self.ids['template']), element('name', 'Gamma')]
        if surface:
            meta.append(element('cached_limit', children=[element('min', children=[array('double', [100., 200., 1.], '<f8')]), element('max', children=[array('double', [140., 230., 5.], '<f8')])]))
        model = element(subject, children=meta, attrs=attrs)
        template = element('ContTemplateSubject', children=[element('unique_tag', self.ids['template']), element('is_predefined', True), element('initial_unit', 'm' if surface else 'gAPI'), element('standard_measurement', 'Length' if surface else 'API_Gamma_Ray')], attrs=attrs)
        units = [element('temp_'+key+'_unit', val) for key,val in [('xy','m'), ('z','m' if metric else 'ft'), ('time','ms')]]
        units += [element(key+'_unit_customized', False) for key in ('xy','z','time')]
        settings = element('ProjectSubject', children=[element('style', children=[element('units_style', children=units), element('proj_unit_system', children=[element('name', 'Metric')]), element('proj_units_customized', False)])], attrs=attrs)
        (native/'ptd_store/Model.ptd').write_bytes(envelope(frame(model, template, settings, split=True)))
        path = native/'ptd_store/Data.ptd'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE data(data_pk INTEGER PRIMARY KEY,droid TEXT,name TEXT,version INTEGER,blob_type TEXT); CREATE TABLE blob_parts(data_fk INTEGER,part INTEGER,blob_data BLOB);')
            db.execute('INSERT INTO data VALUES(1,?,?,1,?)', ('://Petrel/'+tag, '', kind))
            db.execute('INSERT INTO blob_parts VALUES(1,0,?)', (envelope(frame(doc or (surface_doc(kind) if surface else log_doc(kind)), split=True)),))
            db.execute("INSERT INTO data VALUES(2,'://Petrel/UnrelatedMetadata','',1,'')")
        return package

    def run_fixture(self, **kwargs):
        package = self.fixture(**kwargs)
        with redirect_stdout(io.StringIO()): report = r.run(package)
        self.assertTrue(report.get('source_unchanged'), report)
        return package, report, report['objects'][0]

    def test_continuous_irregular_log_end_to_end(self):
        package, report, record = self.run_fixture()
        self.assertEqual(record['status'], 'decoded'); self.assertEqual(record['las_status'], 'written_verified')
        out = package/'native_data'
        las = lasio.read(next(out.rglob('*.las')), null_policy='strict')
        np.testing.assert_array_equal(las.index, [10.25, 11.25, 13.25])
        self.assertEqual(las.well.STEP.value, 0); self.assertTrue(np.isnan(las.data[1,1]))
        self.assertEqual(las.well.UWI.value, self.ids['well'])
        for artifact in record['artifacts']: self.assertEqual(r.sha(out/artifact['path']), artifact['sha256'])

    def test_character_encoding_and_null(self):
        _, _, record = self.run_fixture(doc=log_doc(char=True, values=[0,255,3]))
        self.assertEqual(record['null_count'], 1); self.assertEqual(record['status'], 'decoded')

    def test_discrete_intervals_remain_boundary_csv(self):
        _, _, record = self.run_fixture(kind='IntWellLog', doc=log_doc('IntWellLog', char=True, values=[2,255,4], intervals=True))
        self.assertEqual(record['las_status'], 'not_written')
        self.assertTrue(record['interval_records']); self.assertEqual(record['sample_count'], 3)

    def test_unknown_units_do_not_become_las(self):
        package, _, record = self.run_fixture(metric=False)
        self.assertEqual(record['status'], 'missing_metadata'); self.assertFalse(list(package.rglob('*.las')))
        self.assertTrue(list(package.rglob('samples.csv')))

    def test_empty_log_not_counted_as_decoded(self):
        _, _, record = self.run_fixture(doc=log_doc(md=[], values=[]))
        self.assertEqual(record['status'], 'empty_supported_object')

    def test_source_version_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(version=[99]))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_nonzero_log_base_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(base=1))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_mismatched_log_lengths_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(values=[1.]))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_nonfinite_index_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(md=[1.,float('nan'),2.]))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_unordered_log_preserved_without_las(self):
        _, _, record = self.run_fixture(doc=log_doc(md=[13.,10.,11.]))
        self.assertEqual(record['las_status'], 'not_written')

    def test_las_null_collision_preserves_real_value(self):
        package, _, record = self.run_fixture(doc=log_doc(values=[-999.25,r.FLOAT_NULL,2.]))
        las = lasio.read(next(package.rglob('curve.las')), null_policy='strict')
        self.assertEqual(las.data[0,1], -999.25); self.assertTrue(np.isnan(las.data[1,1]))

    def test_regular_surface_geometry_mask_and_cells(self):
        package, _, record = self.run_fixture(kind='RegValGrid2', surface=True)
        self.assertEqual(record['status'], 'decoded'); self.assertEqual(record['defined_nodes'], 5)
        xyz = np.loadtxt(next(package.rglob('surface.xyz')))
        np.testing.assert_array_equal(xyz, [[100,200,1],[120,200,2],[140,200,3],[100,230,4],[120,230,5]])
        self.assertEqual(len(np.loadtxt(next(package.rglob('cells.csv')),delimiter=',',skiprows=1,ndmin=2)), 2)

    def test_explicit_xyz_surface(self):
        _, _, record = self.run_fixture(kind='ValGrid2', surface=True)
        self.assertEqual(record['status'], 'decoded'); self.assertEqual(record['model_bounds_check'], 'passed')

    def test_surface_rotation_rejected(self):
        _, _, record = self.run_fixture(kind='RegValGrid2', surface=True, doc=surface_doc(rotation=0.3))
        self.assertEqual(record['status'], 'missing_metadata')

    def test_inherited_surface_geometry_rejected(self):
        _, _, record = self.run_fixture(kind='RegValGrid2', surface=True, doc=surface_doc(context=False))
        self.assertEqual(record['status'], 'missing_metadata')

    def test_topology_not_discarded_silently(self):
        _, _, record = self.run_fixture(kind='RegValGrid2', surface=True, doc=surface_doc(connections=True))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_bad_mask_length_rejected(self):
        _, _, record = self.run_fixture(kind='RegValGrid2', surface=True, doc=surface_doc(mask=b'\xff\xff'))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_source_change_invalidates_run(self):
        package = self.fixture(); original = r.recover_object
        def changed(*args):
            result = original(*args)
            with (package/'08_native_project/ptd_store/Model.ptd').open('ab') as stream: stream.write(b'changed')
            return result
        with patch.object(r, 'recover_object', side_effect=changed), redirect_stdout(io.StringIO()): report = r.run(package)
        self.assertEqual(report['status'], 'failed'); self.assertFalse(report['source_unchanged'])

    def test_missing_parts_rejected(self):
        package = self.fixture()
        with closing(sqlite3.connect(package/'08_native_project/ptd_store/Data.ptd')) as db, db:
            raw = db.execute('SELECT blob_data FROM blob_parts').fetchone()[0]
            db.execute('UPDATE blob_parts SET blob_data=?',(raw[:10],));db.execute('INSERT INTO blob_parts VALUES(1,2,?)',(raw[10:],))
        with redirect_stdout(io.StringIO()): report = r.run(package)
        self.assertEqual(report['objects'][0]['status'], 'unsupported_layout')

    def test_newer_unsupported_type_hides_old_log(self):
        package = self.fixture()
        with closing(sqlite3.connect(package/'08_native_project/ptd_store/Data.ptd')) as db, db:
            db.execute('INSERT INTO data SELECT 3,droid,name,2,? FROM data WHERE data_pk=1',('UnsupportedNewLog',))
        with redirect_stdout(io.StringIO()): report = r.run(package)
        self.assertEqual(report['objects'], [])

    def test_existing_output_not_overwritten(self):
        package, _, _ = self.run_fixture()
        with self.assertRaises(FileExistsError): r.run(package)

    def test_coverage_counts_only_valid_native_outputs(self):
        package, report, record = self.run_fixture()
        counts = audit.gather_native_inventory(package)
        self.assertEqual(counts['decoded_object_type_counts'], {'FloatWellLog': 1})
        self.assertEqual(counts['native_las_files'], 1)
        path = package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        report['status'] = 'failed'; r.write_json(path, report)
        self.assertEqual(audit.gather_native_inventory(package)['decoded_object_type_counts'], {})
        report['status'] = 'partial'; report['source_unchanged'] = False; r.write_json(path, report)
        self.assertEqual(audit.gather_native_inventory(package)['decoded_object_type_counts'], {})

    def test_partial_output_never_renamed_success(self):
        package = self.fixture()
        with patch.object(r, 'write_las', side_effect=r.NativeError('Injected read-back failure')), redirect_stdout(io.StringIO()): report = r.run(package)
        self.assertEqual(report['objects'][0]['status'], 'unsupported_layout')
        self.assertEqual(report['objects'][0]['artifacts'], [])
        self.assertTrue(list(package.rglob('*.partial')))

    def test_length_framing_not_marker_scan(self):
        raw = bytes([0xa0,0xa1,0xa2,1,3,0x42])*12
        node = next(b.read_documents(frame(element('root', raw), split=True)))
        self.assertEqual(node.scalar(),raw)

    def test_dictionary_element(self):
        payload = b'BXML\x01\xa1'+name('root')+b'\xa0\x03\x42\x00\x83\xa2'
        node = next(b.read_documents(payload)); self.assertEqual((node.name,node.scalar()),('root',1))

    def test_truncated_and_overflow_containers(self):
        raw = envelope(frame(log_doc()))
        for bad in (raw[:-1], b'LZ4\x01'+bytes(8), raw[:4]+struct.pack('<I',1)+raw[8:]):
            with self.assertRaises(b.NativeError): b.object_document(bad)
        with self.assertRaises(b.NativeError): b.Cursor(b'\xff\xff\xff\xff\x08').varint()
        with self.assertRaises(b.NativeError): b.decompress(raw,limit=4)

    def test_ambiguous_field_rejected(self):
        n = next(b.read_documents(frame(element('root',children=[element('a',1),element('a',2)]))))
        with self.assertRaises(b.NativeError): n.get('a')

    def test_invalid_boolean_array_rejected(self):
        with self.assertRaises(b.NativeError): next(b.read_documents(frame(element('root',children=[array('bool',[0,2],'u1')]))))

    def test_unbalanced_document_rejected(self):
        with self.assertRaises(b.NativeError): next(b.read_documents(frame(element('root')[:-1])))


if __name__ == '__main__': unittest.main()
