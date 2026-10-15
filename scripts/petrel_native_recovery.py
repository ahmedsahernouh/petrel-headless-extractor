# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Recover observed native well logs and surface arrays without Petrel.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
See docs/NATIVE_LOGS_SURFACES.md for the validated profiles and limitations.
"""
from __future__ import annotations

import argparse
import base64
import collections
from contextlib import closing
from contextvars import ContextVar
import csv
import hashlib
from importlib import metadata as distribution_metadata
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import uuid
import geoviewer_io as gio
from geoviewer_diagnostics import event

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_native_binary as binary
import time
from petrel_native_binary import NativeError, Node
from petrel_native_log_evidence import category_legend

VERSION = '1.0.0'
_operation = ContextVar('native_operation', default=None)
_failure_metadata = ContextVar('native_failure_metadata', default=None)
_resume_binding = ContextVar('native_resume_binding', default=None)


def decoder_identity():
    """Content identity for this numeric reader, independent of path/version label.

    Dependency versions identify the declared Python environment; this is not
    a digest of every installed runtime binary or a scientific qualification.
    """
    scripts = Path(__file__).resolve().parent
    modules = ('petrel_native_recovery.py', 'petrel_native_binary.py',
               'petrel_native_log_evidence.py', 'petrel_surface_export.py',
               'geoviewer_io.py', 'geoviewer_diagnostics.py')
    try:
        lasio_version = distribution_metadata.version('lasio')
    except distribution_metadata.PackageNotFoundError:
        lasio_version = 'not_installed'
    return dict(schema='geoviewer.native-decoder-identity/1',
                module_sha256={name: sha(scripts/name) for name in modules},
                python=dict(implementation=sys.implementation.name,
                            version=list(sys.version_info[:3])),
                dependencies=dict(numpy=np.__version__, lasio=lasio_version))


def phase(operation, **fields):
    context = _operation.get()
    if context is not None:
        context.update(operation=operation, **fields)
        event('object_phase', **context)
TYPES = ('FloatWellLog', 'IntWellLog', 'RegValGrid2', 'ValGrid2')
PROFILES = {
    'FloatWellLog': [1, 3, 0, 2, 0, 1],
    'IntWellLog': [1, 3, 0, 2, 0, 1],
    'RegValGrid2': [1, 1, 1, 0, 0, 0, 0, 2, 0, 1, 1],
    'ValGrid2': [0, 0, 0, 0, 0, 2, 0, 1, 1],
}
REGULAR_GRID_V0 = [0, 1, 1, 0, 0, 0, 0, 2, 0, 1, 1]
SURFACE_LIMIT_V8 = [8, 2, 12, 1, 0, 1, 17, 0, 0, 1]
SURFACE_LIMIT_V9_19 = [9, 2, 13, 1, 2, 19, 0, 0, 1]
SURFACE_LIMIT_V10_2_20 = [10, 2, 13, 1, 2, 20, 0, 0, 1]
SURFACE_ATTRIBUTE_XYZ_V6 = [6, 3, 13, 1, 2, 20, 0, 0, 1]
SURFACE_OWN_CONTEXT_V11 = [11, 3, 13, 1, 2, 20, 0, 0, 1]
QA_MODEL_PROFILES = {
    'QualityAssuranceSubject': [1, 11, 3, 13, 1, 2, 20, 0, 0, 1],
    'QualityAssuranceAttrSubject': [0, 6, 3, 13, 1, 2, 20, 0, 0, 1],
}
MAX_SURFACE_NODES = 10_000_000
MODEL_TYPES = {'ContTemplateSubject', 'DiscTemplateSubject', 'WellLogSubject',
               'LogTemplateSubject', 'WellTraceSubject', 'SurfaceSubject', 'SurfaceAttrSubject'} | QA_MODEL_PROFILES.keys()
FLOAT_NULL = float(np.finfo(np.float32).max)
NAMESPACE = 'http://www.slb.com/Petrel/2011/03/Serialization'
WINDOWS_PATH_LIMIT = 250
TEXT_BUFFER_BYTES = 1024 * 1024
ATOMIC_TEMP_NAMES = ('_commit.json.' + '0'*12 + '.tmp',
                     'metadata.json.' + '0'*12 + '.tmp')


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


def unit_declarations(node, locator, expected=()):
    """Expose bounded typed declarations without treating them as stored units."""
    if node is None:
        return dict(status='record_absent', locator=locator, fields={})
    names = set(expected)
    names.update(c.name for c in node.children if any(
        token in c.name.casefold() for token in ('unit', 'measurement', 'factor', 'offset')))
    if len(names) > 64:
        return dict(status='declaration_field_bound_exceeded', locator=locator,
                    field_count=len(names), fields={})
    fields = {}
    for name in sorted(names):
        item = dict(locator=locator+'/'+name, status='absent')
        children = [c for c in node.children if c.name == name]
        if len(children) > 1:
            fields[name] = dict(item, status='ambiguous_duplicate_declaration', count=len(children))
            continue
        child = children[0] if children else None
        if child is not None:
            if isinstance(child, binary.Array) or child.children:
                item.update(status='nested_declaration_not_interpreted')
            else:
                try:
                    raw = child.scalar()
                except NativeError:
                    fields[name] = dict(item, status='non_scalar_declaration_not_interpreted')
                    continue
                if ((raw is None or type(raw) in (bool, int))
                        or (type(raw) is float and math.isfinite(raw))
                        or (isinstance(raw, str) and len(raw) <= 4096)):
                    item.update(status='recorded_uninterpreted', raw_value=raw)
                else:
                    item.update(status='unsupported_or_excessive_scalar')
        fields[name] = item
    return dict(status='inspected', locator=locator, record_type=node.name,
                record_version=node.attrs.get('Version'), fields=fields,
                scope='Native declarations only; factors, offsets and unit meanings not applied or calibrated')


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
        self.category_tables = {}
        project_nodes = []
        for node in binary.read_documents(binary.decompress(binary.read_bounded(model_path))):
            if node.name == 'ProjectSubject':
                project_nodes.append(node)
            if node.name == 'DiscColorTableSubject':
                # Optional display evidence must not break unrelated numeric readers.
                try:
                    table_tag = exact_uuid(node.get('unique_tag'))
                    self.category_tables.setdefault(table_tag, []).append(node)
                except (NativeError, ValueError, TypeError):
                    pass
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
        self.project_unit_declarations = dict(status='ambiguous_or_absent_project_record',
                                              project_record_count=len(project_nodes))
        if len(project_nodes) == 1:
            style = project_nodes[0].child('style')
            units = style.child('units_style')
            self.project_units = {key: units.get('temp_'+key+'_unit') for key in ('xy', 'z', 'time')}
            self.project_units['system'] = style.child('proj_unit_system').get('name')
            self.metric_profile = (
                self.project_units == dict(xy='m', z='m', time='ms', system='Metric')
                and style.get('proj_units_customized') is False
                and all(units.get(key+'_unit_customized') is False for key in ('xy', 'z', 'time')))
            self.project_unit_declarations = dict(
                status='inspected', source_role='project_display_preferences_not_stored_sample_units',
                record_type=project_nodes[0].name, record_version=project_nodes[0].attrs.get('Version'),
                style=unit_declarations(style, 'Model.ptd/ProjectSubject/style', ('proj_units_customized',)),
                units_style=unit_declarations(units, 'Model.ptd/ProjectSubject/style/units_style',
                                              ('is_used', 'reference_datum')),
                project_system=unit_declarations(style.child('proj_unit_system'),
                                                  'Model.ptd/ProjectSubject/style/proj_unit_system', ('name',)))

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
        if kind == 'RegValGrid2': expected = expected | QA_MODEL_PROFILES.keys()
        if node is None or stub is None or node.name not in expected or stub['kind'] != node.name:
            raise MissingMetadata('Exact project/model UUID and class linkage is missing')
        if node.name in QA_MODEL_PROFILES and node.attrs.get('Version') != QA_MODEL_PROFILES[node.name]:
            raise MissingMetadata('QA model profile is outside the validated exact profile')
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
        unit_gaps = []
        if not self.metric_profile:
            unit_gaps.append('Project unit settings are outside the validated metric profile; display units are not applied to native samples')
        if template is None or template.name not in ('ContTemplateSubject', 'DiscTemplateSubject'):
            unit_gaps.append('No exact supported measurement template resolved for this object')
        elif unit is None:
            unit_gaps.append('Template unit declaration is retained but not validated for this native profile')
        info = dict(
            object_id=tag, name=stub['name'] or node.get('name') or tag,
            subject_type=node.name, ancestry=parents,
            well=wells[0]['name'] if wells else None,
            well_id=wells[0]['object_id'] if wells else None,
            external_uwi=None,
            external_uwi_status='unresolved; native well UUID is not an external UWI',
            template_id=template_id, unit=unit, measurement=measurement,
            declared_template_unit=template.get('initial_unit') if template is not None else None,
            unit_status='validated_metric_profile' if unit is not None else 'unresolved',
            unit_resolution_gaps=unit_gaps,
            depth_unit='m' if self.metric_profile else None,
            horizontal_unit='m' if self.metric_profile else None,
            project_units=self.project_units, crs_status='unresolved',
            source_sign_convention='preserved; no time/depth sign reversal')
        info['native_unit_evidence'] = dict(
            schema='geoviewer.native-unit-declarations/1',
            project_matches_existing_metric_gate=self.metric_profile,
            scope='Evidence for exact linked native declarations; no new unit resolution or transformation',
            project=self.project_unit_declarations,
            template=unit_declarations(template, f'Model.ptd/unique_tag={template_id}',
                ('is_predefined', 'property_type', 'standard_measurement', 'initial_unit',
                 'unit_is_customized', 'unit_measurement_is_customized')),
            object_overrides=unit_declarations(node, f'Model.ptd/unique_tag={tag}'),
            remaining_requirement=('Independent stored-value/unit calibration for this exact native profile; '
                                   'project display settings and template declarations alone do not establish stored units'))
        if kind == 'IntWellLog':
            try:
                color_table_id = (template.get('default_color_table_tag', '')
                                  if template is not None and template.name == 'DiscTemplateSubject' else '')
                if not isinstance(color_table_id, str):
                    raise ValueError('Invalid default colour-table identity')
                info['native_category_legend'] = category_legend(
                    getattr(self, 'category_tables', {}).get(color_table_id, []), template_id, color_table_id)
            except (NativeError, ValueError, TypeError) as exc:
                info['native_category_legend'] = dict(status='unresolved', reason=str(exc),
                                                     code_mapping_status='unvalidated')
        if node.name in QA_MODEL_PROFILES:
            state = {}
            for key in ('is_recalculation_needed', 'up_to_date'):
                field = node.child(key, required=False)
                value = field.scalar() if field is not None else None
                if field is not None and type(value) is not bool:
                    raise MissingMetadata('QA computation flag must be boolean: '+key)
                state[key] = dict(state='present' if field is not None else 'absent', value=value)
            crs = node.child('original_crs', required=False)
            info.update(unit=None, measurement=None, depth_unit=None,
                        declared_template_measurement=measurement,
                        unit_status='unresolved_QA_scalar_semantics', qa_state=state,
                        qa=dict(computation_validity='unresolved', scalar_semantics='unresolved',
                                xyz_third_column='stored native scalar; not asserted depth or elevation',
                                model_profile=node.attrs.get('Version'),
                                target_link_tag=node.get('target_link_tag'), target_geometry_used=False,
                                original_crs_reference=crs.get('tag') if crs is not None else None),
                        reason='stored native QA values; computation validity and scalar units unresolved; '+
                               '; '.join(key+'='+('absent' if item['state']=='absent' else str(item['value']).lower())
                                         for key,item in state.items()))
        info['native_unit_evidence']['unit_resolution_status'] = info['unit_status']
        return node, info


def decode_log(node, kind):
    raw_md = scalar_array(node.child('md'), 'double')
    index_encoding = raw_md.dtype.name if len(raw_md) else 'no_payload_empty'
    md = raw_md.astype(np.float64)
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
        value_encoding = 'uint8' if len(values) else 'no_payload_empty'
        missing = values == 255
        null_encoding = 'uint8 255'
    else:
        raw_values = scalar_array(values_node.child('float_values'), 'float')
        value_encoding = raw_values.dtype.name if len(raw_values) else 'no_payload_empty'
        values = raw_values.astype(np.float64)
        missing = values == FLOAT_NULL
        null_encoding = 'positive IEEE float32 maximum'
    if len(md) != len(values) or not np.all(np.isfinite(md)) or not np.all(np.isfinite(values)):
        details = dict(md_count=len(md), value_count=len(values),
            array_lengths_equal=len(md) == len(values),
            nonfinite_md_count=int((~np.isfinite(md)).sum()),
            nonfinite_value_count=int((~np.isfinite(values)).sum()),
            nan_value_count=int(np.isnan(values).sum()),
            positive_infinity_value_count=int(np.isposinf(values).sum()),
            negative_infinity_value_count=int(np.isneginf(values).sum()),
            first_nonfinite_md_positions=np.flatnonzero(~np.isfinite(md))[:8].tolist(),
            first_nonfinite_value_positions=np.flatnonzero(~np.isfinite(values))[:8].tolist(),
            scope='Bounded diagnostic counts; nonfinite values are not converted to nulls')
        exc = NativeError('Non-finite log value/index or unequal array lengths; '
                          f'md_count={len(md)}, value_count={len(values)}, '
                          f'nonfinite_md={details["nonfinite_md_count"]}, '
                          f'nonfinite_values={details["nonfinite_value_count"]}')
        exc.native_log_diagnostics = details
        raise exc
    if kind == 'IntWellLog' and np.any(values[~missing] != np.trunc(values[~missing])):
        bad = (~missing) & (values != np.trunc(values))
        raise NativeError('IntWellLog contains non-integral categorical codes; '
                          f'count={int(bad.sum())}, first_values={values[bad][:8].tolist()}. '
                          'No declared alternative null encoding was validated; raw codes are not rounded or relabelled')
    if np.any(np.abs(md) == FLOAT_NULL):
        raise NativeError('Undefined measured-depth index')
    intervals = node.get('has_discrete_intervals', False)
    if type(intervals) is not bool:
        raise NativeError('Invalid interval flag')
    with np.errstate(over='ignore', invalid='ignore'):
        delta = np.diff(md)
    sampling_regularity = ('insufficient_samples' if len(md) < 2 else
                           'undetermined_numeric_range' if not np.all(np.isfinite(delta)) else
                           'uniform' if np.all(delta == delta[0]) else 'nonuniform')
    return md, values, missing, dict(null_encoding=null_encoding, interval_records=intervals,
        sample_count=len(md), null_count=int(missing.sum()),
        curve_kind=('continuous_scalar' if kind == 'FloatWellLog' and not intervals else
                    'categorical_codes' if kind == 'IntWellLog' else 'interval_records'),
        log_kind=('continuous_scalar' if kind == 'FloatWellLog' and not intervals else
                  'categorical_codes' if kind == 'IntWellLog' else 'interval_records'),
        index_kind='measured_depth',
        index_type='measured_depth', index_role='native_sample_position',
        index_shape='scalar_1d', index_encoding=index_encoding, index_base=0,
        value_role='native_codes' if kind == 'IntWellLog' else 'native_scalar_values',
        value_shape='scalar_1d', value_encoding=value_encoding,
        native_null_value=255 if encoded else FLOAT_NULL,
        null_semantics=dict(representation='raw_value retained plus is_null mask',
                            source_rule='Exact equality to validated native sentinel: '+null_encoding),
        sampling='native order and sample positions; no interpolation or resampling',
        sampling_kind='interval_records' if intervals else 'point_samples',
        sampling_regularity=sampling_regularity,
        md_strictly_increasing=bool(np.all(md[1:] > md[:-1])),
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
        # Observed older streams write one separate padding byte (00 or ff)
        # when Size is divisible by eight. Its value is not a definition bit.
        ignore = mask.child('ignore', required=False)
        padding = ignore.scalar() if ignore is not None else None
        if ((count % 8 == 0) != (ignore is not None)
                or (ignore is not None and (not isinstance(padding, bytes) or len(padding) != 1))):
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
    # Petrel 2024.5 adds this typed flag without changing the payload's Version.
    # Retain it as evidence; it does not replace array/mask/geometry validation.
    consistency = node.child('is_known_consistent', required=False)
    if consistency is not None:
        if type(consistency.scalar()) is not bool:
            raise NativeError('Surface consistency flag must be Boolean')
        fields.append('is_known_consistent')
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
    streamed = count > MAX_SURFACE_NODES
    if streamed:
        from petrel_regular_grid_stream import MAX_NODES, MAX_AXIS, CHUNK, RegularColumn
        # A separate packed regular-grid resource profile. Explicit XYZ grids,
        # scalar-node arrays and unknown regular profiles retain their caps.
        # V0 packed values/masks/geometry have a separate retained-fixture proof;
        # this resource admission does not bypass the Model/sign checks below.
        grid_children = node.child('grid').children
        if (kind != 'RegValGrid2' or node.attrs.get('Version') not in (PROFILES['RegValGrid2'], REGULAR_GRID_V0)
                or count > MAX_NODES or max(nx, ny) > MAX_AXIS
                or len(grid_children) != 1 or not isinstance(grid_children[0], binary.Array)
                or grid_children[0].name != 'node'
                or grid_children[0].values.dtype.kind != 'f'
                or grid_children[0].values.dtype.itemsize != 4
                or dims.dtype.itemsize != 4):
            raise NativeError('Surface node count exceeds validated memory bound for this storage profile')
    if node.get('has_connections') is not False or node.get('has_segments') is not False:
        raise NativeError('Surface connections/segments are not validated')
    valid = bitmask(node, 'node_defs', count)
    cells = bitmask(node, 'cell_defs', (nx-1)*(ny-1))
    values = scalar_array(node.child('grid'), 'node', count if kind == 'RegValGrid2' else count*3)
    # Retained legacy RegValGrid2 stores use NaN in explicitly undefined slots.
    # Keep those raw values and the exact definition mask; a NaN in a defined
    # node, an infinity, or non-finite XYZ geometry remains an invalid payload.
    masked_nan_count = 0
    check_chunk = CHUNK if streamed else max(1, len(values))
    for start in range(0, len(values), check_chunk):
        part = values[start:start+check_chunk]
        masked_nan = (np.isnan(part) & ~valid[start:start+check_chunk]
                      if kind == 'RegValGrid2' else np.zeros(len(part), dtype=bool))
        if np.any(~np.isfinite(part) & ~masked_nan):
            raise NativeError('Non-finite surface payload in defined nodes or unsupported non-finite encoding')
        masked_nan_count += int(masked_nan.sum())
    if not streamed:
        i = np.tile(np.arange(nx), ny); j = np.repeat(np.arange(ny), nx)
    geometry = dict(node_size=[nx, ny], ordering='i/x fastest, then j/y',
                    mask_encoding='packed bits, least significant bit first; 1=defined',
                    cell_mask_preserved=True,
                    masked_nan_nodes=masked_nan_count,
                    masked_nan_scope='Explicit native node-definition mask only; no inferred nulls or filled values',
                    native_is_known_consistent=consistency.scalar() if consistency else None)
    if kind == 'RegValGrid2':
        if values.dtype.kind != 'f' or values.dtype.itemsize != 4:
            raise NativeError('Only float32 regular-grid values are validated')
        angle = node.child('original_rotation').get('radians')
        if (type(angle) not in (int, float) or not math.isfinite(angle) or abs(angle) > 2*math.pi):
            raise NativeError('Regular-grid rotation must be finite radians within one turn')
        if (type(node.get('has_coordinate_context')) is not bool
                or node.get('axis_flip_state', 0 if legacy else None) != 0
                or node.child('dip').get('radians') != 0):
            raise MissingMetadata('Only untilted, unflipped regular-grid geometry is validated')
        origin = scalar_array(node.child('original_min'), 'double', 2)
        extent = scalar_array(node.child('original_max'), 'double', 2)
        inc = scalar_array(node.child('original_inc'), 'double', 2)
        if not np.all(np.isfinite(np.r_[origin, extent, inc])) or np.any(inc <= 0):
            raise NativeError('Invalid regular-grid origin/increments')
        if not np.allclose(origin+(dims-1)*inc, extent, rtol=0, atol=1e-5):
            raise NativeError('Surface extents disagree with dimensions and increments')
        cos_angle, sin_angle = math.cos(angle), math.sin(angle)
        if streamed:
            i, j, x, y = [RegularColumn(count, nx, field, origin, inc, angle) for field in ('i', 'j', 'x', 'y')]
            z = values
            geometry['coordinate_storage_profile'] = ('packed_regular_v0_chunked/1' if legacy else 'packed_regular_chunked/1')
            geometry['coordinate_chunk_nodes'] = CHUNK
            geometry['resource_node_limit'] = MAX_NODES
        else:
            u = i*inc[0]; v = j*inc[1]
            x = origin[0]+u*cos_angle-v*sin_angle
            y = origin[1]+u*sin_angle+v*cos_angle
            z = values.astype(np.float64)
        geometry.update(origin=origin.tolist(), increments=inc.tolist(), maximum=extent.tolist(),
                        coordinate_context_declared=node.get('has_coordinate_context'),
                        rotation_radians=angle, axis_aligned=(angle == 0),
                        coordinate_transform='XY = original_min + R(radians) * [i*dx,j*dy]; original_max is the unrotated parameter extent',
                        i_basis=[float(inc[0]*cos_angle),float(inc[0]*sin_angle)],
                        j_basis=[float(-inc[1]*sin_angle),float(inc[1]*cos_angle)])
    else:
        if values.dtype.kind != 'f' or values.dtype.itemsize != 8:
            raise NativeError('Only float64 XYZ ValGrid2 records are validated')
        x, y, z = values.reshape(count, 3).T
        geometry['coordinate_source'] = 'explicit native XYZ triples'
    if streamed:
        for start in range(0, count, CHUNK):
            take = slice(start, min(start+CHUNK, count))
            xx, yy, zz = x[take], y[take], z[take]
            valid[take] &= (np.abs(xx) != FLOAT_NULL) & (np.abs(yy) != FLOAT_NULL) & (np.abs(zz) != FLOAT_NULL)
            if np.any(np.abs(xx[valid[take]]) > 1e10) or np.any(np.abs(yy[valid[take]]) > 1e10):
                raise NativeError('Surface coordinates exceed validated bound')
    else:
        valid &= (np.abs(x) != FLOAT_NULL) & (np.abs(y) != FLOAT_NULL) & (np.abs(z) != FLOAT_NULL)
        if np.any(np.abs(x[valid]) > 1e10) or np.any(np.abs(y[valid]) > 1e10):
            raise NativeError('Surface coordinates exceed validated bound')
    geometry.update(node_count=count, defined_nodes=int(valid.sum()))
    return i, j, x, y, z, valid, cells, geometry


def safe_name(value):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', str(value)).strip('_')[:40] or 'object'


def compact_object_id(value):
    """Return a case-insensitive, reversible encoding of all 128 UUID bits."""
    return base64.b32encode(uuid.UUID(value).bytes).decode('ascii').rstrip('=').lower()


def _object_relative_paths(kind, staging=False):
    paths = ['metadata.json', '_commit.json']
    if kind.endswith('WellLog'):
        paths += ['samples.csv', 'curve.las']
        if staging: paths += ['las.partial/curve.las', *ATOMIC_TEMP_NAMES]
    else:
        paths += ['nodes.csv', 'surface.xyz', 'node_definitions.csv',
                  'usable_node_definitions.csv', 'cell_definitions.csv', 'cells.csv',
                  'surface.zmap', 'grid_preview.npz']
        if staging:
            paths += ['zmap.partial/surface.zmap', 'preview.partial/grid_preview.npz',
                      *ATOMIC_TEMP_NAMES]
    return paths


def _paths_fit(root, folder, relatives):
    return all(len(str(root/folder/Path(relative))) < WINDOWS_PATH_LIMIT for relative in relatives)


def object_output_plan(directory, info):
    """Choose stable final naming and a safe staging form without touching storage."""
    directory = Path(directory)
    identity = uuid.UUID(info['object_id'])
    human = safe_name(info['name'])[:8]
    legacy = human+'_'+identity.hex
    permanent = _object_relative_paths(info['blob_type'])
    staged = _object_relative_paths(info['blob_type'], staging=True)
    final_name = legacy if _paths_fit(directory, legacy, permanent) else None
    if final_name is None:
        token = compact_object_id(str(identity))
        for count in range(len(human), -1, -1):
            candidate = (human[:count]+'_' if count else '')+token
            if _paths_fit(directory, candidate, permanent):
                final_name = candidate; break
        if final_name is None:
            longest = max((str(directory/token/Path(relative)) for relative in permanent), key=len)
            excess = len(longest) - (WINDOWS_PATH_LIMIT-1)
            raise QCError(f'Output root is too long for Windows file tools: collision-free 128-bit object path needs '
                          f'{len(longest)} characters, limit is {WINDOWS_PATH_LIMIT-1}; shorten the output root by at least {excess}')
    staging_name = final_name+'.partial'
    if _paths_fit(directory, staging_name, staged):
        return dict(final_name=final_name, staging_name=staging_name, compact_staging=False)
    compact_stage = '.g000000.partial'
    if not _paths_fit(directory, compact_stage, staged):
        longest = max((str(directory/compact_stage/Path(relative)) for relative in staged), key=len)
        excess = len(longest) - (WINDOWS_PATH_LIMIT-1)
        raise QCError(f'Output root is too long for Windows file tools: temporary path needs {len(longest)} '
                      f'characters, limit is {WINDOWS_PATH_LIMIT-1}; shorten the output root by at least {excess}')
    return dict(final_name=final_name, staging_name=None, compact_staging=True)


def committed_outputs(directory, object_id):
    identity = uuid.UUID(object_id)
    token = compact_object_id(str(identity))
    patterns = ('*_'+identity.hex+'/_commit.json', identity.hex+'/_commit.json',
                '*_'+token+'/_commit.json', token+'/_commit.json')
    found = {candidate for pattern in patterns for candidate in directory.glob(pattern)}
    if len(found) > 1:
        raise NativeError('Multiple committed outputs exist for native object '+str(identity))
    return sorted(found)


def _compact_staging(directory):
    for _ in range(100):
        candidate = directory/('.g'+uuid.uuid4().hex[:6]+'.partial')
        if not candidate.exists(): return candidate
    raise FileExistsError('Could not allocate a unique compact object staging directory')


def object_output(directory, info):
    plan = object_output_plan(directory, info)
    final = directory/plan['final_name']
    if plan['compact_staging']:
        pending = _compact_staging(directory)
    else:
        pending = directory/plan['staging_name']
        if pending.exists():
            pending = None
            for _ in range(100):
                candidate = final.with_name(final.name+'.'+uuid.uuid4().hex[:6]+'.partial')
                if _paths_fit(directory, candidate.name, _object_relative_paths(info['blob_type'], staging=True)) and not candidate.exists():
                    pending = candidate; break
            if pending is None:
                pending = _compact_staging(directory)
    gio.contained(directory, pending); gio.contained(directory, final)
    if final.exists():
        raise FileExistsError('Existing object output requires verified resume: '+str(final))
    phase('create_staging', pending_path=str(pending), destination=str(final))
    pending.mkdir(parents=True, exist_ok=False)
    return final, pending


def csv_write(path, header, rows):
    with path.open('x', encoding='utf-8', newline='', buffering=TEXT_BUFFER_BYTES) as stream:
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
    # This reader has no calibrated external-UWI profile. Native linkage UUIDs
    # must remain distinct from industry well identifiers.
    las.well.UWI.value = ''
    las.params.append(lasio.HeaderItem(mnemonic='PETREL_WELL_UUID',
                                      value=header_text(info['well_id']),
                                      descr='Native Petrel well linkage UUID; not UWI'))
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
                 'External UWI: unresolved; native well UUID retained separately.\n'
                 'One native curve; original MD positions, no resampling. STEP=0 means irregular sampling.')
    with path.open('x', encoding='utf-8', newline='\n', buffering=TEXT_BUFFER_BYTES) as stream:
        las.write(stream, version=2.0, wrap=False, fmt='%.17g',
                  STRT=number(md[0]), STOP=number(md[-1]), STEP=number(step))
    checked = lasio.read(path, mnemonic_case='preserve', null_policy='strict')
    if (not np.array_equal(checked.index, md) or not np.array_equal(np.isnan(checked.data[:, 1]), missing)
            or not np.array_equal(checked.data[~missing, 1], values[~missing])
            or checked.curves[1].unit != info['unit'] or checked.curves[0].unit != info['depth_unit']
            or checked.well.UWI.value != ''
            or checked.params.PETREL_WELL_UUID.value != header_text(info['well_id'])):
        raise QCError('LAS sample/unit/null/identity read-back failed')


def recover_object(blob, tag, kind, metadata, directory, preview_only=False, source_locator=None):
    phase('decode_payload', attempted_decoder_profile='petrel_native_recovery:' + kind)
    storage_profile = None
    if kind == 'ValGrid2':
        directory.parent.mkdir(parents=True, exist_ok=True)
        node, storage_profile = binary.packed_valgrid_document(blob, spool_directory=directory.parent)
    else:
        node = binary.object_document(blob)
    phase('validate_object_profile', object_version=node.attrs.get('Version'))
    versions = [PROFILES[kind]] + ([REGULAR_GRID_V0] if kind == 'RegValGrid2' else [])
    if node.name != 'data' or node.attrs.get('Type') != kind or node.attrs.get('Version') not in versions:
        raise NativeError('Native object type/version is outside the validated profile')
    if node.attrs.get('xmlns') != NAMESPACE:
        raise NativeError('Unsupported object serialization namespace')
    phase('resolve_metadata')
    model, info = metadata.resolve(tag, kind)
    is_qa = info['subject_type'] in QA_MODEL_PROFILES
    if is_qa and (kind != 'RegValGrid2' or node.attrs.get('Version') != PROFILES['RegValGrid2']
                  or node.get('has_coordinate_context') is not True):
        raise MissingMetadata('QA requires the exact current RegValGrid2 profile with coordinate context true')
    phase('resolve_metadata', name=info['name'], well=info.get('well'))
    info.update(blob_type=kind, object_version=node.attrs['Version'], blob_sha256=hashlib.sha256(blob).hexdigest())
    if storage_profile is not None:
        info['native_storage_profile'] = storage_profile
    info['native_source_locator'] = source_locator or dict(
        object_id=tag, blob_type=kind, location_status='Data.ptd row not supplied')
    failure_metadata = _failure_metadata.get()
    if failure_metadata is not None:
        # Metadata already established before payload decoding remains evidence
        # on failure. Never copy later artifact/QC success fields into this snapshot.
        failure_metadata.update(info)
    if kind.endswith('WellLog'):
        phase('decode_log')
        md, values, missing, details = decode_log(node, kind)
        info.update(details)
        info.update(md_unit=info['depth_unit'], md_reference=None,
                    md_reference_status='unresolved; no native MD datum/reference validated',
                    category_lookup=None,
                    category_lookup_status='unresolved' if kind == 'IntWellLog' else 'not_applicable',
                    native_source_locator=source_locator or dict(
                        object_id=tag, blob_type=kind, location_status='Data.ptd row not supplied'),
                    native_decoder_profile=dict(blob_type=kind, object_version=node.attrs['Version'],
                                                value_encoding=details['value_encoding']),
                    research_gaps=(["MD datum/reference"] +
                                   (["category label lookup"] if kind == 'IntWellLog' else []) +
                                   (["interval boundary direction"] if details['interval_records'] else [])))
        if not len(md): return dict(info, status='empty_supported_object', artifacts=[])
        final_output, output = object_output(directory, info)
        csv_path = output/'samples.csv'
        phase('write_csv', path=str(csv_path))
        expected = np.column_stack([np.arange(len(md)), md, values, missing.astype(int)])
        csv_write(csv_path, ['sample_index', 'md', 'raw_value', 'is_null'],
                  ([int(k), number(d), number(v), int(m)] for k, d, v, m in expected))
        phase('validate_csv', path=str(csv_path)); check_csv(csv_path, expected)
        info['format_results'] = {'CSV': {'status':'validated','file':'samples.csv'}}
        info['las_status'] = 'not_written'
        if (not details['interval_records'] and kind == 'FloatWellLog' and len(md) >= 2
                and np.all(np.diff(md) > 0) and info['unit'] is not None and info['depth_unit'] is not None):
            # Keep an optional LAS failure out of the accepted CSV bundle.
            las_pending = output/'las.partial'
            try:
                las_pending.mkdir()
                phase('write_validate_las', path=str(las_pending/'curve.las'))
                write_las(las_pending/'curve.las', md, values, missing, info)
                gio.retry_io(lambda:(las_pending/'curve.las').rename(output/'curve.las'),
                             operation='finalize_las',source=las_pending/'curve.las',destination=output/'curve.las',
                             context={'object_id':tag,'name':info['name']})
                info['las_status'] = 'written_verified'
                info['format_results']['LAS'] = {'status':'validated','file':'curve.las'}
                try: las_pending.rmdir()
                except OSError as exc:event('staging_cleanup_deferred',severity='warning',path=str(las_pending),reason=str(exc))
            except Exception as exc:
                if gio.systemic(exc): raise
                info['las_status'] = 'failed'; info['export_status'] = 'partial'
                info['format_results']['LAS'] = dict(status='failed',**gio.error_details(exc))
                event('format_failed',severity='error',object_id=tag,name=info['name'],format='LAS',
                      operation='write_validate_las',**gio.error_details(exc))
        else:
            info['las_reason'] = 'Requires continuous float curve, resolved units and increasing MD; CSV preserves original records'
        info['category_labels_status'] = 'not_decoded' if kind == 'IntWellLog' else 'not_applicable'
    else:
        phase('decode_surface')
        own_attribute = (info['subject_type'] == 'SurfaceAttrSubject'
                         and (kind == 'ValGrid2' or node.get('has_coordinate_context') is False))
        own_surface_context = (kind == 'RegValGrid2'
                               and node.attrs.get('Version') == PROFILES['RegValGrid2']
                               and node.get('has_coordinate_context') is False
                               and info['subject_type'] == 'SurfaceSubject'
                               and model.attrs.get('Version') == SURFACE_OWN_CONTEXT_V11)
        legacy_surface_limit = (info['subject_type'] == 'SurfaceSubject'
                                and model.attrs.get('Version') == SURFACE_LIMIT_V10_2_20
                                and model.child('cached_limit', required=False) is None)
        if legacy_surface_limit:
            rejected = []
            if kind != 'RegValGrid2' or node.attrs.get('Version') != REGULAR_GRID_V0:
                rejected.append(f'payload={kind}, Version={node.attrs.get("Version")!r}')
            if node.get('has_coordinate_context') is not True:
                rejected.append(f'has_coordinate_context={node.get("has_coordinate_context")!r}')
            if model.child('limit', required=False) is None:
                rejected.append('own limit absent')
            flag = model.child('used_force_negative_depth', required=False)
            if flag is None:
                rejected.append('used_force_negative_depth absent')
            elif model.get('used_force_negative_depth') is not False:
                value = model.get('used_force_negative_depth')
                rejected.append(f'used_force_negative_depth={value!r} ({type(value).__name__})')
            if rejected:
                raise MissingMetadata('Legacy SurfaceSubject own-limit profile requires exact V0 regular grid, coordinate context true, explicit false depth flag and own limit without cache; rejected: ' + '; '.join(rejected))
        if is_qa and model.child('cached_limit', required=False) is None:
            raise MissingMetadata('QA geometry requires its own exact cached bounds')
        if own_surface_context and model.child('cached_limit', required=False) is None:
            raise MissingMetadata('Modern surface own geometry requires its own exact cached bounds')
        if own_attribute and (model.attrs.get('Version') != SURFACE_ATTRIBUTE_XYZ_V6
                                   or model.child('cached_limit', required=False) is None):
            raise MissingMetadata('Surface attribute requires the validated model profile and its own exact cached bounds')
        if (kind == 'RegValGrid2' and node.attrs.get('Version') != REGULAR_GRID_V0
                and node.get('has_coordinate_context') is False and not (own_attribute or own_surface_context)):
            raise MissingMetadata('Current regular grid without coordinate context requires validated own attribute geometry')
        i, j, x, y, z, valid, cells, details = decode_surface(node, kind)
        info.update(details)
        if not np.any(valid): return dict(info, status='empty_supported_object', artifacts=[], dataset_exported=False)
        if info['subject_type'] == 'SurfaceSubject' or own_attribute or is_qa:
            limits = model.child('cached_limit', required=False)
            info['model_bounds_source'] = 'own_cached_limit'
            enclosing_limit = False
            if limits is None and model.attrs.get('Version') == [9, 2, 13, 1, 0, 1, 18, 0, 0, 1]:
                limits = model.child('limit')
                info['model_bounds_source'] = 'own_limit'
                enclosing_limit = True
                lo = scalar_array(limits.child('min'), 'double', 3)
                hi = scalar_array(limits.child('max'), 'double', 3)
                if np.all(lo == FLOAT_NULL) and np.all(hi == FLOAT_NULL):
                    info['model_bounds_check'] = 'unavailable_native_cache_undefined'
                    limits = None
            elif limits is None and model.attrs.get('Version') in (SURFACE_LIMIT_V8, SURFACE_LIMIT_V9_19):
                # The observed older limit profile requires exact bounds;
                # it does not inherit the newer profile's containment allowance.
                limits = model.child('limit')
                info['model_bounds_source'] = 'own_limit'
            elif legacy_surface_limit:
                limits = model.child('limit')
                info['model_bounds_source'] = 'own_limit'
                info['model_bounds_profile'] = 'SurfaceSubject_10_2_13_1_2_20_0_0_1_RegValGrid2_V0_own_limit'
            elif limits is None:
                raise MissingMetadata('Surface model bounds layout is not validated')
            if limits is not None:
                lo = scalar_array(limits.child('min'), 'double', 3)
                hi = scalar_array(limits.child('max'), 'double', 3)
                if not np.all(np.isfinite(np.r_[lo,hi])) or np.any(lo>hi):
                    raise QCError('Invalid native surface model bounds')
                if np.all(lo == FLOAT_NULL) and np.all(hi == FLOAT_NULL):
                    raise MissingMetadata('Native surface model bounds are explicitly undefined; independent geometry confirmation unavailable')
                if (own_surface_context or is_qa or legacy_surface_limit) and np.any(np.abs(np.r_[lo,hi]) >= FLOAT_NULL):
                    label = 'Legacy surface own limit' if legacy_surface_limit else 'Modern surface own cached bounds'
                    raise QCError(label + ' contain undefined sentinel values')
                if info.get('coordinate_storage_profile') in ('packed_regular_chunked/1', 'packed_regular_v0_chunked/1'):
                    from petrel_regular_grid_stream import bounds
                    actual_lo, actual_hi = bounds(x, y, z, valid)
                else:
                    actual_lo = np.array([x[valid].min(), y[valid].min(), z[valid].min()])
                    actual_hi = np.array([x[valid].max(), y[valid].max(), z[valid].max()])
                # Exact retained SurfaceSubject fixtures declare this flag and
                # cache positive Z bounds while the native array holds negative
                # Z. Normalize only the comparison; exported native samples and
                # coordinates are never negated or converted to display units.
                force_negative = model.get('used_force_negative_depth', False)
                if type(force_negative) is not bool:
                    raise QCError('Native forced-negative-depth flag must be Boolean')
                info['model_bounds_native_min'] = lo.tolist()
                info['model_bounds_native_max'] = hi.tolist()
                if (model.name == 'SurfaceSubject' and own_surface_context
                        and model.attrs.get('Version') == SURFACE_OWN_CONTEXT_V11
                        and not is_qa and not enclosing_limit and force_negative
                        and lo[2] >= 0 and hi[2] >= 0 and actual_hi[2] <= 0):
                    lo = lo.copy(); hi = hi.copy()
                    lo[2], hi[2] = -hi[2], -lo[2]
                    info['model_bounds_sign_comparison'] = 'declared_used_force_negative_depth; cached Z limits negated for comparison only'
                else:
                    info['model_bounds_sign_comparison'] = 'native cached limits unchanged'
                # This named profile has exact observed agreement on all six
                # coordinates; do not inherit the older profiles' tolerance.
                bounds_match = ((actual_lo == lo) & (actual_hi == hi) if (own_surface_context or is_qa or legacy_surface_limit) else
                                np.isclose(actual_lo,lo,rtol=0,atol=1e-3) & np.isclose(actual_hi,hi,rtol=0,atol=1e-3))
                crs = model.child('original_crs', required=False)
                phase('validate_surface_model_bounds', model_profile=model.attrs.get('Version'),
                      model_bounds_source=info['model_bounds_source'],
                      actual_min=actual_lo.tolist(), actual_max=actual_hi.tolist(),
                      model_min=lo.tolist(), model_max=hi.tolist(),
                      bounds_exact_by_axis=bounds_match.tolist(),
                      original_crs_reference=crs.get('tag') if crs is not None else None,
                      coordinate_transformation='not_applied; units and CRS not inferred')
                exact = bool(np.all(bounds_match))
                if exact:
                    info['model_bounds_check'] = 'passed'
                elif enclosing_limit and np.all(actual_lo >= lo-1e-3) and np.all(actual_hi <= hi+1e-3):
                    info['model_bounds_check'] = 'within_enclosing_native_limit'
                else:
                    raise QCError('Surface coordinates/defined values disagree with native model bounds')
            if own_attribute:
                info['attribute_geometry_check'] = ('explicit_XYZ' if kind == 'ValGrid2' else 'own_regular_geometry') + '_matches_own_cached_bounds; no inherited coordinates'
            if own_surface_context:
                info['surface_geometry_check'] = 'own_regular_geometry_matches_own_cached_bounds_exactly; no inherited coordinates'
                info['model_bounds_comparison'] = 'exact_float64_equality'
            if is_qa:
                info['qa_geometry_check'] = 'own_regular_geometry_matches_own_cached_bounds_exactly; no inherited coordinates'
                info['model_bounds_comparison'] = 'exact_float64_equality'
            if legacy_surface_limit:
                info['model_bounds_comparison'] = 'exact_float64_equality_all_six_defined_node_xyz_extrema'
        if kind == 'RegValGrid2' and info.get('rotation_radians') != 0 and info.get('model_bounds_check') != 'passed':
            raise MissingMetadata('Rotated regular grid requires exact independent model bounds')
        if kind == 'RegValGrid2' and info.get('coordinate_context_declared') is False and info.get('model_bounds_check') != 'passed':
            raise MissingMetadata('Regular grid without coordinate context requires exact independent model bounds')
        final_output, output = object_output(directory, info)
        from petrel_surface_export import write_surface, preview
        phase('write_validate_surface', path=str(output))
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
    info['status'] = 'decoded' if not is_qa and info['unit'] is not None and info['depth_unit'] is not None else 'missing_metadata'
    if info['status'] != 'decoded' and not is_qa:
        info['reason'] = ('Numeric payload recovered to open files; units unresolved. Not counted as a unit-resolved decoded object. '
                          + '; '.join(info.get('unit_resolution_gaps', []))).strip()
    info['numeric_round_trip'] = 'exact'
    phase('write_metadata', path=str(output/'metadata.json'))
    write_json(output/'metadata.json', info)
    phase('hash_artifacts')
    info['artifacts'] = [dict(path=(final_output/p.name).relative_to(directory).as_posix(), sha256=sha(p), bytes=p.stat().st_size)
                         for p in sorted(output.iterdir()) if p.is_file()]
    gio.atomic_json(output/'_commit.json',dict(schema='geoviewer.object/1',tool_version=VERSION,
                                             resume_binding=_resume_binding.get(),record=info))
    phase('finalize_directory')
    gio.finalize_directory(output, final_output, context={'object_id':tag,'name':info['name'],
                           'well':info.get('well'),'blob_type':kind,'decoder_profile':node.attrs.get('Version')})
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


def run(package, *, grids_only=False, preview_only=False, resume=False):
    package = Path(package).resolve(strict=True)
    native = package/'08_native_project'
    output = package/'07_workflows_reports/native_recovery'
    output.mkdir(parents=True, exist_ok=resume)
    report = dict(tool_version=VERSION, operation='native_logs_surfaces', objects=[], status='running',
                  runtime_gui_used=False, petrel_process_launched=False)
    report_path = output/'native_recovery_report.json'
    sources = []; before = None; journal = None
    binding_token = None
    resume_admitted = not resume
    try:
        decoder = decoder_identity()
        options = dict(grids_only=bool(grids_only), preview_only=bool(preview_only))
        previous = None
        if resume:
            try:
                previous = json.loads(report_path.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError) as exc:
                raise NativeError('Resume rejected: prior report is not valid JSON') from exc
            old_binding = previous.get('resume_binding') if isinstance(previous, dict) else None
            if (not isinstance(old_binding, dict)
                    or old_binding.get('schema') != 'geoviewer.native-resume/1'
                    or previous.get('tool_version') != VERSION
                    or old_binding.get('decoder') != decoder
                    or old_binding.get('options') != options):
                raise NativeError('Resume rejected: decoder identity/options changed or prior binding absent')
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
        binding = dict(schema='geoviewer.native-resume/1', decoder=decoder,
                       source_sha256=before, options=options)
        report['resume_binding'] = binding
        if resume:
            if previous.get('source_hashes_before') != before or previous.get('resume_binding') != binding:
                raise NativeError('Resume rejected: source identity changed')
        resume_admitted = True
        binding_token = _resume_binding.set(binding)
        gio.atomic_json(report_path, report)
        journal = gio.Journal(output/'objects.jsonl')
        metadata_error = None
        try: metadata = Metadata(sources[0], sources[1])
        except (NativeError, UnicodeError, OSError) as exc: metadata_error = str(exc)
        directory = package/'native_data'; directory.mkdir(exist_ok=resume)
        with closing(sqlite3.connect(binary.sqlite_readonly_uri(dbpath), uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            objects = select_objects(db)
            if grids_only: objects = [row for row in objects if row[4] in ('RegValGrid2','ValGrid2')]
            report['object_type_counts'] = dict(collections.Counter(r[4] for r in objects))
            consecutive_io_failures=0
            for index, (pk, tag, name, version, kind) in enumerate(objects):
                object_started=time.monotonic()
                context = dict(object_id=tag,name=name or tag,blob_type=kind,object_index=index+1,object_total=len(objects),operation='read_blob')
                token = _operation.set(context)
                metadata_snapshot = {}
                metadata_token = _failure_metadata.set(metadata_snapshot)
                record = dict(object_id=tag, name=name or tag, blob_type=kind, data_pk=pk, registry_version=version,
                              decoder_family='petrel_native_recovery:' + kind, artifacts=[])
                event('object_started',**context)
                try:
                    if metadata_error: raise MissingMetadata(metadata_error)
                    blob = read_blob(db, pk)
                    source_locator = None
                    if kind.endswith('WellLog'):
                        source_locator = dict(
                            path='08_native_project/ptd_store/Data.ptd', table='data',
                            data_pk=pk, registry_version=version, object_id=tag,
                            payload_table='blob_parts', payload_key='data_fk')
                    reused = None
                    if resume:
                        for candidate in committed_outputs(directory, tag):
                            saved = json.loads(candidate.read_text(encoding='utf-8'))
                            item = saved['record']
                            if (saved.get('schema') != 'geoviewer.object/1'
                                    or saved.get('resume_binding') != binding
                                    or saved.get('tool_version') != VERSION or item.get('object_id') != tag
                                    or item.get('blob_sha256') != hashlib.sha256(blob).hexdigest()):
                                raise NativeError('Committed object source/decoder/options/profile changed or binding absent')
                            for artifact in item.get('artifacts',[]):
                                path = gio.contained(directory,directory/artifact['path'])
                                if not path.is_file() or sha(path) != artifact['sha256']:
                                    raise NativeError('Committed artifact integrity mismatch')
                            reused = item
                    record.update(reused or recover_object(blob, tag, kind, metadata, directory,
                                                          preview_only, source_locator))
                    if not reused:
                        record['decoder_profile'] = context.get('attempted_decoder_profile')
                    if reused: record['reused_committed_output'] = True
                    consecutive_io_failures=0
                except (NativeError, UnicodeError, ValueError, KeyError, TypeError) as exc:
                    state = 'missing_metadata' if isinstance(exc, MissingMetadata) else 'conversion_failed' if isinstance(exc, QCError) else 'unsupported_layout'
                    record.update(metadata_snapshot)
                    if getattr(exc, 'native_log_diagnostics', None):
                        record['log_array_diagnostics'] = exc.native_log_diagnostics
                    record.update(status=state,name=context['name'],well=context.get('well'),reason=str(exc),error=gio.error_details(exc,**context))
                except Exception as exc:
                    record.update(metadata_snapshot)
                    consecutive_io_failures+=1
                    record.update(status='finalization_failed' if context['operation']=='finalize_directory' else 'write_failed',
                                  name=context['name'],well=context.get('well'),
                                  reason=str(exc),error=getattr(exc,'geoviewer_context',gio.error_details(exc,**context)))
                    if gio.systemic(exc):
                        report['objects'].append(record); journal.append(record)
                        event('object_failed',severity='error',**record)
                        raise
                    if consecutive_io_failures>=3:
                        event('output_health_probe',consecutive_io_failures=consecutive_io_failures,path=str(directory))
                        try:gio.output_probe(directory)
                        except OSError:
                            report['objects'].append(record);journal.append(record)
                            raise
                finally:
                    for diagnostic_key in ('attempted_decoder_profile', 'object_version'):
                        if diagnostic_key in context:
                            record[diagnostic_key] = context[diagnostic_key]
                    if record.get('error'):
                        record['failure_checkpoint'] = context['operation']
                    _operation.reset(token)
                    _failure_metadata.reset(metadata_token)
                report['objects'].append(record)
                record['elapsed_seconds']=round(time.monotonic()-object_started,3)
                journal.append(record)
                event('object_committed' if record.get('artifacts') else
                      'object_checked' if record['status']=='empty_supported_object' else 'object_failed',
                      severity='info' if record.get('artifacts') or record['status']=='empty_supported_object' else 'error',
                      **{key:record.get(key) for key in ('object_id','name','blob_type','status','reason','error','elapsed_seconds',
                          'registry_version','object_version','decoder_family','decoder_profile','attempted_decoder_profile','failure_checkpoint')})
                print('OBJECT_RESULT '+json.dumps({key:record.get(key) for key in ('object_id','name','blob_type','status','reason','elapsed_seconds')},ensure_ascii=True),flush=True)
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
    except (OSError,MemoryError) as exc:
        report.update(status='failed', reason=str(exc),error=gio.error_details(exc))
    finally:
        if binding_token is not None:
            _resume_binding.reset(binding_token)
        if before is not None and 'source_unchanged' not in report:
            try:
                report['source_hashes_after'] = {p.relative_to(package).as_posix():sha(p) for p in sources}
                report['source_unchanged'] = before == report['source_hashes_after']
                if not report['source_unchanged']: report.update(status='failed',reason='Native sources changed during recovery')
            except OSError as exc:
                report['source_integrity'] = 'not_completed'; report['source_integrity_reason'] = str(exc)
        if journal: journal.close()
        report['object_status_counts'] = dict(collections.Counter(r['status'] for r in report['objects']))
        report['has_gaps'] = any((r['status'] not in ('decoded','empty_supported_object') or
                                  r.get('export_status')=='partial') for r in report['objects'])
        if report['has_gaps'] and report['status'] == 'completed':
            report['status'] = 'partial'
        if resume and not resume_admitted:
            # A rejected request is not a replacement for the prior extraction.
            # Preserve its aggregate report, journal and object commits exactly.
            report_path = output/('resume_rejected_'+uuid.uuid4().hex+'.json')
            report['resume_admitted'] = False
        gio.atomic_json(report_path, report)
    if report.get('reason'):
        print('Native recovery '+report['status']+': '+report['reason'],flush=True)
        event('category_outcome',severity='error' if report['status']=='failed' else 'warning',
              category='native_logs_surfaces',status=report['status'],reason=report['reason'])
    print('Native recovery report: '+str(report_path), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-package', type=Path, required=True)
    parser.add_argument('--grids-only', action='store_true', help='Recover native surface/grid objects only into a new recovery directory')
    args = parser.parse_args()
    with gio.run_lock(args.export_package):
        result = run(args.export_package, grids_only=args.grids_only)
    # Unsupported metadata/layouts are explicit partial extraction, not fatal
    # to preservation and the other independent project decoders.
    return 1 if result['status'] == 'failed' else 10 if result.get('has_gaps') or result['status'] in ('not_available','unsupported_layout') else 0


if __name__ == '__main__':
    raise SystemExit(main())
