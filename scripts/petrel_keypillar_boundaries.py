# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Export the bounded native KeyPillars BoundarySegment profile as lines.

Zero-GUI retained-IR processing. No Petrel/Ocean, original project reads,
coordinate conversion, surface generation or independent receiving acceptance.
"""
import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import petrel_fault_controls as controls
import petrel_reservoir_structure as structure
import petrel_native_binary as binary

SCHEMA = 'fieldviewer.keypillar-boundary-geometry/1'


def decode_boundaries(node):
    """Preserve native XYZ vertices/order and explicit closure; infer no surface."""
    need = controls.require
    need(isinstance(node, binary.Node) and node.name == 'data' and
         node.attrs.get('Type') == 'KeyPillars' and node.attrs.get('Version') == [0, 2, 0, 1, 1] and
         set(node.attrs).issubset({'Type', 'Version', 'xmlns', 'Id'}) and
         ('xmlns' not in node.attrs or node.attrs['xmlns'] == 'http://www.slb.com/Petrel/2011/03/Serialization') and
         ('Id' not in node.attrs or type(node.attrs['Id']) is int and node.attrs['Id'] >= 0),
         'unsupported KeyPillars root')
    controls.unique_fields(node, {'user_data', 'faults', 'boundaries'})
    collection = node.child('boundaries')
    need(isinstance(collection, binary.Node) and not collection.content and
         type(collection.attrs.get('Size')) is int and
         collection.attrs == {'Size': len(collection.children)}, 'boundary count/profile mismatch')
    records = []
    seen = set()
    for ordinal, item in enumerate(collection.children):
        need(isinstance(item, binary.Node) and item.name == 'item' and
             set(item.attrs) == {'Id', 'Type', 'Version'} and
             type(item.attrs['Id']) is int and item.attrs['Id'] >= 0 and
             item.attrs['Id'] not in seen and item.attrs['Type'] == 'BoundarySegment' and
             item.attrs['Version'] == [0, 0, 1, 2, 0, 1, 1], 'unsupported boundary profile or duplicate ID')
        seen.add(item.attrs['Id'])
        controls.unique_fields(item, {'user_data', 'vertices', 'has_attr', 'has_object_ids',
                                      'is_closed', 'min_type', 'max_type'})
        user = item.child('user_data')
        need(isinstance(user, binary.Node) and user.attrs == {'Size': 0} and
             not user.children and not user.content, 'boundary user data unsupported')
        has_attr = controls.scalar(item, 'has_attr', bool)
        has_ids = controls.scalar(item, 'has_object_ids', bool)
        closed = controls.scalar(item, 'is_closed', bool)
        min_type = controls.scalar(item, 'min_type', int)
        max_type = controls.scalar(item, 'max_type', int)
        need(has_attr is False and has_ids is False and min_type == max_type == 0,
             'boundary attribute/object-ID/endpoint-type variant unsupported')
        vertices = item.child('vertices')
        need(isinstance(vertices, binary.Node) and not vertices.content and
             set(vertices.attrs) == {'Size'} and type(vertices.attrs['Size']) is int and
             vertices.attrs['Size'] >= 2 and len(vertices.children) == 1,
             'invalid boundary vertex collection')
        array = vertices.children[0]
        count = vertices.attrs['Size']
        need(isinstance(array, binary.Array) and array.name == 'double' and
             array.values.dtype == np.dtype('float64') and
             array.values.shape == (3 * count,) and np.isfinite(array.values).all(),
             'expected finite flat float64 XYZ vertices')
        records.append(dict(ordinal=ordinal, native_id=item.attrs['Id'], native_type=item.attrs['Type'],
                            native_version=item.attrs['Version'], is_closed=closed,
                            has_attr=has_attr, has_object_ids=has_ids, min_type=min_type, max_type=max_type,
                            vertex_count=count, edge_count=count if closed else count-1,
                            native_xyz=array.values.reshape(count, 3).copy()))
    return records


def _vtp(points, closed):
    root = ET.Element('VTKFile', type='PolyData', version='0.1', byte_order='LittleEndian')
    poly = ET.SubElement(root, 'PolyData')
    piece = ET.SubElement(poly, 'Piece', NumberOfPoints=str(len(points)), NumberOfVerts='0',
                          NumberOfLines='1', NumberOfStrips='0', NumberOfPolys='0')
    pts = ET.SubElement(piece, 'Points')
    ET.SubElement(pts, 'DataArray', type='Float64', NumberOfComponents='3', format='ascii').text = ' '.join(
        format(float(v), '.17g') for v in points.reshape(-1))
    connectivity = list(range(len(points))) + ([0] if closed else [])
    lines = ET.SubElement(piece, 'Lines')
    ET.SubElement(lines, 'DataArray', type='Int64', Name='connectivity', format='ascii').text = ' '.join(map(str, connectivity))
    ET.SubElement(lines, 'DataArray', type='Int64', Name='offsets', format='ascii').text = str(len(connectivity))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _verify_vtp(path, expected, closed):
    controls.require(not list(ET.parse(path).getroot().iter('Polys')), 'unexpected surface output')
    piece = ET.parse(path).find('./PolyData/Piece')
    data = np.fromstring(piece.find('./Points/DataArray').text, sep=' ', dtype=np.float64)
    controls.require(data.shape == (expected.size,) and np.array_equal(data.view(np.uint64),
                     expected.reshape(-1).view(np.uint64)), 'VTP coordinate bits differ')
    parts = {x.attrib['Name']:np.fromstring(x.text, sep=' ', dtype=np.int64)
             for x in piece.findall('./Lines/DataArray')}
    indices = list(range(len(expected))) + ([0] if closed else [])
    controls.require(np.array_equal(parts['connectivity'], indices) and
                     np.array_equal(parts['offsets'], [len(indices)]), 'boundary line closure/order differs')


def export_boundaries(bundle, identity_file, output):
    """Read only a retained ZIP/identity receipt; create a new outside directory."""
    need = controls.require
    bundle = controls._safe_path(bundle, allow_relative=True)
    identity_file = controls._safe_path(identity_file, allow_relative=True)
    out = controls._safe_path(output, allow_missing=True)
    need(not out.exists() and out.parent.is_dir(), 'output must be new with existing parent')
    identities = json.loads(identity_file.read_text(encoding='utf-8'))
    controls.validate_identities(identities)
    source = controls._safe_path(identities['native_source_project'], allow_missing=True)
    need(all(out != p and p not in out.parents for p in (source.parent, bundle.parent, identity_file.parent)),
         'output inside native source or input directory')
    paths = {'bundle':bundle, 'identities':identity_file, 'petrel_keypillar_boundaries.py':Path(__file__),
             'petrel_fault_controls.py':Path(controls.__file__),
             'petrel_reservoir_structure.py':Path(structure.__file__),
             'petrel_native_binary.py':Path(binary.__file__)}
    before = {name:controls.sha(path) for name,path in paths.items()}
    need(before['bundle'] == identities['keypillars_bundle_sha256'], 'bundle identity hash mismatch')
    def unchanged():
        need({name:controls.sha(path) for name,path in paths.items()} == before, 'input or decoder changed')
        need(controls._safe_path(out, allow_missing=True) == out, 'output path changed')
    node, receipt = structure.read_bundle(bundle)
    need(receipt['manifest']['provenance'].get('object_id') == identities['keypillars_object_id'],
         'KeyPillars provenance object mismatch')
    records = decode_boundaries(node)
    unchanged()
    out.mkdir()
    try:
        report = dict(schema=SCHEMA, status='verified_boundary_geometry',
                      qualification='native_boundary_lines_only', source_keypillars_object_id=identities['keypillars_object_id'],
                      native_source_project=identities['native_source_project'],
                      native_source_hashes=identities['native_source_hashes'],
                      source_manifest_sha256=receipt['manifest_sha256'], sha256_before=before,
                      boundary_count=len(records), vertex_count=sum(r['vertex_count'] for r in records),
                      edge_count=sum(r['edge_count'] for r in records), coordinate_conversion_applied=False,
                      native_boundary_vertical_meaning_qualified=False, scientific_units_qualified=False,
                      fault_surfaces_qualified=False, fault_connectivity_qualified=False,
                      grid_boundary_association_qualified=False, complete_model_qualified=False,
                      independent_receiver_validated=False, boundaries=[], files={},
                      file_index_scope='data_and_multiblock_assets_excludes_report_self')
        tree = ET.Element('VTKFile', type='vtkMultiBlockDataSet', version='1.0', byte_order='LittleEndian')
        multi = ET.SubElement(tree, 'vtkMultiBlockDataSet')
        for record in records:
            points = record.pop('native_xyz')
            name = f"boundary_{record['ordinal']:04d}_id_{record['native_id']}"
            npy, vtp = name+'.vertices.npy', name+'.vtp'
            np.save(out/npy, points, allow_pickle=False)
            loaded = np.load(out/npy, allow_pickle=False)
            need(loaded.dtype == np.dtype('float64') and loaded.shape == points.shape and
                 np.array_equal(loaded.view(np.uint64), points.view(np.uint64)), 'NPY coordinate bits differ')
            (out/vtp).write_bytes(_vtp(points, record['is_closed']))
            _verify_vtp(out/vtp, points, record['is_closed'])
            record.update(native_xyz=points.tolist(), assets=dict(vertices_npy=npy, vtp_lines=vtp))
            report['boundaries'].append(record)
            ET.SubElement(multi, 'DataSet', index=str(record['ordinal']), name=f"Native boundary {record['native_id']}", file=vtp)
        (out/'boundaries.vtm').write_bytes(ET.tostring(tree, encoding='utf-8', xml_declaration=True))
        report['files']={p.name:dict(sha256=controls.sha(p), bytes=p.stat().st_size) for p in sorted(out.iterdir())}
        unchanged()
        report['sha256_after']={name:controls.sha(path) for name,path in paths.items()}
        (out/'REPORT.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        unchanged()
        return report
    except Exception as exc:
        (out/'REPORT.json').unlink(missing_ok=True)
        (out/'FAILED.json').write_text(json.dumps(dict(schema=SCHEMA,status='failed',error=str(exc),sha256_before=before),indent=2),encoding='utf-8')
        raise


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle',required=True);ap.add_argument('--identities',required=True);ap.add_argument('--output',required=True)
    args=ap.parse_args(argv)
    try:
        result=export_boundaries(args.bundle,args.identities,args.output)
        print(json.dumps(dict(status=result['status'],boundary_count=result['boundary_count'],vertex_count=result['vertex_count'],report=str(Path(args.output)/'REPORT.json'))))
        return 0
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        print(json.dumps(dict(status='failed',error=str(exc))))
        return 2


if __name__ == '__main__':sys.exit(main())
