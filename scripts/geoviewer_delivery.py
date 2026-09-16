"""Publish a shallow, portable index of accepted exports and enrich the HTML report.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Inputs: completed recovery receipts, exact output files and project context.
Outputs: sibling EXPORTS directory, CSV/JSON file index and report sections.
No Petrel or source writes. Hard links retain one physical copy while preserving
validated package paths. Unsupported filesystems retain direct original links.
Only accepted files enter the converted index; previews never become exports.
"""
from __future__ import annotations
import csv
import base64
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from geoviewer_metadata import PRODUCT, VERSION, write_json


def read(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError: return {} if default is None else default


def slug(value):
    return re.sub(r'[^A-Za-z0-9_-]+','_',str(value)).strip('_')[:70] or 'object'


def href(path, report):
    return quote(os.path.relpath(path,Path(report).parent).replace('\\','/'),safe='/')


def publish_exports(package, inventory, destination):
    """Idempotent new-output publication; never silently copy large files."""
    package=Path(package); destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    context=read(package/'01_project_metadata/project_context.json')
    names={x['object_id']:x['name'] for x in context.get('objects',[])}
    records=[]; findings=[]
    def link(source, category, identity, name, label, qc, unit='unknown', digest=None):
        source=Path(source)
        if not source.is_file(): raise ValueError('Accepted export is missing: '+str(source))
        if not source.resolve().is_relative_to(package.resolve()) and category!='seismic':
            raise ValueError('Export escaped the validated package')
        stem=slug(name)+'_'+slug(identity)[:36]+'_'+slug(source.stem)
        target=destination/category/(stem+source.suffix.lower())
        target.parent.mkdir(parents=True,exist_ok=True)
        layout='single_physical_copy_hardlink'
        try:
            if target.exists():
                if not os.path.samefile(source,target):raise ValueError('Export name collision: '+str(target))
            else:os.link(source,target)
            if not os.path.samefile(source,target):raise ValueError('Hard-link identity check failed')
        except OSError as exc:
            target=source;layout='original_path_filesystem_cannot_hardlink'
            findings.append('Shallow link unavailable; original validated file retained: '+str(exc))
        records.append(dict(object_id=identity,name=name,category=category,format=label,
            label=source.stem.replace('_',' ')+' ('+label+')',
            path=os.path.relpath(target,destination).replace('\\','/'),bytes=target.stat().st_size,
            status='exported',numerical_qc=qc,units=unit or 'unknown',sha256=digest or '',
            integrity='receipt_sha256' if digest else 'file_identity_and_prior_numerical_QC',storage=layout))
    native=read(package/'07_workflows_reports/native_recovery/native_recovery_report.json')
    if not native: native=read(package/'07_workflows_reports/native_recovery/native_recovery.json')
    for item in native.get('objects',[]):
        if item.get('status') not in ('decoded','missing_metadata') or item.get('numeric_round_trip')!='exact':continue
        category='logs' if item.get('blob_type','').endswith('WellLog') else 'grids'
        name=item.get('object_name') or item.get('name') or names.get(item['object_id'],item['object_id'])
        if item.get('well'):name=item['well']+'_'+name
        for artifact in item.get('artifacts',[]):
            source=package/'native_data'/artifact['path']
            if source.suffix.lower() not in ('.csv','.las','.xyz','.dat','.zmap','.txt','.json'):continue
            link(source,category,item['object_id'],name,source.suffix[1:].upper(),'exact_readback',item.get('unit'),artifact.get('sha256'))
    spatial=read(package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json')
    accepted={x['object_id']:x for x in spatial.get('objects',[]) if x.get('status')=='decoded'}
    # Shared CSVs retain native segment/vertex indices. No XY sorting is applied.
    for relative,category in [('05_spatial/polygons/native_polygons_vertices.csv','polygons'),
            ('05_spatial/points/native_points_vertices.csv','points'),
            ('02_wells/trajectories/native_well_trajectory_records.csv','trajectories')]:
        source=package/relative
        if not source.is_file():continue
        handles={};writers={};counts={}
        try:
            with source.open(encoding='utf-8-sig',newline='') as stream:
                reader=csv.DictReader(stream)
                for row in reader:
                    identity=row.get('object_id','')
                    if identity not in accepted:continue
                    if identity not in writers:
                        name=names.get(identity) or row.get('well_name') or row.get('object_name') or identity
                        target=destination/category/(slug(name)+'_'+slug(identity)+'.csv')
                        target.parent.mkdir(parents=True,exist_ok=True)
                        # Rebuilding this derived table is allowed only inside the owned destination.
                        handles[identity]=target.open('w',encoding='utf-8',newline='')
                        writers[identity]=csv.DictWriter(handles[identity],fieldnames=reader.fieldnames)
                        writers[identity].writeheader();counts[identity]=[target,name,0]
                    writers[identity].writerow(row);counts[identity][2]+=1
        finally:
            for handle in handles.values():handle.close()
        for identity,(target,name,count) in counts.items():
            with target.open(encoding='utf-8',newline='') as check:
                if sum(1 for _ in csv.DictReader(check))!=count:raise ValueError('Split CSV record count mismatch')
            records.append(dict(object_id=identity,name=name,category=category,format='CSV',
                path=target.relative_to(destination).as_posix(),bytes=target.stat().st_size,status='exported',
                numerical_qc='native_decoder; row-preserving split; count checked',units='unknown',
                sha256='',integrity='derived_from_validated_shared_CSV',storage='object_table',record_count=count))
    # Well-head tables are complete open tables; labels stay explicit if not a per-well export.
    heads=package/'02_wells/well_headers/native_well_heads.csv'
    if heads.is_file() and spatial.get('model_well_head_decode',{}).get('status')!='failed_closed':
        link(heads,'well_headers','well_headers','All_well_headers','CSV','native_model_cross_checks')
    workflows=read(package/'01_project_metadata/workflow_recovery.json')
    for item in workflows.get('objects',[]):
        if item.get('status')!='definition_recovered':continue
        for artifact in item['artifacts']:
            link(package/'native_workflows'/artifact['path'],'workflows',item['object_id'],item['name'],
                 'JSON definition (not executable)','typed_structure_only','not_applicable',artifact.get('sha256'))
    for item in inventory.get('objects',[]):
        if item.get('status')!='converted' or item.get('result',{}).get('status')!='passed':continue
        result=item['result']; folder=Path(result['receipt_path']).parent
        for artifact in result['artifacts']:
            source=folder/artifact['path']
            if source.suffix.lower() not in ('.segy','.json'):continue
            link(source,'seismic',item['id'],item['name'],source.suffix[1:].upper(),
                'every_amplitude_and_trace_geometry',result['summary']['profile']['source_vertical_unit'],artifact.get('sha256'))
    fields=['object_id','name','category','format','label','path','bytes','status','numerical_qc','units','sha256','integrity','storage','record_count']
    with (destination/'FILE_INDEX.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(records)
    result=dict(product=PRODUCT,version=VERSION,files=records,findings=list(dict.fromkeys(findings)),
        scope='Accepted decoded/converted objects only. Full project inventory is a separate report section.',
        storage_note='Large accepted files use hard links: one physical copy with a shallow user path and validated package compatibility path. Copying the entire run to another filesystem may duplicate hard-linked bytes. Keep the report, EXPORTS and data directories together.')
    write_json(destination/'FILE_INDEX.json',result)
    return result


def metadata_section(context):
    def value(key):
        item=context.get(key,{})
        return escape(str(item.get('value','unknown'))) if isinstance(item,dict) else escape(str(item or 'unknown'))
    saved=context.get('last_native_save',{})
    licenses=context.get('license_evidence',[])
    has_license_value=any(x.get('value') not in ('',None) for x in licenses)
    types=context.get('payload_type_counts',{})
    seismic=context.get('seismic_objects',[])
    base=sum(not x.get('virtual') for x in seismic);virtual=sum(bool(x.get('virtual')) for x in seismic)
    rows=''.join('<tr><td>'+escape(k)+'</td><td>'+escape(str(v))+'</td></tr>' for k,v in [
        ('Latest saved Petrel version',value('saved_version')),('Original Petrel version',value('original_version')),
        ('Recorded build label',context.get('build_label') or 'unknown'),
        ('Native last save',str(saved.get('value','unknown'))+'; timezone '+str(saved.get('timezone','unknown'))),
        ('Filesystem last write (different evidence)',context.get('filesystem_last_write_utc','unknown')),
        ('Storage family',context.get('layout','unknown')),('Saved by (recorded)',context.get('saved_by') or 'unknown')])
    workflow_rows=''.join('<details><summary>'+escape(w.get('name','Workflow'))+' — '+str(w.get('command_count',0))+' serialized entries</summary><ol>'+''.join('<li>'+escape(c)+'</li>' for c in w.get('command_types',[]))+'</ol></details>' for w in context.get('workflows',{}).get('objects',[]))
    dependency_rows=''.join('<tr><td>'+escape(str(d.get('name','')))+' '+escape(str(d.get('version','')))+'</td><td>'+escape(str(d.get('license','unspecified'))[:300])+'</td></tr>' for d in context.get('software_licenses',[]))
    return f'''<section id="project-identity"><h2>Project identity and compatibility</h2>
    <table>{rows}</table><p>Version values come from native metadata when present; unknown values are not inferred from file names. A detected release is not a claim of complete support for that release.</p>
    <p>Native seismic objects: <b>{len(seismic)}</b> ({base} base, {virtual} virtual). These are object definitions, not a count of accessible ZGY files. File availability, previews and conversion outcomes are listed separately below.</p>
    <details><summary>Native payload types and recorded history</summary><pre>{escape(json.dumps(dict(payload_types=types,history=context.get('history',[]),findings=context.get('findings',[])),indent=2))}</pre></details>
    <h3>Licenses and attribution</h3><p>GeoViewer_data_extractor: MIT license. Bundled dependency licenses are retained in the distribution; see its LICENSE and THIRD_PARTY_NOTICES.md. Petrel project data retains its original ownership and permissions.</p>
    <p>Project license metadata: {'recorded fields below; these do not establish current entitlement' if has_license_value else 'no license value recorded in the inspected fields; current Petrel license and modules cannot be determined from this project' }.</p>
    <pre>{escape(json.dumps(licenses,indent=2)) if licenses else ''}</pre>
    <details><summary>Bundled dependency license metadata</summary><p>Display summaries; full license texts are retained in the installed distribution.</p><table>{dependency_rows or '<tr><td>Dependency inventory not available in this report context.</td></tr>'}</table></details>
    <p>FV / FieldViewer is a visual family affiliation only. No FieldViewer application code is required.</p>
    <h3>Recovered workflow definitions</h3><p>Readable typed definitions preserve serialized order and references. Listed steps do not establish executable control flow. Execution and native re-import are not supported.</p>{workflow_rows or '<p>No saved workflow definition was recovered in this run.</p>'}</section>'''


def exports_section(index, destination, report):
    groups={}
    for item in index.get('files',[]):groups.setdefault((item['category'],item['object_id'],item['name']),[]).append(item)
    rows=[]
    for (category,identity,name),items in sorted(groups.items()):
        links=' · '.join('<a href="'+href(Path(destination)/x['path'],report)+'">'+escape(x.get('label',x['format']))+'</a>' for x in items)
        rows.append('<tr><td>'+escape(category)+'</td><td>'+escape(name)+'<br><small>'+escape(identity)+'</small></td><td>'+links+'</td><td>'+escape(items[0]['units'])+'</td><td>'+escape(items[0]['numerical_qc'])+'</td></tr>')
    return f'''<section id="converted-data"><h2>Open the extracted data</h2><p><b>{len(groups)} export groups · {len(index.get('files',[]))} files.</b> This index contains accepted outputs, separate from the complete native project inventory. A group represents one recovered object or an explicitly labelled aggregate table, such as all well headers.</p>
    <p><a href="{href(Path(destination)/'FILE_INDEX.csv',report)}">Download file index (CSV)</a> · <a href="{href(Path(destination),report)}/">Open EXPORTS folder</a></p>
    <p><code>samples.csv</code> means the full recovered well-log sample table: sample index, measured depth, raw value and null flag. It is not a small preview. Seismic figures and their amplitude statistics are bounded previews, not full-volume statistics. Unknown units require user interpretation when loading the data.</p>
    <input aria-label="Search converted data" placeholder="Filter converted objects or formats…" oninput="document.querySelectorAll('#exports-table tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase()))">
    <div class="scroll"><table id="exports-table"><thead><tr><th>Category</th><th>Object</th><th>Files</th><th>Units</th><th>Validation scope</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5">No accepted exports yet. Consult the inventory and per-object reasons.</td></tr>'}</tbody></table></div>
    <p>{escape(index.get('storage_note',''))}</p><details><summary>Delivery findings</summary><pre>{escape(json.dumps(index.get('findings',[]),indent=2))}</pre></details></section>'''


def decorate(report, context, index, destination, log_path=None, event_path=None):
    report=Path(report);text=report.read_text(encoding='utf-8')
    text=text.replace('<title>','<title>'+PRODUCT+' · ',1)
    if 'software_licenses' not in context:
        context=dict(context,software_licenses=read(Path(__file__).resolve().parents[1]/'00_manifest/dependency_inventory.json',[]))
    # Called after the original report is redelivered, avoiding repeated sections.
    asset=Path(__file__).resolve().parents[1]/'fv-mark.svg'
    if not asset.is_file():asset=Path(__file__).resolve().parents[1]/'docs/assets/fv-mark.svg'
    icon='data:image/svg+xml;base64,'+base64.b64encode(asset.read_bytes()).decode('ascii')
    text=text.replace('</head>','<link rel="icon" href="'+icon+'"></head>',1)
    badge='<div style="display:flex;align-items:center;gap:14px;margin-bottom:20px"><img alt="FieldViewer FV" width="52" height="52" src="'+icon+'"><div><h1>'+PRODUCT+'</h1><span>FieldViewer family · '+VERSION+'</span></div></div>'
    extra=badge+'<style>#converted-data .scroll{max-height:540px;overflow:auto}#converted-data table{width:100%}#project-identity pre{max-height:280px;overflow:auto;white-space:pre-wrap}</style>'
    if log_path:extra+='<p>Diagnostics: <a href="'+href(log_path,report)+'">Detailed process log</a> · <a href="'+href(event_path,report)+'">Structured events</a></p>'
    if index is not None:extra+=exports_section(index,destination,report)
    extra+=metadata_section(context)
    text=text.replace('<nav>','<nav><a href="#converted-data">Extracted files</a><a href="#project-identity">Version and history</a>',1)
    text=text.replace('<main>','<main>'+extra,1)
    if '<main>' not in text:text=text.replace('<body>','<body>'+extra,1)
    text=text.replace('not_registered','not listed in package manifest (not a decode failure)')
    temporary=report.with_suffix('.html.tmp');temporary.write_text(text,encoding='utf-8');temporary.replace(report)


def partial_report(report, context, status, reason, log_path=None):
    groups={}
    for item in context.get('objects',[]):groups.setdefault(item['category'],[]).append(item)
    tree=''.join('<details><summary>'+escape(kind)+' ('+str(len(items))+')</summary><ul>'+''.join('<li>'+escape(x['name'])+' <small>'+escape(x['object_id'])+'</small></li>' for x in items)+'</ul></details>' for kind,items in sorted(groups.items()))
    text='<!doctype html><html><head><meta charset="utf-8"><title>'+PRODUCT+' report</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:20px;background:#f5f8fc;color:#172638}table{border-collapse:collapse}td{padding:8px;border-bottom:1px solid #ccc}pre{white-space:pre-wrap}details{margin:12px}</style></head><body><main><h1>'+PRODUCT+'</h1><h2>'+escape(status)+'</h2><p>'+escape(reason)+'</p>'+metadata_section(context)+'<h2>Native project inventory (metadata)</h2>'+tree
    if log_path:text+='<p><a href="'+href(log_path,report)+'">Process log</a></p>'
    text+='</main></body></html>'
    temporary=Path(report).with_suffix('.html.tmp');temporary.write_text(text,encoding='utf-8');temporary.replace(report)
