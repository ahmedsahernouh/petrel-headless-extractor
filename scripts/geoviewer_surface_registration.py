# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Derive regular node registration from exact, already-recorded native checks.

No source or artifact reads, resampling, Z interpretation or receiving import.
The output describes historical decoder evidence; current file availability is
assessed separately. A native parent link is required before comparing lattices.
"""
from collections import Counter
import math
import uuid

RECEIPT = '07_workflows_reports/native_recovery/native_recovery_report.json'
PROFILES = ([1, 1, 1, 0, 0, 0, 0, 2, 0, 1, 1], [0, 1, 1, 0, 0, 0, 0, 2, 0, 1, 1])
METHOD = 'exact_native_parent_and_integer_node_indices'
ORDINARY_MAX_NODES = 10_000_000
# Receipt-only recognition of the decoder's separately validated resource
# profile. Keep this module independent of native parsers/numpy and bulk I/O.
CHUNKED_PROFILE = 'packed_regular_chunked/1'
LEGACY_CHUNKED_PROFILE = 'packed_regular_v0_chunked/1'
CHUNKED_MAX_NODES = 32_000_000
CHUNKED_MAX_AXIS = 65_536


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _pair(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(x) in (int, float) and math.isfinite(x) for x in value))


def _grid(item):
    profile = item.get('object_version')
    if (item.get('blob_type') != 'RegValGrid2' or not isinstance(profile, list)
            or not all(type(x) is int for x in profile) or profile not in PROFILES
            or item.get('subject_type') not in ('SurfaceSubject', 'SurfaceAttrSubject')
            or item.get('status') not in ('decoded', 'missing_metadata')
            or item.get('numeric_round_trip') != 'exact' or item.get('dataset_exported') is not True
            or item.get('cell_mask_preserved') is not True):
        raise ValueError('Exact supported full numeric grid receipt is required')
    dims = item.get('node_size')
    if (not isinstance(dims, list) or len(dims) != 2 or not all(type(x) is int and x > 0 for x in dims)
            or type(item.get('node_count')) is not int
            or item['node_count'] != math.prod(dims)):
        raise ValueError('Invalid or unsupported full node dimensions/count')
    if item['node_count'] > ORDINARY_MAX_NODES:
        expected_resource = LEGACY_CHUNKED_PROFILE if profile == PROFILES[1] else CHUNKED_PROFILE
        if (item.get('coordinate_storage_profile') != expected_resource
                or item['node_count'] > CHUNKED_MAX_NODES or max(dims) > CHUNKED_MAX_AXIS):
            raise ValueError('Full node dimensions exceed the supported recorded resource profile')
    defined = item.get('defined_nodes')
    if type(defined) is not int or not 0 < defined <= item['node_count']:
        raise ValueError('Defined-node count outside full lattice')
    for name in ('origin', 'increments', 'maximum', 'i_basis', 'j_basis'):
        if not _pair(item.get(name)):
            raise ValueError('Missing or nonfinite native geometry field: '+name)
    angle = item.get('rotation_radians')
    if type(angle) not in (int, float) or not math.isfinite(angle) or abs(angle) > 2*math.pi:
        raise ValueError('Invalid native rotation')
    dx, dy = item['increments']
    if dx <= 0 or dy <= 0 or item.get('ordering') != 'i/x fastest, then j/y':
        raise ValueError('Unsupported native increment/order')
    c, s = math.cos(angle), math.sin(angle)
    if item['i_basis'] != [dx*c, dx*s] or item['j_basis'] != [-dy*s, dy*c]:
        raise ValueError('Recorded basis differs from exact native rotation formula')
    expected_max = [o+(n-1)*d for o,n,d in zip(item['origin'], dims, item['increments'])]
    if any(not math.isfinite(x) or abs(x-y) > 1e-5 for x,y in zip(expected_max, item['maximum'])):
        raise ValueError('Native unrotated parameter extent differs from dimensions')
    return dict(schema='geoviewer.native-grid-registration/1', dimensions=list(dims),
        origin_xy=list(item['origin']), i_basis=list(item['i_basis']), j_basis=list(item['j_basis']),
        index_order='i_fastest_j_slowest', index_base=0, registration='nodes', node_key=['i', 'j'],
        source_formula='XY = origin_xy + i*i_basis + j*j_basis',
        validation_scope='Recorded exact native decoder readback; no fresh bulk read or CRS/domain calibration')


def _parent(item):
    candidates = []
    direct = item.get('parent_id')
    if direct:
        candidates.append(direct)
    ancestry = item.get('ancestry')
    if ancestry:
        if (not isinstance(ancestry, list) or not isinstance(ancestry[0], dict)
                or ancestry[0].get('object_id') != item['object_id']):
            raise ValueError('Ancestry must start with this exact native object')
        if ancestry[0].get('parent'):
            candidates.append(ancestry[0]['parent'])
    if not candidates or not all(_uuid(v) for v in candidates) or len(set(candidates)) != 1:
        raise ValueError('Exact native parent is missing or conflicting')
    return candidates[0]


def derive_surface_evidence(report, project_key, receipt=RECEIPT):
    """Return additive catalogue evidence, keeping unresolved joins explicit."""
    objects = [x for x in report.get('objects', []) if isinstance(x, dict)]
    ids = Counter(x.get('object_id') for x in objects if isinstance(x.get('object_id'), str))
    indexed = {x['object_id']: (i, x) for i,x in enumerate(report.get('objects', []))
               if isinstance(x, dict) and _uuid(x.get('object_id')) and ids[x['object_id']] == 1}
    grids = {}; rows = []

    def emit(tag, number, field, value, dependencies=None):
        rows.append(dict(entity_key=project_key+':native:'+tag, field=field, raw_value=value,
            locator=receipt+'#/objects/'+str(number), resolution='derived_from_bound_native_receipt',
            dependencies=dependencies or [], scope='Historical exact native numeric and identity evidence; current availability remains separate'))

    for tag, (number,item) in indexed.items():
        if item.get('subject_type') not in ('SurfaceSubject', 'SurfaceAttrSubject'):
            continue
        try:
            if report.get('source_unchanged') is not True:
                raise ValueError('Native source integrity was not recorded unchanged')
            grids[tag] = _grid(item)
            emit(tag, number, 'grid_registration', grids[tag])
        except (ValueError, TypeError, OverflowError) as exc:
            emit(tag, number, 'grid_registration_evidence', dict(status='unresolved', reason=str(exc)))
    for tag, (number,item) in indexed.items():
        if item.get('subject_type') != 'SurfaceAttrSubject':
            continue
        value = dict(validated=False, method=METHOD)
        try:
            parent = _parent(item)
            value['parent_entity_key'] = project_key+':native:'+parent
            if parent not in indexed or indexed[parent][1].get('subject_type') != 'SurfaceSubject':
                raise ValueError('Exact parent must be an unambiguous SurfaceSubject')
            if tag not in grids or parent not in grids:
                raise ValueError('Both full native numeric lattice receipts are required')
            if grids[tag] != grids[parent]:
                raise ValueError('Parent and property lattices differ; no resampling or nearest-node join')
            value.update(validated=True, count=item['node_count'], node_key=['i','j'],
                definition_policy='require_both_node_masks', property_defined_nodes=item['defined_nodes'],
                parent_defined_nodes=indexed[parent][1]['defined_nodes'],
                validation_scope='Exact native parent ID plus identical regular XY/index descriptors and recorded numeric readback; masks may differ; no physical Z/unit/CRS inference')
            emit(tag, number, 'node_registration', value,
                 [receipt+'#/objects/'+str(indexed[parent][0]), receipt+'#/objects/'+str(number)])
        except (ValueError, TypeError, OverflowError) as exc:
            value['reason'] = str(exc)
            emit(tag, number, 'node_registration', value)
    return rows
