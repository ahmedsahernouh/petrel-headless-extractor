# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only native project identity, compatibility, object and workflow evidence.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Inputs: exact .pet/store pair. Outputs: JSON metadata and optional readable
workflow definitions. No Petrel, Ocean, deserialization callbacks or execution.
Unknown layouts retain explicit inventory limitations. Definitions are not a
native re-import format. File writers use a caller-owned new output directory.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import petrel_native_binary as binary
from petrel_native_binary import Node, Array

PRODUCT = 'GeoViewer_data_extractor'
VERSION = '1.0.0'


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def walk(node):
    if isinstance(node, Node):
        yield node
        for child in node.children:
            yield from walk(child)


def date_evidence(value):
    """MC-NBFX DateTime ticks; never replace an unknown zone with this host's zone."""
    if not isinstance(value, dict) or value.get('nbfx_type') != 0x96:
        return dict(status='unknown', raw=value)
    try:
        bits=int.from_bytes(bytes.fromhex(value['raw_hex']), 'little')
        ticks=bits & ((1 << 62)-1); kind=bits >> 62
        stamp=datetime(1,1,1)+timedelta(microseconds=ticks//10)
        text=stamp.strftime('%Y-%m-%dT%H:%M:%S')+f'.{ticks % 10_000_000:07d}'
        # NBFX's UTC flag is authoritative; local/unspecified has no stored offset.
        return dict(status='recorded', value=text+('Z' if kind==1 else ''),
                    timezone='UTC' if kind==1 else 'unknown', kind_bits=kind, raw=value)
    except (ValueError, OverflowError, KeyError):
        return dict(status='unknown', raw=value)


def typed(node):
    if isinstance(node, Array):
        return dict(name=node.name, array_dtype=str(node.values.dtype), values=node.values.tolist())
    def value(x):
        return dict(bytes_hex=x.hex()) if isinstance(x, bytes) else x
    return dict(name=node.name, attributes=node.attrs, content=[value(v) for v in node.content],
                children=[typed(c) for c in node.children])


def native_models(project):
    return binary.read_documents(binary.decompress(binary.read_bounded(project.with_suffix('.ptd')/'Model.ptd')))


def merge_model_record(n, result, stubs):
    """Interpret one framed subject; ambiguous fields never select a first value."""
    tag=str(n.get('unique_tag') or '')
    if tag and tag!='0':
        name=str(n.get('name') or tag)
        row=stubs.setdefault(tag,dict(object_id=tag,category=n.name,name=name,
                                     parent_id='',status='metadata_only'))
        row['model_version']=n.attrs.get('Version')
        if n.get('name'): row['name']=name
    if n.name=='ProjectSubject':
        for field,out in [('version_string','saved_version'),('original_version_string','original_version')]:
            val=n.get(field)
            result[out]=dict(status='recorded' if val else 'unknown',value=val,
                             source='Model.ptd/ProjectSubject/'+field)
        result['build_label']=n.get('version_build')
        result['saved_by']=n.get('version_user')
        result['project_comments']=n.get('comments')
        result['history_reference']=n.child('history').get('name_tag') if n.child('history',False) else None
        style=n.child('style',False)
        if style:
            units=style.child('units_style',False)
            result['display_units']={k:units.get('temp_'+k+'_unit') for k in ('xy','z','time')} if units else {}
        for child in walk(n):
            if re.search('licen[cs]e', child.name, re.I) and not child.children:
                result['license_evidence'].append(dict(field=child.name,value=child.scalar(),scope='recorded metadata; not current entitlement'))
    if n.name in ('SeismicSubject','VirtualSeismicSubject'):
        references=[]
        def scan(node, path=''):
            here=path+'/'+node.name
            if node.name in ('history','import_info','update_info'): return
            if not node.children:
                val=node.scalar()
                if isinstance(val,str) and val.lower().endswith(('.zgy','.sgy','.segy')):
                    references.append(dict(field=here,value=val))
            for child in node.children:
                if isinstance(child,Node):scan(child,here)
        scan(n)
        result['seismic_objects'].append(dict(object_id=tag,name=n.get('name') or tag,
            subject_type=n.name,virtual=n.name=='VirtualSeismicSubject',references=references))


def model_record_error(node, index, error):
    candidates=[]
    for child in node.children:
        if isinstance(child,Node) and child.name=='unique_tag':
            try:
                value=child.scalar()
                if isinstance(value,str) and value:candidates.append(value)
            except ValueError:pass
    return dict(record_index=index,category=node.name,candidate_object_ids=candidates,
                status='metadata_unresolved',reason=str(error))


def compatibility(context):
    """Keep failure reasons with the pipeline gate instead of a bare Boolean."""
    return {key:context[key] for key in ('layout','native_decoders_applicable','model_readable',
        'model_metadata_complete','model_records_read','model_record_error_count',
        'object_inventory_complete','reason','findings')}


def inspect_project(project):
    project=Path(project).resolve(strict=True); store=project.with_suffix('.ptd')
    if not store.is_dir(): raise ValueError('Matching .ptd directory is missing')
    result=dict(product=PRODUCT, extractor_version=VERSION, project_file=str(project),
                project_name=project.stem, saved_version=dict(status='unknown'),
                last_native_save=dict(status='unknown'), original_version=dict(status='unknown'),
                filesystem_last_write_utc=datetime.fromtimestamp(project.stat().st_mtime, timezone.utc).isoformat(),
                layout='unrecognized', model_readable=False, object_inventory_complete=False,
                objects=[], findings=[], seismic_objects=[], license_evidence=[], history=[],
                model_metadata_complete=False,model_records_read=0,model_record_errors=[])
    stubs={}
    try:
        roots=list(binary.read_documents(binary.project_payload(binary.read_bounded(project,64*1024*1024))))
        if len(roots)!=1: raise ValueError('Expected one project registry')
        result['last_native_save']=date_evidence(roots[0].get('saved_time',None))
        for entry in roots[0].child('class_infos').child('map').children:
            kind=entry.get('key')
            for n in walk(entry.child('value')):
                if n.attrs.get('Type')=='SubjectStub' and n.get('uniqueTag') not in ('',0):
                    tag=str(n.get('uniqueTag'))
                    if tag in stubs: raise ValueError('Duplicate project identity')
                    stubs[tag]=dict(object_id=tag,category=str(kind),name=str(n.get('visualName') or tag),
                                    parent_id=str(n.get('parentTag') or ''),status='metadata_only')
        result['object_inventory_complete']=True
    except (ValueError,OSError,UnicodeError) as exc:
        result['findings'].append('Project hierarchy incomplete: '+str(exc))
    try:
        for index,n in enumerate(native_models(project)):
            result['model_records_read']+=1
            try:merge_model_record(n,result,stubs)
            except (ValueError,UnicodeError) as exc:
                result['model_record_errors'].append(model_record_error(n,index,exc))
        # This flag describes complete container/framing readability. An isolated
        # subject interpretation error must not disable unrelated numeric readers.
        if not result['model_records_read']:raise ValueError('Model container has no subject records')
        result['model_readable']=True
    except (ValueError,OSError,UnicodeError) as exc:
        result['findings'].append('Model metadata incomplete: '+str(exc))
    dbpath=store/'Data.ptd'
    if dbpath.is_file():
        try:
            if any(Path(str(dbpath)+s).exists() for s in ('-wal','-journal')):
                raise ValueError('Active SQLite transaction sidecars; close source project and use a stable snapshot')
            with sqlite3.connect(binary.sqlite_readonly_uri(dbpath)+'&immutable=1',uri=True) as db:
                db.execute('PRAGMA query_only=ON')
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'data','blob_parts'} <= tables:raise ValueError('Unrecognized native database schema')
                result['layout']='model_sqlite_bxml'
                result['payload_type_counts']=dict(db.execute('SELECT blob_type,COUNT(*) FROM data GROUP BY blob_type'))
                ref=result.get('history_reference')
                if ref:
                    from petrel_native_recovery import read_blob
                    rows=db.execute("SELECT data_pk,droid FROM data WHERE blob_type='History' ORDER BY version DESC,data_pk DESC").fetchall()
                    for pk,droid in rows:
                        if str(droid).rsplit('/',1)[-1]!=ref:continue
                        try:
                            history=binary.object_document(read_blob(db,pk))
                            for entry in walk(history):
                                dates={c.name:date_evidence(c.scalar()) for c in entry.children
                                       if isinstance(c,Node) and not c.children and isinstance(c.scalar(),dict)
                                       and c.scalar().get('nbfx_type')==0x96}
                                if dates:
                                    fields={c.name:c.scalar() for c in entry.children if isinstance(c,Node) and not c.children
                                            and isinstance(c.scalar(),(str,int,bool))}
                                    result['history'].append(dict(dates=dates,fields=fields))
                        except (ValueError,OSError) as exc:result['findings'].append('History: '+str(exc))
                        break
        except (ValueError,OSError,sqlite3.Error) as exc:
            result['findings'].append('Native database unavailable: '+str(exc))
    elif any(store.glob('*.ptd')):
        result['layout']='legacy_distributed_or_unknown'
        result['findings'].append('No Data.ptd; native numeric decoding is unsupported for this storage family')
    result['objects']=list(stubs.values())
    result['object_type_counts']=dict(Counter(r['category'] for r in result['objects']))
    result['model_record_error_count']=len(result['model_record_errors'])
    result['model_metadata_complete']=result['model_readable'] and not result['model_record_errors']
    result['seismic_inventory_complete']=result['model_readable'] and not any(
        row['category'] in ('SeismicSubject','VirtualSeismicSubject') for row in result['model_record_errors'])
    result['native_decoders_applicable']=result['layout']=='model_sqlite_bxml' and result['model_readable']
    if result['model_record_errors']:
        result['findings'].append(f"{result['model_record_error_count']} model records have unresolved metadata; "
                                  'independent supported decoders remain eligible when their inputs are readable')
    result['reason']=('; '.join(result['findings']) or
        ('Native container and database readable; object-specific validation remains required'
         if result['native_decoders_applicable'] else 'Native container/database profile is not supported'))
    return result


def export_workflows(project, destination, context):
    """Preserve exact typed definitions; command payloads are inert data only."""
    from petrel_native_recovery import read_blob
    project=Path(project); destination=Path(destination)
    receipt=dict(status='not_available',objects=[],execution_supported=False,reimport_supported=False)
    if not context.get('native_decoders_applicable'):return receipt
    dbpath=project.with_suffix('.ptd')/'Data.ptd'
    names={row['object_id']:row['name'] for row in context['objects']}
    with sqlite3.connect(binary.sqlite_readonly_uri(dbpath)+'&immutable=1',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("SELECT data_pk,droid,name,version FROM data WHERE blob_type='Commands' ORDER BY version DESC,data_pk DESC").fetchall()
        seen=set()
        for pk,droid,slot,version in rows:
            key=(droid,slot)
            if key in seen:continue
            seen.add(key); tag=str(droid).rsplit('/',1)[-1]
            item=dict(object_id=tag,name=names.get(tag,'Workflow '+tag),status='unsupported',artifacts=[])
            try:
                blob=read_blob(db,pk); node=binary.object_document(blob)
                if node.attrs.get('Type')!='Commands':raise ValueError('Not a Commands definition')
                commands=[dict(index=i,type=c.attrs.get('Type',c.name),definition=typed(c)) for i,c in enumerate(node.children)]
                stem=re.sub('[^A-Za-z0-9_-]+','_',item['name'])[:50]+'_'+tag[:12]
                target=destination/(stem+'.json')
                if target.exists():raise ValueError('Workflow output already exists')
                write_json(target,dict(object_id=tag,name=item['name'],registry_slot=slot,registry_version=version,
                    source_petrel_version=context['saved_version'],commands=commands,root_attributes=node.attrs,
                    execution_supported=False,native_reimport_supported=False,
                    interpretation='Serialized order and references retained; command semantics and execution not validated'))
                item.update(status='definition_recovered',command_count=len(commands),command_types=[c['type'] for c in commands],
                            artifacts=[dict(path=target.name,bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())])
            except (ValueError,OSError,UnicodeError) as exc:item['reason']=str(exc)
            receipt['objects'].append(item)
    receipt['status']='completed' if all(x['status']=='definition_recovered' for x in receipt['objects']) else 'partial'
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--workflow-output',type=Path)
    args=parser.parse_args();context=inspect_project(args.project)
    write_json(args.output,context)
    write_json(args.output.with_name('native_compatibility.json'),compatibility(context))
    from geoviewer_diagnostics import event
    for issue in context['model_record_errors']:
        event('model_metadata_unresolved',severity='warning',**issue)
    if context['findings']:print('Native metadata: '+context['reason'],flush=True)
    if args.workflow_output:
        receipt=export_workflows(args.project,args.workflow_output,context)
        write_json(args.output.with_name('workflow_recovery.json'),receipt)
    print('Native compatibility: '+context['layout']+'; saved Petrel '+str(context['saved_version'].get('value','unknown')),flush=True)


if __name__=='__main__':main()
