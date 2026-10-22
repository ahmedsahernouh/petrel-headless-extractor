"""Bounded KeyPillars control geometry from a verified structural IR bundle.

Tier: zero-GUI file processing. This does not infer fault topology, surfaces,
units, coordinates, or an independently received model.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

import petrel_reservoir_structure
import petrel_native_binary
from petrel_reservoir_structure import read_bundle
from petrel_native_binary import Array, Node


SCHEMA = 'fieldviewer.keypillar-control-geometry/1'
IDENTITY_SCHEMA = 'fieldviewer.keypillar-identities/1'
FAULT_FIELDS = {'user_data','directions','boundary_types','fake_fault','no_cells','tag','size',
                'direction','auto_assigned','pillar_types','pillars'}
PILLAR_FIELDS = {'degree','old_value','is_truncated','geometry','unique_indices','sequences',
                 'has_is_faulted','faulted_state','index','fault_tags','user_translation',
                 'locked_point','is_side_towards_east'}


class ControlError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ControlError(message)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def unique_fields(node, expected):
    require(isinstance(node, Node) and not node.content and
            len(node.children) == len(expected) and
            {x.name for x in node.children} == expected,
            f'{getattr(node,"name", "record")}: unsupported fields or duplicates')


def scalar(node, name, kind):
    x = node.child(name)
    require(isinstance(x, Node) and not x.attrs and not x.children and len(x.content) == 1,
            f'{name}: expected one scalar')
    v = x.content[0]
    require(type(v) is kind, f'{name}: invalid scalar type')
    if kind is float:
        require(math.isfinite(v), f'{name}: nonfinite scalar')
    return v


def number(node, name):
    x = node.child(name)
    require(isinstance(x, Node) and not x.attrs and not x.children and len(x.content) == 1,
            f'{name}: expected one numeric scalar')
    v = x.content[0]
    require(type(v) in (int, float) and math.isfinite(v), f'{name}: invalid number')
    return v


def vector(node, name, length):
    x = node.child(name)
    require(isinstance(x, Node) and not x.attrs and not x.content and len(x.children) == 1,
            f'{name}: invalid vector wrapper')
    a = x.children[0]
    require(isinstance(a, Array) and a.name == 'double' and a.values.dtype == np.dtype('float64')
            and a.values.shape == (length,) and np.isfinite(a.values).all(),
            f'{name}: expected finite float64[{length}]')
    return a.values.copy()


def typed_vector(node, name, item, dtype, size=None):
    x = node.child(name)
    require(isinstance(x, Node) and x.attrs == {'Size': -1} and not x.content,
            f'{name}: invalid vector profile')
    if not x.children:
        return []
    require(len(x.children) == 1 and isinstance(x.children[0], Array),
            f'{name}: invalid vector part count')
    a = x.children[0]
    require(a.name == item and a.values.dtype == np.dtype(dtype) and a.values.ndim == 1
            and (size is None or a.values.shape == (size,)), f'{name}: invalid vector storage')
    return a.values.tolist()


def metadata(p):
    # Metadata is carried for inspection; locked_point is never geometry.
    index = scalar(p, 'index', int)
    truncated = scalar(p, 'is_truncated', bool)
    require(truncated is False, 'truncated control unsupported')
    translation = vector(p, 'user_translation', 2)
    require(np.all(translation == 0), 'nonzero user translation unsupported')
    locked = vector(p, 'locked_point', 3)
    for empty_name in ('sequences','faulted_state'):
        empty = p.child(empty_name)
        require(empty.attrs == {'Size':-1} and not empty.content and not empty.children,
                f'{empty_name}: unsupported nonempty profile')
    fault_tags = p.child('fault_tags')
    require(fault_tags.attrs == {'Size':-1} and not fault_tags.content and
            len(fault_tags.children) == 4 and all(x.name == 'tag' for x in fault_tags.children),
            'fault_tags: unsupported profile')
    raw_tags = []
    for tag_node in fault_tags.children:
        require(not tag_node.attrs and not tag_node.children and len(tag_node.content) == 1 and
                type(tag_node.content[0]) is int, 'fault_tags: invalid tag scalar')
        raw_tags.append(tag_node.content[0])
    flags = {'is_truncated': truncated,
             'is_side_towards_east': scalar(p, 'is_side_towards_east', bool)}
    return {'index': index, 'flags': flags, 'user_translation': translation.tolist(),
            'old_value': number(p,'old_value'), 'fault_tags': raw_tags,
            'sequences_declared_size':-1,'faulted_state_declared_size':-1,
            'locked_point_metadata': locked.tolist(),
            'unique_indices': typed_vector(p, 'unique_indices', 'index', 'int32',4),
            'has_is_faulted': typed_vector(p, 'has_is_faulted', 'bool', 'uint8',4)}


def decode_controls(node, identities):
    """Pure decode of observed native controls; returns records and float64 arrays."""
    require(isinstance(node, Node) and node.name == 'data' and
            node.attrs.get('Type') == 'KeyPillars' and
            node.attrs.get('Version') == [0,2,0,1,1], 'unsupported KeyPillars root')
    unique_fields(node, {'user_data','faults','boundaries'})
    faults = node.child('faults')
    require(faults.attrs.get('Size') == len(faults.children), 'fault count mismatch')
    require(len(identities['definitions']) == len(faults.children), 'identity definition count mismatch')
    output = []
    tags = set()
    for ordinal, (fault, definition) in enumerate(zip(faults.children, identities['definitions'])):
        require(fault.name == 'item' and fault.attrs.get('Type') == 'PillarFault' and
                fault.attrs.get('Version') == [3,2,0,1,1], f'fault {ordinal}: unsupported profile')
        unique_fields(fault, FAULT_FIELDS)
        tag = scalar(fault, 'tag', int)
        require(tag >= 0 and tag not in tags and definition['tag'] == tag,
                f'fault {ordinal}: tag/identity mismatch or duplicate')
        tags.add(tag)
        count = scalar(fault, 'size', int)
        pillars = fault.child('pillars')
        require(pillars.attrs == {'Size': -1} and not pillars.content and
                len(pillars.children) == count and count >= 0, f'fault {tag}: pillar count mismatch')
        controls = []
        endpoints = np.empty((count, 2, 3), dtype=np.float64)
        source_scalars = 0
        for i,p in enumerate(pillars.children):
            require(p.name == 'pillar' and p.attrs == {'Version': 7},
                    f'fault {tag} control {i}: unsupported pillar')
            unique_fields(p, PILLAR_FIELDS)
            degree = scalar(p, 'degree', int)
            require(degree in (0,1), f'fault {tag} control {i}: unsupported degree')
            geom = p.child('geometry')
            require(geom.attrs == {'Version': 1}, 'unsupported geometry version')
            if degree == 1:
                unique_fields(geom, {'min','max'})
                lo = vector(geom, 'min', 3); hi = vector(geom, 'max', 3)
                raw = {'min': lo.tolist(), 'max': hi.tolist()}
                endpoints[i] = [lo, hi]
                source_scalars += 6
                construction = 'native_min_max_vectors'
            else:
                unique_fields(geom, {'position','zmin','zmax'})
                xy = vector(geom, 'position', 2)
                zmin = number(geom, 'zmin'); zmax = number(geom, 'zmax')
                raw = {'position': xy.tolist(), 'zmin': zmin, 'zmax': zmax}
                endpoints[i] = [[xy[0],xy[1],zmin],[xy[0],xy[1],zmax]]
                source_scalars += 4
                construction = 'visualization_from_native_xy_z_bounds'
            controls.append({'ordinal': i, 'degree': degree, 'construction': construction,
                             'raw_geometry': raw, **metadata(p)})
        output.append({'ordinal': ordinal, 'tag': tag, 'subject_uuid': definition['subject_uuid'],
                       'source_name': definition['source_name'],
                       'grid_reference_occurrences': definition['grid_reference_occurrences'],
                       'declared_size': count, 'control_count': count,
                       'source_parameter_scalars': source_scalars,
                       'expanded_endpoint_scalars': 6*count,
                       'controls': controls, 'endpoints': endpoints})
    return output


def _uuid(v):
    require(type(v) is str, 'UUID must be string')
    try: canonical = str(uuid.UUID(v))
    except ValueError as e: raise ControlError('invalid UUID') from e
    require(v == canonical, 'UUID must be canonical')


def validate_identities(d):
    require(type(d) is dict and set(d) == {'schema','keypillars_object_id',
            'keypillars_bundle_sha256','native_source_project','native_source_hashes',
            'qualification','grid_fault_model_association',
            'direct_grid_to_keypillars_uuid_link_established','definitions','evidence'},
            'invalid identity schema fields')
    require(d['schema'] == IDENTITY_SCHEMA and d['qualification'] == 'source_identity_links_only',
            'invalid identity qualification')
    _uuid(d['keypillars_object_id'])
    require(type(d['keypillars_bundle_sha256']) is str and
            re.fullmatch('[0-9a-f]{64}', d['keypillars_bundle_sha256']), 'invalid bundle hash')
    source = d['native_source_project']
    require(type(source) is str and Path(source).is_absolute() and source.lower().endswith('.pet'),
            'invalid native source project path')
    require(type(d['native_source_hashes']) is dict and
            set(d['native_source_hashes']) == {'project','model','data'} and
            all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v)
                for v in d['native_source_hashes'].values()), 'invalid native source hashes')
    require(d['grid_fault_model_association'] == 'single_same_model_context_numeric_identity_match'
            and d['direct_grid_to_keypillars_uuid_link_established'] is False,
            'unsupported grid association qualification')
    require(type(d['evidence']) is list and all(type(x) is dict and
            set(x) == {'path','sha256'} and type(x['path']) is str and
            type(x['sha256']) is str and re.fullmatch('[0-9a-f]{64}',x['sha256'])
            for x in d['evidence']), 'invalid identity evidence list')
    require(type(d['definitions']) is list, 'definitions must be list')
    ids = set(); tags = set()
    for x in d['definitions']:
        require(type(x) is dict and set(x) == {'tag','subject_uuid','source_name',
                'grid_reference_occurrences'}, 'invalid definition fields')
        require(type(x['tag']) is int and x['tag'] >= 0 and x['tag'] not in tags,
                'invalid or duplicate tag')
        _uuid(x['subject_uuid'])
        require(x['subject_uuid'] not in ids, 'duplicate subject UUID')
        require(type(x['source_name']) is str and '\x00' not in x['source_name'],
                'invalid source name')
        require(type(x['grid_reference_occurrences']) is int and
                x['grid_reference_occurrences'] >= 0, 'invalid reference count')
        tags.add(x['tag']); ids.add(x['subject_uuid'])


def _safe_path(path, allow_missing=False, allow_relative=False):
    p = Path(path)
    require((p.is_absolute() or allow_relative) and '..' not in p.parts and '.' not in p.parts,
            'path must be normalized')
    if not p.is_absolute(): p = Path.cwd()/p
    q = p
    while True:
        require(not q.is_symlink(), 'symlink/reparse path rejected')
        if os.name == 'nt' and q.exists():
            import ctypes
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(q))
            require(attrs == 0xFFFFFFFF or not attrs & 0x400, 'reparse path rejected')
        if q == q.parent: break
        q = q.parent
    if not allow_missing: require(p.is_file(), 'input file missing')
    return p.resolve(strict=False)


def _xml_lines(points):
    n = len(points)
    root = ET.Element('VTKFile', {'type':'PolyData','version':'0.1','byte_order':'LittleEndian'})
    poly = ET.SubElement(root, 'PolyData')
    piece = ET.SubElement(poly, 'Piece', {'NumberOfPoints':str(2*n),'NumberOfVerts':'0',
        'NumberOfLines':str(n),'NumberOfStrips':'0','NumberOfPolys':'0'})
    pts = ET.SubElement(piece,'Points')
    ET.SubElement(pts,'DataArray',{'type':'Float64','NumberOfComponents':'3','format':'ascii'}).text = '\n' + '\n'.join(' '.join(format(float(v),'.17g') for v in row) for row in points.reshape(-1,3)) + '\n'
    lines = ET.SubElement(piece,'Lines')
    ET.SubElement(lines,'DataArray',{'type':'Int64','Name':'connectivity','format':'ascii'}).text = ' '.join(str(i) for i in range(2*n))
    ET.SubElement(lines,'DataArray',{'type':'Int64','Name':'offsets','format':'ascii'}).text = ' '.join(str(2*(i+1)) for i in range(n))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _readback_vtp(path, expected):
    root = ET.parse(path).getroot()
    require(root.attrib.get('type') == 'PolyData' and not list(root.iter('Polys')),
            'invalid VTP geometry')
    piece = root.find('./PolyData/Piece')
    require(int(piece.attrib['NumberOfLines']) == len(expected) and
            int(piece.attrib['NumberOfPoints']) == 2*len(expected), 'VTP count mismatch')
    a = piece.find('./Points/DataArray')
    vals = np.array([float(v) for v in a.text.split()], dtype=np.float64)
    require(vals.size == expected.size and np.array_equal(vals.view(np.uint64),
            expected.reshape(-1).view(np.uint64)), 'VTP numeric bits differ')


def export_controls(bundle, identity_file, output):
    bundle = _safe_path(bundle,allow_relative=True)
    identity_file = _safe_path(identity_file,allow_relative=True)
    out = _safe_path(output, allow_missing=True)
    require(not out.exists(), 'output must be new')
    d = json.loads(identity_file.read_text(encoding='utf-8'))
    validate_identities(d)
    source = _safe_path(d['native_source_project'], allow_missing=True)
    forbidden = [source.parent, bundle.parent, identity_file.parent]
    require(all(out != x and x not in out.parents for x in forbidden),
            'output inside source or input parent')
    require(out.parent.is_dir(), 'output parent missing')
    bh, ih = sha(bundle), sha(identity_file)
    decoder_files = {'petrel_fault_controls.py':Path(__file__),
                     'petrel_reservoir_structure.py':Path(petrel_reservoir_structure.__file__),
                     'petrel_native_binary.py':Path(petrel_native_binary.__file__)}
    decoder_before = {name:sha(path) for name,path in decoder_files.items()}
    def verify_unchanged():
        require(sha(bundle) == bh and sha(identity_file) == ih, 'input changed during export')
        require({name:sha(path) for name,path in decoder_files.items()} == decoder_before,
                'decoder source changed during export')
        require(_safe_path(out,allow_missing=True) == out, 'output path changed during export')
    require(bh == d['keypillars_bundle_sha256'], 'bundle hash differs from identity')
    node, receipt = read_bundle(bundle)
    require(receipt['manifest']['provenance'].get('object_id') == d['keypillars_object_id'],
            'bundle provenance object ID mismatch')
    records = decode_controls(node,d)
    verify_unchanged()
    out.mkdir()
    try:
        blocks = ET.Element('VTKFile',{'type':'vtkMultiBlockDataSet','version':'1.0','byte_order':'LittleEndian'})
        mb = ET.SubElement(blocks,'vtkMultiBlockDataSet')
        manifest = {'schema':SCHEMA,'status':'verified_control_geometry','qualification':'native_control_geometry_only',
                    'source':{'bundle_sha256':bh,'identity_sha256':ih,
                              'decoder_source_sha256_before':decoder_before,
                              'bundle_manifest_sha256':receipt['manifest_sha256'],
                              'keypillars_object_id':d['keypillars_object_id'],
                              'native_source_project':d['native_source_project'],
                              'native_source_hashes':d['native_source_hashes'],
                              'identity_qualification':d['qualification']},
                    'definition_count':len(records),'boundary_record_count':len(node.child('boundaries').children),
                    'boundaries_exported':False,
                    'control_count':sum(x['control_count'] for x in records),
                    'source_parameter_scalars':sum(x['source_parameter_scalars'] for x in records),
                    'expanded_endpoint_scalars':sum(x['expanded_endpoint_scalars'] for x in records),
                    'all_fault_topology':False,'scientific_units_validated':False,
                    'independent_receiver_validated':False,'complete_model_export':False,
                    'definitions':[],'file_index_scope':'data_and_multiblock_assets_excludes_report_self',
                    'files':{}}
        for r in records:
            name = f"definition_{r['ordinal']:04d}_tag_{r['tag']}"
            points = r.pop('endpoints')
            if len(points):
                npy, vtp = name+'.endpoints.npy',name+'.vtp'
                np.save(out/npy,points,allow_pickle=False)
                (out/vtp).write_bytes(_xml_lines(points))
                read = np.load(out/npy,allow_pickle=False)
                require(read.dtype == np.float64 and read.shape == points.shape and
                        np.array_equal(read.view(np.uint64),points.view(np.uint64)), 'NPY readback differs')
                _readback_vtp(out/vtp,points)
                ET.SubElement(mb,'DataSet',{'index':str(r['ordinal']),
                    'name':r['source_name'],'file':vtp})
                r['assets']={'endpoints_npy':npy,'vtp_lines':vtp}
            else:
                r['assets']={}; r['state']='empty_source_definition'
            manifest['definitions'].append(r)
        (out/'controls.vtm').write_bytes(ET.tostring(blocks,encoding='utf-8',xml_declaration=True))
        parsed = ET.parse(out/'controls.vtm').getroot()
        require(all(not Path(x.attrib['file']).is_absolute() for x in parsed.iter('DataSet')),
                'nonrelative multiblock asset')
        for p in sorted(out.iterdir()): manifest['files'][p.name]={'sha256':sha(p),'bytes':p.stat().st_size}
        verify_unchanged()
        manifest['source']['decoder_source_sha256_after'] = {name:sha(path) for name,path in decoder_files.items()}
        (out/'REPORT.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        verify_unchanged()
        return manifest
    except Exception as e:
        (out/'REPORT.json').unlink(missing_ok=True)
        (out/'FAILED.json').write_text(json.dumps({'schema':SCHEMA,'status':'failed',
            'error':str(e),'bundle_sha256_before':bh,'identity_sha256_before':ih,
            'decoder_source_sha256_before':decoder_before,
            'decoder_source_sha256_after':{name:sha(path) for name,path in decoder_files.items()}},indent=2),encoding='utf-8')
        raise


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle',required=True);ap.add_argument('--identities',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args(argv)
    try:
        result=export_controls(a.bundle,a.identities,a.output)
        print(json.dumps({'status':result['status'],'report':str(Path(a.output)/'REPORT.json'),
                          'definition_count':result['definition_count'],
                          'control_count':result['control_count']}))
        return 0
    except Exception as e:
        print(f'{type(e).__name__}: {e}',file=sys.stderr)
        print(json.dumps({'status':'failed','error':str(e)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
