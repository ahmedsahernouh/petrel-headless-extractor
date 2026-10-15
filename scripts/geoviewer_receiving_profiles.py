# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Deterministic preparation assessments. No receiving runtime or import is invoked."""
from __future__ import annotations

import copy
import json
from geoviewer_io import read_up_to
import math
from pathlib import Path

from geoviewer_contract import _resolve, _unlinked, canonical, digest
from geoviewer_metadata_evidence import known as _known, resolve_values, single, status_is, typed_key


PROFILE_REVISION = {'source': 'source-prerequisites/6',
                    'opendtect': 'opendtect-7.0.10-odbind-1.0.0-preparation/7',
                    'paleoscan': 'paleoscan-2023.1.1-file-preparation/7'}
PROFILES = {
    'opendtect': {
        'version': '7.0.10', 'api_version': '1.0.0',
        'evidence_basis': 'October 2026 local ODBind/native source study, not runtime or license validation',
        'routes': {'well_log': 'Well log write to an explicitly bound existing well',
                   'surface_geometry': 'Horizon3D Z on compatible survey lattice',
                   'surface_property': 'Horizon3D auxiliary values on exact parent lattice',
                   'seismic': 'Seismic3D complete component/trace write'},
        'limitations': 'Survey inspection does not create surveys. Well/track/marker/time-depth creation needs a later native helper; fault/Horizon2D wrappers are read-only; culture/wavelet write wrappers were not established.'},
    'paleoscan': {
        'version': '2023.1.1', 'api_version': None,
        'evidence_basis': 'Installed guide and Python-reference study; candidate file preparation, no runtime/API entitlement validation',
        'routes': {'well_log': 'Original scalar well-log samples', 'well': 'Well identity and head table',
                   'trajectory': 'Full stored well deviation/track table',
                   'surface_geometry': 'XYZ horizon with proved reference-volume binding',
                   'surface_property': 'Mapped property joined to exact horizon geometry',
                   'seismic': '3D SEG-Y with explicit axis/header profile',
                   'marker': 'Identified well markers', 'checkshot': 'Identified time-depth table',
                   'culture_polygon': 'Ordered culture geometry'},
        'limitations': 'Documented candidate import-file routes only; no GUI, Python, licensing, discretization or successful receiving import qualification. Other versions need separate profiles.'}}

MAX_DECISIONS_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_TOTAL = 16 * 1024 * 1024
SOURCE_FIELDS = frozenset(('unit', 'crs', 'horizontal_unit', 'vertical_domain', 'vertical_unit',
    'vertical_reference', 'time_convention', 'md_unit', 'md_reference', 'log_kind', 'index_kind',
    'sampling_kind', 'index_shape', 'value_shape', 'null_semantics', 'surface_role', 'grid_registration', 'node_registration',
    'well_identity', 'track_semantics', 'head_semantics', 'marker_semantics', 'checkshot_semantics',
    'polygon_topology', 'components'))


def _bounded_json(path: Path, maximum: int) -> dict:
    _unlinked(path.absolute())
    with path.open('rb') as handle:
        raw = read_up_to(handle, maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Preparation input exceeds explicit byte bound')
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('Duplicate decision JSON member: ' + name)
            result[name] = value
        return result
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON constant')))
    if not isinstance(value, dict):
        raise ValueError('Preparation input must be a JSON object')
    return value


def load_decisions(path: Path | None, *, generation: str, manifest_sha256: str,
                   profile: str, entity_keys: set[str]) -> dict:
    """Read explicit decisions and their small local evidence, never original project paths.

    Digest binding proves which reference was cited, not its scientific truth. The
    assertion remains attributed to its named reviewer/method in every assessment.
    """
    if path is None:
        return {}
    value = _bounded_json(path, MAX_DECISIONS_BYTES)
    if (value.get('schema') != 'geoviewer.preparation-decisions/1' or
            value.get('generation') != generation or value.get('manifest_sha256') != manifest_sha256 or
            value.get('profile_revision') != PROFILE_REVISION[profile]):
        raise ValueError('Preparation decisions are not bound to this generation and profile')
    nodes = value.get('entities')
    if not isinstance(nodes, dict) or not set(nodes).issubset(entity_keys):
        raise ValueError('Preparation decisions contain unknown entities')
    total = 0
    read_cache = {}
    for key, record in nodes.items():
        if not isinstance(record, dict) or not isinstance(record.get('evidence'), list) or not record['evidence']:
            raise ValueError('Every preparation decision needs explicit local evidence')
        if not record.get('reviewer') or not record.get('method'):
            raise ValueError('Preparation decision reviewer and method are required')
        for ref in record['evidence']:
            if not isinstance(ref, dict) or not ref.get('description'):
                raise ValueError('Invalid decision evidence reference')
            candidate = _resolve(path.absolute().parent, ref.get('path', ''))
            if candidate.suffix.lower() not in ('.json', '.txt', '.md', '.csv'):
                raise ValueError('Decision evidence must be a small local metadata reference')
            cache_key = str(candidate)
            if cache_key not in read_cache:
                with candidate.open('rb') as handle:
                    raw = read_up_to(handle, MAX_REFERENCE_BYTES + 1)
                total += len(raw)
                if len(raw) > MAX_REFERENCE_BYTES or total > MAX_REFERENCE_TOTAL:
                    raise ValueError('Decision reference evidence exceeds byte bound')
                read_cache[cache_key] = digest(raw)
            if read_cache[cache_key] != ref.get('sha256'):
                raise ValueError('Decision reference digest mismatch')
        assertions = record.get('source_assertions', {})
        if not isinstance(assertions, dict) or not set(assertions).issubset(SOURCE_FIELDS):
            raise ValueError('Unknown source assertion field; QC, identity and publication cannot be overridden')
        for name, assertion in assertions.items():
            if (not isinstance(assertion, dict) or 'value' not in assertion or
                    assertion.get('scope') != 'exact_source_entity' or
                    type(assertion.get('evidence_index')) is not int or
                    not 0 <= assertion['evidence_index'] < len(record['evidence'])):
                raise ValueError('Source assertion lacks exact-entity evidence binding: ' + name)
    return nodes


def _text(value):
    return isinstance(value, str) and _known(value)


def _single(fields, name):
    return single(fields.get(name, []))


def _finite(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def metadata_object(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Expected metadata object: ' + name)
    return value


def _nondegenerate(i, j):
    determinant = i[0] * j[1] - i[1] * j[0]
    return _finite(determinant) and determinant != 0


def _grid(value):
    if not isinstance(value, dict):
        return False
    dims = value.get('dimensions')
    if not isinstance(dims, list) or len(dims) != 2 or not all(type(x) is int and x > 0 for x in dims):
        return False
    for key in ('origin_xy', 'i_basis', 'j_basis'):
        v = value.get(key)
        if not isinstance(v, list) or len(v) != 2 or not all(_finite(x) for x in v):
            return False
    i, j = value['i_basis'], value['j_basis']
    return _nondegenerate(i, j) and value.get('index_order') in (
        'i_major_j_minor', 'j_major_i_minor', 'i_fastest_j_slowest', 'j_fastest_i_slowest')


def _record(value, required):
    return isinstance(value, dict) and all(_known(value.get(k)) for k in required)


CORE_BLOCKERS = frozenset(('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD', 'SOURCE_REVISION_IDENTITY_UNRESOLVED',
    'SOURCE_RECOVERY_NOT_COMPLETE',
    'PUBLICATION_NOT_FINAL', 'PARTIAL_PUBLICATION_PRESENT', 'NUMERICAL_QC_UNVALIDATED',
    'RECORDED_PAYLOAD_IDENTITY_MISSING', 'FILE_INTEGRITY_UNAVAILABLE', 'DEPENDENCY_CYCLE',
    'SEISMIC_METADATA_UNBOUND', 'SEISMIC_METADATA_STALE', 'SEISMIC_AXIS_CONFLICT',
    'SEISMIC_GEOMETRY_CONFLICT', 'SEISMIC_NUMERICAL_QC_UNVALIDATED', 'NODE_MASK_UNAVAILABLE', 'HEAD_METADATA_STALE',
    'NATIVE_VERTEX_GAPS_UNRESOLVED'))


def exact_relationship_target(links, entity_keys, entity_categories, category):
    """Duplicate citations may agree; candidates or competing targets cannot."""
    targets = {r.get('target_key') for r in links}
    if (not links or len(targets) != 1 or
            any(r.get('status') != 'exact_id_reference' for r in links)):
        return None
    target = next(iter(targets))
    if target not in entity_keys or entity_categories.get(target) != category:
        return None
    return target


def assess_mapping(*, profile: str, node: dict, fields: dict, relations: dict,
                   artifacts: list[dict], core_reasons: list[str], decision: dict | None,
                   entity_keys: set[str], entity_categories: dict[str, str] | None = None) -> dict:
    """Evaluate one source object against a specified version's candidate mapping."""
    if profile not in PROFILES:
        raise ValueError('Receiving preparation needs a named versioned profile')
    spec = PROFILES[profile]
    category = node['category']
    key = node['entity_key']
    decision = decision or {}
    entity_categories = entity_categories or {}
    source_observations = {field: list(values) for field, values in fields.items()}
    effective = {field: _single(fields, field) for field in SOURCE_FIELDS}
    diagnostics = []
    reasons = set()
    assertions = decision.get('source_assertions', {})
    applied_assertions = set()

    def block(code, field, reason, evidence=None, action='Resolve from exact source and independent reference evidence'):
        reasons.add(code)
        diagnostics.append(dict(entity_key=key, artifact_key=None, code=code, severity='blocker',
            affected_mapping=profile + ':' + category, field_or_link=field, reason=reason,
            current_evidence=evidence, searched_scope='bound source catalogue and explicit preparation decisions', next_action=action))

    for code in core_reasons:
        if code in CORE_BLOCKERS or code.startswith('CONFLICTING_'):
            block(code, 'source_payload_qc', code.replace('_', ' ').lower())
    for field, assertion in assertions.items():
        recorded = [v for v in fields.get(field, []) if _known(v)]
        value = assertion['value']
        if recorded and any(typed_key(v) != typed_key(value) for v in recorded):
            block('SOURCE_DECISION_CONFLICT', field, 'Explicit decision conflicts with recorded source; neither is silently preferred', recorded)
        else:
            effective[field] = copy.deepcopy(value)
            applied_assertions.add(field)

    def need(field, code=None, predicate=_text):
        resolution = resolve_values(source_observations.get(field, []))
        if resolution['status'] == 'conflict':
            block('CONFLICTING_' + field.upper(), field,
                  'Incompatible source observations; no implicit precedence or decision override', resolution['alternatives'])
            return False
        value = effective.get(field)
        if not predicate(value):
            block(code or (field.upper() + '_UNRESOLVED'), field, 'Required source field is missing, ambiguous or invalid', value)
            return False
        return True

    if category not in spec['routes']:
        block('TARGET_MAPPING_UNSUPPORTED', 'target_route', spec['limitations'], category,
              'Preserve source data; validate an appropriate receiving route separately')
    if decision.get('target_version') != spec['version'] or decision.get('target_api_version') != spec['api_version']:
        block('TARGET_PROFILE_DECISION_REQUIRED', 'target_version', 'Select exactly the source-inspected version/profile; no universal compatibility',
              {'version': spec['version'], 'api_version': spec['api_version']})
    if not _text(decision.get('target_object_id')):
        block('TARGET_OBJECT_DECISION_REQUIRED', 'target_object_id', 'An explicit target object identity is required; display names are insufficient')

    def exact_dependency(kind):
        links = relations.get(kind, [])
        category = {'source_well': 'well', 'native_parent': 'surface_geometry'}[kind]
        target = exact_relationship_target(links, entity_keys, entity_categories, category)
        if target is None:
            block('RELATIONSHIP_UNRESOLVED', kind,
                  'Exactly one correctly typed identity and exclusively exact links are required', links)
            return None
        return target

    coordinate_categories = ('surface_geometry', 'surface_property', 'well', 'trajectory', 'culture_polygon', 'culture_point')
    if category in coordinate_categories:
        need('crs', 'CRS_UNRESOLVED')
        if not status_is(fields, 'crs_status', 'verified') and 'crs' not in assertions:
            block('CRS_EVIDENCE_UNVALIDATED', 'crs', 'A CRS label/candidate alone is insufficient')
        need('horizontal_unit')
        need('vertical_domain', predicate=lambda v: v in ('time', 'depth', 'elevation'))
        need('vertical_unit')
        if effective['vertical_domain'] == 'time':
            need('time_convention', predicate=lambda v: v in ('OWT', 'TWT'))
        else:
            need('vertical_reference')
    if category in ('well_log', 'surface_property'):
        need('unit', 'STORED_VALUE_UNIT_UNRESOLVED')
        if not status_is(fields, 'unit_status', 'validated_metric_profile') and 'unit' not in assertions:
            block('STORED_UNIT_PROFILE_UNVALIDATED', 'unit', 'Stored unit needs calibrated profile or explicit exact-source evidence')
    if category in ('well_log', 'marker', 'checkshot', 'trajectory'):
        dependency = exact_dependency('source_well')
        target = decision.get('well_target')
        if not (_record(target, ('source_entity_key', 'target_id', 'state')) and
                all(_text(target[k]) for k in ('source_entity_key', 'target_id', 'state')) and
                target['source_entity_key'] == dependency and target['state'] in ('existing', 'planned')):
            block('WELL_TARGET_BINDING_UNRESOLVED', 'well_target', 'Target well must bind the exact source well identity, not a matching name', target)
        elif profile == 'opendtect' and target['state'] != 'existing':
            block('TARGET_DEPENDENCY_CREATION_UNSUPPORTED', 'well_target', 'Inspected ODBind writes logs to existing wells; creating the planned well needs a later helper')
        need('md_unit'); need('md_reference')
    if category == 'well_log':
        need('log_kind', 'LOG_KIND_UNSUPPORTED', lambda v: v == 'continuous_scalar')
        need('index_kind', 'LOG_INDEX_SEMANTICS_UNRESOLVED', lambda v: v == 'measured_depth')
        need('sampling_kind', 'LOG_INTERVAL_SEMANTICS_UNRESOLVED', lambda v: v == 'point_samples')
        for field in ('index_shape', 'value_shape'):
            code = 'LOG_' + field.upper() + ('_UNSUPPORTED' if _known(effective.get(field)) else '_UNRESOLVED')
            need(field, code, lambda v: v == 'scalar_1d')
        # Summary labels or a reviewer decision cannot erase contradictory raw
        # serialization evidence. Absent optional aliases are not inferred;
        # index/value shape above remains mandatory for this scalar route.
        source_checks = {
            'interval_records': lambda v: v is False,
            'curve_kind': lambda v: v == 'continuous_scalar',
            'index_type': lambda v: v == 'measured_depth',
            'value_role': lambda v: v == 'native_scalar_values',
            'blob_type': lambda v: v == 'FloatWellLog',
        }
        for field, supported in source_checks.items():
            observations = [v for v in source_observations.get(field, []) if _known(v)]
            if any(not supported(v) for v in observations):
                block('LOG_SOURCE_SEMANTICS_CONFLICT', field,
                      'Recorded source evidence contradicts continuous scalar MD point samples; no implicit array flattening or interval conversion',
                      observations, 'Preserve original samples and resolve the exact source representation before selecting a mapping')
        need('null_semantics', predicate=lambda v: isinstance(v, dict) and
             all(_text(v.get(k)) for k in ('representation', 'source_rule')))
    if category == 'well':
        need('well_identity'); need('head_semantics', predicate=lambda v: _record(v, ('xyz_roles', 'vertical_reference', 'source_locator')))
    if category == 'trajectory':
        need('track_semantics', predicate=lambda v: _record(v, ('station_order', 'md_source', 'vertical_reference', 'full_track')) and v['full_track'] is True and v['md_source'] == 'stored_measured_depth')
    if category in ('marker', 'checkshot'):
        need(category + '_semantics', predicate=lambda v: _record(v, ('source_role', 'reference_validation')))
        if category == 'checkshot':
            need('time_convention', predicate=lambda v: v in ('OWT', 'TWT')); need('vertical_reference')
    if category == 'culture_polygon':
        need('polygon_topology', predicate=lambda v: _record(v, ('parts', 'vertex_order', 'ring_roles', 'attribute_binding')))

    if category in ('surface_geometry', 'surface_property'):
        expected_role = 'horizon_geometry' if category == 'surface_geometry' else 'mapped_property'
        need('surface_role', 'SURFACE_VALUE_ROLE_UNRESOLVED', lambda v: v == expected_role)
        need('grid_registration', 'GRID_REGISTRATION_UNRESOLVED', _grid)
        if category == 'surface_property':
            parent = exact_dependency('native_parent')
            if entity_categories.get(parent) != 'surface_geometry':
                block('PARENT_GEOMETRY_UNRESOLVED', 'native_parent', 'Parent must be geometry, not a property, well, QA scalar or folder')
            need('node_registration', 'NODE_REGISTRATION_UNVERIFIED', lambda v: _record(v, ('parent_entity_key', 'method', 'count', 'validated')) and
                 v['parent_entity_key'] == parent and v['validated'] is True and type(v['count']) is int and v['count'] > 0 and
                 _grid(effective.get('grid_registration')) and v['count'] == math.prod(effective['grid_registration']['dimensions']))
        survey = decision.get('survey')
        if not (_record(survey, ('target_id', 'crs', 'horizontal_unit', 'vertical_domain', 'vertical_unit')) and
                _text(survey['target_id'])):
            block('SURVEY_DECISION_REQUIRED', 'survey', 'Explicit survey/reference-volume context required', survey)
        else:
            for field in ('crs', 'horizontal_unit', 'vertical_domain', 'vertical_unit'):
                if survey[field] != effective[field]:
                    block('SURVEY_CONTEXT_CONFLICT', field, 'Source and planned survey differ; no implicit transform', {'source': effective[field], 'target': survey[field]})
            reference_field = 'time_convention' if effective['vertical_domain'] == 'time' else 'vertical_reference'
            if survey.get(reference_field) != effective.get(reference_field):
                block('SURVEY_CONTEXT_CONFLICT', reference_field, 'Survey vertical reference/convention differs or is missing')
            if profile == 'opendtect' and survey.get('grid_registration') != effective['grid_registration']:
                block('SURVEY_LATTICE_MISMATCH', 'grid_registration', 'Horizon3D requires compatible IL/XL/XY lattice; no lattice is invented')
            if profile == 'paleoscan':
                binding = decision.get('reference_volume_binding')
                reference = exact_relationship_target(relations.get('reference_seismic', []),
                    entity_keys, entity_categories, 'seismic')
                if not (_record(binding, ('source_entity_key', 'target_volume_id', 'method')) and
                        all(_text(binding[k]) for k in ('source_entity_key', 'target_volume_id', 'method')) and
                        binding['source_entity_key'] == reference):
                    block('REFERENCE_VOLUME_BINDING_UNRESOLVED', 'reference_volume_binding', 'Matching footprints alone do not prove the receiving reference volume')

    if category == 'seismic':
        plan = metadata_object(_single(fields, 'seismic_conversion'), 'seismic_conversion')
        qc = metadata_object(_single(fields, 'conversion_qc'), 'conversion_qc')
        geom = metadata_object(plan.get('geometry'), 'geometry')
        axis = metadata_object(plan.get('native_axis'), 'native_axis')
        source_values = {'crs': plan.get('crs'), 'horizontal_unit': plan.get('horizontal_unit'),
                         'vertical_domain': plan.get('domain'), 'vertical_unit': plan.get('source_vertical_unit'),
                         'time_convention': plan.get('time_convention'), 'vertical_reference': plan.get('vertical_reference'),
                         'components': plan.get('components')}
        for field, value in source_values.items():
            source_observations.setdefault(field, []).append(value)
            if field in assertions and _known(value) and typed_key(value) != typed_key(assertions[field]['value']):
                block('SOURCE_DECISION_CONFLICT', field, 'Decision differs from receipt-bound seismic metadata', value)
            elif field not in assertions:
                effective[field] = single(source_observations[field])
        need('crs', 'CRS_UNRESOLVED')
        if plan.get('crs_verified') is not True and 'crs' not in assertions:
            block('CRS_EVIDENCE_UNVALIDATED', 'crs', 'Seismic CRS remains a declaration/candidate')
        need('horizontal_unit'); need('vertical_unit')
        need('vertical_domain', predicate=lambda v: v in ('time', 'depth', 'length'))
        if effective['vertical_domain'] == 'time':
            need('time_convention', predicate=lambda v: v in ('OWT', 'TWT'))
        else:
            need('vertical_reference')
        if not (geom.get('storage_kind') == 'regular_3d_cube' and geom.get('full_rectangular_storage') is True and
                geom.get('trace_order') == 'inline_major_crossline_minor' and
                all(type(geom.get(k)) is int and geom[k] > 0 for k in ('inline_count', 'crossline_count')) and
                type(geom.get('trace_count')) is int and geom['trace_count'] > 0 and
                geom['trace_count'] == geom.get('inline_count', 0) * geom.get('crossline_count', 0) and
                plan.get('size') == [geom['inline_count'], geom['crossline_count'], axis.get('count')] and
                all(isinstance(geom.get(k), list) and len(geom[k]) == 2 and all(_finite(x) for x in geom[k]) for k in ('origin_xy', 'inline_basis', 'crossline_basis')) and
                _nondegenerate(geom['inline_basis'], geom['crossline_basis']) and
                all(isinstance(plan.get(k), list) and len(plan[k]) == 2 and all(_finite(x) for x in plan[k]) and plan[k][1] != 0 for k in ('inline', 'crossline'))):
            block('SEISMIC_GEOMETRY_UNRESOLVED', 'geometry', 'Complete regular 3D trace geometry required; 2D/gather/virtual names are not materialization')
        if not (type(axis.get('count')) is int and axis['count'] > 0 and _finite(axis.get('origin')) and _finite(axis.get('increment')) and axis['increment'] != 0):
            block('SEISMIC_AXIS_UNRESOLVED', 'native_axis', 'Exact finite physical sampling required')
        header = metadata_object(plan.get('segy_header_profile'), 'segy_header_profile')
        trace_fields = metadata_object(header.get('trace_fields'), 'trace_fields')
        required_trace = {'inline': (189, 4), 'crossline': (193, 4), 'x': (181, 4), 'y': (185, 4),
                          'coordinate_scalar': (71, 2), 'coordinate_units': (89, 2),
                          'sample_count': (115, 2), 'sample_interval_us': (117, 2), 'delay_ms': (109, 2)}
        if not (header.get('schema') == 'fieldviewer.generated-segy/1' and header.get('revision') == '1.0' and
                header.get('endian') == 'big' and header.get('sample_format_code') == 5 and
                all(isinstance(trace_fields.get(k), dict) and (trace_fields[k].get('byte'), trace_fields[k].get('width')) == spec
                    for k, spec in required_trace.items()) and qc.get('binary_headers_verified') is True):
            block('SEGY_HEADER_QC_UNVALIDATED', 'segy_header_profile', 'Require generated header profile and binary/trace validation; historical partial header checks are insufficient')
        if not (all(type(v) is int and v > 0 for v in
                    (qc.get('traces'), qc.get('samples_verified'), geom.get('trace_count'), axis.get('count'))) and
                qc['traces'] == geom['trace_count'] and qc['samples_verified'] == geom['trace_count'] * axis['count']):
            block('SEISMIC_QC_COUNT_CONFLICT', 'conversion_qc', 'Validated trace/sample counts must cover the entire claimed volume')
        components = effective.get('components')
        mapping = decision.get('component_mapping')
        if not (isinstance(components, list) and components and isinstance(mapping, list) and len(mapping) == len(components) and
                all(isinstance(c, dict) and type(c.get('index')) is int and c['index'] == i and isinstance(m, dict) and
                    type(m.get('source_index')) is int and m['source_index'] == i and
                    (c.get('name') is None or _text(c['name'])) and m.get('source_name') == c.get('name') and
                    _text(m.get('target_name')) for i, (c, m) in enumerate(zip(components, mapping))) and
                len({m['target_name'] for m in mapping}) == len(mapping)):
            block('SEISMIC_COMPONENT_MAPPING_UNRESOLVED', 'components', 'One explicit target mapping per source component is required; no ignored extra or zero-filled missing components')
        survey = decision.get('survey')
        if not (_record(survey, ('target_id', 'crs', 'horizontal_unit', 'vertical_domain', 'vertical_unit', 'geometry')) and
                _text(survey['target_id'])):
            block('SURVEY_DECISION_REQUIRED', 'survey', 'Exact planned survey geometry/context required')
        else:
            for field in ('crs', 'horizontal_unit', 'vertical_domain', 'vertical_unit'):
                if survey[field] != effective[field]:
                    block('SURVEY_CONTEXT_CONFLICT', field, 'Source and survey differ; implicit conversion forbidden')
            reference_field = 'time_convention' if effective['vertical_domain'] == 'time' else 'vertical_reference'
            if survey.get(reference_field) != effective.get(reference_field):
                block('SURVEY_CONTEXT_CONFLICT', reference_field, 'Source and survey vertical reference/convention differ')
            if survey['geometry'] != geom:
                block('SURVEY_LATTICE_MISMATCH', 'geometry', 'Source trace geometry must match planned survey, not just extent')

    derived = decision.get('derived_mapping')
    derived_valid = (_record(derived, ('operation', 'losses', 'prerequisites')) and _text(derived['operation']) and
                     isinstance(derived['losses'], list) and all(_text(v) for v in derived['losses']) and
                     isinstance(derived['prerequisites'], list) and all(_text(v) for v in derived['prerequisites']))
    if derived is not None and not derived_valid:
        block('DERIVED_MAPPING_UNSPECIFIED', 'derived_mapping', 'Derived proposals require explicit losses and prerequisites')
    if 'NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD' in reasons:
        state = 'blocked_missing_payload'
    elif any('QC' in code or 'INTEGRITY' in code or 'PUBLICATION' in code or 'HASH' in code or 'STALE' in code for code in reasons):
        state = 'blocked_qc'
    elif reasons & {'TARGET_MAPPING_UNSUPPORTED', 'TARGET_DEPENDENCY_CREATION_UNSUPPORTED',
                    'LOG_KIND_UNSUPPORTED', 'LOG_INDEX_SHAPE_UNSUPPORTED', 'LOG_VALUE_SHAPE_UNSUPPORTED'}:
        state = 'unsupported_target_mapping'
    elif any('RELATIONSHIP' in code or 'BINDING' in code or 'REGISTRATION' in code or 'DEPENDENCY' in code for code in reasons):
        state = 'blocked_relationship'
    elif reasons and not (derived_valid and reasons <= {'SURVEY_LATTICE_MISMATCH', 'SURVEY_CONTEXT_CONFLICT'}):
        state = 'blocked_metadata'
    elif derived_valid:
        state = 'requires_derived_mapping'
    else:
        state = 'qualified_for_planned_mapping'
    return dict(entity_key=key, profile_revision=PROFILE_REVISION[profile], receiving_state=state,
                candidate_mapping=spec['routes'].get(category), reason_codes=sorted(reasons), diagnostics=diagnostics,
                effective_source_fields={field: dict(value=copy.deepcopy(value),
                    provenance='user_override' if field in applied_assertions else 'source_observations',
                    source_resolution=resolve_values(source_observations.get(field, []))) for field, value in sorted(effective.items())},
                source_assertions=copy.deepcopy(assertions), decision_provenance={k: copy.deepcopy(decision[k]) for k in ('reviewer', 'method', 'evidence') if k in decision},
                proposed_derivation=copy.deepcopy(derived), target_version=spec['version'], target_api_version=spec['api_version'],
                qualification_scope='Specified profile preparation only; explicit decisions remain attributed claims; no runtime, license, scientific or successful import acceptance',
                storage_guidance='Do not direct-link original/retained evidence. Future loader must use independently staged copies and validated deletion policy.')
