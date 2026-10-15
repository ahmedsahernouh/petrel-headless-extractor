# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Offline human view of committed catalogue/assessment metadata; no payload reads."""
from collections import Counter, defaultdict
from html import escape
import hashlib
import json
from geoviewer_io import read_up_to, contract_rows
import os
from pathlib import Path
import tempfile

from geoviewer_contract import _read, _resolve, _unlinked, digest, SCHEMA, SHARDED_SCHEMA
from geoviewer_assessment_shards import (SCHEMA as SHARDED_ASSESSMENT_SCHEMA,
                                         MAX_TOTAL_ROWS as MAX_ASSESSMENT_ROWS,
                                         verify_sharded_assessment)

PROFILES = ('source', 'opendtect', 'paleoscan')
MAX_ROWS = 1_000_000
# Detailed HTML groups diagnostics in memory. The assessment format caps all
# lists together at MAX_ASSESSMENT_ROWS physical rows; an individual diagnostic
# list may exceed the detail cap and is then linked through the bounded summary.
MAX_DETAIL_DIAGNOSTICS = MAX_ROWS
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_TABLE_BYTES = 256 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024 * 1024


class HumanReportSizeError(ValueError):
    """Only the aggregate HTML output bound exceeded during detailed rendering."""


def _tables(manifest_path, manifest):
    result = {}; total_bytes = 0
    for name in ('entities', 'artifacts', 'relationships', 'metadata_evidence', 'gaps'):
        spec = manifest['tables'][name]
        if type(spec['rows']) is not int or not 0 <= spec['rows'] <= MAX_ROWS:
            raise ValueError('Report table row bound exceeded')
        path = _resolve(manifest_path.parent, spec['path'])
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError('Report table escapes generation')
        rows = []; h = hashlib.sha256(); size = 0
        with path.open('rb') as handle:
            while line := handle.readline(MAX_ROW_BYTES + 1):
                size += len(line)
                total_bytes += len(line)
                if len(line) > MAX_ROW_BYTES or total_bytes > MAX_TABLE_BYTES or len(rows) >= MAX_ROWS:
                    raise ValueError('Report table byte/row bound exceeded')
                h.update(line); row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('Report table row must be an object')
                rows.append(row)
        if h.hexdigest() != spec['sha256'] or len(rows) != spec['rows']:
            raise ValueError('Report table identity/count mismatch: '+name)
        result[name] = rows
    return result


def _flat_tables_fit_detail_budget(manifest_path, manifest):
    """Select detailed rendering using actual flat-table sizes, not estimates.

    The bounded summary subsequently validates every table's rows and hash.
    The detailed loader retains its own byte and row guards in case a file
    changes between this size check and its read.
    """
    total_bytes = 0
    for name in ('entities', 'artifacts', 'relationships', 'metadata_evidence', 'gaps'):
        path = _resolve(manifest_path.parent, manifest['tables'][name]['path'])
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError('Report table escapes generation')
        total_bytes += path.stat().st_size
        if total_bytes > MAX_TABLE_BYTES:
            return False
    return True


def _index(rows, key):
    result = {}
    for row in rows:
        value = row[key]
        if value in result:
            raise ValueError('Duplicate report identity: '+key)
        result[value] = row
    return result


def _text(value):
    return escape(str(value) if value is not None else 'unresolved', quote=True)


def write_report(path, *, tables, assessments, generation, manifest_sha256):
    """Render already bound inputs; a summary cannot upgrade any assessment state.

    Linear grouping, bounded metadata and streamed HTML avoid a whole report
    string. No scientific files, receiving applications or external URLs are read.
    The new destination is immutable; on failure no final report is published.
    """
    path = Path(path).absolute(); _unlinked(path)
    if path.exists():
        raise ValueError('Human report already exists')
    if set(assessments) != set(PROFILES):
        raise ValueError('Human report requires all preparation profiles')
    if any(len(rows) > MAX_ROWS for rows in tables.values()):
        raise ValueError('Report row bound exceeded')
    entities = _index(tables['entities'], 'entity_key')
    artifacts = _index(tables['artifacts'], 'artifact_key')
    evaluated = {}
    for profile, report in assessments.items():
        if (report.get('generation') != generation or report.get('manifest_sha256') != manifest_sha256
                or report.get('profile') != profile):
            raise ValueError('Human report assessment binding mismatch')
        rows = _index(report['assessments'], 'entity_key')
        if set(rows) != set(entities):
            raise ValueError('Human report entity coverage mismatch')
        expected = dict(entities=len(entities), artifacts=len(artifacts),
            source_ready=sum(r['source_state'] == 'source_prerequisites_satisfied' for r in rows.values()),
            source_blocked=sum(r['source_state'] == 'source_blocked' for r in rows.values()),
            qualified_for_planned_mapping=sum(r['receiving_state'] == 'qualified_for_planned_mapping' for r in rows.values()),
            receiving_qualified=0, receiving_imports_executed=0)
        if any(type(report['counts'].get(k)) is not int or report['counts'][k] != v for k,v in expected.items()):
            raise ValueError('Human report assessment count contradiction')
        if expected['source_ready'] + expected['source_blocked'] != len(entities):
            raise ValueError('Human report contains unknown source assessment states')
        availability = _index(report['artifact_availability'],'artifact_key')
        if set(availability) != set(artifacts):
            raise ValueError('Human report artifact coverage mismatch')
        evaluated[profile] = rows
    grouped = {name: defaultdict(list) for name in ('artifacts','relationships','metadata_evidence','gaps')}
    for name, groups in grouped.items():
        field = 'source_key' if name == 'relationships' else 'entity_key'
        for row in tables[name]:
            key = row.get(field)
            if key not in entities:
                raise ValueError('Human report row has unknown entity')
            if name == 'relationships' and row.get('target_key') not in entities:
                raise ValueError('Human report relationship target is absent')
            if name == 'artifacts':
                owners = row.get('owner_keys',[key])
                if (not owners or len(owners)!=len(set(owners)) or key not in owners
                        or any(owner not in entities for owner in owners)):
                    raise ValueError('Human report artifact has invalid owners')
                for owner in owners:groups[owner].append(row)
            else:groups[key].append(row)
    availability_by_profile = {p:_index(r['artifact_availability'],'artifact_key') for p,r in assessments.items()}
    for rows in evaluated.values():
        if any(rows[key].get('artifact_count') != len(grouped['artifacts'][key]) for key in entities):
            raise ValueError('Human report artifact ownership count contradiction')
        for key,row in rows.items():
            preferred=row.get('preferred_artifact_key')
            if preferred is not None and preferred not in {a['artifact_key'] for a in grouped['artifacts'][key]}:
                raise ValueError('Human report preferred artifact is not owned by its entity')
    diagnostics = defaultdict(list); global_diagnostics = []
    for profile, report in assessments.items():
        if len(report['diagnostics']) > MAX_DETAIL_DIAGNOSTICS:
            raise ValueError('Report diagnostic row bound exceeded')
        for row in report['diagnostics']:
            if row.get('entity_key') is None:
                global_diagnostics.append((profile,row))
            elif row['entity_key'] not in entities:
                raise ValueError('Human report diagnostic has unknown entity')
            else:
                diagnostics[row['entity_key']].append((profile,row))
    shared = {}
    for key,rows in diagnostics.items():
        for profile,row in rows:
            marker=tuple(str(row.get(k) or '') for k in ('code','field_or_link','reason','next_action'))
            item=shared.setdefault(marker,dict(row=row,entities=set(),profiles=set(),severities=set()))
            item['entities'].add(key);item['profiles'].add(profile);item['severities'].add(str(row.get('severity') or 'unresolved'))
    ranked=sorted(shared.items(),key=lambda pair:(-len(pair[1]['entities']),pair[0]))
    lane_counts = Counter(n.get('source_lane','unknown') for n in entities.values())
    categories = defaultdict(list)
    for key,node in entities.items():
        categories[(str(node.get('category') or 'unresolved'),str(node.get('source_lane') or 'unknown'))].append(key)
    outputs = []; total_size = 0; published = []
    def open_output(final):
        _unlinked(final)
        if final.exists():raise ValueError('Human report detail already exists')
        handle=tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,prefix='preparation_',suffix='.tmp',delete=False)
        row=dict(final=final,pending=Path(handle.name),handle=handle,size=0,hash=hashlib.sha256())
        outputs.append(row);return row
    main=open_output(path); current=main; part=None
    def write(text):
        nonlocal total_size
        raw = text.encode('utf-8'); total_size += len(raw);current['size'] += len(raw)
        if total_size > MAX_REPORT_BYTES:
            raise HumanReportSizeError('Human report exceeds explicit output byte bound; no rows silently omitted')
        current['hash'].update(raw); current['handle'].write(raw)
    def detail(title, rows):
        if not rows: return
        write('<details><summary>'+_text(title)+' ('+str(len(rows))+')</summary>')
        for row in rows:
            encoded = json.dumps(row, ensure_ascii=False, allow_nan=False, indent=2)
            if len(encoded.encode('utf-8')) > MAX_ROW_BYTES * 4:
                raise ValueError('Human report detail row exceeds byte bound')
            write('<pre>'+escape(encoded)+'</pre>')
        write('</details>')
    try:
        write('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Field Viewer · Preparation evidence</title>
<style>body{font:15px system-ui;color:#172638;background:#f5f8fc;margin:24px auto;padding:0 20px;max-width:1500px}h1{font-size:28px}table{width:100%;border-collapse:collapse;background:white}td,th{border:1px solid #d5dce6;padding:9px;text-align:left;vertical-align:top}th{background:#e5edf7}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:7px 0}pre{max-height:280px;overflow:auto;background:#eef2f7;padding:10px}input,button{font:inherit;padding:8px}input{width:min(90%,650px)}small{display:block;overflow-wrap:anywhere}.scroll{overflow:auto}tr[hidden]{display:none}.note{border-left:4px solid #9c6816;background:#fff5de;padding:12px}#objects td:first-child{min-width:180px}nav{padding:10px 0}button{margin:4px}</style></head><body><h1>Preparation evidence</h1>
<p class="note">Source recovery, available files and preparation for a planned mapping are separate states. No OpendTect or PaleoScan import was executed. Physical meaning, units and coordinate references remain unresolved wherever the evidence says so.</p>''')
        write('<p>Generation <code>'+_text(generation)+'</code><br>Manifest SHA-256 <code>'+_text(manifest_sha256)+'</code></p>')
        write('<p><b>'+str(len(entities))+' catalogue entities · '+str(len(artifacts))+' distinct artifacts.</b> Entities include native subjects, referenced objects and role-qualified records; these are not counts of decoded volumes or independent datasets.</p>')
        write('<p>Entity lanes: '+_text(dict(sorted(lane_counts.items())))+'</p>')
        write('<table id="profile-counts"><thead><tr><th>Profile</th><th>Source prerequisites satisfied</th><th>Source blocked</th><th>Prepared for specified mapping</th><th>Receiving runtime qualified</th><th>Imports executed</th></tr></thead><tbody>')
        for profile in PROFILES:
            c = assessments[profile]['counts']
            write('<tr><th>'+_text(profile)+'</th>'+''.join('<td>'+str(c[k])+'</td>' for k in ('source_ready','source_blocked','qualified_for_planned_mapping','receiving_qualified','receiving_imports_executed'))+'</tr>')
        write('</tbody></table><p>Assessment integrity scope: '+_text(assessments['source'].get('integrity_policy'))+'</p>')
        write('<details><summary>Counts by category and source lane</summary><p>Artifacts are distinct within each row. A shared artifact can belong to more than one category; do not sum these rows as a global file count. Preparation counts are planned mappings, not imports.</p><div class="scroll"><table id="category-counts"><thead><tr><th>Category</th><th>Source lane</th><th>Entities</th><th>Owned artifacts</th><th>Source ready</th><th>OpendTect prepared</th><th>PaleoScan prepared</th></tr></thead><tbody>')
        for (category,lane),keys in sorted(categories.items()):
            count_files=len({a['artifact_key'] for key in keys for a in grouped['artifacts'][key]})
            ready=sum(evaluated['source'][key]['source_state']=='source_prerequisites_satisfied' for key in keys)
            prepared=[sum(evaluated[p][key]['receiving_state']=='qualified_for_planned_mapping' for key in keys) for p in ('opendtect','paleoscan')]
            write('<tr><th>'+_text(category)+'</th><td>'+_text(lane)+'</td>'+''.join('<td>'+str(n)+'</td>' for n in (len(keys),count_files,ready,*prepared))+'</tr>')
        write('</tbody></table></div></details>')
        for profile in PROFILES:
            name='validation_'+profile+'.json'
            if (path.parent/name).is_file():write('<p><a href="'+name+'">Complete '+profile+' assessment JSON</a></p>')
        detail('Global diagnostics', [dict(profile=p,diagnostic=r) for p,r in global_diagnostics])
        if ranked:
            blockers=open_output(path.with_name(path.stem+'.blockers.html'))
            write('<details><summary>Shared diagnostics and affected populations</summary><p>Review order uses distinct affected-entity counts. Each shared diagnostic is counted once across profiles. Resolving one item may leave other blockers; these counts do not predict independently qualified entities.</p><div class="scroll"><table id="shared-diagnostics"><thead><tr><th>Code / field</th><th>Severity</th><th>Affected entities</th><th>Exact population and next check</th></tr></thead><tbody>')
            current=blockers
            write('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Shared preparation diagnostics</title><style>body{font:15px system-ui;max-width:1200px;margin:24px auto;padding:16px}code,li{overflow-wrap:anywhere}details{margin:12px 0}</style></head><body><a href="'+_text(path.name)+'">Back to preparation evidence</a><h1>Shared diagnostics</h1><p>Generation '+_text(generation)+'. Exact affected populations; one resolved diagnostic does not establish full qualification.</p>')
            ordinals={key:n for n,key in enumerate(entities)}
            for n,(_,item) in enumerate(ranked):
                row=item['row'];current=main
                write('<tr><td>'+_text(row.get('code'))+'<br>'+_text(row.get('field_or_link'))+'</td><td>'+_text(', '.join(sorted(item['severities'])))+'</td><td>'+str(len(item['entities']))+'</td><td><a href="'+_text(blockers['final'].name)+'#diagnostic-'+str(n)+'">Evidence and affected objects</a></td></tr>')
                current=blockers
                write('<section id="diagnostic-'+str(n)+'"><h2>'+_text(row.get('code'))+'</h2><p>'+_text(row.get('reason'))+'</p><p>Field/link: '+_text(row.get('field_or_link'))+' · Profiles: '+_text(', '.join(sorted(item['profiles'])))+'</p><p>Next: '+_text(row.get('next_action'))+'</p><details><summary>'+str(len(item['entities']))+' affected entities</summary><ul>')
                for key in sorted(item['entities'],key=ordinals.get):
                    ordinal=ordinals[key];node=entities[key];name=path.stem+f'.details_{ordinal//50+1:04d}.html'
                    write('<li><a href="'+_text(name)+'#object-'+str(ordinal)+'">'+_text(node.get('name'))+'</a> · '+_text(node.get('source_id'))+' · '+_text(key)+'</li>')
                write('</ul></details></section>')
            write('</body></html>');current=main;write('</tbody></table></div></details>')
        write('''<h2>Objects, files and blockers</h2><p>Filter names, native IDs, categories, states or blocker codes. Open an evidence link for exact field evidence and next checks. File checks are recorded assessment results; this page does not recheck payloads.</p>
<input id="query" aria-label="Search preparation evidence" placeholder="Search objects, IDs or blockers"><nav><button id="previous">Previous</button><span id="page-status" aria-live="polite"></span><button id="next">Next</button></nav>
<div class="scroll"><table id="objects"><thead><tr><th>Object / native identity</th><th>Source and delivery states</th><th>Preparation and unresolved information</th></tr></thead><tbody>''')
        for ordinal, (key,node) in enumerate(contract_rows(entities.items(), "human_report_rows", generation=generation)):
            src=evaluated['source'][key]; group=grouped['artifacts'][key]
            if ordinal % 50 == 0:
                if part:
                    current=part;write('</tbody></table></body></html>');part['handle'].flush();os.fsync(part['handle'].fileno());part['handle'].close()
                part=open_output(path.with_name(path.stem+f'.details_{ordinal//50+1:04d}.html'));current=part
                write('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Preparation evidence details</title><style>body{font:15px system-ui;max-width:1400px;margin:24px auto;padding:16px;color:#172638}td{vertical-align:top;border:1px solid #ccd;padding:12px}table{width:100%;border-collapse:collapse}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto}details{margin:10px 0}small{display:block;overflow-wrap:anywhere}</style></head><body><p><a href="'+_text(path.name)+'">Back to searchable preparation evidence</a></p><p>Generation '+_text(generation)+'. These are recorded declarations and assessments, not new payload or import checks.</p><table><tbody>')
            current=main
            codes=sorted({str(d.get('code')) for _,d in diagnostics[key]})
            write('<tr class="entity"'+(' hidden' if ordinal >= 50 else '')+'><td><b>'+_text(node.get('name'))+'</b><small>'+_text(node.get('source_id'))+'</small><small>'+_text(node.get('category'))+' · '+_text(node.get('source_lane'))+'</small></td><td>'+_text(src['source_state'])+'<br>'+str(len(group))+' owned artifact records<br>'+_text(src.get('state_axes',{}).get('publication'))+'</td><td>')
            for profile in ('opendtect','paleoscan'):write(_text(profile)+': '+_text(evaluated[profile][key]['receiving_state'])+'<br>')
            write(_text('; '.join(codes))+'<p><a href="'+_text(part['final'].name)+'#object-'+str(ordinal)+'">Reasons, next checks and complete source evidence</a></p></td></tr>')
            current=part
            write('<tr id="object-'+str(ordinal)+'"><td><b>'+_text(node.get('name'))+'</b><small>'+_text(node.get('source_id'))+'</small><small>'+_text(node.get('category'))+' · '+_text(node.get('source_lane'))+'</small>')
            detail('Catalogue identity', [node]); write('</td><td>')
            write(_text(src['source_state'])+'<br>'+str(len(group))+' owned artifact records')
            preferred=src.get('preferred_artifact_key')
            if preferred:
                detail('Preferred full payload (recorded assessment)', [dict(artifact=artifacts[preferred],availability=availability_by_profile['source'][preferred])])
            else:write('<p>No preferred available full payload is recorded.</p>')
            detail('Recovery / availability / QC scope', [src.get('state_axes',{})])
            detail('Artifacts (recorded paths; no guessed relocation)', [dict(artifact=a,
                assessed_availability={p:rows[a['artifact_key']] for p,rows in availability_by_profile.items()}) for a in group])
            write('</td><td>')
            for profile in ('opendtect','paleoscan'):
                a=evaluated[profile][key]
                write('<b>'+_text(profile)+':</b> '+_text(a['receiving_state'])+'<br>')
                mapping=a.get('receiving_assessment')
                if mapping:
                    # The same effective source dictionary is repeated by every
                    # profile. Show source evidence once below; explicit mapping
                    # decisions retain their complete effective field values.
                    summary={k:v for k,v in mapping.items() if k!='effective_source_fields' or mapping.get('decision_provenance')}
                    detail(profile+' mapping assessment (source evidence below)', [summary])
            seen=set(); write('<ul>')
            for profile,d in diagnostics[key]:
                marker=(d.get('code'),d.get('field_or_link'),d.get('reason'),d.get('next_action'))
                if marker in seen: continue
                seen.add(marker)
                write('<li><b>'+_text(d.get('code'))+'</b> — '+_text(d.get('reason')))
                write('<small>Field/link: '+_text(d.get('field_or_link'))+' · Severity: '+_text(d.get('severity'))+'</small>')
                if d.get('next_action'):write('<br>Next: '+_text(d['next_action']))
                write('</li>')
            write('</ul>')
            detail('Recorded unresolved metadata', grouped['gaps'][key])
            detail('Source field evidence (not physical interpretation)', grouped['metadata_evidence'][key])
            detail('Relationships and resolution scope', grouped['relationships'][key])
            write('</td></tr>')
        if part:
            current=part;write('</tbody></table></body></html>')
        current=main
        if not entities:write('<tr><td colspan="3">No catalogue entities were recorded. Empty inventory is not proof of complete native recovery.</td></tr>')
        write('''</tbody></table></div><script>
const rows=Array.from(document.querySelectorAll('#objects tr.entity'));
const searchable=rows.map(r=>r.textContent.toLowerCase());let selected=rows.map((_,i)=>i),page=0;const size=50;
function draw(){rows.forEach(r=>r.hidden=true);const pages=Math.max(1,Math.ceil(selected.length/size));page=Math.max(0,Math.min(page,pages-1));selected.slice(page*size,(page+1)*size).forEach(i=>rows[i].hidden=false);document.getElementById('page-status').textContent=` ${selected.length} matching entities · page ${page+1} of ${pages} `;document.getElementById('previous').disabled=page===0;document.getElementById('next').disabled=page+1>=pages;}
let timer;document.getElementById('query').addEventListener('input',e=>{clearTimeout(timer);timer=setTimeout(()=>{const q=e.target.value.toLowerCase();selected=[];searchable.forEach((s,i)=>{if(s.includes(q))selected.push(i)});page=0;draw()},120)});
document.getElementById('previous').onclick=()=>{page--;draw()};document.getElementById('next').onclick=()=>{page++;draw()};draw();
</script><noscript><style>tr.entity[hidden]{display:table-row}</style>Search and pagination require JavaScript; all rows remain visible here.</noscript></body></html>''')
        for row in outputs:
            handle=row['handle']
            if not handle.closed:handle.flush();os.fsync(handle.fileno());handle.close()
        # Publish the index last so it never points to a partially written set.
        for row in [*outputs[1:],main]:
            _unlinked(row['final'])
            if row['final'].exists():raise ValueError('Human report destination appeared during generation')
            row['pending'].rename(row['final']);published.append(row['final'])
    except BaseException:
        for final in published:final.unlink(missing_ok=True)
        raise
    finally:
        for row in outputs:
            row['handle'].close();row['pending'].unlink(missing_ok=True)
    return dict(path=str(path),sha256=main['hash'].hexdigest(),bytes=main['size'],generation=generation,
        manifest_sha256=manifest_sha256,entities=len(entities),artifacts=len(artifacts),
        details=[dict(filename=row['final'].name,sha256=row['hash'].hexdigest(),bytes=row['size']) for row in outputs[1:]],
        total_bytes=total_size,
        scope='Human rendering of bound catalogue and recorded assessments; no fresh payload or receiving-runtime validation')


def publish_human_report(path, manifest_path, assessments, generation, manifest_sha256):
    manifest_path=Path(manifest_path);_unlinked(manifest_path)
    with manifest_path.open('rb') as f:raw=read_up_to(f, MAX_ROW_BYTES+1)
    if len(raw)>MAX_ROW_BYTES or digest(raw)!=manifest_sha256:
        raise ValueError('Human report manifest binding failed')
    manifest=json.loads(raw)
    if manifest['generation']!=generation:raise ValueError('Human report generation mismatch')
    schema = manifest.get('schema')
    if schema == SHARDED_SCHEMA:
        return _publish_bounded_summary(Path(path), manifest_path, manifest, assessments,
                                        generation, manifest_sha256)
    if schema != SCHEMA:
        raise ValueError('Unsupported human report contract schema')
    if set(assessments) == set(PROFILES) and any(
           len(assessments[profile].get('diagnostics', [])) > MAX_DETAIL_DIAGNOSTICS
           for profile in PROFILES):
        return _publish_bounded_summary(Path(path), manifest_path, manifest, assessments,
                                        generation, manifest_sha256)
    if not _flat_tables_fit_detail_budget(manifest_path, manifest):
        return _publish_bounded_summary(Path(path), manifest_path, manifest, assessments,
                                        generation, manifest_sha256)
    tables = _tables(manifest_path, manifest)
    try:
        return write_report(path,tables=tables,assessments=assessments,
            generation=generation,manifest_sha256=manifest_sha256)
    except HumanReportSizeError:
        # write_report has already checked all row references, ownership and
        # assessment states, and has removed every unpublished detail page.
        pass
    # Leave the exception handler before rereading source files so its traceback
    # cannot retain the large detailed-render buffers during summary validation.
    del tables
    return _publish_bounded_summary(Path(path), manifest_path, manifest, assessments,
                                    generation, manifest_sha256)


def _summary_reference_checks(manifest_path, manifest, identities, assessed_by_profile, assessments):
    """Keep the detailed writer's ownership and reference guards in summary mode."""
    entities = identities['entities']; artifacts = identities['artifacts']
    owned = defaultdict(set)
    for key, artifact in artifacts.items():
        entity_key = artifact.get('entity_key')
        owners = artifact.get('owner_keys', [entity_key])
        if (entity_key not in entities or not isinstance(owners, list) or not owners
                or len(owners) != len(set(owners)) or entity_key not in owners
                or any(owner not in entities for owner in owners)):
            raise ValueError('Summary artifact has invalid owners')
        for owner in owners:
            owned[owner].add(key)
    for rows in assessed_by_profile.values():
        for key, row in rows.items():
            if row.get('artifact_count') != len(owned[key]):
                raise ValueError('Summary artifact ownership count contradiction')
            preferred = row.get('preferred_artifact_key')
            if preferred is not None and preferred not in owned[key]:
                raise ValueError('Summary preferred artifact is not owned by entity')
    for report in assessments.values():
        diagnostics = report.get('diagnostics', [])
        if len(diagnostics) > MAX_ASSESSMENT_ROWS:
            raise ValueError('Report diagnostic row bound exceeded')
        if any(row.get('entity_key') is not None and row['entity_key'] not in entities
               for row in diagnostics):
            raise ValueError('Summary diagnostic has unknown entity')
    for name in ('relationships', 'metadata_evidence', 'gaps'):
        spec = manifest['tables'][name]
        aggregate = hashlib.sha256(); aggregate_rows = aggregate_bytes = 0
        for part in spec.get('shards', [spec]):
            target = _resolve(manifest_path.parent, part['path'])
            if not target.is_relative_to(manifest_path.parent):
                raise ValueError('Summary table escapes generation')
            part_hash = hashlib.sha256(); part_rows = part_bytes = 0
            with target.open('rb') as stream:
                while line := stream.readline(MAX_ROW_BYTES + 1):
                    part_rows += 1; part_bytes += len(line)
                    if (len(line) > MAX_ROW_BYTES or part_rows > MAX_ROWS
                            or part_bytes > MAX_TABLE_BYTES or not line.endswith(b'\n')):
                        raise ValueError('Summary reference table byte/row bound exceeded')
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('Summary reference row must be an object')
                    owner = row.get('source_key') if name == 'relationships' else row.get('entity_key')
                    if owner not in entities or name == 'relationships' and row.get('target_key') not in entities:
                        raise ValueError('Summary row has unknown entity reference')
                    part_hash.update(line); aggregate.update(line)
            if part_rows != part['rows'] or part_hash.hexdigest() != part['sha256']:
                raise ValueError('Summary reference table identity/count mismatch')
            if 'bytes' in part and part_bytes != part['bytes']:
                raise ValueError('Summary reference shard byte mismatch')
            aggregate_rows += part_rows; aggregate_bytes += part_bytes
        if (aggregate_rows != spec['rows'] or 'shards' in spec and
                (aggregate_bytes != spec['bytes'] or aggregate.hexdigest() != spec['sha256'])):
            raise ValueError('Summary reference aggregate identity mismatch')


def _publish_bounded_summary(path, manifest_path, manifest, assessments, generation, manifest_sha256):
    """Bounded display of a verified flat or sharded generation; full evidence stays linked."""
    from validate_geoviewer_contract import _table_rows

    schema = manifest.get('schema')
    if schema not in (SCHEMA, SHARDED_SCHEMA):
        raise ValueError('Unsupported summary contract schema')
    if set(manifest.get('tables', {})) != {'entities', 'artifacts', 'relationships', 'metadata_evidence', 'gaps'}:
        raise ValueError('Summary contract table set mismatch')
    if set(assessments) != set(PROFILES):
        raise ValueError('Summary requires all preparation profiles')
    identities = {}
    for name, spec in manifest['tables'].items():
        rows, _, _ = _table_rows(manifest_path.parent, name, spec, schema,
                                 collect=name in ('entities', 'artifacts'))
        if name in ('entities', 'artifacts'):
            identities[name] = _index(rows, 'entity_key' if name == 'entities' else 'artifact_key')
    entity_count = len(identities['entities'])
    artifact_count = len(identities['artifacts'])
    assessed_by_profile = {}
    for profile, report in assessments.items():
        if (report.get('generation') != generation or report.get('manifest_sha256') != manifest_sha256
                or report.get('profile') != profile):
            raise ValueError('Summary assessment binding/count mismatch')
        assessed = _index(report.get('assessments', []), 'entity_key')
        assessed_by_profile[profile] = assessed
        availability = _index(report.get('artifact_availability', []), 'artifact_key')
        if set(assessed) != set(identities['entities']) or set(availability) != set(identities['artifacts']):
            raise ValueError('Summary assessment entity/artifact coverage mismatch')
        counts = report['counts']
        expected = dict(entities=entity_count, artifacts=artifact_count,
                        source_ready=sum(row.get('source_state') == 'source_prerequisites_satisfied' for row in assessed.values()),
                        source_blocked=sum(row.get('source_state') == 'source_blocked' for row in assessed.values()),
                        qualified_for_planned_mapping=sum(row.get('receiving_state') == 'qualified_for_planned_mapping' for row in assessed.values()),
                        receiving_qualified=0, receiving_imports_executed=0)
        if (expected['source_ready'] + expected['source_blocked'] != entity_count
                or any(type(counts.get(key)) is not int or counts[key] != value for key, value in expected.items())):
            raise ValueError('Summary assessment counts contradict source catalogue')
    _summary_reference_checks(manifest_path, manifest, identities, assessed_by_profile, assessments)
    lines = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Field Viewer preparation evidence</title>',
             '<style>body{font:16px system-ui;max-width:1100px;margin:24px auto;padding:20px;color:#172638}table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:8px;text-align:left}code{overflow-wrap:anywhere}li{margin:8px}</style></head><body>',
             '<h1>Preparation evidence</h1>',
             '<p>Generation <code>'+_text(generation)+'</code>; manifest SHA-256 <code>'+_text(manifest_sha256)+'</code>.</p>',
             '<p><b>'+str(entity_count)+' catalogue entities and '+str(artifact_count)+' distinct artifacts.</b> These are not counts of decoded datasets or imports.</p>',
             '<p>This page is a bounded summary because detailed HTML, flat-table loading or diagnostic grouping exceeded its explicit display limit, or the contract uses shards. Every source evidence row remains in the hash-verified contract tables and complete assessments linked below. Detailed metadata and diagnostics are not fully rendered in HTML. No receiving import was executed.</p>',
             '<table><thead><tr><th>Profile</th><th>Source ready</th><th>Source blocked</th><th>Prepared for mapping</th><th>Receiving qualified</th><th>Imports executed</th></tr></thead><tbody>']
    for profile in PROFILES:
        counts = assessments[profile]['counts']
        lines.append('<tr><th>'+_text(profile)+'</th>'+''.join('<td>'+str(counts[key])+'</td>' for key in
                     ('source_ready','source_blocked','qualified_for_planned_mapping','receiving_qualified','receiving_imports_executed'))+'</tr>')
    lines.append('</tbody></table>')
    for profile in PROFILES:
        name = 'validation_' + profile + '.json'
        assessment_path = path.parent / name
        assessment_manifest, _ = _read(assessment_path)
        if (assessment_manifest.get('generation') != generation or
                assessment_manifest.get('manifest_sha256') != manifest_sha256 or
                assessment_manifest.get('profile') != profile or
                assessment_manifest.get('counts') != assessments[profile]['counts']):
            raise ValueError('Summary linked assessment binding mismatch: '+profile)
        if assessment_manifest.get('schema') == SHARDED_ASSESSMENT_SCHEMA:
            verify_sharded_assessment(assessment_path, assessment_manifest,
                                      entities=set(identities['entities']),
                                      artifacts=set(identities['artifacts']))
        elif assessment_manifest != assessments[profile]:
            raise ValueError('Summary linked assessment content mismatch: '+profile)
        lines.append('<p><a href="'+_text(name)+'">Complete '+_text(profile)+' assessment JSON</a></p>')
        if assessment_manifest.get('schema') == SHARDED_ASSESSMENT_SCHEMA:
            lines.append('<details><summary>'+_text(profile)+' complete assessment members</summary><ul>')
            for field in assessment_manifest['list_order']:
                for part in assessment_manifest['lists'][field]['members']:
                    member = _resolve(path.parent, part['path'])
                    if not member.is_relative_to(path.parent):
                        raise ValueError('Assessment member link escapes report folder')
                    lines.append('<li><a href="'+_text(part['path'])+'">'+_text(field)+' '+
                                 str(part['index'])+'</a> · '+str(part['rows'])+' rows · SHA-256 <code>'+
                                 _text(part['sha256'])+'</code></li>')
            lines.append('</ul></details>')
    lines.append('<h2>Complete contract tables</h2><ul>')
    for name, spec in manifest['tables'].items():
        parts = spec['shards'] if 'shards' in spec else [spec]
        lines.append('<li>'+_text(name)+': '+str(spec['rows'])+' rows; '+str(len(parts))+' verified file(s).<ul>')
        for part in parts:
            target = _resolve(manifest_path.parent, part['path'])
            href = os.path.relpath(target, path.parent).replace('\\', '/')
            lines.append('<li><a href="'+_text(href)+'">'+_text(part['path'])+'</a> · SHA-256 <code>'+_text(part['sha256'])+'</code> · '+str(part['rows'])+' rows</li>')
        lines.append('</ul></li>')
    lines.append('</ul><p>Source and receiving assessments are separately hash-bound to this generation. Availability and scientific interpretation remain subject to their recorded limits.</p></body></html>')
    raw = ''.join(lines).encode('utf-8')
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('Summary report exceeds explicit output byte bound')
    path = Path(path).absolute(); _unlinked(path)
    if path.exists():
        raise ValueError('Human report already exists')
    with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='preparation_', suffix='.tmp', delete=False) as stream:
        pending = Path(stream.name)
        try:
            if stream.write(raw) != len(raw):
                raise OSError('Incomplete summary report write')
            stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        _unlinked(path)
        if path.exists():
            raise ValueError('Human report destination appeared during generation')
        pending.rename(path)
    finally:
        pending.unlink(missing_ok=True)
    return dict(path=str(path), sha256=digest(raw), bytes=len(raw), generation=generation,
                manifest_sha256=manifest_sha256, entities=entity_count, artifacts=artifact_count,
                details=[], total_bytes=len(raw),
                scope='Bounded summary of hash-verified contract; all metadata rows remain in linked tables; no receiving execution')
