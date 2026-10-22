# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Opt-in, read-only reservoir IR research CLI. Does not produce RESCUE."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import traceback

import petrel_native_binary as binary
import petrel_native_recovery as recovery
import petrel_reservoir_structure as structure


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024**2), b''): h.update(block)
    return h.hexdigest()


def registry(db):
    latest = {}
    cursor = db.execute('SELECT data_pk,droid,name,version,blob_type FROM data')
    for i, row in enumerate(cursor):
        if i >= 200_000: raise structure.Error('Registry bound exceeded')
        pk, droid, name, version, kind = row
        if type(pk) is not int or type(version) is not int:
            raise structure.Error('Invalid native registry key/version')
        key = (droid, name)
        if key not in latest or (version, pk) > (latest[key][3], latest[key][0]): latest[key] = row
    result = []
    for pk, droid, name, version, kind in latest.values():
        if kind in ('PillarGrid2', 'KeyPillars', 'FloatProperty', 'IntProperty'):
            result.append(dict(pk=pk, object_id=recovery.exact_uuid(droid.rsplit('/', 1)[-1]),
                               kind=kind, registry_name=name, registry_version=version))
    if len({x['object_id'] for x in result}) != len(result):
        raise structure.Error('Ambiguous multiple reservoir payloads for an object UUID')
    return result


def run(project, output, grid_id=None, max_properties=None, inventory_only=False, object_ids=None):
    project = Path(project).resolve(strict=True)
    if project.suffix.casefold() != '.pet': raise structure.Error('Expected exact .pet file')
    store = project.with_suffix('.ptd')
    paths = dict(project=project, model=store/'Model.ptd', data=store/'Data.ptd')
    if not all(p.is_file() for p in paths.values()): raise structure.Error('Missing matching native stores')
    output = Path(output).resolve()
    if output == project.parent or project.parent in output.parents:
        raise structure.Error('Output must be outside the source project directory')
    if max_properties is not None and max_properties < 0: raise structure.Error('Negative property limit')
    for suffix in ('-wal', '-shm', '-journal'):
        if Path(str(paths['data'])+suffix).exists(): raise structure.Error('Source transaction sidecar present')
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema='petrel.reservoir.probe/1', status='running',
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  sources={k:str(p) for k,p in paths.items()}, objects=[],
                  runtime_gui_used=False, petrel_process_launched=False,
                  complete_model_export=False, rescue_export=False,
                  limitations=['Cell and pillar index mapping unresolved',
                               'Fault-side corner mapping unresolved',
                               'Property masks are not established ACTNUM',
                               'Units, categories and null meanings not calibrated'])
    report_path = output/'RESULT.json'
    def save(): report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    def event(phase, **kw): print(json.dumps(dict(phase=phase, **kw)), flush=True)
    before = None
    decoder_paths = [Path(__file__).parent/name for name in
        ('petrel_native_binary.py','petrel_native_recovery.py','petrel_reservoir_structure.py',Path(__file__).name)]
    decoder_before = {p.name:sha(p) for p in decoder_paths}
    report['decoder_sha256_before'] = decoder_before
    try:
        event('hash_sources_before')
        before = {k:sha(p) for k,p in paths.items()}; report['source_sha256_before'] = before; save()
        metadata = recovery.Metadata(paths['project'], paths['model'])
        with closing(sqlite3.connect(binary.sqlite_readonly_uri(paths['data'])+'&immutable=1', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            rows = registry(db)
            grids = [r for r in rows if r['kind'] == 'PillarGrid2']
            if grid_id is None:
                if len(grids) != 1: raise structure.Error('Multiple/no grids: select exact --grid-id')
                grid_id = grids[0]['object_id']
            grid_id = recovery.exact_uuid(grid_id)
            if sum(r['object_id'] == grid_id for r in grids) != 1: raise structure.Error('Grid not found')
            selected = []
            grid_models = {a['object_id'] for a in metadata.ancestry(grid_id) if a['kind'] == 'ModelSubject'}
            for row in rows:
                ancestry = metadata.ancestry(row['object_id'])
                if row['object_id'] == grid_id or grid_id in {a['object_id'] for a in ancestry}:
                    row['relationship_status'] = 'exact_grid_identity_or_ancestor'
                elif row['kind'] == 'KeyPillars' and grid_models & {a['object_id'] for a in ancestry}:
                    row['relationship_status'] = 'same_model_subject_grid_relationship_unresolved'
                else: continue
                row['ancestry'] = ancestry; selected.append(row)
            report.update(grid_id=grid_id, linked_registry=selected,
                          project_reservoir_registry_entries=len(rows),
                          linked_property_entries=sum(r['kind'].endswith('Property') for r in selected))
            save()
            if object_ids:
                object_ids = {recovery.exact_uuid(x) for x in object_ids}
                if not object_ids <= {r['object_id'] for r in selected}:
                    raise structure.Error('Requested object is outside the selected grid/model scope')
            metadata_dir = output/'model_metadata'; metadata_dir.mkdir()
            report['metadata_records'] = []
            selected_ids = object_ids or {r['object_id'] for r in selected}
            for node in binary.read_documents(binary.decompress(binary.read_bounded(paths['model']))):
                if node.name not in ('PillarGridSubject','KeyPillarsSubject','PropertySubject'): continue
                tag = node.get('unique_tag')
                if tag not in selected_ids: continue
                target = metadata_dir/(tag+'.structural.zip')
                receipt = structure.write_bundle(node,target,dict(object_id=tag,source_model_sha256=before['model']))
                report['metadata_records'].append(dict(object_id=tag,bundle=str(target.relative_to(output)),
                    bundle_sha256=sha(target),exact_typed_readback=receipt['exact_typed_readback']))
                if tag == grid_id:
                    report['grid_declarations_uninterpreted'] = {name:node.get(name) for name in
                        ('is_swap_index_i','is_swap_index_j','is_swap_index_k',
                         'was_originally_right_handed','is_flipped_along_j','used_force_negative_depth')}
            save()
            property_count = 0
            for row in selected:
                if inventory_only: continue
                if object_ids and row['object_id'] not in object_ids: continue
                if row['kind'].endswith('Property'):
                    if max_properties is not None and property_count >= max_properties: continue
                    property_count += 1
                event('read_object', object_id=row['object_id'], kind=row['kind'], pk=row['pk'])
                result = dict(row)
                try:
                    blob = recovery.read_blob(db, row['pk'])
                    node = binary.object_document(blob)
                    if node.attrs.get('Type') != row['kind']:
                        raise structure.Error('Registry/payload type mismatch')
                    provenance = dict(row, compressed_sha256=hashlib.sha256(blob).hexdigest(),
                                      source_data_sha256=before['data'])
                    target = output/(row['object_id']+'.structural.zip')
                    result.update(structure.write_bundle(node, target, provenance), bundle=target.name,
                                  bundle_sha256=sha(target), status='structural_ir_verified')
                except Exception as exc:
                    result.update(status='unsupported_or_failed', error=str(exc), traceback=traceback.format_exc())
                report['objects'].append(result); save()
                event('object_finished', object_id=row['object_id'], status=result['status'])
            report['status'] = ('inventory_only' if inventory_only else
                                'structural_ir_partial' if any(r['status'] != 'structural_ir_verified' for r in report['objects'])
                                else 'structural_ir_verified')
            report['all_linked_payloads_selected'] = (not inventory_only and
                len(report['objects']) == len(selected))
    except BaseException as exc:
        report.update(status='failed_or_interrupted', error=str(exc), traceback=traceback.format_exc())
    finally:
        if before is not None:
            event('hash_sources_after')
            try:
                after = {k:sha(p) for k,p in paths.items()}
                report.update(source_sha256_after=after, sources_unchanged=after == before)
                if after != before: report['status'] = 'source_changed'
            except Exception as exc:
                report.update(status='source_integrity_unverified', integrity_error=str(exc))
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report['decoder_sha256_after'] = {p.name:sha(p) for p in decoder_paths}
        report['decoder_unchanged'] = report['decoder_sha256_after'] == decoder_before
        if not report['decoder_unchanged']: report['status'] = 'decoder_changed_during_run'
        save(); event('finished', status=report['status'], report=str(report_path))
    return 0 if report['status'] in ('inventory_only','structural_ir_verified') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--grid-id'); parser.add_argument('--max-properties', type=int)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--object-id', action='append', help='Exact linked object UUID; repeat to select several')
    args = parser.parse_args()
    sys.exit(run(args.project,args.output,args.grid_id,args.max_properties,args.inventory_only,args.object_id))
