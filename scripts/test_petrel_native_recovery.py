# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
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


def chunked_envelope(*pieces):
    # One global magic. Later chunks contain only length/opaque words + block.
    return b'LZ4\x01'+b''.join(envelope(piece)[4:] for piece in pieces)


def log_doc(kind='FloatWellLog', md=None, values=None, char=False, intervals=False, version=None, base=0):
    md = [10.25, 11.25, 13.25] if md is None else md
    values = [1.25, r.FLOAT_NULL, 2.5] if values is None else values
    md_element = element('md', children=[array('double', md, '<f8')] if len(md) else [], attrs={'Size': len(md)})
    val = element('char_values', bytes(values), attrs={'Size': len(values)}) if char else element('float_values', children=[array('float', values, '<f4')] if len(values) else [], attrs={'Size': len(values)})
    return element('data', children=[md_element, element('log_values', children=[element('is_char', char), val]),
                   element('min_index', base), element('has_discrete_intervals', intervals)],
                   attrs={'xmlns': r.NAMESPACE, 'Type': kind, 'Version': version or r.PROFILES[kind]})


def polygon_doc(parts, closed=None, *, outer_count=None, inner_version=None, attributes=False):
    closed = closed or [False]*len(parts)
    items=[]
    for index,points in enumerate(parts):
        items.append(element('item',children=[element('user_data',attrs={'Size':0}),
            element('vertices',children=[array('double',np.ravel(points),'<f8')] if points else [],attrs={'Size':len(points)}),
            element('has_attr',False),element('has_object_ids',False),element('is_closed',closed[index])],
            attrs={'Id':index+2,'Type':'Polygon3','Version':inner_version or [0,1,2,0,1,1]}))
    children=[element('user_data',attrs={'Size':0}),element('array',children=items,
        attrs={'Size':len(parts) if outer_count is None else outer_count}),element('has_attr',attributes)]
    if attributes:children.append(element('attributes',children=[element('unresolved_property',42.)]))
    return element('data',children=children,attrs={'xmlns':r.NAMESPACE,'Id':1,'Type':'Polygons3','Version':[1,2,0,1,1]})


def surface_doc(kind='RegValGrid2', rotation=0, context=True, dims=(3, 2), mask=b'\x1f', connections=False, packed=False, legacy=False, consistent=None):
    n = dims[0]*dims[1]
    vals = list(range(1, n+1))
    children = [element('user_data', attrs={'Size': 0}), element('node_size', children=[array('int', dims, '<i4')]),
                element('has_node_defs', True), element('has_cell_defs', False),
                element('has_connections', connections), element('has_segments', False),
                (element('node_defs', children=[element('bools',mask)]+([element('ignore',b'\x00')] if n%8==0 else []), attrs={'Size':n})
                 if packed else element('node_defs', children=[element('bool_count', n), element('bitmask', mask)]))]
    if kind == 'RegValGrid2':
        for field, val in [('original_inc', [20., 30.]), ('original_min', [100., 200.]),
                           ('original_max', [100.+20*(dims[0]-1), 200.+30*(dims[1]-1)])]:
            children.append(element(field, children=[array('double', val, '<f8')]))
        children += [element('has_coordinate_context', context)]+([] if legacy else [element('axis_flip_state', 0)])+[
                     element('original_rotation', children=[element('radians', rotation)]),
                     element('dip', children=[element('radians', 0)])]
        children.append(element('grid', children=[array('node', vals, '<f4')], attrs={'Size': -1}))
    else:
        xyz = [[100.+i*20, 200.+j*30, vals[j*dims[0]+i]] for j in range(dims[1]) for i in range(dims[0])]
        children.append(element('grid', children=[array('node', np.ravel(xyz), '<f8')], attrs={'Size': -1}))
    if consistent is not None:children.append(element('is_known_consistent',consistent))
    return element('data', children=children, attrs={'xmlns': r.NAMESPACE, 'Type': kind, 'Version': r.REGULAR_GRID_V0 if legacy else r.PROFILES[kind]})


class RecoveryTests(unittest.TestCase):
    def test_nan_only_in_explicitly_undefined_regular_nodes(self):
        values = np.arange(1, 7, dtype='<f4')
        masked = values.copy(); masked[5] = np.nan
        doc = surface_doc().replace(values.tobytes(), masked.tobytes())
        node = b.object_document(envelope(frame(doc)))
        decoded = r.decode_surface(node, 'RegValGrid2')
        self.assertEqual(decoded[-1]['masked_nan_nodes'], 1)
        self.assertTrue(np.isnan(decoded[4][5]))
        self.assertFalse(decoded[5][5])
        package, _, record = self.run_fixture(kind='RegValGrid2', surface=True, doc=doc)
        self.assertEqual(record['numeric_round_trip'], 'exact')
        self.assertEqual(record['masked_nan_nodes'], 1)
        nodes = np.loadtxt(next(package.rglob('nodes.csv')), delimiter=',', skiprows=1)
        self.assertTrue(np.isnan(nodes[5, 5]))
        self.assertEqual(nodes[5, 6], 0)
        xyz = np.loadtxt(next(package.rglob('surface.xyz')))
        self.assertTrue(np.isfinite(xyz).all())
        for index, value in ((0, np.nan), (5, np.inf), (5, -np.inf)):
            bad = values.copy(); bad[index] = value
            bad_node = b.object_document(envelope(frame(surface_doc().replace(values.tobytes(), bad.tobytes()))))
            with self.assertRaisesRegex(b.NativeError, 'Non-finite surface payload'):
                r.decode_surface(bad_node, 'RegValGrid2')

    def test_modern_surface_consistency_field_does_not_bypass_checks(self):
        for kind in ('RegValGrid2','ValGrid2'):
            for flag in (True,False):
                node=b.object_document(envelope(frame(surface_doc(kind,consistent=flag))))
                decoded=r.decode_surface(node,kind)
                self.assertEqual(decoded[-1]['native_is_known_consistent'],flag)
                self.assertEqual(decoded[0].size,6)
            with self.assertRaises(b.NativeError):
                r.decode_surface(b.object_document(envelope(frame(surface_doc(kind,consistent='true')))),kind)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Petrel native fixture ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ids = {key: str(uuid.uuid4()) for key in ('well', 'log', 'surface', 'template')}

    def test_object_output_path_budget_and_identity(self):
        root = Path(r'\\192.0.2.123\example\fixtures\XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX')
        self.assertEqual(len(str(root)), 189)
        ids = ('00000000-0000-0000-0000-000000000001',
               '00000000-0000-0000-0000-000000000002')
        for kind in r.TYPES:
            for name in ('Top_HS_E', 'BF_2025_Faille_014_bis_and_a_much_longer_label'):
                info = dict(name=name, object_id=ids[0], blob_type=kind)
                plan = r.object_output_plan(root, info)
                self.assertTrue(plan['compact_staging'])
                identity = (uuid.UUID(ids[0]).hex if kind.endswith('WellLog')
                            else r.compact_object_id(ids[0]))
                self.assertIn(identity, plan['final_name'])
                final_paths = [root/plan['final_name']/Path(relative)
                               for relative in r._object_relative_paths(kind)]
                stage_paths = [root/'.g000000.partial'/Path(relative)
                               for relative in r._object_relative_paths(kind, staging=True)]
                self.assertTrue(all(len(str(path)) < 250 for path in final_paths))
                self.assertTrue(all(len(str(path)) < 250 for path in stage_paths))
                if kind == 'FloatWellLog':
                    self.assertTrue(any(str(path).endswith(r'las.partial\curve.las') for path in stage_paths))
                if kind == 'RegValGrid2':
                    for atomic in r.ATOMIC_TEMP_NAMES: self.assertTrue(any(path.name == atomic for path in stage_paths))
        first = r.object_output_plan(root, dict(name='same', object_id=ids[0], blob_type='RegValGrid2'))
        second = r.object_output_plan(root, dict(name='same', object_id=ids[1], blob_type='RegValGrid2'))
        self.assertNotEqual(first['final_name'].lower(), second['final_name'].lower())

    def test_object_output_preserves_legacy_short_path_and_rejects_impossible_root(self):
        object_id = '01234567-89ab-cdef-0123-456789abcdef'
        info = dict(name='Gamma Ray', object_id=object_id, blob_type='FloatWellLog')
        plan = r.object_output_plan(Path(r'C:\PetrelOut\native_data'), info)
        self.assertEqual(plan, dict(final_name='Gamma_Ra_'+uuid.UUID(object_id).hex,
                                    staging_name='Gamma_Ra_'+uuid.UUID(object_id).hex+'.partial',
                                    compact_staging=False))
        impossible = Path(r'\\server\share')/('x'*230)
        with self.assertRaisesRegex(r.QCError, r'path needs \d+ characters.*shorten the output root by at least \d+'):
            r.object_output_plan(impossible, dict(info, blob_type='RegValGrid2'))

    def test_atomic_metadata_boundary_and_stale_legacy_stage_fallback(self):
        relatives = r._object_relative_paths('FloatWellLog', staging=True)
        self.assertEqual(max(r.ATOMIC_TEMP_NAMES, key=len), 'metadata.json.'+'0'*12+'.tmp')
        root = Path('C:/')/('x'*180)
        longest = max(len(str(root/'.g000000.partial'/Path(relative))) for relative in relatives)
        root = Path('C:/')/('x'*(180+249-longest))
        self.assertEqual(max(len(str(root/'.g000000.partial'/Path(relative))) for relative in relatives), 249)
        self.assertTrue(r._paths_fit(root, '.g000000.partial', relatives))
        self.assertFalse(r._paths_fit(Path(str(root)+'x'), '.g000000.partial', relatives))

        directory = self.root/'staging'; directory.mkdir()
        info = dict(name='Gamma', object_id=self.ids['log'], blob_type='FloatWellLog')
        plan = r.object_output_plan(directory, info)
        stale = directory/plan['staging_name']; stale.mkdir()
        default_max = max(len(str(stale/Path(relative))) for relative in relatives)
        with patch.object(r, 'WINDOWS_PATH_LIMIT', default_max+1):
            final, pending = r.object_output(directory, info)
        self.assertEqual(final.name, plan['final_name'])
        self.assertRegex(pending.name, r'^\.g[0-9a-f]{6}\.partial$')
        self.assertTrue(all(len(str(pending/Path(relative))) < default_max+1 for relative in relatives))

    def test_compact_output_mapping_is_resumable(self):
        package = self.fixture(); directory = package/'native_data'
        object_id = self.ids['log']; legacy = 'Gamma_'+uuid.UUID(object_id).hex
        legacy_longest = max(len(str(directory/legacy/Path(relative)))
                             for relative in r._object_relative_paths('FloatWellLog'))
        with patch.object(r, 'WINDOWS_PATH_LIMIT', legacy_longest), redirect_stdout(io.StringIO()):
            report = r.run(package)
        item = report['objects'][0]
        output_name = Path(item['artifacts'][0]['path']).parts[0]
        self.assertIn(r.compact_object_id(object_id), output_name)
        self.assertEqual(item['object_id'], object_id); self.assertEqual(item['name'], 'Gamma')
        with (patch.object(r, 'WINDOWS_PATH_LIMIT', legacy_longest),
              patch.object(r, 'recover_object', side_effect=AssertionError('Must reuse compact output')),
              redirect_stdout(io.StringIO())):
            resumed = r.run(package, resume=True)
        self.assertTrue(resumed['objects'][0]['reused_committed_output'])

    def fixture(self, *, kind='FloatWellLog', doc=None, metric=True, surface=False, limits=None, limit_profile=False, surface_subject='SurfaceSubject', model_version=None, omit_bounds=False, force_negative=None):
        package = self.root/'package'; native = package/'08_native_project'
        (native/'project_file').mkdir(parents=True); (native/'ptd_store').mkdir()
        tag = self.ids['surface' if surface else 'log']
        subject = surface_subject if surface else 'WellLogSubject'
        stub = element('item', children=[element('uniqueTag', tag), element('parentTag', '' if surface else self.ids['well']), element('visualName', 'Surface' if surface else 'Gamma')], attrs={'Type': 'SubjectStub'})
        wellstub = element('item', children=[element('uniqueTag', self.ids['well']), element('parentTag', ''), element('visualName', 'Synthetic Well')], attrs={'Type': 'SubjectStub'})
        def entry(key, child): return element('entry', children=[element('key', key), element('value', children=[child])])
        project = element('ProjectSerializer', children=[element('class_infos', children=[element('map', children=[entry(subject, stub), entry('WellTraceSubject', wellstub)])])])
        (native/'project_file/test.pet').write_bytes(b'\xff\xff\x01\x00\x11\x00ProjectSerializer'+bytes(9)+envelope(frame(project)))
        attrs = {'xmlns': r.NAMESPACE}
        meta = [element('unique_tag', tag), element('template', self.ids['template']), element('name', 'Gamma')]
        if surface and not omit_bounds:
            lo,hi=limits or ([100.,200.,1.],[140.,230.,5.])
            meta.append(element('limit' if limit_profile else 'cached_limit', children=[element('min', children=[array('double',lo,'<f8')]), element('max', children=[array('double',hi,'<f8')])]))
        model_attrs = dict(attrs, **({'Version':[9,2,13,1,0,1,18,0,0,1]} if limit_profile else {}))
        if model_version is not None: model_attrs['Version'] = model_version
        if force_negative is not None:
            meta.append(element('used_force_negative_depth',force_negative))
        model = element(subject, children=meta, attrs=model_attrs)
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
        self.assertEqual(las.well.UWI.value, '')
        self.assertEqual(las.params.PETREL_WELL_UUID.value, self.ids['well'])
        for artifact in record['artifacts']: self.assertEqual(r.sha(out/artifact['path']), artifact['sha256'])

    def test_character_encoding_and_null(self):
        _, _, record = self.run_fixture(doc=log_doc(char=True, values=[0,255,3]))
        self.assertEqual(record['null_count'], 1); self.assertEqual(record['status'], 'decoded')

    def test_discrete_intervals_remain_boundary_csv(self):
        _, _, record = self.run_fixture(kind='IntWellLog', doc=log_doc('IntWellLog', char=True, values=[2,255,4], intervals=True))
        self.assertEqual(record['las_status'], 'not_written')

    def test_modern_float_encoded_integer_log_codes(self):
        _,_,record=self.run_fixture(kind='IntWellLog',doc=log_doc('IntWellLog',char=False,values=[9.,15.,9.],intervals=True))
        self.assertEqual(record['numeric_round_trip'],'exact')
        self.assertEqual(record['las_status'],'not_written')
        with self.assertRaises(b.NativeError):
            r.decode_log(b.object_document(envelope(frame(log_doc('IntWellLog',char=False,values=[9.,15.5,9.])))),'IntWellLog')
        self.assertTrue(record['interval_records']); self.assertEqual(record['sample_count'], 3)

    def test_unknown_units_do_not_become_las(self):
        package, report, record = self.run_fixture(metric=False)
        self.assertEqual(record['status'], 'missing_metadata'); self.assertFalse(list(package.rglob('*.las')))
        self.assertTrue(list(package.rglob('samples.csv')))
        self.assertEqual(report['status'], 'partial')
        self.assertTrue(report['has_gaps'])
        self.assertEqual(record['numeric_round_trip'], 'exact')
        self.assertTrue(any('outside the validated metric profile' in message
                            for message in record['unit_resolution_gaps']))
        self.assertIn('display units are not applied',record['reason'])

    def test_empty_log_not_counted_as_decoded(self):
        with patch.object(r, 'event') as events:
            _, report, record = self.run_fixture(doc=log_doc(md=[], values=[]))
        self.assertEqual(record['status'], 'empty_supported_object')
        self.assertEqual(report['status'], 'completed')
        self.assertFalse(report['has_gaps'])
        self.assertNotIn('object_failed', [call.args[0] for call in events.call_args_list])
        self.assertIn('object_checked', [call.args[0] for call in events.call_args_list])

    def test_source_version_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(version=[99]))
        self.assertEqual(record['status'], 'unsupported_layout')
        self.assertEqual(record['object_version'], [99])
        self.assertEqual(record['attempted_decoder_profile'], 'petrel_native_recovery:FloatWellLog')
        self.assertEqual(record['failure_checkpoint'], 'validate_object_profile')
        self.assertNotIn('decoder_profile', record)

    def test_nonzero_log_base_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(base=1))
        self.assertEqual(record['status'], 'unsupported_layout')

    def test_mismatched_log_lengths_rejected(self):
        _, _, record = self.run_fixture(doc=log_doc(values=[1.]))
        self.assertEqual(record['status'], 'unsupported_layout')
        self.assertEqual(record['failure_checkpoint'], 'decode_log')
        self.assertEqual(record['object_version'], r.PROFILES['FloatWellLog'])
        self.assertNotIn('decoder_profile', record)

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

    def test_declared_forced_negative_cached_bounds_preserve_raw_values(self):
        original=np.arange(1,7,dtype='<f4')
        doc=surface_doc(context=False).replace(original.tobytes(),(-original).tobytes())
        package,_,record=self.run_fixture(kind='RegValGrid2',surface=True,doc=doc,force_negative=True,
                                         model_version=r.SURFACE_OWN_CONTEXT_V11)
        self.assertEqual(record['model_bounds_check'],'passed')
        self.assertEqual(record['model_bounds_native_min'],[100.,200.,1.])
        self.assertIn('comparison only',record['model_bounds_sign_comparison'])
        xyz=np.loadtxt(next(package.rglob('surface.xyz')))
        np.testing.assert_array_equal(xyz[:,2],[-1.,-2.,-3.,-4.,-5.])
        self.assertEqual(record['numeric_round_trip'],'exact')

    def test_negative_grid_without_declared_flag_stays_rejected(self):
        original=np.arange(1,7,dtype='<f4')
        doc=surface_doc(context=False).replace(original.tobytes(),(-original).tobytes())
        _,_,record=self.run_fixture(kind='RegValGrid2',surface=True,doc=doc,
                                   model_version=r.SURFACE_OWN_CONTEXT_V11,force_negative=False)
        self.assertEqual(record['status'],'conversion_failed')

    def test_forced_negative_flag_does_not_relax_xy_or_z_bounds(self):
        original=np.arange(1,7,dtype='<f4')
        doc=surface_doc(context=False).replace(original.tobytes(),(-original).tobytes())
        _,_,record=self.run_fixture(kind='RegValGrid2',surface=True,doc=doc,
            model_version=r.SURFACE_OWN_CONTEXT_V11,force_negative=True,
            limits=([101.,200.,1.],[140.,230.,5.]))
        self.assertEqual(record['status'],'conversion_failed')

    def test_undefined_native_model_bounds_are_metadata_gap(self):
        _,_,record=self.run_fixture(kind='RegValGrid2',surface=True,
            model_version=r.SURFACE_OWN_CONTEXT_V11,limits=([r.FLOAT_NULL]*3,[r.FLOAT_NULL]*3))
        self.assertEqual(record['status'],'missing_metadata')
        self.assertIn('explicitly undefined',record['reason'])
        self.assertEqual(record['artifacts'],[])

    def test_explicit_xyz_surface(self):
        _, _, record = self.run_fixture(kind='ValGrid2', surface=True)
        self.assertEqual(record['status'], 'decoded'); self.assertEqual(record['model_bounds_check'], 'passed')

    def test_surface_rotation_with_unrotated_bounds_rejected(self):
        _, _, record = self.run_fixture(kind='RegValGrid2', surface=True, doc=surface_doc(rotation=0.3))
        self.assertEqual(record['status'], 'conversion_failed')

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
        record=report['objects'][0]
        self.assertEqual(record['status'], 'decoded')
        self.assertEqual(record['las_status'], 'failed')
        self.assertTrue(report['has_gaps'])
        self.assertTrue(any(a['path'].endswith('samples.csv') for a in record['artifacts']))
        self.assertFalse(any(a['path'].endswith('.las') for a in record['artifacts']))
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

    def test_chunked_bxml_at_every_byte_boundary(self):
        payload = frame(element('root', 'A'*150), element('other', 42))
        for split in range(1, len(payload)):
            with self.subTest(split=split):
                actual = b.decompress(chunked_envelope(payload[:split], payload[split:]))
                self.assertEqual(actual, payload)
                nodes = list(b.read_documents(actual))
                self.assertEqual([node.scalar() for node in nodes], ['A'*150, 42])

    def test_chunked_project_model_and_log_recovery(self):
        package = self.fixture()
        native = package/'08_native_project'
        model = native/'ptd_store/Model.ptd'
        payload = b.decompress(model.read_bytes())
        model.write_bytes(chunked_envelope(payload[:6], payload[6:37], payload[37:]))
        project = native/'project_file/test.pet'
        raw = project.read_bytes(); payload = b.project_payload(raw)
        project.write_bytes(raw[:32]+chunked_envelope(payload[:7], payload[7:]))
        with closing(sqlite3.connect(native/'ptd_store/Data.ptd')) as db, db:
            blob = db.execute('SELECT blob_data FROM blob_parts WHERE data_fk=1').fetchone()[0]
            payload = b.decompress(blob)
            db.execute('UPDATE blob_parts SET blob_data=? WHERE data_fk=1',
                       (chunked_envelope(payload[:9], payload[9:]),))
        with redirect_stdout(io.StringIO()): report = r.run(package)
        self.assertTrue(report['source_unchanged'])
        self.assertEqual(report['objects'][0]['status'], 'decoded')
        las = lasio.read(next(package.rglob('curve.las')), null_policy='strict')
        np.testing.assert_array_equal(las.index, [10.25, 11.25, 13.25])
        self.assertTrue(np.isnan(las.data[1, 1]))

    def test_chunked_length_bounds_and_trailing_data(self):
        good = chunked_envelope(b'first block', b'second block')
        first = envelope(b'first block')
        invalid = [good[:-1], good+b'\0', b'LZ4\x01', first+bytes(8),
                   first+struct.pack('<II', 0xffffffff, 0),
                   first+envelope(b'second complete envelope')]
        invalid += [first+good[len(first):len(first)+n] for n in range(1, 8)]
        for blob in invalid:
            with self.subTest(blob=blob):
                with self.assertRaises(b.NativeError): b.decompress(blob)

    def test_chunked_cumulative_output_and_block_count_bounds(self):
        blob = chunked_envelope(b'123456', b'abcdef')
        self.assertEqual(b.decompress(blob, limit=12), b'123456abcdef')
        with self.assertRaisesRegex(b.NativeError, 'bound'): b.decompress(blob, limit=11)
        with patch.object(b, 'MAX_LZ4_BLOCKS', 1):
            with self.assertRaisesRegex(b.NativeError, 'block count'): b.decompress(blob)

    def test_chunked_blocks_do_not_share_match_dictionary(self):
        # Block two attempts a match without first emitting its own literals.
        invalid = b'\x00\x01\x00\x50final'
        blob = envelope(b'abcde')+struct.pack('<II', len(invalid), 0)+invalid
        with self.assertRaisesRegex(b.NativeError, 'Invalid LZ4 match offset'):
            b.decompress(blob)

    def test_chunked_overlapping_matches_reset_per_block(self):
        # Each independent raw block emits a literal 'a', four matching 'a's,
        # and eight final literals, satisfying the LZ4 end-of-block conditions.
        block = b'\x10a\x01\x00\x8012345678'
        chunk = struct.pack('<II', len(block), 0)+block
        self.assertEqual(b.decompress(b'LZ4\x01'+chunk+chunk), b'aaaaa12345678'*2)

    def test_ambiguous_field_rejected(self):
        n = next(b.read_documents(frame(element('root',children=[element('a',1),element('a',2)]))))
        with self.assertRaises(b.NativeError): n.get('a')

    def test_invalid_boolean_array_rejected(self):
        with self.assertRaises(b.NativeError): next(b.read_documents(frame(element('root',children=[array('bool',[0,2],'u1')]))))

    def test_unbalanced_document_rejected(self):
        with self.assertRaises(b.NativeError): next(b.read_documents(frame(element('root')[:-1])))


if __name__ == '__main__': unittest.main()
