"""Offline figures and object catalogue from recovered Petrel evidence.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor

Inputs: an existing extraction package, decoder receipts and numeric CSV files.
Outputs: new PNG/SVG figures, their hashes and a portable HTML report section.
No Petrel/Ocean, external map tiles, source writes or conversion claims. Plot
failures remain per-object findings. Unknown units/CRS stay explicitly unknown.
"""
from __future__ import annotations

import base64
from collections import Counter
import csv
import hashlib
import html
import json
import math
from pathlib import Path
import re
import uuid
import shutil
import tempfile
from contextlib import redirect_stdout
import io

import numpy as np

MAX_CSV_BYTES = 128 * 1024 * 1024
MAX_ROWS = 2_000_000
MAX_LOG_FIGURES = 64
MAX_SURFACE_FIGURES = 36
MAX_SEISMIC_FIGURES = 3
MAX_PLOT_POINTS = 6000


def esc(value):
    return html.escape(str(value), quote=True)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def contained(package, relative):
    path = (package / relative).resolve()
    if not path.is_relative_to(package.resolve()) or '.partial' in str(relative):
        raise ValueError('Artifact is outside the completed extraction package')
    if not path.is_file():
        raise ValueError('Artifact file is missing')
    return path


def load_json(path):
    if not path.is_file():
        return {}
    with path.open(encoding='utf-8-sig') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('Expected an object receipt')
    return value


def stats(values):
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not len(values):
        return dict(valid_count=0, minimum=None, maximum=None, mean=None, std=None)
    return dict(valid_count=int(len(values)), minimum=float(values.min()),
                maximum=float(values.max()), mean=float(values.mean()), std=float(values.std()))


def sample_indices(count, limit=MAX_PLOT_POINTS):
    return np.linspace(0, count-1, min(count, limit), dtype=int) if count else np.array([], dtype=int)


def numeric_csv(path, fields):
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError('Preview CSV exceeds 128 MiB; original data remains linked')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        if next(csv.reader(stream), []) != fields:
            raise ValueError('Unexpected numeric CSV columns')
    data = np.loadtxt(path, delimiter=',', skiprows=1, ndmin=2, max_rows=MAX_ROWS+1)
    if data.shape[1] != len(fields) or len(data) > MAX_ROWS:
        raise ValueError('Preview row limit or schema mismatch')
    return data


class Figures:
    def __init__(self, package):
        # Agg writes images without opening a desktop window. Dependencies and
        # font files are shipped in the offline runtime, not loaded from a CDN.
        import matplotlib
        matplotlib.use('Agg', force=True)
        from matplotlib import pyplot as plt
        self.plt = plt
        plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9,
                             'axes.spines.top': False, 'axes.spines.right': False,
                             'axes.labelcolor': '#395260', 'text.color': '#183642',
                             'axes.edgecolor': '#b9cbd2', 'svg.fonttype': 'none',
                             'text.parse_math': False})
        self.package = package
        self.directory = package / '07_workflows_reports' / 'visuals' / uuid.uuid4().hex[:8]
        self.directory.mkdir(parents=True, exist_ok=False)
        self.items = []

    def save(self, fig, name, group, caption, source='', object_id=''):
        index = len(self.items)
        stem = f'{index:03d}_' + re.sub(r'[^A-Za-z0-9_-]', '_', name)[:36]
        paths = {}
        for suffix in ('png', 'svg'):
            path = self.directory / (stem+'.'+suffix)
            fig.savefig(path, dpi=110, facecolor='white', bbox_inches='tight')
            paths[suffix] = path.relative_to(self.package).as_posix()
        self.plt.close(fig)
        png = self.package / paths['png']
        item = dict(name=name, group=group, caption=caption, source=source,
                    object_id=object_id, **paths,
                    sha256={key: sha(self.package/value) for key, value in paths.items()},
                    data_uri='data:image/png;base64,'+base64.b64encode(png.read_bytes()).decode('ascii'))
        self.items.append(item)
        return index


def recovery_charts(audit, figures):
    native = audit['native_inventory']
    counts = native.get('decoded_object_type_counts', {})
    inventory = native.get('registry_by_type', {})
    kinds = ['Polygons3', 'Points3', 'FloatWellLog', 'IntWellLog', 'RegValGrid2',
             'ValGrid2', 'FaultInterpretation', 'PillarGrid2', 'FloatProperty', 'IntProperty']
    kinds = [k for k in kinds if k in inventory or counts.get(k)]
    if kinds:
        decoded = [counts.get(k, 0) for k in kinds]
        remaining = [max(0, inventory.get(k, {}).get('unique_object_ids', 0)-n) for k,n in zip(kinds, decoded)]
        fig, ax = figures.plt.subplots(figsize=(10, max(3.5, len(kinds)*.42)), layout='constrained')
        ax.barh(kinds, decoded, color='#087f8c', label='Decoded objects')
        ax.barh(kinds, remaining, left=decoded, color='#dce5eb', label='Other registry IDs')
        for i, (a,b) in enumerate(zip(decoded, remaining)):
            denominator = f'{inventory[kinds[i]]["unique_object_ids"]:,}' if kinds[i] in inventory else '?'
            ax.text(a+b+max(decoded+remaining+[1])*.012, i, f'{a:,} / {denominator}', va='center', fontsize=8)
        ax.invert_yaxis(); ax.set_xlabel('Object IDs'); ax.set_title('Native recovery by category', loc='left', weight='bold')
        ax.margins(x=.18); ax.legend(loc='lower right', fontsize=8)
        figures.save(fig, 'Native recovery', 'Overview',
                     'Selected categories: decoded / registry IDs; ? means no registry denominator. Other IDs can be unsupported, empty, unresolved or unattempted. Counts do not establish scientific acceptance.')
    states = native.get('native_recovery_status_counts', {})
    if states:
        fig, ax = figures.plt.subplots(figsize=(8, 3.6), layout='constrained')
        labels = [k.replace('_', ' ') for k in states]
        ax.barh(labels, list(states.values()), color=['#087f8c' if k=='decoded' else '#d4973b' for k in states])
        ax.set_xlabel('Objects in the log/surface decoder receipt'); ax.invert_yaxis()
        ax.set_title('Log and surface recovery outcomes', loc='left', weight='bold')
        for i,v in enumerate(states.values()): ax.text(v, i, f'  {v:,}', va='center')
        ax.margins(x=.15)
        figures.save(fig, 'Recovery outcomes', 'Overview', 'Receipt counts for attempted log/surface objects only; this is not a whole-project completion percentage.')


def read_artifacts(package, item):
    links = []
    for artifact in item.get('artifacts', []):
        relative = 'native_data/'+artifact['path']
        path = contained(package, relative)
        links.append(dict(label=path.name, path=relative, sha256=artifact.get('sha256', '')))
    return links


def plot_native(package, item, record, figures):
    kind = item.get('blob_type', '')
    is_log = kind.endswith('WellLog')
    target = 'samples.csv' if is_log else 'nodes.csv'
    artifact = next(a for a in record['links'] if a['label']==target)
    path = contained(package, artifact['path'])
    if not artifact['sha256'] or sha(path) != artifact['sha256']:
        raise ValueError('CSV hash differs from decoder receipt')
    before = path.stat()
    name = item.get('name') or item['object_id'][:8]
    unit = item.get('unit') or 'unknown unit'
    if is_log:
        data = numeric_csv(path, ['sample_index', 'md', 'raw_value', 'is_null'])
        valid = (data[:,3]==0) & np.isfinite(data[:,1]) & np.isfinite(data[:,2])
        x, value = data[valid,1], data[valid,2]
        record.update(stats(value), rows=int(len(data)), null_count=int((~valid).sum()),
                      depth_range=[float(x.min()), float(x.max())] if len(x) else [])
        if not len(x): raise ValueError('No valid finite samples to plot')
        idx = sample_indices(len(x), 2500)
        fig, (ax, hist) = figures.plt.subplots(1, 2, figsize=(9, 5.2), width_ratios=[1.1, 1], layout='constrained')
        ax.scatter(value[idx], x[idx], s=3, color='#087f8c', linewidths=0)
        ax.invert_yaxis(); ax.set_xlabel(f'{name} [{unit}]'); ax.set_ylabel('Measured depth ['+(item.get('depth_unit') or 'unknown')+']')
        ax.grid(alpha=.18); hist.hist(value, bins=min(40, max(1, int(math.sqrt(len(value))))), color='#d49b46')
        hist.set_xlabel(f'Native value [{unit}]'); hist.set_ylabel('Valid sample / boundary records')
        ax.ticklabel_format(style='plain', useOffset=False); hist.ticklabel_format(style='plain',useOffset=False)
        fig.suptitle((item.get('well') or 'Unresolved well')+' | '+name, fontsize=13, weight='bold')
        caption = f'{len(value):,} valid of {len(data):,} records; {len(idx):,} plotted points. Statistics/histogram use all valid records. No interpolation.'
        if kind=='IntWellLog': caption += ' Categorical codes: labels and interval direction remain unresolved; histogram counts boundary records, not thickness.'
        group = 'Well logs'
    else:
        data = numeric_csv(path, ['node_index', 'i', 'j', 'x', 'y', 'raw_value', 'defined'])
        valid = (data[:,6]==1) & np.isfinite(data[:,3:6]).all(axis=1)
        xyv = data[valid,3:6]; value = xyv[:,2]
        record.update(stats(value), rows=int(len(data)), null_count=int((~valid).sum()))
        if not len(value): raise ValueError('No defined finite nodes to plot')
        idx = sample_indices(len(value))
        fig, (ax, hist) = figures.plt.subplots(1, 2, figsize=(11, 4.8), width_ratios=[1.45, 1], layout='constrained')
        cloud = ax.scatter(xyv[idx,0], xyv[idx,1], c=value[idx], s=6, marker='s', cmap='viridis', linewidths=0,
                           vmin=float(value.min()), vmax=float(value.max()), rasterized=True)
        xyunit = item.get('horizontal_unit') or 'unknown'
        ax.set_xlabel(f'Native X [{xyunit}]'); ax.set_ylabel(f'Native Y [{xyunit}]'); ax.set_aspect('equal', adjustable='box')
        ax.ticklabel_format(style='plain', useOffset=False)
        for label in ax.get_xticklabels(): label.set_rotation(20)
        colorbar=figures.plt.colorbar(cloud, ax=ax, shrink=.8, label=(item.get('measurement') or 'Value')+f' [{unit}]')
        colorbar.formatter.set_useOffset(False); colorbar.update_ticks()
        hist.hist(value, bins=40, color='#087f8c'); hist.set_xlabel(f'Native value [{unit}]'); hist.set_ylabel('Defined nodes')
        hist.ticklabel_format(style='plain',useOffset=False)
        fig.suptitle(name, fontsize=13, weight='bold')
        caption = f'{len(value):,} defined of {len(data):,} nodes; {len(idx):,} displayed. Point preview: no gridding, contours or topology inferred. Original sign/domain retained. CRS: '+(item.get('crs_status') or 'unknown')+'.'
        group = 'Surfaces'
    if before.st_size != path.stat().st_size or before.st_mtime_ns != path.stat().st_mtime_ns or sha(path) != artifact['sha256']:
        figures.plt.close(fig)
        raise ValueError('CSV changed while preparing its figure')
    record['figure'] = figures.save(fig, name, group, caption, artifact['path'], item['object_id'])
    record['preview_status'] = 'plotted'


def seismic_previews(package, figures, records, issues):
    from petrel_file_convert import open_zgy
    from petrel_seismic_integrity import file_state, readonly_source
    paths = [(p,p.relative_to(package).as_posix(),None) for p in sorted((package/'08_native_project').rglob('*.zgy')) if p.is_file()]
    reference_path='01_project_metadata/project_seismic_inventory.json'
    inventory=load_json(package/reference_path)
    paths += [(Path(item['source']),reference_path,item) for item in inventory.get('objects',[]) if item.get('format')=='.zgy']
    for index, (candidate,relative,item) in enumerate(paths):
        identity=item['id'] if item else relative
        record = dict(name=item['name'] if item else candidate.stem, object_id=identity, category='Seismic', status='referenced_at_source' if item else 'preserved_only',
                      preview_status='not_selected_budget', links=[dict(label='ZGY source', path=relative)])
        if item: record.update(source=str(candidate),metadata=item.get('metadata',{}),source_sha256=None,hash_status='not_requested',size_bytes=item.get('file_state',{}).get('size_bytes'))
        records.append(record)
        if item and item['status'] in ('missing','unsupported'):
            record.update(status=item['status'],preview_status='unavailable',reason=item.get('reason',''))
            issues.append(dict(object_id=identity,reason=item.get('reason','Unavailable seismic source')))
            continue
        if index >= MAX_SEISMIC_FIGURES: continue
        try:
            path = candidate if item else contained(package, relative); before = file_state(path)
            if item and before!=item.get('file_state'): raise ValueError('Source changed since seismic inventory')
            with readonly_source(path), open_zgy(path) as reader:
                ni,nj,nk = map(int, reader.size)
                if min(ni,nj,nk) < 1: raise ValueError('Empty ZGY volume')
                # A bounded central patch avoids reading/hashing an entire large cube.
                njp,nkp = min(nj,256),min(nk,512); j0,k0 = (nj-njp)//2,(nk-nkp)//2
                data = np.empty((1,njp,nkp), dtype=np.float32)
                reader.read((ni//2,j0,k0), data)
                finite = data[np.isfinite(data)]
                if not len(finite): raise ValueError('No finite amplitudes in central preview patch')
                clip = float(np.percentile(np.abs(finite),98)) or 1
                fig, (ax, hist) = figures.plt.subplots(1,2,figsize=(11,4.5),width_ratios=[1.5,1],layout='constrained')
                image = ax.imshow(np.ma.masked_invalid(data[0].T), aspect='auto', cmap='seismic', vmin=-clip,vmax=clip,
                                  extent=[j0-.5,j0+njp-.5,k0+nkp-.5,k0-.5],interpolation='nearest')
                ax.set_xlabel('Crossline index (zero based)'); ax.set_ylabel('Sample index (zero based)')
                figures.plt.colorbar(image,ax=ax,label='Decoded amplitude',shrink=.8)
                hist.hist(finite,bins=50,color='#087f8c');hist.set_xlabel('Decoded amplitude');hist.set_ylabel('Patch samples')
                fig.suptitle(path.stem+' | central inline patch',weight='bold')
                record.update(stats(finite), dimensions=[ni,nj,nk], preview_origin=[ni//2,j0,k0], preview_shape=[1,njp,nkp])
                caption=f'Inline index {ni//2}; central {njp} × {nkp} patch of {ni} × {nj} × {nk}. Patch statistics only. Display clipped at ±{clip:.5g} (98th percentile). Axis units are sample indices; no domain/CRS inferred. Preview does not mean SEG-Y conversion.'
                if before!=file_state(path):
                    figures.plt.close(fig);raise ValueError('ZGY changed during preview read')
                record['figure']=figures.save(fig,path.stem,'Seismic',caption,relative,relative)
                record['preview_status']='plotted'
        except Exception as error:
            record['preview_status']='unavailable'; record['reason']=str(error)
            issues.append(dict(object_id=relative, reason=str(error)))


def build_visuals(package, audit, output_package=None, report_only=False):
    destination = output_package or package
    figures = Figures(destination)
    records, issues = [], []
    recovery_charts(audit, figures)
    receipt_path = package/'07_workflows_reports/native_recovery/native_recovery_report.json'
    receipt = load_json(receipt_path)
    trusted = receipt.get('status') in ('completed','partial') and receipt.get('source_unchanged') is True
    budgets = Counter()
    for item in sorted(receipt.get('objects', []), key=lambda x:(x.get('well') or '',x.get('name') or '',x.get('object_id') or '')):
        kind = item.get('blob_type',''); category = 'Well logs' if kind.endswith('WellLog') else 'Surfaces'
        record = {key:item.get(key,'') for key in ('object_id','name','well','blob_type','status','reason','unit','depth_unit')}
        record.update(category=category,preview_status='no_completed_payload',links=[])
        records.append(record)
        if not trusted:
            record.update(status='untrusted_receipt', reason='Decoder source-preservation check did not pass')
            continue
        try:
            record['links']=read_artifacts(package,item)
            if item.get('status') not in ('decoded','missing_metadata') or not record['links']: continue
            limit = MAX_LOG_FIGURES if category=='Well logs' else MAX_SURFACE_FIGURES
            if budgets[category]>=limit:
                record['preview_status']='not_selected_budget';continue
            plot_native(package,item,record,figures)
            budgets[category]+=1
        except Exception as error:
            record.update(preview_status='unavailable',reason=str(error))
            issues.append(dict(object_id=item.get('object_id'),reason=str(error)))
    spatial = load_json(package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json')
    for item in spatial.get('objects',[]):
        kind=item.get('blob_type','')
        relative = ('05_spatial/polygons/native_polygons_vertices.csv' if kind=='Polygons3' else
                    '05_spatial/points/native_points_vertices.csv' if kind=='Points3' else
                    '02_wells/trajectories/native_well_trajectory_records.csv')
        links=[dict(label='Native CSV',path=relative)] if (package/relative).is_file() else []
        records.append(dict(name=item.get('object_name') or item.get('blob_type','Spatial object'),
                            object_id=item.get('object_id',''), category='Spatial objects',
                            status=item.get('status','unknown'),reason=item.get('reason',''),
                            preview_status='see_spatial_map',links=links))
    seismic_previews(destination,figures,records,issues)
    if report_only:
        binary_sources=[p for p in (destination/'08_native_project/ptd_store').glob('Data.ptd') if p.is_file()]
        source=binary_sources[0].relative_to(destination).as_posix() if binary_sources else ''
        for record in records:
            if record.get('category')=='Seismic':continue
            record['links']=[]
            record['decoder_status']=record.get('status')
            if record.get('status')=='decoded':record['status']='preview_only'
        for figure in figures.items:
            if figure.get('group') in ('Surfaces','Well logs'):
                figure['source']=source
                figure['caption']+=' Report-only mode: temporary numeric tables were discarded; source link opens the preserved native binary.'
    inventory = data_inventory(destination, receipt, records)
    # A machine-readable catalogue is useful even when an individual plot fails.
    summary = dict(version='1.0', figures=figures.items, objects=records, issues=issues, inventory=inventory,report_only=report_only,
                   limits=dict(log_figures=MAX_LOG_FIGURES,surface_figures=MAX_SURFACE_FIGURES,
                               seismic_figures=MAX_SEISMIC_FIGURES,csv_bytes=MAX_CSV_BYTES,rows=MAX_ROWS),
                   source_boundary='Derived CSV figures are checked against decoder receipt hashes. ZGY patch reads use file stat checks, not a new full-file hash.')
    output = figures.directory/'visual_report.json'
    summary['summary_path']=output.relative_to(destination).as_posix()
    serializable=dict(summary,figures=[{k:v for k,v in f.items() if k!='data_uri'} for f in figures.items])
    output.write_text(json.dumps(serializable,indent=2,allow_nan=False),encoding='utf-8')
    return summary


def report_only_visuals(package, audit):
    """Decode into disposable scratch space for figures, retaining no datasets.

    Only the preserved native .pet/Model.ptd/Data.ptd are copied. Temporary
    outputs and their receipts never become exported/decoded dataset claims.
    """
    import petrel_native_recovery as recovery
    import export_petrel_native_spatial_zero_gui as spatial
    import report_petrel_project_audit as reporter
    import argparse
    failures=[];preview_receipt={}
    with tempfile.TemporaryDirectory(prefix='Petrel Report ') as temporary:
        scratch=Path(temporary)
        if not scratch.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError('Temporary preview directory is outside the designated temp root')
        for relative in ['08_native_project/project_file','08_native_project/ptd_store']:
            (scratch/relative).mkdir(parents=True)
        inputs=list((package/'08_native_project/project_file').glob('*.pet'))
        inputs += [package/'08_native_project/ptd_store'/n for n in ('Model.ptd','Data.ptd')]
        for source in inputs:
            if source.is_file():
                source=contained(package,source.relative_to(package).as_posix())
                shutil.copy2(source,scratch/source.relative_to(package.resolve()))
        log=io.StringIO()
        try:
            with redirect_stdout(log):preview_receipt=recovery.run(scratch)
            if preview_receipt.get('status')=='failed':failures.append(preview_receipt.get('error','Native log/surface preview unavailable'))
        except Exception as error:failures.append(str(error))
        try:
            with redirect_stdout(log):
                spatial.run(argparse.Namespace(export_package=str(scratch),data_file=None,well_tops_validation_csv=None,validation_tolerance=.02))
        except Exception as error:failures.append(str(error))
        preview_wells=reporter.gather_wells(scratch)
        audit['spatial_overview']=reporter.gather_spatial_overview(scratch,preview_wells)
        result=build_visuals(scratch,audit,output_package=package,report_only=True)
        result['temporary_preview_failures']=failures
        result['preview_source_hashes']=preview_receipt.get('source_hashes_before',{})
        result['preview_sources_unchanged']=preview_receipt.get('source_unchanged',False)
        result['preview_decoder_version']=preview_receipt.get('tool_version','unknown')
        # This metadata is retained alongside figures; transient filesystem paths are omitted.
        output=package/result['summary_path']
        output.write_text(json.dumps(dict(result,figures=[{k:v for k,v in f.items() if k!='data_uri'} for f in result['figures']]),indent=2,allow_nan=False),encoding='utf-8')
        return result


def data_inventory(package, receipt, records):
    """Complete union of discovered project subjects, registry IDs and receipts.

    Project folder placement comes only from explicit parent UUIDs. Unknown
    placement, missing parents and cycles are retained as separate roots.
    """
    import petrel_native_binary as binary
    from petrel_native_recovery import walk, exact_uuid
    nodes = {}; findings = []; project_count = 0; registry_ids = set()
    projects = list((package/'08_native_project'/'project_file').glob('*.pet'))
    if len(projects)==1:
        try:
            project = contained(package, projects[0].relative_to(package).as_posix())
            docs = list(binary.read_documents(binary.project_payload(binary.read_bounded(project))))
            if len(docs)!=1: raise ValueError('Expected one project document')
            for entry in docs[0].child('class_infos').child('map').children:
                for stub in walk(entry.child('value')):
                    if stub.attrs.get('Type')!='SubjectStub' or stub.get('uniqueTag') in (0,''):continue
                    tag=exact_uuid(stub.get('uniqueTag'))
                    if tag in nodes:raise ValueError('Duplicate project subject identity')
                    nodes[tag]=dict(object_id=tag,name=stub.get('visualName') or entry.get('key'),
                                    kind=entry.get('key'),parent=stub.get('parentTag'),
                                    status='metadata_only',placement='native_parent_UUID',links=[])
            project_count=len(nodes)
        except Exception as error:
            nodes={};findings.append('Full project hierarchy unavailable: '+str(error))
    else:
        findings.append('No single preserved .pet project document; using available ancestry and registry evidence')
    for item in receipt.get('objects',[]):
        for ancestor in item.get('ancestry',[]):
            tag=ancestor['object_id']
            if tag not in nodes:
                nodes[tag]=dict(ancestor,status='metadata_only',placement='decoder_ancestry',links=[])
    registry=package/'01_project_metadata/native_data_object_registry.csv'
    if registry.is_file():
        with registry.open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                tag=row.get('object_id')
                if not tag:continue
                registry_ids.add(tag)
                nodes.setdefault(tag,dict(object_id=tag,name=row.get('object_type') or tag,
                                          kind=row.get('object_type','unknown'),parent='',status='metadata_only',
                                          placement='folder_unresolved',links=[]))
                nodes[tag].setdefault('registry_types',set()).add(row.get('object_type','unknown'))
    for record in records:
        tag=record['object_id']
        node=nodes.setdefault(tag,dict(object_id=tag,name=record.get('name') or tag,
                                       kind=record.get('blob_type') or record.get('category','unknown'),parent='',placement='folder_unresolved'))
        for key in ('status','links','figure','preview_status','reason'):
            if key in record:node[key]=record[key]
    for node in nodes.values():
        if 'registry_types' in node:node['registry_types']=sorted(node['registry_types'])
        parent=node.get('parent')
        seen={node['object_id']}; cursor=parent
        while cursor in nodes and cursor not in seen and len(seen)<=128:
            seen.add(cursor);cursor=nodes[cursor].get('parent')
        if cursor in seen or len(seen)>128:
            node['display_parent']='';node['placement']='cycle_or_depth_limit'
        else:
            node['display_parent']=parent if parent in nodes else ''
            if parent and parent not in nodes:node['placement']='parent_not_recovered'
    return dict(nodes=list(nodes.values()),node_count=len(nodes),project_subjects=project_count,
                registry_ids=len(registry_ids),findings=findings,
                scope='All discovered subject UUIDs, unique registry IDs and decoder/file records; native payload completeness is separate.')


def render_inventory(audit, href):
    inventory=audit.get('visual_report',{}).get('inventory',{})
    nodes=inventory.get('nodes',[]);children={}
    for node in nodes:children.setdefault(node.get('display_parent',''),[]).append(node)
    for group in children.values():group.sort(key=lambda n:(n.get('name') or '',n['object_id']))
    def render(node, depth=0):
        tag=node['object_id'];name=node.get('name') or tag;kind=node.get('kind','unknown');status=node.get('status','metadata_only')
        nested=children.get(tag,[])
        search=' '.join(str(node.get(k,'')) for k in ('name','kind','object_id','status','reason','registry_types')).lower()
        links=''.join(f'<a href="{href(audit,a["path"])}">{esc(a["label"])}</a> ' for a in node.get('links',[]))
        if 'figure' in node:links+=f'<a href="#figure-{node["figure"]}">Figure</a>'
        text=f'<b>{esc(name)}</b> <span class="tree-kind">{esc(kind)}</span> <span class="visual-status">{esc(status)}</span>'
        info=f'<div class="tree-detail"><code>{esc(tag)}</code> · {esc(node.get("placement","unknown"))}<br>{esc(node.get("reason",""))} {links}</div>'
        return (f'<details class="object-node" data-object-id="{esc(tag)}" data-search="{esc(search)}" data-status="{esc(status)}"'+(' open' if depth==0 else '')+'>'
                f'<summary>{text} <small>({len(nested)} children)</small></summary>{info}'
                +''.join(render(child,depth+1) for child in nested)+'</details>')
    roots=children.get('',[])
    return ('<section id="data-inventory"><h2>Complete data inventory tree</h2>'
            f'<p><strong>{len(nodes):,}</strong> discovered entries · {inventory.get("project_subjects",0):,} native project subjects · {inventory.get("registry_ids",0):,} unique registry IDs. These populations overlap; they are not added together.</p>'
            '<p class="note">Native parent links preserve the decoded project hierarchy. Entries with unresolved placement remain visible as separate roots. Metadata-only folders or objects do not mean their payload was converted.</p>'
            '<div class="figure-toolbar"><input id="inventory-search" type="search" aria-label="Search data inventory" placeholder="Search every data object, folder, UUID, category or status">'
            '<button type="button" data-inventory-action="expand">Expand all</button><button type="button" data-inventory-action="collapse">Collapse all</button>'
            '<span id="inventory-count" role="status"></span></div>'
            '<div id="data-tree" class="data-tree">'+(''.join(render(n) for n in roots) or '<p>No object registry/hierarchy available in this package.</p>')+'</div>'
            +''.join(f'<p class="note">{esc(f)}</p>' for f in inventory.get('findings',[]))+'</section>')


STYLE = r'''
 nav{max-width:100%;overflow-x:auto;white-space:nowrap}section,.brief-grid>div,.cols>div{min-width:0}td{overflow-wrap:anywhere}
 .data-tree{max-height:700px;overflow:auto;padding:14px;background:#f8fbfc;border:1px solid #d8e2e6;border-radius:8px;font-size:12px}
 .object-node{margin:3px 0 3px 15px;border-left:1px solid #d8e2e6;padding-left:10px}.object-node summary{cursor:pointer;padding:7px 2px;overflow-wrap:anywhere}
 .object-node[hidden]{display:none!important}.tree-kind{color:#617681;margin:0 10px}.tree-detail{padding:5px 15px 10px;color:#617681;font-size:11px;overflow-wrap:anywhere}.tree-detail a{margin-right:8px}
 .visual-intro{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}
 .visual-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}
 .visual-kpis div{border-left:3px solid #087f8c;padding:10px 14px;background:#f1f7f8}
 .visual-kpis b{display:block;font-size:26px;color:#173e4c}.visual-kpis span{font-size:12px;color:#597280}
 .figure-toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.figure-toolbar select,.figure-toolbar input{padding:10px;border:1px solid #b9cbd2;border-radius:6px;background:white;max-width:100%}
 .figure-toolbar select{min-width:170px}.figure-toolbar input{flex:1;min-width:180px}
 .visual-gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
 .visual-gallery figure{background:white;border:1px solid #d8e2e6}.visual-gallery img{width:100%;height:auto;max-height:none;display:block}
 .visual-gallery figcaption{padding:16px;font-size:12px;line-height:1.6}.visual-gallery figcaption strong{display:block;font-size:15px;color:#173e4c}
 .visual-gallery .figure-links{display:flex;gap:14px;margin-top:10px;flex-wrap:wrap}.visual-gallery figure[hidden],tr[hidden]{display:none!important}
 .visual-status{display:inline-block;padding:3px 7px;background:#e7f3f3;border-radius:4px;white-space:nowrap}
 #object-catalogue td{max-width:360px;overflow-wrap:anywhere}#object-catalogue td a{display:inline-block;margin-right:8px}
 .visual-empty{padding:18px;background:#f5f8fa;border-left:3px solid #d4973b}
 @media(max-width:900px){.visual-gallery{grid-template-columns:1fr}.visual-kpis{grid-template-columns:repeat(2,1fr)}}
 @media print{.figure-toolbar,.visual-intro button{display:none}.visual-gallery{grid-template-columns:1fr}.visual-gallery figure{break-inside:avoid}}
'''

SCRIPT = r'''
 (()=>{
   const inv=document.getElementById('data-tree'),isearch=document.getElementById('inventory-search');
   if(inv){const all=[...inv.querySelectorAll('.object-node')];
     const apply=()=>{const q=isearch.value.trim().toLowerCase();let n=0;
       all.forEach(node=>{let ancestor=node.parentElement.closest('.object-node');let match=node.dataset.search.includes(q);
         while(q&&!match&&ancestor){match=ancestor.dataset.search.includes(q);ancestor=ancestor.parentElement.closest('.object-node');}
         node.hidden=!!q&&!match;if(!node.hidden)n++;});
       [...all].reverse().forEach(node=>{if(q&&[...node.children].some(c=>c.classList.contains('object-node')&&!c.hidden)){node.hidden=false;node.open=true;}});
       document.getElementById('inventory-count').textContent=n+' matching entries / '+all.length+' total';};
     isearch.addEventListener('input',apply);apply();document.querySelectorAll('[data-inventory-action]').forEach(b=>b.addEventListener('click',()=>all.forEach(n=>n.open=b.dataset.inventoryAction==='expand')));
   }
   const group=document.getElementById('figure-group'),find=document.getElementById('figure-search');
   const filterFigures=()=>{let n=0;document.querySelectorAll('[data-figure-group]').forEach(f=>{
     f.hidden=!!(group.value&&f.dataset.figureGroup!==group.value)||!f.dataset.search.includes(find.value.toLowerCase());if(!f.hidden)n++;
   });document.getElementById('figure-count').textContent=n+' figures shown';};
   if(group){group.addEventListener('change',filterFigures);find.addEventListener('input',filterFigures);filterFigures();}
   document.querySelectorAll('a[href^="#figure-"]').forEach(a=>a.addEventListener('click',()=>{group.value='';find.value='';filterFigures();}));
   const search=document.getElementById('object-search'),status=document.getElementById('object-status');
   const filterObjects=()=>{let n=0;document.querySelectorAll('#object-catalogue tbody tr').forEach(row=>{
     row.hidden=!row.dataset.search.includes(search.value.toLowerCase())||!!(status.value&&row.dataset.status!==status.value);if(!row.hidden)n++;
   });document.getElementById('object-count').textContent=n+' objects shown';};
   if(search){search.addEventListener('input',filterObjects);status.addEventListener('change',filterObjects);}
   const print=document.getElementById('print-report');if(print)print.addEventListener('click',()=>window.print());
 })();
'''


def render_section(audit, href):
    visual=audit.get('visual_report',{})
    figures=visual.get('figures',[]);objects=visual.get('objects',[])
    if not visual:return '<section id="visual-report"><h2>Data figures</h2><p>Visual report unavailable.</p></section>'
    groups=sorted({f['group'] for f in figures})
    cards=[]
    for index,f in enumerate(figures):
        links=''.join(f'<a href="{href(audit,f[key])}" download>{key.upper()}</a>' for key in ('png','svg'))
        if f.get('source'):links+=f'<a href="{href(audit,f["source"])}">Source data</a>'
        cards.append(f'<figure id="figure-{index}" data-figure-group="{esc(f["group"])}" data-search="{esc((f["name"]+" "+f["caption"]+" "+f.get("object_id","")).lower())}">'
                     f'<img loading="lazy" src="{f["data_uri"]}" alt="{esc(f["name"])}">'
                     f'<figcaption><strong>{esc(f["name"])}</strong>{esc(f["caption"])}<div class="figure-links">{links}</div></figcaption></figure>')
    rows=[]
    for o in objects:
        links=''.join(f'<a href="{href(audit,a["path"])}">{esc(a["label"])}</a>' for a in o.get('links',[]))
        if 'figure' in o:links+=f'<a href="#figure-{o["figure"]}">Figure</a>'
        n=o.get('valid_count');limits=f'{o.get("minimum"):.6g} to {o.get("maximum"):.6g}' if n else '—'
        rows.append(f'<tr data-object-id="{esc(o.get("object_id",""))}" data-status="{esc(o.get("status","unknown"))}" data-search="{esc(json.dumps(o,ensure_ascii=False).lower())}">'
                    f'<td><strong>{esc(o.get("name") or o.get("object_id",""))}</strong><br>{esc(o.get("well",""))}<br><small>{esc(o.get("object_id",""))}</small></td>'
                    f'<td>{esc(o.get("category",""))}</td><td><span class="visual-status">{esc(o.get("status","unknown"))}</span><br>{esc(o.get("preview_status",""))}</td>'
                    f'<td>{n if n is not None else "—"}</td><td>{limits}<br>{esc(o.get("unit") or "unit not resolved")}</td>'
                    f'<td>{esc(o.get("reason",""))}{links}</td></tr>')
    options=''.join(f'<option value="{esc(g)}">{esc(g)}</option>' for g in groups)
    statuses=''.join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in sorted({o.get('status','unknown') for o in objects}))
    omitted=sum(o.get('preview_status')=='not_selected_budget' for o in objects)
    temporary_findings=''.join(f'<p class="visual-empty">Preview stage: {esc(reason)}</p>' for reason in visual.get('temporary_preview_failures',[]))
    recovered_count=sum(o.get('status')==('preview_only' if visual.get('report_only') else 'decoded') for o in objects)
    recovered_label='Objects decoded for previews only' if visual.get('report_only') else 'Decoded catalogue objects'
    return f'''<section id="visual-report"><div class="visual-intro"><div><h2>Explore the recovered data</h2>
    <p>Maps, log tracks and distributions derived from the files in this extraction.</p></div><button id="print-report" type="button">Print / save PDF</button></div>
    <div class="visual-kpis"><div><b>{len(figures):,}</b><span>Data figures</span></div><div><b>{len(objects):,}</b><span>Objects in decoder/file catalogue</span></div>
    <div><b id="decoded-catalogue-count" data-base-count="{recovered_count}">{recovered_count:,}</b><span>{recovered_label}</span></div><div><b>{len(visual.get('issues',[])):,}</b><span>Preview issues</span></div></div>
    {temporary_findings}<p class="note">Preview sampling never changes data. {omitted} objects exceed the figure budget; they remain inventoried. Missing plots do not mean missing project data. ZGY preview statistics describe only the labelled patch. Dataset exports are absent when report-only is selected.</p>
    <div class="figure-toolbar"><select id="figure-group" aria-label="Figure category"><option value="">All figures</option>{options}</select><input id="figure-search" type="search" aria-label="Search figures" placeholder="Search figures by well, object, name or unit"><span id="figure-count" role="status"></span></div>
    <div class="visual-gallery">{''.join(cards) or '<p class="visual-empty">No numeric payloads are available for plotting in this package. See the coverage and object inventory.</p>'}</div></section>
    <section id="objects"><h2>Object catalogue and data links</h2><p>Decoder outcomes and preserved ZGY files; the complete native registry is listed separately below.</p>
    <div class="figure-toolbar"><input id="object-search" type="search" aria-label="Search objects" placeholder="Search well, object, UUID, reason or format"><select id="object-status" aria-label="Recovery status"><option value="">All recovery states</option>{statuses}</select><span id="object-count" role="status">{len(objects)} objects shown</span></div>
    <div class="scroll"><table id="object-catalogue"><thead><tr><th>Object / well</th><th>Category</th><th>Recovery / preview</th><th>Valid records</th><th>Value range</th><th>Reason / files</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    <p class="note">Statistics are available for plotted objects. Null/undefined records are excluded. <a href="{href(audit,visual['summary_path'])}">Download figure statistics, limits and provenance JSON</a>.</p></section>'''
