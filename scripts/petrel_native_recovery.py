"""Recover observed native well logs and surface arrays without Petrel.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
See docs/NATIVE_LOGS_SURFACES.md for the validated profiles and limitations.
"""
from __future__ import annotations

import argparse
import collections
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import uuid

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_native_binary as binary
from petrel_native_binary import NativeError, Node

VERSION = '0.7.0'
TYPES = ('FloatWellLog', 'IntWellLog', 'RegValGrid2', 'ValGrid2')
PROFILES = {
    'FloatWellLog': [1, 3, 0, 2, 0, 1],
    'IntWellLog': [1, 3, 0, 2, 0, 1],
    'RegValGrid2': [1, 1, 1, 0, 0, 0, 0, 2, 0, 1, 1],
    'ValGrid2': [0, 0, 0, 0, 0, 2, 0, 1, 1],
}
REGULAR_GRID_V0 = [0, 1, 1, 0, 0, 0, 0, 2, 0, 1, 1]
MAX_SURFACE_NODES = 10_000_000
MODEL_TYPES = {'ContTemplateSubject', 'DiscTemplateSubject', 'WellLogSubject',
               'LogTemplateSubject', 'WellTraceSubject', 'SurfaceSubject', 'SurfaceAttrSubject'}
FLOAT_NULL = float(np.finfo(np.float32).max)
NAMESPACE = 'http://www.slb.com/Petrel/2011/03/Serialization'


class MissingMetadata(NativeError):
    pass


class QCError(NativeError):
    pass


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def walk(node):
    if isinstance(node, Node):
        yield node
        for child in node.children:
            yield from walk(child)


def exact_uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise NativeError('Invalid native object UUID') from exc


def scalar_array(node, child, size=None):
    if node.attrs.get('Size') == 0 and not node.children:
        values = np.array([], dtype=np.float64)
    else:
        values = node.array(child)
    if values.ndim != 1 or values.dtype.kind not in 'fiu' or (size is not None and len(values) != size):
        raise NativeError(f'{node.name}: invalid numeric array shape/type')
    declared = node.attrs.get('Size', -1)
    if declared != -1 and declared != len(values):
        raise NativeError(f'{node.name}: array Size does not match payload')
    return values


class Metadata:
    def __init__(self, project_path, model_path):
        projects = list(binary.read_documents(binary.project_payload(binary.read_bounded(project_path))))
        if len(projects) != 1:
            raise MissingMetadata('Expected one project document')
        self.stubs = {}
        for entry in projects[0].child('class_infos').child('map').children:
            if not isinstance(entry, Node):
                raise MissingMetadata('Unsupported project class map')
            kind = entry.get('key')
            for node in walk(entry.child('value')):
                if node.attrs.get('Type') != 'SubjectStub':
                    continue
                raw_tag = node.get('uniqueTag')
                if raw_tag in (0, ''): continue  # Unidentified structural root stub.
                tag = exact_uuid(raw_tag)
                if tag in self.stubs:
                    raise MissingMetadata('Duplicate project object identity')
                self.stubs[tag] = dict(kind=kind, parent=node.get('parentTag'), name=node.get('visualName'))
        self.model = {}
        project_nodes = []
        for node in binary.read_documents(binary.decompress(binary.read_bounded(model_path))):
            if node.name == 'ProjectSubject':
                project_nodes.append(node)
            if node.name not in MODEL_TYPES:
                continue
            raw_tag = node.get('unique_tag')
            if raw_tag in (0, ''): continue  # Unidentified default template.
            tag = exact_uuid(raw_tag)
            if tag in self.model:
                raise MissingMetadata('Duplicate model object identity')
            if node.attrs.get('xmlns') != NAMESPACE:
                raise MissingMetadata('Unsupported model serialization namespace')
            self.model[tag] = node
        self.project_units = {}
        self.metric_profile = False
        if len(project_nodes) == 1:
            style = project_nodes[0].child('style')
            units = style.child('units_style')
            self.project_units = {key: units.get('temp_'+key+'_unit') for key in ('xy', 'z', 'time')}
            self.project_units['system'] = style.child('proj_unit_system').get('name')
            self.metric_profile = (
                self.project_units == dict(xy='m', z='m', time='ms', system='Metric')
                and style.get('proj_units_customized') is False
                and all(units.get(key+'_unit_customized') is False for key in ('xy', 'z', 'time')))

    def ancestry(self, tag):
        result = []; seen = set()
        while tag in self.stubs:
            if tag in seen or len(seen) >= 128:
                raise MissingMetadata('Cyclic or excessively deep project ancestry')
            seen.add(tag); stub = self.stubs[tag]
            result.append(dict(object_id=tag, **stub)); tag = stub['parent']
        return result

    def resolve(self, tag, kind):
        node = self.model.get(tag)
        stub = self.stubs.get(tag)
        expected = {'WellLogSubject'} if kind.endswith('WellLog') else {'SurfaceSubject', 'SurfaceAttrSubject'}
        if node is None or stub is None or node.name not in expected or stub['kind'] != node.name:
            raise MissingMetadata('Exact project/model UUID and class linkage is missing')
        parents = self.ancestry(tag)
        wells = [p for p in parents if p['kind'] == 'WellTraceSubject']
        if kind.endswith('WellLog') and len(wells) != 1:
            raise MissingMetadata('Expected exactly one well ancestor')
        log_template = self.model.get(node.get('log_template_tag')) if kind.endswith('WellLog') else None
        template_id = node.get('template') or (log_template.get('template') if log_template else '')
        template = self.model.get(template_id)
        unit = None; measurement = None
        if template is not None and template.name in ('ContTemplateSubject', 'DiscTemplateSubject'):
            measurement = template.get('standard_measurement')
            if (self.metric_profile and template.get('is_predefined') is True
                    and template.get('unit_is_customized', False) is False
                    and template.get('unit_measurement_is_customized', False) is False):
                value = template.get('initial_unit')
                if isinstance(value, str) and value:
                    candidate = value.strip() or '_'
                    if re.fullmatch(r'[A-Za-z0-9_%/^.*()-]{1,32}', candidate): unit = candidate
        return node, dict(
            object_id=tag, name=stub['name'] or node.get('name') or tag,
            subject_type=node.name, ancestry=parents,
            well=wells[0]['name'] if wells else None,
            well_id=wells[0]['object_id'] if wells else None,
            template_id=template_id, unit=unit, measurement=measurement,
            declared_template_unit=template.get('initial_unit') if template is not None else None,
            unit_status='validated_metric_profile' if unit is not None else 'unresolved',
            depth_unit='m' if self.metric_profile else None,
            horizontal_unit='m' if self.metric_profile else None,
            project_units=self.project_units, crs_status='unresolved',
            source_sign_convention='preserved; no time/depth sign reversal')


def decode_log(node, kind):
    md = scalar_array(node.child('md'), 'double').astype(np.float64)
    if len(md) > 2_000_000: raise NativeError('Log sample count exceeds validated memory bound')
    values_node = node.child('log_values')
    encoded = values_node.get('is_char')
    if type(encoded) is not bool or node.get('min_index') != 0:
        raise NativeError('Only observed zero-base log encoding is validated')
    if encoded:
        char = values_node.child('char_values'); raw = char.scalar()
        if raw == '' and char.attrs.get('Size') == 0: raw = b''
        if not isinstance(raw, bytes) or char.attrs.get('Size') != len(raw):
            raise NativeError('Character log byte count mismatch')
        values = np.frombuffer(raw, dtype='u1').astype(np.float64)
        missing = values == 255
        null_encoding = 'uint8 255'
    else:
        if kind != 'FloatWellLog':
            raise NativeError('Non-character IntWellLog is not validated')
        values = scalar_array(values_node.child('float_values'), 'float').astype(np.float64)
        missing = values == FLOAT_NULL
        null_encoding = 'positive IEEE float32 maximum'
    if len(md) != len(values) or not np.all(np.isfinite(md)) or not np.all(np.isfinite(values)):
        raise NativeError('Non-finite log value/index or unequal array lengths')
    if np.any(np.abs(md) == FLOAT_NULL):
        raise NativeError('Undefined measured-depth index')
    intervals = node.get('has_discrete_intervals', False)
    if type(intervals) is not bool:
        raise NativeError('Invalid interval flag')
    return md, values, missing, dict(null_encoding=null_encoding, interval_records=intervals,
        sample_count=len(md), null_count=int(missing.sum()),
        sampling='native order and sample positions; no interpolation or resampling',
        interval_boundary_semantics='unresolved; original boundary records only' if intervals else None)


def bitmask(node, name, count):
    flag = node.get('has_'+name)
    if type(flag) is not bool:
        raise NativeError('Invalid definition-mask flag')
    if not flag:
        if node.child(name, required=False) is not None:
            raise NativeError('Unexpected definition mask')
        return np.ones(count, dtype=bool)
    mask = node.child(name)
    fields = [child.name for child in mask.children]
    if sorted(fields) == ['bitmask', 'bool_count']:
        raw = mask.child('bitmask').scalar()
        declared = mask.get('bool_count')
    elif sorted(fields) in (['bools'], ['bools', 'ignore']):
        raw = mask.child('bools').scalar(); declared = mask.attrs.get('Size')
        # The observed older packed-bool layout writes a zero padding byte
        # in a separate ignore field exactly when Size is divisible by eight.
        ignore = mask.child('ignore', required=False)
        if (count % 8 == 0) != (ignore is not None) or (ignore is not None and ignore.scalar() != b'\x00'):
            raise NativeError('Unsupported packed-bool padding layout')
    else:
        raise NativeError('Unsupported surface definition-mask fields')
    if declared != count or not isinstance(raw, bytes) or len(raw) != (count+7)//8:
        raise NativeError('Surface definition-mask length mismatch')
    return np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder='little')[:count].astype(bool)


def decode_surface(node, kind):
    legacy = kind == 'RegValGrid2' and node.attrs.get('Version') == REGULAR_GRID_V0
    fields = ['user_data','node_size','has_node_defs','has_cell_defs','has_connections','has_segments','grid']
    fields += [name for name in ('node_defs','cell_defs') if node.get('has_'+name) is True]
    if kind == 'RegValGrid2':
        fields += ['original_inc','original_min','original_max','has_coordinate_context','original_rotation','dip']
        if not legacy: fields += ['axis_flip_state']
    if node.content or sorted(c.name for c in node.children) != sorted(fields):
        raise NativeError('Surface fields differ from the validated profile')
    user = node.child('user_data')
    if user.attrs.get('Size') != 0 or user.children or user.content:
        raise NativeError('Surface user data is not validated')
    dims = scalar_array(node.child('node_size'), 'int', 2)
    if dims.dtype.kind not in 'iu' or np.any(dims < 2):
        raise NativeError('Surface dimensions must be two positive cell-bearing axes')
    nx, ny = map(int, dims); count = nx*ny
    if count > MAX_SURFACE_NODES:
        raise NativeError('Surface node count exceeds validated memory bound')
    if node.get('has_connections') is not False or node.get('has_segments') is not False:
        raise NativeError('Surface connections/segments are not validated')
    valid = bitmask(node, 'node_defs', count)
    cells = bitmask(node, 'cell_defs', (nx-1)*(ny-1))
    values = scalar_array(node.child('grid'), 'node', count if kind == 'RegValGrid2' else count*3)
    if not np.all(np.isfinite(values)):
        raise NativeError('Non-finite surface payload')
    i = np.tile(np.arange(nx), ny); j = np.repeat(np.arange(ny), nx)
    geometry = dict(node_size=[nx, ny], ordering='i/x fastest, then j/y',
                    mask_encoding='packed bits, least significant bit first; 1=defined',
                    cell_mask_preserved=True)
    if kind == 'RegValGrid2':
        if values.dtype.kind != 'f' or values.dtype.itemsize != 4:
            raise NativeError('Only float32 regular-grid values are validated')
        if ((not legacy and node.get('has_coordinate_context') is not True)
                or type(node.get('has_coordinate_context')) is not bool
                or node.get('axis_flip_state', 0 if legacy else None) != 0
                or node.child('original_rotation').get('radians') != 0
                or node.child('dip').get('radians') != 0):
            raise MissingMetadata('Only explicit unrotated, untilted, unflipped regular-grid geometry is validated')
        origin = scalar_array(node.child('original_min'), 'double', 2)
        extent = scalar_array(node.child('original_max'), 'double', 2)
        inc = scalar_array(node.child('original_inc'), 'double', 2)
        if not np.all(np.isfinite(np.r_[origin, extent, inc])) or np.any(inc <= 0):
            raise NativeError('Invalid regular-grid origin/increments')
        if not np.allclose(origin+(dims-1)*inc, extent, rtol=0, atol=1e-5):
            raise NativeError('Surface extents disagree with dimensions and increments')
        x = origin[0]+i*inc[0]; y = origin[1]+j*inc[1]; z = values.astype(np.float64)
        geometry.update(origin=origin.tolist(), increments=inc.tolist(), maximum=extent.tolist(),
                        coordinate_context_declared=node.get('has_coordinate_context'))
    else:
        if values.dtype.kind != 'f' or values.dtype.itemsize != 8:
            raise NativeError('Only float64 XYZ ValGrid2 records are validated')
        x, y, z = values.reshape(count, 3).T
        geometry['coordinate_source'] = 'explicit native XYZ triples'
    valid &= (np.abs(x) != FLOAT_NULL) & (np.abs(y) != FLOAT_NULL) & (np.abs(z) != FLOAT_NULL)
    if np.any(np.abs(x[valid]) > 1e10) or np.any(np.abs(y[valid]) > 1e10):
        raise NativeError('Surface coordinates exceed validated bound')
    geometry.update(node_count=count, defined_nodes=int(valid.sum()))
    return i, j, x, y, z, valid, cells, geometry


def safe_name(value):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', str(value)).strip('_')[:40] or 'object'


def object_output(directory, info):
    final = directory/(safe_name(info['name'])[:8]+'_'+uuid.UUID(info['object_id']).hex)
    pending = final.with_name(final.name+'.partial')
    if len(str(pending/'metadata.json')) >= 250:
        raise QCError('Output path is too long for Windows file tools; choose a shorter output root')
    pending.mkdir(parents=True, exist_ok=False)
    return final, pending


def csv_write(path, header, rows):
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(header); writer.writerows(rows)


def number(value):
    return format(float(value), '.17g')


def check_csv(path, expected):
    actual = np.loadtxt(path, delimiter=',', skiprows=1, ndmin=2)
    if actual.shape != expected.shape or not np.array_equal(actual, expected):
        raise QCError('CSV numeric read-back failed')


def header_text(value):
    return re.sub(r'[\r\n\x00-\x1f:]', ' ', str(value)).strip()


def write_las(path, md, values, missing, info):
    import lasio
    null = -999.25
    while np.any(values[~missing] == null): null *= 10
    las = lasio.LASFile()
    las.well.WELL.value = header_text(info['well'])
    las.well.UWI.value = info['well_id']
    las.well.NULL.value = null
    las.append_curve('DEPT', md, unit=info['depth_unit'], descr='Native measured depth')
    clean = values.copy(); clean[missing] = np.nan
    mnemonic = re.sub(r'[^A-Za-z0-9_]', '', info['name'])[:32] or 'VALUE'
    if mnemonic.upper() == 'DEPT': mnemonic = 'VALUE'
    las.append_curve(mnemonic, clean, unit=info['unit'], descr=header_text(info['name']))
    delta = np.diff(md)
    step = delta[0] if np.allclose(delta, delta[0], rtol=0, atol=1e-8) else 0.0
    las.other = ('Website: https://saherlabs.dev/\n'
                 'Project: https://github.com/ahmedsahernouh/petrel-headless-extractor\n'
                 'Source object UUID: '+info['object_id']+'\n'
                 'One native curve; original MD positions, no resampling. STEP=0 means irregular sampling.')
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        las.write(stream, version=2.0, wrap=False, fmt='%.17g',
                  STRT=number(md[0]), STOP=number(md[-1]), STEP=number(step))
    checked = lasio.read(path, mnemonic_case='preserve', null_policy='strict')
    if (not np.array_equal(checked.index, md) or not np.array_equal(np.isnan(checked.data[:, 1]), missing)
            or not np.array_equal(checked.data[~missing, 1], values[~missing])
            or checked.curves[1].unit != info['unit'] or checked.curves[0].unit != info['depth_unit']):
        raise QCError('LAS sample/unit/null read-back failed')


def recover_object(blob, tag, kind, metadata, directory, preview_only=False):
    node = binary.object_document(blob)
    versions = [PROFILES[kind]] + ([REGULAR_GRID_V0] if kind == 'RegValGrid2' else [])
    if node.name != 'data' or node.attrs.get('Type') != kind or node.attrs.get('Version') not in versions:
        raise NativeError('Native object type/version is outside the validated profile')
    if node.attrs.get('xmlns') != NAMESPACE:
        raise NativeError('Unsupported object serialization namespace')
    model, info = metadata.resolve(tag, kind)
    info.update(blob_type=kind, object_version=node.attrs['Version'], blob_sha256=hashlib.sha256(blob).hexdigest())
    if kind.endswith('WellLog'):
        md, values, missing, details = decode_log(node, kind)
        info.update(details)
        if not len(md): return dict(info, status='empty_supported_object', artifacts=[])
        final_output, output = object_output(directory, info)
        csv_path = output/'samples.csv'
        expected = np.column_stack([np.arange(len(md)), md, values, missing.astype(int)])
        csv_write(csv_path, ['sample_index', 'md', 'raw_value', 'is_null'],
                  ([int(k), number(d), number(v), int(m)] for k, d, v, m in expected))
        check_csv(csv_path, expected)
        info['las_status'] = 'not_written'
        if (not details['interval_records'] and kind == 'FloatWellLog' and len(md) >= 2
                and np.all(np.diff(md) > 0) and info['unit'] is not None and info['depth_unit'] is not None):
            write_las(output/'curve.las', md, values, missing, info)
            info['las_status'] = 'written_verified'
        else:
            info['las_reason'] = 'Requires continuous float curve, resolved units and increasing MD; CSV preserves original records'
        info['category_labels_status'] = 'not_decoded' if kind == 'IntWellLog' else 'not_applicable'
    else:
        if kind == 'ValGrid2' and info['subject_type'] != 'SurfaceSubject':
            raise MissingMetadata('ValGrid2 attribute geometry inheritance is not validated')
        i, j, x, y, z, valid, cells, details = decode_surface(node, kind)
        info.update(details)
        if not np.any(valid): return dict(info, status='empty_supported_object', artifacts=[])
        if info['subject_type'] == 'SurfaceSubject':
            limits = model.child('cached_limit', required=False)
            enclosing_limit = False
            if limits is None and model.attrs.get('Version') == [9, 2, 13, 1, 0, 1, 18, 0, 0, 1]:
                limits = model.child('limit')
                enclosing_limit = True
                lo = scalar_array(limits.child('min'), 'double', 3)
                hi = scalar_array(limits.child('max'), 'double', 3)
                if np.all(lo == FLOAT_NULL) and np.all(hi == FLOAT_NULL):
                    info['model_bounds_check'] = 'unavailable_native_cache_undefined'
                    limits = None
            elif limits is None:
                raise MissingMetadata('Surface model bounds layout is not validated')
            if limits is not None:
                lo = scalar_array(limits.child('min'), 'double', 3)
                hi = scalar_array(limits.child('max'), 'double', 3)
                if not np.all(np.isfinite(np.r_[lo,hi])) or np.any(lo>hi):
                    raise QCError('Invalid native surface model bounds')
                actual_lo = np.array([x[valid].min(), y[valid].min(), z[valid].min()])
                actual_hi = np.array([x[valid].max(), y[valid].max(), z[valid].max()])
                exact = np.allclose(actual_lo, lo, rtol=0, atol=1e-3) and np.allclose(actual_hi, hi, rtol=0, atol=1e-3)
                if exact:
                    info['model_bounds_check'] = 'passed'
                elif enclosing_limit and np.all(actual_lo >= lo-1e-3) and np.all(actual_hi <= hi+1e-3):
                    info['model_bounds_check'] = 'within_enclosing_native_limit'
                else:
                    raise QCError('Surface coordinates/defined values disagree with native model bounds')
        if kind == 'RegValGrid2' and info.get('coordinate_context_declared') is False and info.get('model_bounds_check') != 'passed':
            raise MissingMetadata('Regular grid without coordinate context requires exact independent model bounds')
        final_output, output = object_output(directory, info)
        from petrel_surface_export import write_surface, preview
        compact = node.child('node_defs', required=False) is not None and node.child('node_defs').child('bools', required=False) is not None
        try:
            if preview_only:
                info.update(preview(output/'grid_preview.npz',x,y,z,valid,cells,info))
                info['dataset_exported'] = False
            else:
                write_surface(output, info, i, j, x, y, z, valid, cells, compact=compact,
                              native_node_defs=bitmask(node, 'node_defs', len(z)))
                info['dataset_exported'] = True
        except ValueError as exc:
            raise QCError(str(exc)) from exc
    info['status'] = 'decoded' if info['unit'] is not None and info['depth_unit'] is not None else 'missing_metadata'
    if info['status'] != 'decoded':
        info['reason'] = 'Numeric payload recovered to open files; units unresolved. Not counted as a unit-resolved decoded object.'
    info['numeric_round_trip'] = 'exact'
    write_json(output/'metadata.json', info)
    output.rename(final_output)
    output = final_output
    info['artifacts'] = [dict(path=p.relative_to(directory).as_posix(), sha256=sha(p), bytes=p.stat().st_size)
                         for p in sorted(output.iterdir()) if p.is_file()]
    return info


def select_objects(db):
    # Choose the latest version before filtering type, so old supported data is
    # never used to stand in for a newer unsupported object of the same identity.
    rows = db.execute('SELECT data_pk,droid,name,version,blob_type FROM data').fetchmany(200_001)
    if len(rows) > 200_000: raise NativeError('Native registry exceeds object bound')
    latest = {}
    for pk, droid, name, version, kind in rows:
        tag = str(droid)
        if type(version) is not int: raise NativeError('Non-integer registry version')
        key = (tag, str(name or ''))
        if key not in latest or (version, pk) > (latest[key][3], latest[key][0]):
            latest[key] = (pk, tag, name, version, kind)
    selected = [(r[0], exact_uuid(r[1].rsplit('/', 1)[-1]), *r[2:]) for r in latest.values() if r[4] in TYPES]
    counts = collections.Counter(r[1] for r in selected)
    if any(n > 1 for n in counts.values()):
        raise NativeError('Ambiguous multiple named payloads for one object')
    return sorted(selected, key=lambda r: (r[4], r[1]))


def read_blob(db, pk):
    parts = []; previous = None; total = 0
    for part, size in db.execute('SELECT part,length(blob_data) FROM blob_parts WHERE data_fk=? ORDER BY part', (pk,)):
        if type(part) is not int or part < 0 or (previous is not None and part != previous+1):
            raise NativeError('Non-contiguous native blob parts')
        if type(size) is not int or size <= 0: raise NativeError('Empty native blob part')
        previous = part; total += size; parts.append(part)
        if total > binary.MAX_DOCUMENT: raise NativeError('Compressed native object exceeds bound')
    if not parts: raise NativeError('Object has no blob parts')
    return b''.join(bytes(r[0]) for r in db.execute('SELECT blob_data FROM blob_parts WHERE data_fk=? ORDER BY part', (pk,)))


def run(package, *, grids_only=False, preview_only=False):
    package = Path(package).resolve(strict=True)
    native = package/'08_native_project'
    output = package/'07_workflows_reports/native_recovery'
    output.mkdir(parents=True, exist_ok=False)
    report = dict(tool_version=VERSION, operation='native_logs_surfaces', objects=[], status='running',
                  runtime_gui_used=False, petrel_process_launched=False)
    report_path = output/'native_recovery_report.json'
    sources = []
    try:
        projects = list((native/'project_file').glob('*.pet'))
        if len(projects) != 1: raise MissingMetadata('Expected exactly one preserved project file')
        sources = [projects[0], native/'ptd_store/Model.ptd', native/'ptd_store/Data.ptd']
        if not all(p.is_file() for p in sources): raise MissingMetadata('Preserved project/Model.ptd/Data.ptd is missing')
        if any(p.is_symlink() or p.is_junction() for p in [native, native/'ptd_store', native/'project_file', *sources]):
            raise NativeError('Linked native source paths are not accepted')
        dbpath = sources[2]
        if any(Path(str(dbpath)+suffix).exists() for suffix in ('-wal', '-journal')):
            raise NativeError('SQLite has transaction sidecars; use a stable preserved snapshot')
        before = {p.relative_to(package).as_posix(): sha(p) for p in sources}
        report['source_hashes_before'] = before
        metadata_error = None
        try: metadata = Metadata(sources[0], sources[1])
        except (NativeError, UnicodeError, OSError) as exc: metadata_error = str(exc)
        directory = package/'native_data'; directory.mkdir(exist_ok=False)
        with closing(sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            objects = select_objects(db)
            if grids_only: objects = [row for row in objects if row[4] in ('RegValGrid2','ValGrid2')]
            report['object_type_counts'] = dict(collections.Counter(r[4] for r in objects))
            for index, (pk, tag, name, version, kind) in enumerate(objects):
                record = dict(object_id=tag, blob_type=kind, data_pk=pk, registry_version=version, artifacts=[])
                try:
                    if metadata_error: raise MissingMetadata(metadata_error)
                    blob = read_blob(db, pk)
                    options = dict(preview_only=True) if preview_only else {}
                    record.update(recover_object(blob, tag, kind, metadata, directory, **options))
                except (NativeError, UnicodeError, ValueError, KeyError, TypeError) as exc:
                    state = 'missing_metadata' if isinstance(exc, MissingMetadata) else 'conversion_failed' if isinstance(exc, QCError) else 'unsupported_layout'
                    record.update(status=state, reason=str(exc))
                report['objects'].append(record)
                if grids_only:
                    write_json(report_path, report)
                if index % 20 == 0 or index+1 == len(objects):
                    print(f'Native logs/surfaces: {index+1}/{len(objects)} objects checked', flush=True)
        report['source_hashes_after'] = {p.relative_to(package).as_posix(): sha(p) for p in sources}
        report['source_unchanged'] = before == report['source_hashes_after']
        if not report['source_unchanged']: raise NativeError('Preserved native sources changed during recovery')
        report['object_status_counts'] = dict(collections.Counter(r['status'] for r in report['objects']))
        report['status'] = 'completed' if all(r['status'] in ('decoded', 'empty_supported_object') for r in report['objects']) else 'partial'
    except KeyboardInterrupt:
        report.update(status='cancelled', reason='Interrupted; partial files are not valid exports')
        raise
    except MissingMetadata as exc:
        report.update(status='not_available', reason=str(exc))
    except (NativeError, sqlite3.Error, UnicodeError) as exc:
        report.update(status='failed' if report.get('source_unchanged') is False else 'unsupported_layout', reason=str(exc))
    except OSError as exc:
        report.update(status='failed', reason=str(exc))
    finally:
        write_json(report_path, report)
    print('Native recovery report: '+str(report_path), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-package', type=Path, required=True)
    parser.add_argument('--grids-only', action='store_true', help='Recover native surface/grid objects only into a new recovery directory')
    args = parser.parse_args()
    result = run(args.export_package, grids_only=args.grids_only)
    # Unsupported metadata/layouts are explicit partial extraction, not fatal
    # to preservation and the other independent project decoders.
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
