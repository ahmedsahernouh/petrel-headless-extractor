# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Experimental reservoir structural IR; never a qualified grid/RESCUE exporter.

Preserves complete typed records and array bytes without assigning ambiguous
mask, corner, unit or ACTNUM semantics. No Petrel, Ocean, pickle or native writes.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

import numpy as np
import petrel_native_binary as binary
from petrel_native_recovery import scalar_array

Error = binary.NativeError
SCHEMA = 'petrel.reservoir.structural-ir/1'
MAX_VALUES = 32_000_000
MAX_BUNDLE_BYTES = 256 * 1024**2
GRID_VERSION = [12, 0, 0, 0, 0, 2, 0, 1, 1]
PROPERTY_VERSIONS = {'FloatProperty': [1, 0, 1, 2, 0, 1],
                     'IntProperty': [2, 0, 1, 2, 0, 1]}


def fields(node, expected):
    names = [c.name for c in node.children]
    if len(names) != len(set(names)) or set(names) != set(expected):
        raise Error(f'{node.name}: unsupported/duplicate structural fields: {names}')


def dimensions(node, rank=3):
    values = scalar_array(node, 'int', rank)
    if values.dtype.kind not in 'iu' or any(int(v) <= 0 for v in values):
        raise Error('Invalid reservoir dimensions')
    result = tuple(int(v) for v in values)
    if math.prod(result) > MAX_VALUES:
        raise Error('Reservoir dimensions exceed structural bound')
    return result


def mask_summary(node):
    fields(node, {'bool_count', 'bitmask'})
    count, raw = node.get('bool_count'), node.get('bitmask')
    if type(count) is not int or not 0 <= count <= MAX_VALUES or not isinstance(raw, bytes):
        raise Error('Invalid packed reservoir mask')
    if len(raw) != (count + 7) // 8:
        raise Error('Packed mask byte length mismatch')
    candidates = {}
    for order in ('little', 'big'):
        bits = np.unpackbits(np.frombuffer(raw, dtype='u1'), bitorder=order)
        candidates[order] = dict(population=int(bits[:count].sum()),
                                 padding_population=int(bits[count:].sum()))
    return dict(declared_bits=count, bytes=len(raw), candidates=candidates,
                bit_order='unresolved', semantics='serialized definition only; not ACTNUM')


def sequence_summary(node, nk):
    if node.attrs.get('Version') != 3:
        raise Error('Unsupported vertical sequence profile')
    fields(node, {'max_truncated', 'min_truncated', 'zvalues', 'has_bools'})
    if node.get('has_bools') is not False:
        raise Error('Vertical sequence boolean extension not yet profiled')
    z = scalar_array(node.child('zvalues'), 'float', nk)
    if not 0 < len(z) <= MAX_VALUES or z.dtype != np.dtype('float32') or not np.isfinite(z).all():
        raise Error('Unsupported/nonfinite vertical sequence')
    return len(z)


def inspect_grid(node):
    if node.attrs.get('Type') != 'PillarGrid2' or node.attrs.get('Version') != GRID_VERSION:
        raise Error('Pillar grid outside observed structural profile')
    connections = node.child('connections', required=False)
    has_connections = node.get('has_connections', None)
    if has_connections is not None and type(has_connections) is not bool:
        raise Error('Invalid has_connections declaration')
    if connections is None and has_connections is not False:
        raise Error('Missing connections without explicit false declaration')
    if connections is not None and has_connections is False:
        raise Error('Connections present despite explicit false declaration')
    size = dimensions(node.child('node_size'), rank=2)
    pillars = node.child('pillars').children
    definitions = mask_summary(node.child('node_defs'))
    if definitions['declared_bits'] != size[0] * size[1]:
        raise Error('Horizontal pillar definition count mismatch')
    if not any(x['population'] == len(pillars) for x in definitions['candidates'].values()):
        raise Error('Pillar record count does not match either mask candidate')
    profiles = {}; geometries = {}; sequence_counts = {}; vertical_lengths = {}; total_z = 0
    opaque_negative_selectors = 0
    base = {'degree', 'old_value', 'is_truncated', 'geometry'}
    variants = [base | {'sequence'}, base | {'sequence', 'fault_tag'},
                base | {'unique_indices', 'sequences', 'has_is_faulted', 'faulted_state', 'fault_tags'}]
    for pillar in pillars:
        if pillar.name != 'pillar' or pillar.attrs.get('Version') != 7:
            raise Error('Unsupported pillar record')
        names = frozenset(c.name for c in pillar.children)
        if names not in variants:
            raise Error('Unknown pillar field variant')
        fields(pillar, names)
        signature = ','.join(sorted(names)); profiles[signature] = profiles.get(signature, 0) + 1
        geom = pillar.child('geometry'); geom_fields = {c.name for c in geom.children}
        if geom.attrs.get('Version') != 1:
            raise Error('Unsupported pillar geometry version')
        if geom_fields == {'min', 'max'} and pillar.get('degree') == 1:
            fields(geom, geom_fields)
            for name in ('min', 'max'):
                if not np.isfinite(scalar_array(geom.child(name), 'double', 3)).all():
                    raise Error('Nonfinite pillar line geometry')
        elif geom_fields == {'position', 'zmin', 'zmax'} and pillar.get('degree') == 0:
            fields(geom, geom_fields)
            if not np.isfinite(scalar_array(geom.child('position'), 'double', 2)).all():
                raise Error('Nonfinite pillar point geometry')
            if not all(type(geom.get(k)) in (int, float) and math.isfinite(geom.get(k))
                       for k in ('zmin', 'zmax')):
                raise Error('Invalid pillar Z range')
        else:
            raise Error('Unknown degree/geometry field combination')
        label = ','.join(sorted(geom_fields)); geometries[label] = geometries.get(label, 0) + 1
        if 'sequence' in names:
            seqs = [pillar.child('sequence')]
        else:
            seqs = pillar.child('sequences').children
            if not seqs or any(s.name != 'sequence' for s in seqs):
                raise Error('Empty/unsupported sequence collection')
            indices = scalar_array(pillar.child('unique_indices'), 'index', 4)
            if indices.dtype.kind not in 'iu' or np.any(indices < -1) or np.any(indices >= len(seqs)):
                raise Error('Per-corner sequence selector out of range')
            # -1 occurs in the pinned source. Preserve it as an opaque marker;
            # do not assign a sequence, remove a corner or infer inactivity.
            opaque_negative_selectors += int(np.count_nonzero(indices == -1))
            flags = scalar_array(pillar.child('has_is_faulted'), 'bool', 4)
            if not np.isin(flags, [0, 1]).all():
                raise Error('Invalid per-corner fault flags')
            states = pillar.child('faulted_state').children
            if len(states) != int(flags.sum()):
                raise Error('Fault state/flag count mismatch')
            sequence_lengths = {len(scalar_array(s.child('zvalues'), 'float')) for s in seqs}
            for state in states:
                if mask_summary(state)['declared_bits'] not in sequence_lengths:
                    raise Error('Fault state vertical length mismatch')
        sequence_counts[str(len(seqs))] = sequence_counts.get(str(len(seqs)), 0) + 1
        for sequence in seqs:
            length = sequence_summary(sequence, None)
            vertical_lengths[str(length)] = vertical_lengths.get(str(length), 0) + 1
            total_z += length
            if total_z > MAX_VALUES: raise Error('Grid Z arrays exceed structural bound')
    return dict(kind='PillarGrid2', horizontal_node_size=list(size), pillar_records=len(pillars),
                pillar_profiles=profiles, geometry_profiles=geometries,
                sequences_per_pillar=sequence_counts, vertical_sequence_lengths=vertical_lengths, z_values=total_z,
                opaque_minus_one_corner_selectors=opaque_negative_selectors,
                node_definition_mask=definitions,
                connection_mask=mask_summary(connections) if connections is not None else None,
                has_connections=has_connections,
                has_actnum_grid=node.get('has_actnum_grid'),
                qualification='structural_only', geometry_indexing='unresolved',
                complete_model_export=False)


def inspect_property(node):
    kind = node.attrs.get('Type')
    if kind not in PROPERTY_VERSIONS or node.attrs.get('Version') != PROPERTY_VERSIONS[kind]:
        raise Error('Property outside observed structural profile')
    fields(node, {'cell_size', 'temp', 'has_def_grid', 'def_grid', 'values'})
    if node.get('has_def_grid') is not True:
        raise Error('Property without explicit definition grid not profiled')
    size = dimensions(node.child('cell_size'))
    definition = mask_summary(node.child('def_grid').child('cell_defs'))
    if definition['declared_bits'] != math.prod(size):
        raise Error('Property lattice/definition count mismatch')
    values = node.child('values'); vector = values.attrs.get('Type', 'float32_pages')
    if vector not in ('float32_pages', 'DiscreteByteVector', 'DiscreteUShortVector'):
        raise Error('Unknown property page representation')
    if kind == 'FloatProperty' and vector != 'float32_pages':
        raise Error('Unexpected continuous-property vector')
    if kind == 'IntProperty' and vector == 'float32_pages':
        raise Error('Unexpected discrete-property vector')
    if vector != 'float32_pages': values = values.child('getData')
    fields(values, {'no_elements', 'pages'})
    declared = values.get('no_elements'); pages = values.child('pages')
    if type(declared) is not int or not 0 <= declared <= MAX_VALUES:
        raise Error('Invalid property value count')
    if pages.attrs.get('Size') != len(pages.children):
        raise Error('Property page count mismatch')
    count = 0; dtypes = {}
    for page in pages.children:
        if page.name != 'page': raise Error('Unknown property page')
        if vector == 'DiscreteByteVector':
            raw = page.scalar()
            if page.children or not isinstance(raw, bytes): raise Error('Invalid byte-vector page')
            count += len(raw); label = 'serialized_bytes'
        else:
            expected = 'float' if vector == 'float32_pages' else 'short'
            fields(page, {expected}); array = page.children[0]
            if not isinstance(array, binary.Array): raise Error('Property page must be typed array')
            want = np.dtype('float32' if expected == 'float' else 'int16')
            if array.values.dtype != want: raise Error('Unexpected property storage dtype')
            count += len(array.values); label = str(want)
        dtypes[label] = dtypes.get(label, 0) + 1
    if count != declared or not any(x['population'] == count for x in definition['candidates'].values()):
        raise Error('Property page/definition-mask population mismatch')
    return dict(kind=kind, cell_size=list(size), stored_values=count, page_count=len(pages.children),
                storage_representation=vector, page_dtypes=dtypes, definition_mask=definition,
                qualification='structural_only', logical_mapping='unresolved',
                category_and_null_semantics='unresolved', complete_model_export=False)


def inspect(node):
    if node.name in ('PillarGridSubject', 'KeyPillarsSubject', 'PropertySubject'):
        # Opaque metadata preservation is intentionally not a decoded grid profile.
        return dict(kind=node.name, record_version=node.attrs.get('Version'),
                    qualification='opaque_model_metadata', object_id=node.get('unique_tag'),
                    complete_model_export=False)
    kind = node.attrs.get('Type')
    if kind == 'PillarGrid2': return inspect_grid(node)
    if kind in PROPERTY_VERSIONS: return inspect_property(node)
    if kind == 'KeyPillars' and node.attrs.get('Version') == [0, 2, 0, 1, 1]:
        fields(node, {'user_data', 'faults', 'boundaries'})
        for name in ('faults', 'boundaries'):
            collection = node.child(name)
            if collection.attrs.get('Size') != len(collection.children):
                raise Error('Key-pillar collection count mismatch')
        return dict(kind=kind, fault_records=len(node.child('faults').children),
                    boundary_records=len(node.child('boundaries').children),
                    qualification='structural_only', topology='unresolved', complete_model_export=False)
    raise Error('Unsupported reservoir structure object type')


def _encode(value, put):
    if isinstance(value, np.generic): value = value.item()
    if isinstance(value, bytes): return {'$bytes': put(value, 'bin')}
    if isinstance(value, float) and not math.isfinite(value): return {'$float': repr(value)}
    if value is None or type(value) in (str, bool, int, float): return value
    if isinstance(value, list): return [_encode(v, put) for v in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) or k.startswith('$') for k in value):
            raise Error('Unsupported typed-record dictionary key')
        return {k: _encode(v, put) for k, v in value.items()}
    raise Error('Unsupported scalar content in structural record')


def _tree(node, put):
    if isinstance(node, binary.Array):
        stream = io.BytesIO(); np.save(stream, node.values, allow_pickle=False)
        return dict(name=node.name, array=put(stream.getvalue(), 'npy'))
    return dict(name=node.name, attrs=_encode(node.attrs, put),
                content=_encode(node.content, put), children=[_tree(c, put) for c in node.children])


def write_bundle(node, path, provenance):
    """Exclusive creation; full byte verification on readback, no semantic promotion."""
    summary = inspect(node); parts = {}; total = 0
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        def put(raw, suffix):
            nonlocal total
            total += len(raw)
            if total > MAX_BUNDLE_BYTES: raise Error('Structural bundle exceeds bound')
            key = f'parts/{len(parts):07d}.{suffix}'
            parts[key] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            archive.writestr(key, raw)
            return key
        tree = _tree(node, put)
        manifest = dict(schema=SCHEMA, summary=summary, provenance=provenance, tree=tree, parts=parts,
                        complete_model_export=False, rescue_file=False)
        raw = json.dumps(manifest, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(raw) + total > MAX_BUNDLE_BYTES: raise Error('Structural manifest exceeds bound')
        archive.writestr('manifest.json', raw)
    restored, receipt = read_bundle(path)
    if structural_digest(node) != structural_digest(restored):
        raise Error('Structural IR readback differs from complete source record')
    return dict(summary=summary, exact_typed_readback=True, parts=len(parts),
                preserved_bytes=total, manifest_sha256=receipt['manifest_sha256'])


def structural_digest(node):
    def fingerprint(raw, suffix):
        return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), encoding=suffix)
    raw = json.dumps(_tree(node, fingerprint), sort_keys=True, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def read_bundle(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist(); names = [x.filename for x in infos]
        if len(names) != len(set(names)) or sum(x.file_size for x in infos) > MAX_BUNDLE_BYTES:
            raise Error('Duplicate entries or excessive expanded structural bundle')
        if 'manifest.json' not in names: raise Error('Missing structural manifest')
        raw = archive.read('manifest.json'); manifest = json.loads(raw)
        if (manifest.get('schema') != SCHEMA or manifest.get('complete_model_export') is not False
                or manifest.get('rescue_file') is not False):
            raise Error('Invalid structural manifest qualification')
        parts = manifest['parts']
        if set(names) != set(parts) | {'manifest.json'}:
            raise Error('Unexpected or missing structural bundle members')
        cache = {}; used = set()
        def get(key):
            if key not in parts or not key.startswith('parts/') or '..' in key.split('/'):
                raise Error('Invalid structural part reference')
            used.add(key)
            if key not in cache:
                value = archive.read(key); meta = parts[key]
                if len(value) != meta['bytes'] or hashlib.sha256(value).hexdigest() != meta['sha256']:
                    raise Error('Structural part hash/length mismatch')
                cache[key] = value
            return cache[key]
        def decode(v):
            if isinstance(v, list): return [decode(x) for x in v]
            if isinstance(v, dict):
                if set(v) == {'$bytes'}: return get(v['$bytes'])
                if set(v) == {'$float'}:
                    if v['$float'] not in ('nan', 'inf', '-inf'): raise Error('Invalid special float')
                    return float(v['$float'])
                return {k: decode(x) for k, x in v.items()}
            return v
        def restore(t):
            if 'array' in t:
                data = get(t['array']); stream = io.BytesIO(data)
                version = np.lib.format.read_magic(stream)
                if version == (1, 0): header = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0): header = np.lib.format.read_array_header_2_0(stream)
                else: raise Error('Unsupported structural array encoding')
                shape, _, dtype = header
                if dtype.hasobject or len(shape) > 4 or math.prod(shape) > MAX_VALUES:
                    raise Error('Unsafe/excessive structural array header')
                if math.prod(shape) * dtype.itemsize != len(data) - stream.tell():
                    raise Error('Structural array header/payload mismatch')
                return binary.Array(t['name'], np.load(io.BytesIO(data), allow_pickle=False))
            return binary.Node(t['name'], attrs=decode(t['attrs']), content=decode(t['content']),
                               children=[restore(c) for c in t['children']])
        node = restore(manifest['tree'])
        if used != set(parts): raise Error('Unreferenced structural parts')
        if inspect(node) != manifest['summary']: raise Error('Structural summary mismatch')
        return node, dict(manifest_sha256=hashlib.sha256(raw).hexdigest(), manifest=manifest)
