# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Offline, bounded, dataset-first view of a FieldViewer delivery.

The summary is presentation only. FILE_INDEX remains the authority for accepted
downloads; project context/inventory supply discovered, possibly unrecovered data.
"""
from __future__ import annotations

import csv
import base64
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import tempfile
import struct
import zipfile
import zlib
from urllib.parse import quote

MAX_JSON_BYTES = 24 * 1024 * 1024
MAX_OBJECTS = 50000
PREVIEW_ROWS = 256
PREVIEW_BYTES = 128 * 1024
TOTAL_PREVIEW_ROWS = 20000
TOTAL_PREVIEW_BYTES = 2 * 1024 * 1024


def _grid_image(package, record, budget):
    """Render a receipt-bound, size-limited derived NPZ; never decode native data.

    Optional bundled numpy reads numeric arrays only. Return a bounded embedded
    PNG of sampled native nodes, preserving undefined values as blank pixels.
    """
    artifact = next((a for a in record.get('artifacts', [])
                     if str(a.get('path', '')).endswith('/grid_preview.npz')), None)
    if not artifact or budget['bytes'] >= 2 * 1024 * 1024:
        return None
    path = _safe_file(package / 'native_data', artifact.get('path'))
    if not path or path.stat().st_size > 2 * 1024 * 1024:
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact.get('sha256'):
        return None
    try:
        import numpy as np
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 8 * 1024 * 1024:
                return None
        with np.load(path, allow_pickle=False) as data:
            values = data['value']; valid = data['valid']
            if values.ndim != 2 or valid.shape != values.shape or values.size > 500000:
                return None
            iy = np.linspace(0, values.shape[0]-1, min(96, values.shape[0]), dtype=int)
            ix = np.linspace(0, values.shape[1]-1, min(96, values.shape[1]), dtype=int)
            values = values[np.ix_(iy, ix)]; valid = valid[np.ix_(iy, ix)].astype(bool) & np.isfinite(values)
            if not valid.any():
                return None
            flip_rows = flip_columns = False
            axes_known = False
            if 'x' in data and 'y' in data and data['x'].shape == data['value'].shape and data['y'].shape == data['value'].shape:
                x = data['x'][np.ix_(iy, ix)]; y = data['y'][np.ix_(iy, ix)]
                def edge_median(edge):
                    finite = edge[np.isfinite(edge)]
                    return float(np.median(finite)) if finite.size else None
                left, right = edge_median(x[:, 0]), edge_median(x[:, -1])
                first_y, last_y = edge_median(y[0, :]), edge_median(y[-1, :])
                axes_known = all(v is not None for v in (left, right, first_y, last_y)) and right != left and last_y != first_y
                if not axes_known:
                    # Curvilinear previews may store zero coordinates along
                    # undefined edges. Estimate axis directions from defined
                    # interior nodes; never let those edge placeholders decide.
                    coordinate_mask = valid & np.isfinite(x) & np.isfinite(y)
                    rr, cc = np.indices(values.shape)
                    if coordinate_mask.sum() >= 3:
                        basis = np.column_stack((cc[coordinate_mask], rr[coordinate_mask], np.ones(coordinate_mask.sum())))
                        coefficients, _, rank, _ = np.linalg.lstsq(basis, np.column_stack((x[coordinate_mask], y[coordinate_mask])), rcond=None)
                        dx, dy = float(coefficients[0, 0]), float(coefficients[1, 1])
                        tolerance_x = max(1., float(np.ptp(x[coordinate_mask]))) * 1e-10
                        tolerance_y = max(1., float(np.ptp(y[coordinate_mask]))) * 1e-10
                        axes_known = rank == 3 and abs(dx) > tolerance_x and abs(dy) > tolerance_y
                        if axes_known:
                            left, right, first_y, last_y = 0., dx, 0., dy
                if axes_known:
                    # Raster rows grow downward. Native increasing Y must grow
                    # upward on screen; X must grow toward the right.
                    flip_rows = last_y > first_y
                    flip_columns = right < left
                    if flip_rows:
                        values = values[::-1, :]; valid = valid[::-1, :]
                    if flip_columns:
                        values = values[:, ::-1]; valid = valid[:, ::-1]
            lo = float(values[valid].min()); hi = float(values[valid].max())
            h, w = values.shape; scan = bytearray()
            for row in range(h):
                scan.append(0)
                for col in range(w):
                    if not valid[row, col]:
                        scan.extend((235, 239, 243, 0)); continue
                    t = (float(values[row, col])-lo)/(hi-lo or 1)
                    scan.extend((round(30+220*t), round(80+130*t), round(160-115*t), 255))
            def chunk(kind, content):
                return struct.pack('>I', len(content))+kind+content+struct.pack('>I', zlib.crc32(kind+content) & 0xffffffff)
            png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(bytes(scan)))+chunk(b'IEND',b'')
            if budget['bytes'] + len(png) > 2 * 1024 * 1024:
                return None
            budget['bytes'] += len(png)
            return {'image': 'data:image/png;base64,'+base64.b64encode(png).decode('ascii'),
                    'rows': h, 'columns': w, 'min': lo, 'max': hi,
                    'orientation': {'coordinates_available': axes_known, 'rows_reversed': flip_rows, 'columns_reversed': flip_columns},
                    'scope': ('Increasing Y toward the top; increasing X toward the right. ' if axes_known else 'Native row/column order; X/Y directions unavailable. ')
                             +'Sampled nodes; undefined values are blank. Rotation and curvilinear geometry are not reprojected.'}
    except (ImportError, OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def _read_json(value, warnings, label):
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        path = Path(value)
        if not path.is_file():
            return {}
        if path.stat().st_size > MAX_JSON_BYTES:
            warnings.append(label + ' exceeds summary read budget')
            return {}
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError) as exc:
        warnings.append(label + ' unavailable: ' + str(exc))
        return {}


def _attach_verified_tops(report, stem, objects, context, warnings):
    """Read a contained, hash-bound post-run tops receipt; preserve base receipts."""
    receipt = report.parent/'Details'/(stem+'_TOPS')/'RECOVERY.json'
    if not receipt.is_file():
        return None
    try:
        data = _read_json(receipt, warnings, 'Post-run tops receipt')
        if data.get('schema') != 'fieldviewer.native-tops/1' or data.get('status') != 'verified':
            raise ValueError('Tops recovery receipt is not verified')
        if data.get('run_receipt_path') != stem+'_data/RUN_RESULT.json':
            raise ValueError('Tops receipt belongs to another report run')
        run = _safe_file(report.parent, data.get('run_receipt_path'))
        table = _safe_file(report.parent, data.get('csv_path'))
        if not run or not table or run.stat().st_size > MAX_JSON_BYTES or table.stat().st_size > 2*1024*1024:
            raise ValueError('Tops receipt paths or bounds are invalid')
        if hashlib.sha256(run.read_bytes()).hexdigest() != data.get('run_receipt_sha256') or hashlib.sha256(table.read_bytes()).hexdigest() != data.get('csv_sha256'):
            raise ValueError('Tops receipt or CSV checksum mismatch')
        target = objects.get(data.get('object_id'))
        if not target or target['category'] != 'Well tops':
            raise ValueError('Verified tops collection is absent from this catalogue')
        with table.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(itertools.islice(csv.DictReader(stream),4097))
        if not rows or len(rows)>4096 or len(rows)!=data.get('row_count'):
            raise ValueError('Tops row count mismatch or summary bound exceeded')
        if {int(r['point_index']) for r in rows} != set(range(len(rows))):
            raise ValueError('Tops point indexes are not unique and complete')
        for row in rows:
            if row.get('object_id') != data['object_id']:
                raise ValueError('Tops row belongs to another native collection')
            well = objects.get(row['well_link_uuid'])
            if not well or well['category']!='Wells' or well['name']!=row['well_name']:
                raise ValueError('Tops exact well link does not match the catalogue')
            if not row['surface'] or not all(math.isfinite(float(row[k])) for k in ('x','y','z','measured_depth')):
                raise ValueError('Tops labels or numeric fields are invalid')
        for row in rows:
            objects[row['well_link_uuid']].setdefault('top_picks',[]).append(row)
        target['top_picks']=rows
        target['files'].insert(0,{'label':'Download well tops (CSV)','href':_href(table,report)})
        target['tops_geometry_only']=False
        target['status']='tops_available'
        target['notes']=[n for n in target['notes'] if not n.startswith('Recovered XYZ point geometry only')]
        target['notes'].append('Native well/formation/MD bindings independently checked against the existing Petrel export. Native Z is not a resolved TVD datum.')
        # Definition/filter subjects stay in the detailed inventory. They are
        # not additional pick tables once this collection has recovered picks.
        for raw in _rows(context.get('objects',[])):
            key=str(raw.get('object_id'))
            if raw.get('category') in ('WellTopsWellSubject','WellTopsSurfaceSubject','WellTopsZoneSubject') and key in objects and not objects[key]['files']:
                objects.pop(key)
        return {'rows':len(rows),'wells':len({r['well_name'] for r in rows}),
                'formations':len({r['surface'] for r in rows}),'units':data.get('units',{}),
                'verification':'Native schema/Model bindings independently compared with the existing Petrel ASCII export.'}
    except (OSError,ValueError,TypeError,KeyError,csv.Error) as exc:
        warnings.append('Post-run well tops unavailable: '+str(exc))
        return None


def _attach_native_tops(package, destination, index, objects, warnings, report):
    """Show only hash-bound native rows whose collection was delivered."""
    if not package:
        return None
    receipt = _read_json(package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json', warnings, 'Native tops receipt')
    binding = receipt.get('well_top_binding', {})
    if binding.get('status') != 'native_schema_bound':
        return None
    try:
        table = _safe_file(package, binding['output_path'])
        if not table or table.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Bound tops table missing or exceeds summary limit')
        if hashlib.sha256(table.read_bytes()).hexdigest() != binding['output_sha256']:
            raise ValueError('Bound tops table checksum mismatch')
        with table.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(itertools.islice(csv.DictReader(stream), 4097))
        if not rows or len(rows) > 4096 or len(rows) != binding['row_count']:
            raise ValueError('Bound tops row count mismatch or summary limit exceeded')
        delivered = {str(r.get('object_id')): _safe_file(destination, r.get('path')) for r in index.get('files', [])
                     if r.get('category') == 'well_tops' and r.get('status') == 'exported'
                     and _safe_file(destination, r.get('path'))}
        grouped = {}
        for row in rows:
            identity = row['object_id']
            target = objects.get(identity)
            well = objects.get(row['well_link_uuid'])
            if identity not in delivered or not target or target['category'] != 'Well tops':
                raise ValueError('Bound tops collection was not accepted for delivery')
            if not well or well['category'] != 'Wells' or well['name'] != row['well_name']:
                raise ValueError('Bound tops exact well identity mismatch')
            if not row['surface'] or not all(math.isfinite(float(row[k])) for k in ('x','y','z','measured_depth')):
                raise ValueError('Invalid bound tops labels or numbers')
            grouped.setdefault(identity, []).append(row)
        for identity, picks in grouped.items():
            if {int(r['point_index']) for r in picks} != set(range(len(picks))):
                raise ValueError('Bound tops indexes are not unique and complete')
        for identity, picks in grouped.items():
            target = objects[identity]
            href = _href(delivered[identity], report)
            target['files'] = [f for f in target['files'] if f['href'] != href]
            target['files'].insert(0, {'label': 'Download well tops (CSV)', 'href': href})
            target['top_picks'] = picks
            target['status'] = 'native_schema_bound'
            target['tops_geometry_only'] = False
            target['notes'] = [n for n in target['notes'] if not n.startswith('Recovered XYZ point geometry only')]
            target['notes'].append('Native Model/attribute identities bound; independent export calibration and units/datum remain separate checks.')
            for row in picks:
                objects[row['well_link_uuid']].setdefault('top_picks', []).append(row)
        return {'rows': len(rows), 'wells': len({r['well_link_uuid'] for r in rows}),
                'formations': len({r['surface'] for r in rows}), 'units': {},
                'verification': 'Native schema and exact Model identities; independent export calibration is separate.'}
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        warnings.append('Native bound well tops unavailable: '+str(exc))
        return None


def _rows(value):
    return value[:MAX_OBJECTS] if isinstance(value, list) else []


def _registry(path, warnings):
    if not path.is_file():
        return []
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            warnings.append('Native object registry exceeds summary read budget')
            return []
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return [dict(row, _registry_payload=True) for row in itertools.islice(csv.DictReader(stream), MAX_OBJECTS)]
    except (OSError, UnicodeError, csv.Error) as exc:
        warnings.append('Native object registry unavailable: ' + str(exc))
        return []


def _safe_file(base, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\0' in relative:
        return None
    if re.match(r'^[A-Za-z]:', relative) or relative.startswith('/'):
        return None
    parts = relative.split('/')
    if any(p in ('', '.', '..') or ':' in p for p in parts):
        return None
    try:
        base = Path(base).resolve()
        target = base.joinpath(*parts).resolve(strict=True)
        if target.is_file() and target.is_relative_to(base):
            return target
    except (OSError, ValueError):
        pass
    return None


def _href(path, report):
    try:
        return quote(os.path.relpath(path, report.parent).replace('\\', '/'), safe='/')
    except ValueError:
        return None


def _category(raw):
    value = str(raw.get('category') or raw.get('subject_type') or raw.get('blob_type') or raw.get('object_type') or raw.get('type') or '').lower()
    if value.endswith('tree') or value.endswith('service') or value.endswith('simulatorsubject'):
        return 'Unclassified'
    if value in ('well_headers', 'welltracesubject', 'wellsubject', 'well'):
        return 'Wells'
    if value in ('logs', 'welllogsubject', 'floatwelllog', 'intwelllog', 'well_log'):
        return 'Well logs'
    if value in ('trajectories', 'trajectory', 'welltrace3') or 'trajectoryprovider' in value:
        return 'Trajectories'
    if value.startswith('welltops') or value in ('well_tops', 'well_markers', 'tops'):
        return 'Well tops'
    if 'horizon' in value:
        return 'Horizons / Interpretations'
    if value in ('grids', 'regvalgrid2', 'valgrid2', 'surface_geometry', 'surfaces') or 'surface' in value:
        return 'Surfaces / Grids'
    if value in ('polygons', 'polygon_parts', 'culture_polygon', 'polygons3', 'polygonssubject') or value.startswith('fault'):
        return 'Polygons / Faults'
    if 'seismic' in value or value in ('zgy', 'segy'):
        return 'Seismic'
    if 'property' in value or value in ('floatproperty', 'intproperty'):
        return 'Properties'
    if 'model' in value or 'pillargrid' in value or 'reservoirgrid' in value:
        return 'Models'
    if 'point' in value or 'top' in value:
        return 'Points'
    if value in ('', 'unknown', 'unclassified'):
        return 'Unclassified'
    return 'Other: ' + str(raw.get('category') or raw.get('subject_type') or raw.get('blob_type') or raw.get('object_type') or raw.get('type'))[:80]


def _polygon_geometry(path, budget):
    """Complete bounded XY linework; never turn a CSV prefix into a shape."""
    size = path.stat().st_size
    if path.suffix.lower() != '.csv' or size > 16 * 1024 * 1024 or budget['bytes'] + size > 48 * 1024 * 1024:
        return None
    budget['bytes'] += size
    parts = []
    seen = set()
    count = 0
    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if not {'part_index', 'vertex_index', 'x', 'y', 'is_closed_native'}.issubset(reader.fieldnames or []):
                return None
            current = None
            declared = None
            for row in reader:
                key = row['part_index']
                closure = row['is_closed_native'].lower()
                if closure not in ('yes', 'no', 'true', 'false', '1', '0'):
                    return None
                if key != current:
                    if parts and declared is not None and len(parts[-1]['points']) != declared:
                        return None
                    if key in seen:
                        return None
                    seen.add(key); current = key
                    parts.append({'id': key, 'closed': row['is_closed_native'].lower() in ('yes', 'true', '1'), 'points': []})
                    declared = int(row['part_vertex_count']) if row.get('part_vertex_count') else None
                part = parts[-1]
                if declared is not None and int(row.get('part_vertex_count', '-1')) != declared:
                    return None
                if int(row['vertex_index']) != len(part['points']):
                    return None
                if part['closed'] != (row['is_closed_native'].lower() in ('yes', 'true', '1')):
                    return None
                xy = [float(row['x']), float(row['y'])]
                if not all(math.isfinite(v) for v in xy):
                    return None
                part['points'].append(xy); count += 1
                if count > 20000 or budget['points'] + count > 200000:
                    return None
        if not count or declared is not None and len(parts[-1]['points']) != declared:
            return None
        budget['points'] += count
        return {'parts': parts, 'vertices': count, 'scope': 'Complete exported XY linework; native part boundaries and closure retained. +X right, +Y up. Z is omitted in this plan view; CRS is unresolved.'}
    except (OSError, ValueError, TypeError, csv.Error, UnicodeError):
        return None


def _preview(path, category, budget, warnings, limits=None):
    if category not in ('Well logs', 'Trajectories', 'Surfaces / Grids', 'Polygons / Faults', 'Well tops') or path.suffix.lower() != '.csv':
        return []
    if path.stat().st_size > 128 * 1024 * 1024:
        return []
    byte_share, row_share = limits or (PREVIEW_BYTES, PREVIEW_ROWS)
    remaining = min(byte_share, TOTAL_PREVIEW_BYTES - budget['bytes'])
    row_limit = min(row_share, TOTAL_PREVIEW_ROWS - budget['rows'])
    if remaining <= 0 or row_limit <= 0:
        if 'Project preview budget reached; some previews omitted' not in warnings:
            warnings.append('Project preview budget reached; some previews omitted')
        return []
    try:
        with path.open('rb') as stream:
            # Read only a bounded prefix, including for long physical CSV files.
            raw_prefix = stream.read(remaining)
        budget['bytes'] += len(raw_prefix)
        prefix = raw_prefix.decode('utf-8-sig', errors='ignore')
        lines = prefix.splitlines(keepends=True)
        if lines and not lines[-1].endswith(('\n', '\r')):
            lines.pop()
        reader = csv.DictReader(itertools.islice(lines, row_limit + 1))
        allowed = {'md', 'measured_depth', 'depth', 'raw_value', 'value', 'x', 'y', 'z',
                   'well_name', 'is_null', 'defined', 'is_defined'}
        result = []
        for row in itertools.islice(reader, row_limit):
            if not isinstance(row, dict):
                continue
            sample = {k: str(v)[:64] for k, v in row.items() if k and k.lower() in allowed}
            invalid = str(row.get('is_null', '')).lower() in ('1', 'true', 'yes') or str(row.get('defined', row.get('is_defined', ''))).lower() in ('0', 'false', 'no')
            for field in ('md', 'measured_depth', 'depth', 'raw_value', 'value', 'x', 'y', 'z'):
                if row.get(field) not in ('', None):
                    try:
                        invalid |= not math.isfinite(float(row[field]))
                    except ValueError:
                        invalid = True
            sample['_valid'] = not invalid
            result.append(sample)
        budget['rows'] += len(result)
        if budget['bytes'] >= TOTAL_PREVIEW_BYTES or budget['rows'] >= TOTAL_PREVIEW_ROWS:
            warnings.append('Project preview budget reached; some previews omitted')
        return result
    except (OSError, UnicodeError, csv.Error):
        return []


def _ownership(raw, known_wells):
    parent = str(raw.get('parent_id') or raw.get('well_id') or '')
    if parent in known_wells:
        return parent
    for ancestor in raw.get('ancestry', []) if isinstance(raw.get('ancestry'), list) else []:
        if not isinstance(ancestor, dict):
            continue
        identity = str(ancestor.get('object_id') or ancestor.get('id') or '')
        if identity in known_wells:
            return identity
        if str(ancestor.get('kind') or ancestor.get('category') or '').lower() in ('welltracesubject', 'wellsubject', 'well'):
            name = str(ancestor.get('name') or '')
            matches = [key for key, item in known_wells.items() if item['name'] == name]
            if len(matches) == 1:
                return matches[0]
    name = str(raw.get('well_name') or '')
    matches = [key for key, item in known_wells.items() if item['name'] == name]
    return matches[0] if name and len(matches) == 1 else None


def _dataset_candidate(raw):
    """Exclude Petrel UI/configuration nodes from the concise data tree."""
    value = str(raw.get('category') or raw.get('subject_type') or raw.get('blob_type') or raw.get('object_type') or '').lower()
    if value == 'surfaceattrsubject':
        return True
    if value in ('horizonfiltersubject', 'horizonwellintersectcache'):
        return False
    if any(token in value for token in ('folder', 'template', 'colortable', 'process', 'proxy',
                                         'calculator', 'setting', 'license', 'catalog', 'datum',
                                         'attribute', 'attrsubject', 'refsubject', 'service',
                                         'subjectshape', 'defaultsubject')):
        return False
    if value.endswith('tree') or value in ('projectsubject', 'objectsubject', 'formatsubject'):
        return False
    if raw.get('_registry_payload'):
        return bool(value)
    category = _category(raw)
    return bool(value) and (category not in ('Unclassified',) and not category.startswith('Other:') or
                            any(token in value for token in ('meshdata', 'interpretationdata',
                                                              'griddata', 'linedata', 'volumedata')))


def publish_summary(report_path, *, package=None, destination=None, inventory=None,
                    context=None, status='running', label='', stages=None,
                    detailed_report=None):
    """Atomically write/refresh *_SUMMARY.html; return concise result metadata.

    ``destination`` is the EXPORTS directory. Existing index and context files
    are read only. Neither an index nor a completed run is required.
    """
    report = Path(report_path).absolute()
    if report.suffix.lower() != '.html' or not report.stem.upper().endswith(('_SUMMARY', '_REPORT')):
        raise ValueError('report_path must be a run-specific *_REPORT.html or *_SUMMARY.html')
    if not report.parent.is_dir():
        raise FileNotFoundError(report.parent)
    stem = report.stem[:-8] if report.stem.upper().endswith('_SUMMARY') else report.stem[:-7]
    destination = Path(destination).absolute() if destination else report.parent / (stem + '_EXPORTS')
    package = Path(package).absolute() if package else None
    warnings = []
    preview_budget = {'rows': 0, 'bytes': 0}
    index = _read_json(destination / 'FILE_INDEX.json', warnings, 'FILE_INDEX.json')
    if not index and (destination / 'FILE_INDEX.csv').is_file():
        try:
            csv_path = destination / 'FILE_INDEX.csv'
            if csv_path.stat().st_size > MAX_JSON_BYTES:
                warnings.append('FILE_INDEX.csv exceeds summary read budget')
            else:
                with csv_path.open(encoding='utf-8-sig', newline='') as stream:
                    index = {'files': list(itertools.islice(csv.DictReader(stream), MAX_OBJECTS))}
        except (OSError, UnicodeError, csv.Error) as exc:
            warnings.append('FILE_INDEX.csv unavailable: ' + str(exc))
    if not index:
        warnings.append('Accepted file index unavailable')
    if context is None and package:
        context = package / '01_project_metadata' / 'project_context.json'
    context = _read_json(context, warnings, 'Project context')
    inventory = _read_json(inventory, warnings, 'Inventory')
    if index.get('partial') or index.get('status') == 'partial':
        status = 'partial' if status in ('running', 'complete', 'completed') else status
    status = str(status or 'unknown')
    objects = {}
    def ensure(key, raw, category=None):
        key = str(key)
        name = str(raw.get('name') or raw.get('object_name') or raw.get('well_name') or '')
        category = category or _category(raw)
        native_type = category[7:] if category.startswith('Other: ') else None
        if native_type:
            category = 'Other data'
        if key not in objects:
            objects[key] = {'key': key, 'name': name or 'Unnamed dataset', 'category': category,
                            'status': str(raw.get('status') or 'metadata_only'), 'parent': None,
                            'unit': str(raw.get('units') or ''), 'files': [], 'preview': [], 'notes': []}
        obj = objects[key]
        if native_type and 'Native type: ' + native_type not in obj['notes']:
            obj['notes'].append('Native type: ' + native_type)
        if name and obj['name'] == 'Unnamed dataset':
            obj['name'] = name
        if obj['category'] == 'Unclassified' and category != 'Unclassified':
            obj['category'] = category
        return obj
    registry = _registry(package / '01_project_metadata' / 'native_data_object_registry.csv', warnings) if package else []
    discovery = (_rows(context.get('objects', [])) + _rows(context.get('seismic_objects', [])) +
                 _rows(inventory.get('native_objects', [])) + registry)
    for raw in discovery:
        if not isinstance(raw, dict):
            continue
        identity = str(raw.get('object_id') or raw.get('id') or raw.get('uuid') or '')
        if identity and _dataset_candidate(raw):
            obj = ensure(identity, raw)
            if raw.get('decode_status') and obj['status'] == 'metadata_only':
                obj['status'] = str(raw['decode_status'])
            if raw.get('parent_id'):
                obj['parent'] = str(raw['parent_id'])
    # Join transport IDs only through the inventory's exact native UUID proof.
    # Names alone never establish that two seismic datasets are the same object.
    seismic_aliases = {}
    for raw in _rows(inventory.get('objects', [])):
        if isinstance(raw, dict):
            identity = str(raw.get('object_id') or raw.get('id') or raw.get('source') or '')
            native_ids = {str(n.get('object_id')) for n in _rows(raw.get('native_objects', []))
                          if isinstance(n, dict) and n.get('object_id')}
            if identity and raw.get('association') == 'exact_native_UUID_in_selected_store' and len(native_ids) == 1:
                native_id = next(iter(native_ids))
                seismic_aliases[identity] = native_id
                identity = native_id
            if identity and _dataset_candidate(raw):
                ensure(identity, raw, 'Seismic')
    wells = {key: obj for key, obj in objects.items() if obj['category'] == 'Wells'}
    context_by_id = {}
    for raw in discovery:
        if isinstance(raw, dict):
            context_by_id.setdefault(str(raw.get('object_id') or raw.get('id')), raw)
    housekeeping_ids = {str(raw.get('object_id') or raw.get('id')) for raw in _rows(context.get('objects', [])) + _rows(context.get('model_subject_identities', []))
                        if isinstance(raw, dict) and str(raw.get('category') or raw.get('subject_type') or '').lower() in ('wellsattributesubject','attributevaluecollection','wellsattrsubject')}
    for key in housekeeping_ids:
        objects.pop(key,None)
    accepted = _rows(index.get('files', []))
    attempted = set()
    for row in _rows(index.get('partial_files', [])):
        if isinstance(row, dict) and row.get('object_id') and row.get('category') != 'well_headers':
            partial_id = str(row['object_id'])
            partial_obj = ensure(seismic_aliases.get(partial_id, partial_id), row)
            partial_obj['status'] = str(row.get('status') or 'checkpoint_only_partial')
            partial_obj['notes'].append('Checkpoint-only file; final accepted delivery is unavailable')
    for row in accepted:
        if not isinstance(row, dict):
            continue
        identity = str(row.get('object_id') or '')
        identity = seismic_aliases.get(identity, identity)
        category = _category(row)
        owner_type = str(context_by_id.get(identity, {}).get('category') or context_by_id.get(identity, {}).get('subject_type') or '').lower()
        if identity in housekeeping_ids or owner_type in ('wellsattributesubject', 'attributevaluecollection', 'wellsattrsubject'):
            continue
        if not identity or row.get('category') in ('well_headers', 'workflows'):
            # Aggregate tables are delivery artifacts, not invented objects.
            continue
        obj = ensure(identity, row, category)
        if obj['category'] == 'Well tops' and row.get('category') == 'points':
            obj['tops_geometry_only'] = True
            note = 'Recovered XYZ point geometry only; well/formation labels and MD/TVD picks are not validated.'
            if note not in obj['notes']:
                obj['notes'].append(note)
        if row.get('units') and obj['unit'].lower() in ('','unknown','unresolved'):
            obj['unit'] = str(row['units'])
        attempted.add(identity)
        if category == 'Wells':
            wells[identity] = obj
        rel = row.get('path')
        target = _safe_file(destination, rel)
        if target and str(row.get('status') or '').lower() in ('exported', 'accepted', 'converted', 'decoded'):
            fmt = str(row.get('format') or target.suffix.lstrip('.')).upper()
            link = _href(target, report)
            if link and fmt not in ('JSON', 'TXT', 'XML'):
                path_name = str(rel).lower()
                if not any(token in path_name for token in ('_mask', '_cells', '_metadata', '_attributes', '_parts')):
                    role = 0 if any(token in path_name for token in ('_samples', '_nodes', '_vertices', '_stations')) else 1
                    obj['files'].append({'label': 'Download ' + fmt, 'href': link, '_role': role})
            if fmt == 'CSV' and not any(token in str(rel).lower() for token in ('_mask', '_cells', '_metadata', '_attributes', '_parts')):
                score = 0 if any(token in str(rel).lower() for token in ('_samples', '_nodes', '_vertices', '_stations')) else 1
                if '_preview_source' not in obj or score < obj['_preview_source'][0]:
                    obj['_preview_source'] = (score, target)
        elif rel:
            warnings.append('Indexed file unavailable or unaccepted for ' + obj['name'])
        note = row.get('metadata_warning')
        if note and note not in obj['notes']:
            obj['notes'].append(str(note)[:240])
    candidates = [obj for obj in objects.values() if '_preview_source' in obj
                  and obj['category'] in ('Well logs', 'Trajectories', 'Surfaces / Grids', 'Polygons / Faults', 'Well tops')]
    # Share the project budget across datasets: earlier trajectories must not
    # consume every byte before logs, grids and faults receive a small preview.
    count = max(1, len(candidates))
    limits = (min(PREVIEW_BYTES, TOTAL_PREVIEW_BYTES // count),
              min(PREVIEW_ROWS, TOTAL_PREVIEW_ROWS // count))
    if count and (limits[0] < PREVIEW_BYTES or limits[1] < PREVIEW_ROWS):
        warnings.append('Project preview budget shared across datasets; sample previews are bounded')
    polygon_budget = {'bytes': 0, 'points': 0}
    for obj in objects.values():
        source = obj.pop('_preview_source', None)
        if source:
            obj['preview'] = _preview(source[1], obj['category'], preview_budget, warnings, limits)
            if obj['category'] == 'Polygons / Faults':
                obj['polygon_preview'] = _polygon_geometry(source[1], polygon_budget)
                if not obj['polygon_preview']:
                    obj['notes'].append('Complete segmented geometry preview unavailable within bounds; use the download. The sample table is not a complete shape.')
    # Well-head aggregate tables may be the only source of exact well names.
    for row in accepted:
        if not isinstance(row, dict) or row.get('category') != 'well_headers':
            continue
        if str(row.get('status') or '').lower() not in ('exported', 'accepted', 'converted', 'decoded'):
            continue
        target = _safe_file(destination, row.get('path'))
        if not target or target.suffix.lower() != '.csv':
            continue
        for record in _preview_well_headers(target):
            name = record.get('well_name') or ''
            matches = [key for key, well in wells.items() if well['name'] == name]
            identity = record.get('object_id') or record.get('well_id')
            if not identity and len(matches) > 1:
                warnings.append('Well header ownership ambiguous for ' + name)
                continue
            identity = identity or (matches[0] if len(matches) == 1 else 'well-name:' + name)
            if name:
                well = ensure(identity, {'name': name, 'status': 'decoded'}, 'Wells')
                wells[identity] = well
                def value(*keys):
                    return next((record[k] for k in keys if record.get(k) not in ('', None)), None)
                well['header'] = {'x': value('x'), 'y': value('y'), 'z': value('z'),
                                  'kb': value('kb', 'kb_elevation'), 'td': value('td', 'total_depth'),
                                  'z_status': value('z_coordinate_status'),
                                  'crs_status': value('crs_status'),
                                  'horizontal_units_status': value('horizontal_units_status'),
                                  'vertical_reference_status': value('vertical_reference_status'),
                                  'download': _href(target, report)}
                well['status'] = 'header_available'
    for key, obj in objects.items():
        if obj['category'] not in ('Well logs', 'Trajectories'):
            continue
        raw = context_by_id.get(key, {})
        owner = _ownership(raw, wells)
        if not owner:
            parent = str(raw.get('parent_id') or '')
            visited = set()
            while parent and parent not in visited:
                visited.add(parent)
                if parent in wells:
                    owner = parent; break
                parent = str(context_by_id.get(parent, {}).get('parent_id') or '')
        if not owner and obj['category'] == 'Trajectories':
            names = {r.get('well_name') for r in obj['preview'] if r.get('well_name')}
            if len(names) == 1:
                owner = _ownership({'well_name': next(iter(names))}, wells)
        if owner:
            obj['parent'] = owner
        else:
            obj['notes'].append('Well ownership unresolved; shown separately')
    for key, obj in objects.items():
        if obj['category'] == 'Properties' and obj['parent'] not in objects:
            obj['notes'].append('Model relationship unavailable')
        priority = {'SEGY': 0, 'LAS': 0, 'ZMAP': 0, 'XYZ': 1, 'CSV': 2}
        obj['files'].sort(key=lambda file: (priority.get(file['label'].removeprefix('Download '), 9), file['_role']))
        unique = {}
        for file in obj['files']:
            fmt = file['label'].removeprefix('Download ')
            if fmt not in unique:
                unique[fmt] = {'label': file['label'], 'href': file['href']}
        obj['files'] = list(unique.values())
        if obj.get('tops_geometry_only'):
            for file in obj['files']:
                file['label'] = 'Download recovered XYZ points (CSV)' if file['label'] == 'Download CSV' else file['label']
        if key in attempted:
            obj['status'] = 'exported' if obj['files'] else 'unavailable'
            if not obj['files']:
                obj['notes'].append('No accepted, accessible data file is available')
        if obj['parent'] == key or obj['parent'] not in objects:
            obj['parent'] = None
    if package:
        recovery = _read_json(package/'07_workflows_reports/native_recovery/native_recovery_report.json', warnings, 'Grid preview receipt')
        image_budget = {'bytes': 0}
        for record in _rows(recovery.get('objects', [])):
            if not isinstance(record, dict):
                continue
            obj = objects.get(str(record.get('object_id')))
            if obj and obj['category'] == 'Surfaces / Grids' and obj['files']:
                obj['grid_preview'] = _grid_image(package, record, image_budget)
    native_tops = _attach_native_tops(package, destination, index, objects, warnings, report)
    top_recovery = None if native_tops else _attach_verified_tops(report, stem, objects, context, warnings)
    if native_tops:
        for raw in _rows(context.get('objects', [])):
            key = str(raw.get('object_id'))
            if raw.get('category') in ('WellTopsWellSubject','WellTopsSurfaceSubject','WellTopsZoneSubject') and key in objects and not objects[key]['files']:
                objects.pop(key)
    # Avoid cycles in corrupt native ancestry.
    for key, obj in objects.items():
        seen = {key}; parent = obj['parent']
        while parent:
            if parent in seen:
                obj['parent'] = None; warnings.append('Cyclic metadata relationship ignored'); break
            seen.add(parent); parent = objects[parent]['parent']
    children = {key: [] for key in objects}
    roots = {}
    for key, obj in objects.items():
        if obj['parent']:
            children[obj['parent']].append(key)
        else:
            roots.setdefault(obj['category'], []).append(key)
    def node(key):
        obj = objects[key]
        return {'id': key, 'name': obj['name'], 'kind': obj['category'], 'status': obj['status'],
                'unit': obj['unit'], 'files': obj['files'], 'preview': obj['preview'],
                'header': obj.get('header'),
                'grid_preview': obj.get('grid_preview'),
                'polygon_preview': obj.get('polygon_preview'),
                'tops_geometry_only': obj.get('tops_geometry_only', False),
                'top_picks': obj.get('top_picks', []),
                'notes': obj['notes'], 'children': [node(k) for k in sorted(children[key], key=lambda x: objects[x]['name'].casefold())]}
    category_order = {name:i for i,name in enumerate(('Wells','Well logs','Trajectories','Well tops','Seismic','Surfaces / Grids','Horizons / Interpretations','Polygons / Faults','Models','Properties','Points'))}
    categories = [{'id': 'category:' + category, 'name': category,
                   'children': [node(k) for k in sorted(keys, key=lambda x: objects[x]['name'].casefold())]}
                  for category, keys in sorted(roots.items(),key=lambda item:(category_order.get(item[0],99),item[0]))]
    detail = None
    if detailed_report:
        candidate = Path(detailed_report).absolute()
        if candidate.is_file():
            detail = _href(candidate, report)
    project_name = str(label or context.get('project_name') or (package.stem if package else report.stem.replace('_SUMMARY', '')))
    counts = {category: sum(obj['category'] == category for obj in objects.values()) for category in sorted({o['category'] for o in objects.values()},key=lambda name:(category_order.get(name,99),name))}
    data = {'name': project_name, 'status': status, 'categories': categories,
            'counts': counts, 'warnings': list(dict.fromkeys(warnings))[:100], 'detail': detail}
    if package:
        spatial = _read_json(package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json', warnings, 'Well-top recovery receipt')
        if 'well_top_validation' in spatial:
            data['well_top_status'] = {'validated_rows': spatial.get('validated_native_well_top_rows'),
                                      'validation': spatial['well_top_validation'],
                                      'recovered_xyz_points': sum(o.get('point_count',0) for o in spatial.get('objects',[])
                                          if isinstance(o,dict) and str(o.get('object_id')) in objects
                                          and objects[str(o['object_id'])]['category']=='Well tops'
                                          and o.get('blob_type')=='Points3')}
    if top_recovery:
        data.setdefault('well_top_status',{})['post_run_recovery']=top_recovery
    if native_tops:
        data.setdefault('well_top_status',{})['native_binding']=native_tops
    template = Path(__file__).with_name('geoviewer_summary.html').read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    rendered = template.replace('/*__DATA__*/', payload)
    handle = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=report.parent,
                                         prefix='.summary-', suffix='.tmp', delete=False) as stream:
            handle = Path(stream.name)
            stream.write(rendered)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(handle, report)
    finally:
        if handle and handle.exists():
            handle.unlink()
    return {'report': str(report), 'report_path': str(report), 'status': status, 'counts': counts,
            'warnings': data['warnings']}


def _preview_well_headers(path):
    try:
        with path.open('rb') as stream:
            prefix = stream.read(256 * 1024).decode('utf-8-sig', errors='ignore')
        lines = prefix.splitlines(keepends=True)
        if lines and not lines[-1].endswith(('\n', '\r')):
            lines.pop()
        allowed = {'object_id', 'well_id', 'well_name', 'x', 'y', 'z', 'kb', 'kb_elevation',
                   'td', 'total_depth', 'z_coordinate_status', 'crs_status',
                   'horizontal_units_status', 'vertical_reference_status'}
        return [{k.lower(): str(v)[:240] for k, v in row.items() if k and k.lower() in allowed and v is not None}
                for row in itertools.islice(csv.DictReader(lines), 2048)]
    except (OSError, UnicodeError, csv.Error):
        return []
