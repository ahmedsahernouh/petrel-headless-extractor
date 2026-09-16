"""Project ZGY discovery, optional conversion and prominent report delivery.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Only the selected store and explicit XML/BXML file fields establish association.
Nearby unreferenced seismic is inventoried, not silently assigned to a project.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

from petrel_seismic_integrity import file_state, SEISMIC_SUFFIXES


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False)+'\n', encoding='utf-8')


def reference_values(project):
    """Only explicit file/path/URI fields, never visual names or fuzzy strings."""
    field=lambda name: bool(re.search(r'file|path|uri|url',str(name),re.I))
    try:
        root=ET.parse(project).getroot()
        values=[]
        for element in root.iter():
            if field(element.tag) and element.text: values.append(element.text)
            values.extend(value for name,value in element.attrib.items() if field(name))
        return values,'XML'
    except ET.ParseError:
        import petrel_native_binary as binary
        stack=list(binary.read_documents(binary.project_payload(binary.read_bounded(project,64*1024*1024))))
        values=[]
        while stack:
            node=stack.pop();stack.extend(node.children)
            if field(node.name) and not node.children:
                value=node.scalar()
                if isinstance(value,str): values.append(value)
            values.extend(value for name,value in node.attrs.items() if field(name) and isinstance(value,str))
        return values,'validated BXML profile'


def discover(project):
    project=Path(project).resolve(); store=project.with_suffix('.ptd')
    rows={}; findings=[]
    def add(path, association, reference=''):
        path=Path(path).resolve()
        key=str(path).casefold()
        if key in rows and rows[key]['association'] != 'unlinked_companion': return
        row=dict(id=hashlib.sha256(key.encode()).hexdigest()[:16], source=str(path), name=path.name,
                 association=association, reference=reference, format=path.suffix.lower(),
                 status='inventoried', sha256=None, hash_status='not_requested', preserved_copy=False)
        if path.is_symlink() or path.is_junction():
            row.update(status='unsupported', reason='Linked filesystem entries are not supported')
        elif not path.is_file(): row.update(status='missing', reason='Referenced source is unavailable')
        else:
            row['file_state']=file_state(path)
            if path.suffix.lower()=='.zgy':
                try:
                    from petrel_file_convert import open_zgy, zgy_metadata
                    from petrel_seismic_integrity import readonly_source
                    with readonly_source(path), open_zgy(path) as reader: row['metadata']=zgy_metadata(reader)
                except Exception as exc: row.update(status='unsupported',reason=str(exc))
        rows[key]=row
    for path in sorted(store.rglob('*')):
        if path.is_file() and path.suffix.lower() in SEISMIC_SUFFIXES: add(path,'selected_project_store')
    native_objects=[]
    try:
        from geoviewer_metadata import inspect_project
        native_objects=inspect_project(project).get('seismic_objects',[])
        for obj in native_objects:
            for reference in obj.get('references',[]):
                value=reference['value']; leaf=Path(value.replace('\\','/')).name
                # Only exact UUID-named storage inside the selected project is relocated.
                match=[r for r in rows.values() if r['association'] in ('selected_project_store','exact_native_UUID_in_selected_store') and
                       re.fullmatch(r'[0-9a-fA-F-]{36}\.zgy',leaf) and Path(r['source']).name.casefold()==leaf.casefold()]
                if match:
                    row=match[0]
                    row.setdefault('native_objects',[]).append(dict(object_id=obj['object_id'],name=obj['name']))
                    row['name']=obj['name'];row['association']='exact_native_UUID_in_selected_store'
                else:
                    key='reference:'+value.casefold()
                    if key not in rows:
                        rows[key]=dict(id=hashlib.sha256(key.encode()).hexdigest()[:16],source=value,name=leaf,
                            association='native_model_external_reference',reference=value,format=Path(leaf).suffix.lower(),
                            status='missing',reason='Referenced file is not present in the selected store. External hosts were not probed; this does not establish that the original file was deleted.',
                            native_objects=[],sha256=None,hash_status='not_available',preserved_copy=False)
                    rows[key]['native_objects'].append(dict(object_id=obj['object_id'],name=obj['name']))
    except (OSError,ValueError) as exc:findings.append('Native seismic object linkage unavailable: '+str(exc))
    # Parse bounded XML or supported BXML fields. No fuzzy binary-string matching.
    if project.stat().st_size <= 64*1024*1024:
        try:
            values,reference_profile=reference_values(project)
            for value in values:
                value=value.strip().strip('"')
                if not value.lower().endswith(tuple(SEISMIC_SUFFIXES)): continue
                if value.lower().startswith('file:'):
                    url=urlsplit(value); value=unquote(url.path)
                    if url.netloc: value='//'+url.netloc+value
                    elif re.match(r'^/[A-Za-z]:',value): value=value[1:]
                elif '://' in value:
                    findings.append('Remote seismic reference is not fetched: '+value); continue
                path=Path(value.replace('\\','/'))
                if not path.is_absolute(): path=project.parent/path
                # A reference to another project's store is not part of this run.
                if any(p.suffix.lower()=='.ptd' and p.resolve()!=store for p in path.parents):
                    findings.append('Neighboring project store reference excluded: '+str(path)); continue
                add(path,'explicit_project_reference',value)
        except (ET.ParseError, ValueError, OSError) as exc:
            findings.append('External project reference discovery unavailable: '+str(exc))
    else: findings.append('Project XML exceeds the 64 MiB reference-discovery limit')
    neighboring={p.resolve() for p in project.parent.glob('*.ptd') if p.resolve()!=store}
    from geoviewer_paths import project_files
    for path in sorted(project_files(project.parent)):
        if path.suffix.lower() not in SEISMIC_SUFFIXES or not path.is_file(): continue
        if any(parent in neighboring for parent in path.resolve().parents): continue
        add(path,'unlinked_companion')
    return dict(version='0.8.0', project_file=str(project), objects=list(rows.values()), findings=findings,native_objects=native_objects,
                discovery_boundary='Selected store and explicit XML or supported BXML file/path fields only. Unlinked companions need an exact-file run. Unparsed references are not inferred.')


def convert_project(inventory, output, enabled, full_hash=False, on_update=None, options=None):
    from petrel_file_convert import execute
    from petrel_seismic_integrity import readonly_source
    from petrel_progress import hash_file
    for row in inventory['objects']:
        if full_hash and (not enabled or row['format']!='.zgy' or row['association']=='unlinked_companion') and row.get('file_state'):
            try:
                source=Path(row['source'])
                with readonly_source(source):
                    before=file_state(source); digest=hash_file(source)
                    if before!=file_state(source): raise ValueError('Source changed while hashing')
                row.update(sha256=digest,hash_status='sha256')
            except (OSError,ValueError) as exc:
                row.update(status='unavailable',reason=str(exc));continue
        if row['status'] in ('missing','unsupported'): continue
        if row['association']=='unlinked_companion':
            row.update(status='unlinked', reason='Project association unproven; use the main BAT with this exact file'); continue
        if row['format']!='.zgy':
            row.update(status='already_open_format', reason='SEG-Y is already an open format'); continue
        if not enabled:
            row.update(status='not_selected', reason='Dataset conversion is off'); continue
        row['status']='converting'
        if on_update: on_update()
        try:
            result=execute(row['source'],output,'zgy-to-segy',{**(options or {}),'full_hash':full_hash,'expected_source_state':row.get('file_state')})
            row.update(status='converted', result=result, receipt_path=result['receipt_path'],
                       output_file=str(Path(result['receipt_path']).parent/'volume.segy'))
            if full_hash:
                row.update(sha256=result['source_hashes_before'].get(str(Path(row['source']).resolve())),hash_status='sha256')
        except Exception as exc:
            row.update(status='conversion_failed', reason=str(exc))
            if full_hash and row.get('file_state') and not row.get('sha256'):
                try:
                    source=Path(row['source'])
                    with readonly_source(source):
                        before=file_state(source);digest=hash_file(source)
                        if before!=file_state(source):raise ValueError('Source changed while hashing')
                    row.update(sha256=digest,hash_status='sha256')
                except (OSError,ValueError) as error:
                    row.update(hash_status='failed',hash_reason=str(error))
        if on_update: on_update()
    inventory['counts']=dict(Counter(row['status'] for row in inventory['objects']))
    inventory['conversion_enabled']=enabled
    inventory['full_seismic_hash']=full_hash
    if on_update: on_update()
    return inventory


def single_file_run(source, output, options):
    """The main BAT's exact-ZGY mode retains the same report-first contract."""
    from datetime import datetime
    import subprocess
    import sys
    import uuid
    from petrel_file_convert import open_zgy, zgy_metadata, InputError
    source=Path(source).resolve();output=Path(output).resolve()
    if output.is_relative_to(source.parent) or any(p.lower().endswith(('.ptd','.pet')) for p in output.parts):
        raise InputError('Choose an output root outside the source directory and native Petrel stores')
    with open_zgy(source) as reader: metadata=zgy_metadata(reader)
    stem=(re.sub(r'[^A-Za-z0-9_-]+','_',source.stem)[:64] or 'Seismic')+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    data=output/(stem+'_data');data.mkdir(parents=True,exist_ok=False)
    package=data/'report';report=output/(stem+'_REPORT.html')
    from geoviewer_delivery import decorate,publish_exports,partial_report
    from geoviewer_diagnostics import Diagnostics
    logs=Diagnostics(output/(stem+'_LOG.txt'),output/(stem+'_EVENTS.jsonl'))
    logs.event('started',source=str(source),options=options,mode='exact_ZGY_file')
    inventory=dict(objects=[dict(id=stem,name=source.name,source=str(source),format='.zgy',
                    association='explicit_file_selection',status='inventoried',file_state=file_state(source),
                    metadata=metadata,sha256=None,hash_status='not_requested')],findings=[],full_seismic_hash=options.get('full_hash',False))
    write_json(package/'01_project_metadata/project_seismic_inventory.json',inventory)
    script=Path(__file__).with_name('report_petrel_project_audit.py')
    try:
        rendered=subprocess.run([sys.executable,'-B',str(script),'--export-package',str(package),'--title',source.stem+' seismic report'],capture_output=True,text=True)
        (data/'report_build.log').write_text(rendered.stdout+'\n'+rendered.stderr,encoding='utf-8')
        logs.event('report_builder',exit_code=rendered.returncode,stdout=rendered.stdout,stderr=rendered.stderr)
        if rendered.returncode: raise RuntimeError('Seismic report failed; see '+str(data/'report_build.log'))
        def update(complete=False):
            write_json(data/'seismic_results.json',inventory)
            deliver_report(package/'PROJECT_REPORT.html',report,inventory,complete)
        update();print('FULL REPORT (ready now): '+str(report),flush=True)
        enabled=not options.get('report_only',False)
        convert_project(inventory,data/'seismic',enabled,options.get('full_hash',False),update,options)
        update(True)
        destination=output/(stem+'_EXPORTS')
        index=publish_exports(package,inventory,destination) if enabled else None
        decorate(report,{'findings':['Exact ZGY file mode; no Petrel project metadata was supplied.']},index,destination,logs.text_path,logs.events_path)
        failed=any(row['status'] in ('unavailable','conversion_failed','unsupported','missing') for row in inventory['objects'])
        logs.event('completed',success=not failed,outcomes=inventory)
        print(('Conversion failed; see report: ' if failed else 'SUCCESS: ')+str(report),flush=True)
        return not failed
    except (Exception,KeyboardInterrupt) as exc:
        logs.event('failed',error=str(exc),exception=type(exc).__name__)
        partial_report(report,{},'Seismic extraction failed',str(exc),logs.text_path)
        raise
    finally:logs.close()


class RelocateLinks(HTMLParser):
    def __init__(self,prefix):
        super().__init__(convert_charrefs=False); self.prefix=prefix; self.output=[]
    def handle_starttag(self,tag,attrs): self.tag()
    def handle_startendtag(self,tag,attrs): self.tag()
    def tag(self):
        raw=self.get_starttag_text()
        def replace(match):
            key,delimiter,value=match.groups()
            if not value or value.startswith(('#','/')) or urlsplit(value).scheme: return match.group(0)
            return key+'='+delimiter+self.prefix+value+delimiter
        self.output.append(re.sub(r'\b(href|src)=([\"\'])(.*?)\2',replace,raw))
    def handle_endtag(self,tag): self.output.append('</'+tag+'>')
    def handle_data(self,data): self.output.append(data)
    def handle_entityref(self,name): self.output.append('&'+name+';')
    def handle_charref(self,name): self.output.append('&#'+name+';')
    def handle_comment(self,data): self.output.append('<!--'+data+'-->')
    def handle_decl(self,data): self.output.append('<!'+data+'>')


def deliver_report(package_report, destination, inventory, complete=False):
    prefix=quote(package_report.parent.relative_to(destination.parent).as_posix(),safe='/')+'/'
    parser=RelocateLinks(prefix); parser.feed(package_report.read_text(encoding='utf-8')); parser.close()
    rows=[]
    for item in inventory['objects']:
        links=[]
        for key,label in [('output_file','Open SEG-Y'),('receipt_path','Conversion checks')]:
            if item.get(key):
                path=Path(item[key]); relative=path.relative_to(destination.parent).as_posix()
                links.append('<a href="'+quote(relative,safe='/')+'">'+label+'</a>')
        reason=item.get('reason','')
        if item.get('result'): reason='Every amplitude and trace geometry checked. '+item['result']['integrity_scope']+' '+item['result']['summary']['profile'].get('import_warning','')
        metadata=escape(json.dumps(item.get('metadata',{}),indent=2))
        checksum='Not calculated — full hashing disabled'
        if item.get('result',{}).get('full_seismic_hash'):
            checksum=item['result']['source_hashes_before'].get(item['source'],'Not calculated')
        elif inventory.get('full_seismic_hash'): checksum='Not calculated — no completed conversion'
        if item.get('sha256'): checksum=item['sha256']
        associations=escape(json.dumps(item.get('native_objects',[]),indent=2))
        detail='<details><summary>Source, native object associations, geometry and checksum</summary><p>'+escape(item['source'])+'</p><p>Size: '+str(item.get('file_state',{}).get('size_bytes','unavailable'))+' bytes</p><p>SHA-256: '+escape(checksum)+'</p><pre>'+associations+'</pre><pre>'+metadata+'</pre></details>'
        rows.append('<tr><td>'+escape(item['name'])+detail+'</td><td>'+escape(item['association'])+'</td><td>'+escape(item['status'])+'</td><td>'+escape(reason)+' '+ ' · '.join(links)+'</td></tr>')
    notices=''.join('<li>'+escape(x)+'</li>' for x in inventory.get('findings',[]))
    section='<section id="project-seismic"><style>#project-seismic pre{white-space:pre-wrap;overflow-wrap:anywhere}#project-seismic td{overflow-wrap:anywhere}</style><h2>Project seismic conversion</h2><p>'+('Run finished.' if complete else 'Report ready; conversion is still in progress. Refresh this page for updates.')+'</p>'
    section+='<p>Full seismic SHA-256: '+('enabled' if inventory.get('full_seismic_hash') else 'off; file-state checks and numerical conversion QC remain active')+'. Raw seismic stays at its source; it is not duplicated in the report package.</p>'
    section+='<table><thead><tr><th>Dataset</th><th>Association</th><th>Conversion</th><th>Details and output</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table><ul>'+notices+'</ul></section>'
    text=''.join(parser.output).replace('<main>','<main>'+section,1).replace('<nav>','<nav><a href="#project-seismic">Seismic conversion</a>',1)
    # Update the full tree/catalogue using stable file IDs without re-reading or
    # re-plotting seismic. Native UUID identities remain untouched.
    by_id={row['id']:row for row in inventory['objects']}
    def outcome(match):
        start,identity,body=match.group(1),match.group(2),match.group(3)
        row=by_id.get(identity)
        if row is None:return match.group(0)
        status=escape(row['status']);reason=escape(row.get('reason',''))
        start=re.sub(r'data-status="[^"]*"','data-status="'+status+'"',start)
        start=re.sub(r'data-search="([^"]*)"',lambda m:'data-search="'+m.group(1)+' '+status+' '+reason.lower()+'"',start)
        body=re.sub(r'(<span class="visual-status">)[^<]*(</span>)',lambda m:m.group(1)+status+m.group(2),body,count=1)
        links=''
        if row.get('output_file'):
            relative=Path(row['output_file']).relative_to(destination.parent).as_posix()
            links=' <a href="'+quote(relative,safe='/')+'">Open SEG-Y</a>'
        body+=('<p class="note">'+reason+links+'</p>') if reason or links else ''
        return start+body
    text=re.sub(r'(<details class="object-node" data-object-id="([^"]+)"[^>]*>)(<summary>.*?</summary><div class="tree-detail">.*?</div>)',outcome,text,flags=re.S)
    # Catalogue rows have a separate layout; update their status/search and links.
    def catalogue(match):
        start,identity,body=match.group(1),match.group(2),match.group(3)
        row=by_id.get(identity)
        if row is None:return match.group(0)
        status=escape(row['status'])
        start=re.sub(r'data-status="[^"]*"','data-status="'+status+'"',start)
        start=re.sub(r'data-search="([^"]*)"',lambda m:'data-search="'+m.group(1)+' '+status+'"',start)
        body=re.sub(r'(<span class="visual-status">)[^<]*(</span>)',lambda m:m.group(1)+status+m.group(2),body,count=1)
        if row.get('output_file'):
            relative=Path(row['output_file']).relative_to(destination.parent).as_posix()
            body=body.removesuffix('</td></tr>')+' <a href="'+quote(relative,safe='/')+'">Open SEG-Y</a></td></tr>'
        return start+body
    text=re.sub(r'(<tr data-object-id="([^"]+)"[^>]*>)(.*?</tr>)',catalogue,text,flags=re.S)
    converted=sum(row['status']=='converted' for row in inventory['objects'])
    text=re.sub(r'(<b id="decoded-catalogue-count" data-base-count="(\d+)">)[^<]+',lambda m:m.group(1)+f'{int(m.group(2))+converted:,}',text)
    states=''.join('<option value="'+escape(s)+'">'+escape(s)+'</option>' for s in sorted({r['status'] for r in inventory['objects']}))
    text=re.sub(r'(<select id="object-status"[^>]*>)',lambda m:m.group(1)+states,text)
    if section not in text: text=text.replace('</body>',section+'</body>')
    temporary=destination.with_suffix('.html.tmp'); temporary.write_text(text,encoding='utf-8'); temporary.replace(destination)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();write_json(args.output,discover(args.project_file));return 0


if __name__=='__main__':raise SystemExit(main())
