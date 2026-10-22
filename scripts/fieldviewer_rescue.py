# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Public beta3 RESCUE source inventory. Static-model export is not included.

Reads metadata from the exact PET/Model/Data pair, without decoding grid payloads,
calling Petrel, transforming CRS or changing the source. Project-bound research
export routines and vendor runtime/profile components are excluded from beta3.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback
from petrel_native_binary import NativeError
import probe_petrel_reservoir_structure as probe
SCHEMA='fieldviewer.rescue-export/1'
DECODER_MODULES=('petrel_native_binary','petrel_native_recovery','probe_petrel_reservoir_structure')


def decoder_paths():
    """Bind loaded local readers and writers to one source/package directory.

    Embedded Python can otherwise mix a source entrypoint with older packaged
    imports. Hash the files actually loaded, including native and geometry code.
    """
    entry=Path(__file__).resolve(strict=True);paths=[entry]
    for name in DECODER_MODULES:
        module=sys.modules.get(name);loaded=getattr(module,'__file__',None)
        if not loaded:raise NativeError('Decoder module is not loaded: '+name)
        path=Path(loaded).resolve(strict=True)
        if path.parent!=entry.parent:
            raise NativeError('Decoder imported outside entrypoint directory: '+name)
        if path.name!=name+'.py' or not path.is_file():
            raise NativeError('Decoder source identity differs: '+name)
        paths.append(path)
    return paths

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def no_links(path):
    path=Path(os.path.abspath(path))
    for part in [path,*path.parents]:
        if part.is_symlink() or (hasattr(os.path,'isjunction') and os.path.isjunction(part)):
            raise NativeError('Source/output/configuration cannot use symbolic links or junctions')
    return path

def source_paths(project,output):
    project=no_links(project).resolve(strict=True);output=no_links(output).resolve()
    if project.suffix.lower()!='.pet' or not project.is_file():raise NativeError('Exact existing .pet file required')
    store=no_links(project.with_suffix('.ptd'))
    paths={'project':project,'model':no_links(store/'Model.ptd'),'data':no_links(store/'Data.ptd')}
    if not all(p.is_file() for p in paths.values()):raise NativeError('Matching .ptd Model/Data stores required')
    if output==project.parent or output.is_relative_to(project.parent):raise NativeError('Output must be outside source project directory')
    if output.exists():raise NativeError('New exclusive output directory required')
    if not output.parent.is_dir():raise NativeError('Output parent must exist')
    for suffix in ('-wal','-shm','-journal'):
        if Path(str(paths['data'])+suffix).exists():raise NativeError('Source transaction sidecar present')
    return paths,output

def inventory_project(project,output):
    """List registered grids and ancestry without selecting or decoding payload blobs.

    An inventory deliberately has no runtime/profile admission or export-ready flag.
    It is useful when a project contains multiple grids or is outside the tested fixture.
    """
    engine_paths=decoder_paths()
    paths,output=source_paths(project,output)
    output.mkdir(exist_ok=False)
    result=dict(schema='fieldviewer.rescue-inventory/1',status='running',
        project_file=str(paths['project']),output_root=str(output),grids=[],
        started_utc=datetime.now(timezone.utc).isoformat(),payload_blob_queries=False,payload_blobs_decoded=False,
        source_files_fully_hashed=True,
        rescue_export=False,complete_model_qualified=False,petrel_launched=False,
        neighboring_projects_ingested=False,
        scope='Native registry and subject metadata only; no geometry/property values or transfer qualification')
    result['decoder_source_paths']={p.name:str(p) for p in engine_paths}
    result['decoder_sha256_before']={p.name:sha(p) for p in engine_paths}
    before=None
    try:
        before={k:sha(p) for k,p in paths.items()};result['source_sha256_before']=before
        metadata=probe.recovery.Metadata(paths['project'],paths['model'])
        project_nodes=[n for n in probe.binary.read_documents(probe.binary.decompress(probe.binary.read_bounded(paths['model']))) if n.name=='ProjectSubject']
        result['saved_version']=dict(value=project_nodes[0].get('version_string') if len(project_nodes)==1 else None,
            source='Model.ptd/ProjectSubject/version_string',project_subject_records=len(project_nodes))
        with closing(sqlite3.connect(probe.binary.sqlite_readonly_uri(paths['data'])+'&immutable=1',uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            rows=probe.registry(db)
        result['reservoir_payload_types']=dict(Counter(row['kind'] for row in rows))
        ancestries={row['object_id']:metadata.ancestry(row['object_id']) for row in rows}
        for row in rows:
            if row['kind']!='PillarGrid2':continue
            grid_id=row['object_id'];ancestry=ancestries[grid_id]
            models={a['object_id'] for a in ancestry if a['kind']=='ModelSubject'}
            properties=[];key_pillars=[]
            for linked in rows:
                parents=ancestries[linked['object_id']]
                if linked['kind'] in ('FloatProperty','IntProperty') and grid_id in {a['object_id'] for a in parents}:
                    properties.append(dict(linked,relationship='exact_grid_ancestor',ancestry=parents))
                if linked['kind']=='KeyPillars' and models & {a['object_id'] for a in parents if a['kind']=='ModelSubject'}:
                    key_pillars.append(dict(linked,relationship='same_model_subject_grid_relationship_unresolved'))
            result['grids'].append(dict(row,ancestry=ancestry,linked_property_count=len(properties),
                linked_properties=properties,key_pillars_candidates=key_pillars,
                payload_profile='not_decoded',export_qualification='not_established'))
        result['unlinked_property_count']=sum(r['kind'] in ('FloatProperty','IntProperty') and not any(
            g['object_id'] in {a['object_id'] for a in ancestries[r['object_id']]} for g in result['grids']) for r in rows)
        result['status']='inventory_verified'
    except Exception as exc:
        result['status']='failed';result['error']=str(exc);result['traceback']=traceback.format_exc()
    finally:
        try:
            result['source_sha256_after']={k:sha(p) for k,p in paths.items()}
            result['source_integrity_unchanged']=before is not None and result['source_sha256_after']==before
            result['decoder_sha256_after']={p.name:sha(p) for p in engine_paths}
            result['decoder_unchanged']=result['decoder_sha256_after']==result['decoder_sha256_before']
            if not result['source_integrity_unchanged'] or not result['decoder_unchanged']:raise NativeError('Source/decoder integrity changed or unavailable')
        except Exception as exc:result['status']='failed';result['integrity_error']=str(exc)
        result['finished_utc']=datetime.now(timezone.utc).isoformat()
        (output/'RESCUE_INVENTORY.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        body=''.join('<tr><td>'+html.escape(g['object_id'])+'</td><td>'+str(g['linked_property_count'])+'</td><td>Not decoded or qualified</td></tr>' for g in result['grids'])
        (output/'RESCUE_INVENTORY.html').write_text('<!doctype html><meta charset="utf-8"><title>RESCUE source inventory</title><h1>RESCUE source inventory</h1><p>'+html.escape(str(paths['project']))+'</p><p>'+html.escape(result['status'])+'</p><p>Metadata only; no RESCUE export or geometry qualification.</p><p>'+html.escape(result.get('error',''))+'</p><table border="1"><tr><th>Grid UUID</th><th>Linked properties</th><th>Qualification</th></tr>'+body+'</table><p><a href="RESCUE_INVENTORY.json">Inventory receipt and source hashes</a></p>',encoding='utf-8')
    return result

def export_rescue(project, output, runtime_config=None):
    raise NativeError('Static-model RESCUE export is not included in public beta3. Use --inventory-only for grid metadata. Full extraction and CRS modes are available separately.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--runtime-config',type=Path,help='Reserved; beta3 does not include the RESCUE exporter')
    parser.add_argument('--inventory-only',action='store_true',help='Read grid metadata only; no model export or payload decoding')
    a=parser.parse_args()
    try:
        if a.inventory_only and a.runtime_config is not None:
            raise NativeError('--inventory-only does not use a writer runtime')
        result=inventory_project(a.project,a.output) if a.inventory_only else export_rescue(a.project,a.output,a.runtime_config)
        print(json.dumps(result));return 0 if result['status']=='inventory_verified' else 2
    except Exception as exc:
        print(json.dumps(dict(schema='fieldviewer.rescue-inventory/1' if a.inventory_only else SCHEMA,status='failed',error=str(exc),complete_model_qualified=False,independent_receiver=False)))
        return 2

if __name__=='__main__':sys.exit(main())
