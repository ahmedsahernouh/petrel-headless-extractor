# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
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
import time
from urllib.parse import quote

from geoviewer_metadata import PRODUCT, VERSION, write_json
from geoviewer_io import atomic_text, atomic_json, contained, retry_io, sha256, systemic
from geoviewer_diagnostics import event
from geoviewer_seismic_delivery import validate_seismic_rows, seismic_invalidations
from geoviewer_output_paths import export_alias_path, ExportPathBudgetError


def read(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError: return {} if default is None else default


def slug(value):
    return re.sub(r'[^A-Za-z0-9_-]+','_',str(value)).strip('_')[:70] or 'object'


def href(path, report):
    target, base = Path(path), Path(report).parent
    try:
        relative = os.path.relpath(target, base)
    except ValueError:
        # Windows may spell the same output share as L: and as a UNC path.
        # Resolve existing parents (the summary itself may still be pending),
        # never guess a mapping from matching path suffixes.
        try:
            relative = os.path.relpath(target.parent.resolve(strict=True)/target.name,
                                       base.resolve(strict=True))
        except (OSError, ValueError):
            event('report_link_absolute_fallback', severity='warning',
                  target=str(target), report=str(report),
                  reason='No verified common filesystem root; link is machine-specific')
            return target.absolute().as_uri()
    return quote(relative.replace('\\','/'),safe='/')


class _SplitCSVWriter:
    """Hash emitted UTF-8 bytes; the owned text stream uses newline=''."""
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def write(self, text):
        raw = text.encode('utf-8')
        written = self.stream.write(text)
        if written != len(text):
            raise OSError('Short split CSV write')
        self.digest.update(raw)
        return written


def _checked_split_sha256(path, expected_count, expected_digest):
    """Count CSV records and hash saved bytes in the same buffered read pass.

    Split writers emit UTF-8 with CRLF record terminators. Binary line iteration
    preserves those bytes and quoted embedded newlines for csv.DictReader.
    Return only after the full read and successful close, before promotion.
    """
    digest = hashlib.sha256()
    with path.open('rb', buffering=1024*1024) as stream:
        def lines():
            for raw in stream:
                digest.update(raw)
                yield raw.decode('utf-8')
        if sum(1 for _ in csv.DictReader(lines())) != expected_count:
            raise ValueError('Split CSV record count mismatch')
    if digest.hexdigest() != expected_digest:
        raise ValueError('Saved split CSV bytes differ from emitted CSV bytes')
    return digest.hexdigest()


def _completed_spatial_tables(package, report, findings, *, only=None):
    """Accept only finished decoder tables, with structural counts, not package QC.

    Called after extraction has stopped. No binary source reads are performed.
    A missing/malformed table excludes that category without hiding other files.
    """
    if (not isinstance(report, dict)
            or report.get('tool') != 'export_petrel_native_spatial_zero_gui.py'
            or not report.get('completed_at_utc')
            or not isinstance(report.get('objects'), list)):
        findings.append('Spatial checkpoint unavailable: no completed decoder receipt')
        return set()
    tables = [
        ('05_spatial/polygons/native_polygons_vertices.csv', 'polygon_vertex_rows'),
        ('05_spatial/polygons/native_polygon_parts.csv', 'polygon_part_rows'),
        ('05_spatial/polygons/native_polygon_attributes.csv', 'polygon_attribute_value_rows'),
        ('05_spatial/points/native_point_attributes.csv', 'point_attribute_value_rows'),
        ('05_spatial/polygons/native_polygon_missing_vertices.csv', 'polygon_missing_vertex_rows'),
        ('05_spatial/points/native_point_missing_vertices.csv', 'point_missing_vertex_rows'),
        ('05_spatial/points/native_points_vertices.csv', 'point_vertex_rows'),
        ('05_spatial/points/native_points_identity.csv', 'point_identity_rows'),
        ('02_wells/trajectories/native_well_trajectory_records.csv', 'trajectory_rows'),
        ('02_wells/well_headers/native_well_heads.csv', 'native_well_head_rows')]
    accepted = set()
    for relative, key in tables:
        if only is not None and relative != only:
            continue
        expected = report.get(key)
        if key in ('polygon_attribute_value_rows', 'point_attribute_value_rows') and expected is None and not (package/relative).exists():
            continue  # Older completed receipts predate this optional table.
        if expected == 0:
            continue
        try:
            if type(expected) is not int or expected < 0:
                raise ValueError('completed receipt has no valid '+key)
            source = contained(package, package/relative)
            before = source.stat()
            with source.open(encoding='utf-8-sig', newline='', buffering=1024*1024) as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise ValueError('missing or duplicate CSV header')
                if key != 'native_well_head_rows' and 'object_id' not in reader.fieldnames:
                    raise ValueError('missing object_id column')
                count = 0
                for row in reader:
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError('incomplete CSV row')
                    count += 1
            if count != expected:
                raise ValueError(f'receipt row count {expected} differs from table {count}')
            if key == 'point_attribute_value_rows':
                expected_hash = report.get('point_attribute_csv_sha256')
                if (not isinstance(expected_hash, str) or len(expected_hash) != 64
                        or sha256(source) != expected_hash):
                    raise ValueError('point attribute CSV SHA-256 differs from completed decoder receipt')
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('table changed during checkpoint validation')
            accepted.add(relative)
        except (OSError, ValueError, csv.Error, UnicodeError) as exc:
            if systemic(exc): raise
            findings.append('Spatial checkpoint excluded '+relative+': '+str(exc))
    top = report.get('well_top_binding') or {}
    relative = '02_wells/well_tops/native_well_top_picks_bound.csv'
    if ((only is None or only == relative) and top.get('row_count',0) and
            top.get('status') in ('native_schema_bound','partial')):
        try:
            if top.get('output_path') != relative:
                raise ValueError('native tops output path differs from receipt')
            expected = top['row_count']; groups = top.get('objects')
            if type(expected) is not int or expected < 1 or not isinstance(groups,list):
                raise ValueError('native tops receipt row/group count invalid')
            group_counts = {}
            for group in groups:
                if group.get('status') != 'native_schema_bound': continue
                identity=group.get('object_id')
                if identity in group_counts or type(group.get('row_count')) is not int or group['row_count'] < 1:
                    raise ValueError('duplicate or invalid bound native tops object')
                group_counts[identity]=group['row_count']
            if sum(group_counts.values()) != expected:
                raise ValueError('native tops object row totals differ from receipt')
            source=contained(package,package/relative); before=source.stat()
            required={'object_id','data_pk','point_index','well_name','surface','measured_depth',
                      'x','y','z','well_link_uuid','binding_status'}
            seen={identity:0 for identity in group_counts}; ordinals={identity:set() for identity in group_counts}
            with source.open(encoding='utf-8-sig',newline='',buffering=1024*1024) as stream:
                reader=csv.DictReader(stream)
                if not reader.fieldnames or not required.issubset(reader.fieldnames) or len(reader.fieldnames)!=len(set(reader.fieldnames)):
                    raise ValueError('native tops CSV header incomplete or duplicate')
                for row in reader:
                    identity=row.get('object_id')
                    if None in row or any(v is None for v in row.values()) or identity not in seen or row['binding_status']!='native_schema_bound':
                        raise ValueError('native tops CSV row incomplete or unbound')
                    ordinal=int(row['point_index'])
                    if ordinal in ordinals[identity]:raise ValueError('duplicate native top point index')
                    ordinals[identity].add(ordinal);seen[identity]+=1
            if seen!=group_counts or any(ordinals[k]!=set(range(v)) for k,v in group_counts.items()):
                raise ValueError('native tops CSV per-object counts or ordinals differ from receipt')
            digest=top.get('output_sha256')
            if not isinstance(digest,str) or len(digest)!=64 or sha256(source)!=digest:
                raise ValueError('native tops CSV SHA-256 differs from completed decoder receipt')
            after=source.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError('native tops table changed during validation')
            accepted.add(relative)
        except (OSError,ValueError,csv.Error,UnicodeError) as exc:
            if systemic(exc):raise
            findings.append('Spatial checkpoint excluded '+relative+': '+str(exc))
    return accepted


def _companion_path(package, value):
    """Interpret manifest paths on either host without accepting linked ancestry."""
    raw=str(value or '').replace('\\','/')
    parts=raw.split('/')
    if (not raw or raw.startswith('/') or ':' in raw or
            any(part in ('', '.', '..') for part in parts)):
        raise ValueError('unsafe companion relative path: '+raw)
    return contained(package, package.joinpath(*parts))


def _companion_candidates(package, findings):
    inventory=package/'00_manifest/companion_source_inventory.csv'
    manifest=package/'00_manifest/export_manifest.csv'
    if not inventory.is_file():
        # A no-companion package has no reason to carry this inventory. A
        # registered derivative, however, has lost the only source/derivative
        # binding that permits final publication.
        if manifest.is_file():
            try:
                with contained(package,manifest).open(encoding='utf-8-sig',newline='') as stream:
                    reader=csv.DictReader(stream)
                    if not reader.fieldnames or 'source_object_type' not in reader.fieldnames:
                        raise ValueError('manifest has no source_object_type column')
                    registered=[row.get('export_file') for row in reader
                                if row.get('source_object_type')=='companion_converted_ascii'
                                or (row.get('source_object_type')=='well_top' and
                                    str(row.get('export_file') or '').replace('\\','/').startswith('02_wells/well_tops/'))]
            except (OSError,ValueError,csv.Error,UnicodeError) as exc:
                if systemic(exc):raise
                findings.append('Companion registrations cannot be checked without source inventory: '+str(exc))
                return [],True
            if registered:
                findings.append('Companion source inventory missing for '+str(len(registered))+
                                ' registered derivative(s); source binding and final delivery QC unestablished')
                return [],True
        return [],False
    try:
        with contained(package,inventory).open(encoding='utf-8-sig',newline='') as stream:
            sources=list(csv.DictReader(stream))
        with contained(package,manifest).open(encoding='utf-8-sig',newline='') as stream:
            exports=list(csv.DictReader(stream))
    except (OSError,ValueError,csv.Error,UnicodeError) as exc:
        if systemic(exc):raise
        findings.append('Companion inventory unavailable: '+str(exc));return [],True
    registrations={}
    for row in exports:
        if row.get('source_object_type') not in ('companion_converted_ascii','well_top'):continue
        key=str(row.get('export_file') or '').replace('\\','/').casefold()
        registrations.setdefault(key,[]).append(row)
    bindings={};source_counts={}
    for row in sources:
        if row.get('status')!='converted_and_preserved':continue
        source_key=str(row.get('source_relative_path') or '').replace('\\','/').casefold()
        source_counts[source_key]=source_counts.get(source_key,0)+1
        for relative in str(row.get('converted_files') or '').split(';'):
            key=relative.replace('\\','/').casefold()
            bindings.setdefault(key,[]).append(row)
    result=[];checked_sources={}
    for key,owners in bindings.items():
        relative=str(owners[0].get('converted_files') or '').split(';')[0].replace('\\','/')
        # Resolve the exact derivative spelling from this owner's listed paths.
        relative=next((part.replace('\\','/') for part in str(owners[0].get('converted_files') or '').split(';')
                       if part.replace('\\','/').casefold()==key),relative)
        try:
            if len(owners)!=1:raise ValueError('ambiguous source binding')
            owner=owners[0];registration=registrations.get(key,[])
            if len(registration)!=1:raise ValueError('missing or ambiguous companion manifest registration')
            item=registration[0]
            source_ref=str(owner.get('source_relative_path') or '').replace('\\','/')
            if source_counts.get(source_ref.casefold())!=1:raise ValueError('ambiguous companion source inventory identity')
            preserved=_companion_path(package,owner.get('preserved_file'))
            derivative=_companion_path(package,relative)
            kind={'las_well_data':'companion_logs','excel_workbook':'companion_workbooks',
                  'petrel_well_tops_ascii':'companion_well_tops','gis_shapefile_geometry':'companion_gis'}.get(owner.get('category'))
            allowed_lane=(relative.startswith('10_converted_ascii/') or
                          (kind=='companion_well_tops' and relative.startswith('02_wells/well_tops/')))
            allowed_formats = ('.csv','.json','.geojson','.prj') if kind=='companion_gis' else ('.csv','.json')
            if not allowed_lane or derivative.suffix.lower() not in allowed_formats:
                raise ValueError('not a supported converted companion derivative')
            if not str(owner.get('preserved_file') or '').replace('\\','/').startswith('09_source_companions/'):
                raise ValueError('preserved source is outside companion source lane')
            _companion_path(package,source_ref)
            top_registration=(kind=='companion_well_tops' and
                              relative.startswith('02_wells/well_tops/') and
                              item.get('source_object_type')=='well_top')
            if top_registration:
                # Historical package scan registered tops as well_top. The
                # companion inventory supplies their exact source binding.
                if str(item.get('source_object_path') or '').replace('\\','/') != 'File export/'+relative:
                    raise ValueError('well-top registration path disagrees with inventory')
            else:
                if item.get('source_object_type')!='companion_converted_ascii':
                    raise ValueError('derivative lacks companion registration')
                if str(item.get('notes') or '').replace('\\','/') != 'Converted from '+source_ref+'; source preserved separately.':
                    raise ValueError('manifest source reference disagrees with inventory')
                if str(item.get('source_object_path') or '').replace('\\','/') not in ('', 'Portable companion extraction/'+relative):
                    raise ValueError('manifest derivative path disagrees with registration')
            declared_format=str(item.get('export_format') or '').upper()
            legacy_top_json=(top_registration and relative.endswith('.summary.json') and declared_format=='CSV')
            if declared_format not in ('', derivative.suffix[1:].upper()) and not legacy_top_json:
                raise ValueError('manifest format disagrees with derivative')
            expected_status='exported' if top_registration else 'exported_zero_gui_read_only'
            if item.get('export_status')!=expected_status or item.get('validation_status')!='validated':
                raise ValueError('manifest derivative is not validated and exported')
            digest=str(item.get('sha256') or '').lower()
            source_digest=str(owner.get('sha256') or '').lower()
            if not re.fullmatch(r'[0-9a-f]{64}',digest) or not re.fullmatch(r'[0-9a-f]{64}',source_digest):
                raise ValueError('missing valid derivative or preserved-source SHA-256')
            if not derivative.is_file() or not preserved.is_file():raise ValueError('derivative or preserved source missing')
            source_key=source_ref.casefold()
            checked=checked_sources.get(source_key)
            if checked is None:
                source_size=preserved.stat().st_size
                if owner.get('size_bytes') not in ('',None) and source_size!=int(owner['size_bytes']):
                    raise ValueError('preserved source size differs from inventory')
                if sha256(preserved)!=source_digest:raise ValueError('preserved source SHA-256 mismatch')
                checked_sources[source_key]=(preserved,source_size,source_digest)
            elif (checked[0]!=preserved or checked[2]!=source_digest or
                  (owner.get('size_bytes') not in ('',None) and checked[1]!=int(owner['size_bytes']))):
                raise ValueError('preserved source declaration conflicts with checked source')
            if item.get('bytes') not in ('',None) and derivative.stat().st_size!=int(item['bytes']):
                raise ValueError('derivative size differs from manifest')
            if sha256(derivative)!=digest:raise ValueError('derivative SHA-256 mismatch')
            if legacy_top_json:
                summary=read(derivative)
                if (not isinstance(summary,dict) or
                    summary.get('converter')!='portable_petrel_well_tops_ascii_parser' or
                    summary.get('native_binary_decode') is not False or
                    summary.get('petrel_export_confirmed') is not True or
                    type(summary.get('records')) is not int or summary['records']<0 or
                    not isinstance(summary.get('source_header_columns'),list) or
                    not isinstance(summary.get('validation_boundary'),str)):
                    raise ValueError('legacy well-top JSON sidecar lacks converter signature')
            if kind is None:raise ValueError('unsupported companion source category')
            identity='companion_'+hashlib.sha256(source_ref.encode('utf-8')).hexdigest()[:24]
            result.append((derivative,kind,identity,source_ref,owner,digest,relative,
                           'well_top_package_scan_inventory_bound' if top_registration else 'companion_converted_ascii',
                           'Historical well_top manifest labels JSON sidecar CSV; actual file is JSON.' if legacy_top_json else ''))
        except (OSError,ValueError) as exc:
            if systemic(exc):raise
            findings.append('Companion derivative rejected '+relative+': '+str(exc))
    return result,False


def publish_exports(package, inventory, destination, checkpoint_index=None, *, spatial_checkpoint=False):
    """Idempotent new-output publication; never silently copy large files."""
    started=time.monotonic()
    package=Path(package).resolve(strict=True); destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True);destination=destination.resolve(strict=True)
    event('delivery_started',package=str(package),destination=str(destination))
    context=read(package/'01_project_metadata/project_context.json')
    names={x['object_id']:x['name'] for x in context.get('objects',[])}
    records=[]; partial_files=[]; findings=[];category_roots={};category_stats={}
    companion_inventory_gap=False;checkpoint_gap=False
    def category_stat(category):
        if category not in category_stats:
            category_stats[category]=dict(attempted=0,exported=0,failed=0,fallback=0)
            event('delivery_category_started',category=category)
        return category_stats[category]
    def category_root(category):
        if category not in category_roots:
            root=contained(destination,destination/category)
            root.mkdir(parents=True,exist_ok=True)
            category_roots[category]=root
        return category_roots[category]
    def current(category,identity,operation):
        stats=category_stat(category);stats['attempted']+=1
        completed=stats['exported']+stats['failed']
        event('delivery_progress',category=category,operation=operation,
              processed_count=completed,completed_count=completed,
              attempted_count=stats['attempted'],exported_count=stats['exported'],
              current_object_id=identity)
    def outcome(category,status):
        category_stats[category][status]+=1
    def link(source, category, identity, name, label, qc, unit='unknown', digest=None, recovery_status='', metadata_warning='', **provenance):
        source=Path(source)
        current(category,identity,'hardlink')
        if not source.is_file():
            findings.append('Accepted export is missing: '+str(source))
            event('delivery_failed',severity='error',object_id=identity,source=str(source),reason=findings[-1])
            outcome(category,'failed')
            return
        resolved_source=source.resolve(strict=True)
        if not resolved_source.is_relative_to(package) and category!='seismic':
            raise ValueError('Export escaped the validated package')
        stem=slug(name)+'_'+slug(identity)[:36]+'_'+slug(source.stem)
        layout='single_physical_copy_hardlink'
        try:
            relative_source=(resolved_source.relative_to(package).as_posix()
                             if resolved_source.is_relative_to(package) else str(resolved_source))
            target=export_alias_path(destination/category,stem+source.suffix.lower(),
                                     identity=[category,identity,relative_source])
            target=contained(destination,target)
            category_root(category)
            if target.exists():
                identical=os.path.samefile(source,target)
            else:
                os.link(source,target);identical=os.path.samefile(source,target)
            if not identical:raise FileExistsError('Export name collision or hard-link identity check failed: '+str(target))
        except ExportPathBudgetError as exc:
            target=source;layout='original_path_filesystem_cannot_hardlink'
            outcome(category,'fallback')
            findings.append('Readable alias path exceeds budget; original validated file retained: '+str(exc))
            event('delivery_fallback',severity='warning',object_id=identity,source=str(source),reason=findings[-1])
        except ValueError as exc:
            if category!='seismic':
                raise
            target=source;layout='original_path_filesystem_cannot_hardlink'
            outcome(category,'fallback')
            findings.append('Seismic delivery link withheld; validated original retained: '+str(exc))
            event('delivery_fallback',severity='warning',object_id=identity,source=str(source),reason=findings[-1])
        except OSError as exc:
            if systemic(exc):raise
            target=source;layout='original_path_filesystem_cannot_hardlink'
            outcome(category,'fallback')
            findings.append('Shallow link unavailable; original validated file retained: '+str(exc))
            event('delivery_fallback',severity='warning',object_id=identity,source=str(source),reason=findings[-1])
        records.append(dict(object_id=identity,name=name,category=category,format=label,
            label=source.stem.replace('_',' ')+' ('+label+')',
            path=os.path.relpath(target,destination).replace('\\','/'),bytes=target.stat().st_size,
            status='exported',numerical_qc=qc,units=unit or 'unknown',sha256=digest or '',
            recovery_status=recovery_status,metadata_warning=metadata_warning,
            integrity=('receipt_sha256_freshly_verified' if category=='seismic' else 'receipt_sha256')
                      if digest else 'file_identity_and_prior_numerical_QC',storage=layout,**provenance))
        outcome(category,'exported')
        return records[-1]
    native={} if spatial_checkpoint else read(package/'07_workflows_reports/native_recovery/native_recovery_report.json')
    if not native and not spatial_checkpoint: native=read(package/'07_workflows_reports/native_recovery/native_recovery.json')
    # Several accepted objects may name one retained native file. Validate the
    # file once before publishing any owner, so damaged bytes or conflicting
    # declarations cannot leave another owner in an accepted index.
    native_declarations={}
    for item in native.get('objects',[]):
        if item.get('status') not in ('decoded','missing_metadata') or item.get('numeric_round_trip')!='exact':continue
        for artifact in item.get('artifacts',[]):
            source=package/'native_data'/artifact['path']
            if source.suffix.lower() not in ('.csv','.las','.xyz','.dat','.zmap','.txt','.json'):continue
            source=contained(package,source)
            native_declarations.setdefault(source,[]).append(artifact)
    rejected_native=set()
    for source,declarations in native_declarations.items():
        digests={str(a.get('sha256') or '').lower() for a in declarations}
        sizes=[a.get('bytes') for a in declarations]
        if len(digests)!=1:
            reason='conflicting artifact declarations'
        elif any(type(size) is not int or size<0 for size in sizes):
            reason='invalid artifact size declaration'
        elif len(set(sizes))>1:
            reason='conflicting artifact declarations'
        elif not re.fullmatch(r'[0-9a-f]{64}',next(iter(digests))):
            reason='missing or invalid SHA-256 declaration'
        elif not source.is_file():
            reason='retained artifact is missing'
        elif source.stat().st_size!=sizes[0]:
            reason='retained artifact size differs from declaration'
        else:
            try:
                reason='' if sha256(source)==next(iter(digests)) else 'retained artifact SHA-256 differs from declaration'
            except OSError as exc:
                if systemic(exc):raise
                reason='retained artifact SHA-256 unavailable: '+str(exc)
        if reason:
            rejected_native.add(source)
            findings.append('Native export withheld for every owner of '+str(source)+': '+reason)
    for item in native.get('objects',[]):
        if item.get('status') not in ('decoded','missing_metadata') or item.get('numeric_round_trip')!='exact':continue
        category='logs' if item.get('blob_type','').endswith('WellLog') else 'grids'
        name=item.get('object_name') or item.get('name') or names.get(item['object_id'],item['object_id'])
        if item.get('well'):name=item['well']+'_'+name
        for artifact in item.get('artifacts',[]):
            source=package/'native_data'/artifact['path']
            if source.suffix.lower() not in ('.csv','.las','.xyz','.dat','.zmap','.txt','.json'):continue
            if contained(package,source) in rejected_native:continue
            link(source,category,item['object_id'],name,source.suffix[1:].upper(),'exact_readback',item.get('unit'),artifact.get('sha256'),
                 recovery_status=item.get('status') or '',metadata_warning=item.get('reason') or '')
    spatial_path=package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
    spatial_signature=''
    if spatial_checkpoint:
        spatial_path=contained(package, spatial_path)
        if spatial_path.is_file():
            raw=spatial_path.read_bytes()
            spatial=json.loads(raw.decode('utf-8-sig'))
            spatial_signature=hashlib.sha256(raw).hexdigest()
        else:spatial={}
        if not isinstance(spatial,dict):raise ValueError('Spatial receipt must be a JSON object')
    else:spatial=read(spatial_path)
    allowed_tables = _completed_spatial_tables(package, spatial, findings) if spatial_checkpoint else None
    point_attribute_relative = '05_spatial/points/native_point_attributes.csv'
    top_relative = '02_wells/well_tops/native_well_top_picks_bound.csv'
    tops_accepted = top_relative in (allowed_tables if allowed_tables is not None else
        _completed_spatial_tables(package,spatial,findings,only=top_relative))
    point_attributes_accepted = True
    if not spatial_checkpoint and (spatial.get('point_attribute_value_rows') is not None or
                                   (package/point_attribute_relative).exists()):
        # Final publication also requires the completed receipt and shared table
        # count; a leftover CSV cannot silently become current evidence.
        checked = _completed_spatial_tables(package, spatial, findings, only=point_attribute_relative)
        point_attributes_accepted = point_attribute_relative in checked
    accepted={x['object_id']:x for x in spatial.get('objects',[]) if x.get('status')=='decoded'}
    # Shared CSVs retain native segment/vertex indices. No XY sorting is applied.
    for relative,category in [('05_spatial/polygons/native_polygons_vertices.csv','polygons'),
            ('05_spatial/polygons/native_polygon_parts.csv','polygon_parts'),
            ('05_spatial/polygons/native_polygon_attributes.csv','polygon_attributes'),
            (point_attribute_relative,'point_attributes'),
            ('05_spatial/polygons/native_polygon_missing_vertices.csv','polygon_missing_vertices'),
            ('05_spatial/points/native_point_missing_vertices.csv','point_missing_vertices'),
            ('05_spatial/points/native_points_vertices.csv','points'),
            ('05_spatial/points/native_points_identity.csv','point_identities'),
            ('02_wells/trajectories/native_well_trajectory_records.csv','trajectories'),
            (top_relative,'well_tops')]:
        source=package/relative
        if spatial_checkpoint and relative not in allowed_tables:continue
        if category == 'point_attributes' and not point_attributes_accepted:continue
        if category == 'well_tops' and not tops_accepted:continue
        auxiliary = category in ('point_identities', 'point_attributes', 'polygon_attributes', 'polygon_parts', 'polygon_missing_vertices', 'point_missing_vertices')
        if spatial_checkpoint or auxiliary or category == 'well_tops':
            kinds={'polygons':{'Polygons3'},'polygon_attributes':{'Polygons3'},'polygon_parts':{'Polygons3'},'points':{'Points3'},'point_identities':{'Points3'},'point_attributes':{'Points3'},'well_tops':{'Points3'},
                   'polygon_missing_vertices':{'Polygons3'},'point_missing_vertices':{'Points3'},
                   'trajectories':{'ExplicitTrajectoryProviderData','MdInclAzimTrajectoryProviderData','XYZTrajectoryProviderData','WellTrace3'}}
            accepted={x['object_id']:x for x in spatial.get('objects',[])
                      if x.get('status') in (('decoded','empty_supported_object') if auxiliary else ('decoded',))
                      and x.get('blob_type') in kinds[category]
                      and (category != 'point_attributes' or
                           (x.get('attributes_status') == 'raw_typed_values_decoded' and
                            type(x.get('attribute_value_count')) is int and x['attribute_value_count'] > 0))}
            if category == 'well_tops':
                bound = {r['object_id']:r for r in spatial.get('well_top_binding',{}).get('objects',[])
                         if r.get('status') == 'native_schema_bound'}
                accepted = {identity:dict(item,**bound[identity]) for identity,item in accepted.items()
                            if identity in bound}
        else:
            accepted={x['object_id']:x for x in spatial.get('objects',[]) if x.get('status')=='decoded'}
        if not source.is_file():continue
        handles={};writers={};emitted={};counts={};failed=set();started_ids=set();complete=False
        def split_failed(identity,exc):
            if systemic(exc):raise exc
            if identity in failed:return
            if identity not in started_ids:
                current(category,identity,'split_csv');started_ids.add(identity)
            failed.add(identity)
            outcome(category,'failed')
            findings.append('Spatial delivery incomplete for '+identity+': '+str(exc))
            event('delivery_failed',severity='error',object_id=identity,source=str(source),reason=str(exc))
        try:
            with source.open(encoding='utf-8-sig',newline='',buffering=1024*1024) as stream:
                reader=csv.DictReader(stream)
                for row in reader:
                    identity=row.get('object_id','')
                    if identity not in accepted or identity in failed:continue
                    try:
                        if identity not in writers:
                            name=names.get(identity) or row.get('well_name') or row.get('object_name') or identity
                            current(category,identity,'split_csv')
                            started_ids.add(identity)
                            target=export_alias_path(destination/category,
                                slug(name)+'_'+slug(identity)+'.csv',identity=[category,identity,relative],
                                staging_suffix='.00000000.partial')
                            target=contained(destination,target)
                            category_root(category)
                            import uuid
                            pending=target.with_name(target.name+'.'+uuid.uuid4().hex[:8]+'.partial')
                            contained(destination,pending)
                            handles[identity]=pending.open('x',encoding='utf-8',newline='',buffering=1024*1024)
                            emitted[identity]=_SplitCSVWriter(handles[identity])
                            writers[identity]=csv.DictWriter(emitted[identity],fieldnames=reader.fieldnames)
                            writers[identity].writeheader();counts[identity]=[target,pending,name,0]
                        writers[identity].writerow(row);counts[identity][3]+=1
                    except (OSError,ExportPathBudgetError) as exc:split_failed(identity,exc)
            complete=True
        except (OSError,csv.Error) as exc:
            split_failed('shared_'+category,exc)
        finally:
            for identity,handle in handles.items():
                try:handle.close()
                except OSError as exc:split_failed(identity,exc)
        if not complete:continue
        for identity,(target,pending,name,count) in counts.items():
            if identity in failed:continue
            try:
                if category == 'point_attributes' and count != accepted[identity]['attribute_value_count']:
                    raise ValueError('Point attribute row count differs from completed object receipt')
                if category == 'well_tops' and count != accepted[identity]['row_count']:
                    raise ValueError('Native well top row count differs from completed object receipt')
                digest=_checked_split_sha256(pending,count,emitted[identity].digest.hexdigest())
                contained(destination,target);contained(destination,pending)
                def promote():
                    if target.exists():
                        if sha256(target)!=digest:raise FileExistsError('Existing delivery file differs; retained both: '+str(target))
                        pending.unlink()
                    else:pending.rename(target)
                retry_io(promote,operation='finalize_spatial_delivery',source=pending,destination=target,context={'object_id':identity})
                metadata_warning = ''
                if category == 'polygon_parts':
                    metadata_warning = 'Native part order/count and separate closure observations; not full geometry, ring/hole roles or exported property attributes. Empty and missing-vertex parts remain explicit.'
                if category == 'polygon_attributes':
                    metadata_warning = 'Raw typed native attribute vectors with original indices and defaults; names/IDs are attached only where exact PET registry and Model records agree. Part association, units, null/default application and ring roles remain unresolved. Not geometry.'
                if category == 'point_attributes':
                    metadata_warning = 'Raw typed native vectors with original vector/value ordinals, numeric bits and opaque defaults. Names, point association, units, roles, null/default application and receiving qualification remain unresolved. Not geometry.'
                if category == 'point_identities':
                    metadata_warning = 'Native serialization identity values and original point ordinals; not scientific attributes or external well IDs. Missing XYZ slots retained.'
                if category == 'well_tops':
                    metadata_warning = 'Native Model/Points3 well and surface labels with MD ordinals. Units, CRS and vertical datum unresolved; Z is native XYZ, not TVD. External scientific validation separate.'
                if category in ('polygon_missing_vertices', 'point_missing_vertices'):
                    metadata_warning = 'Raw components and original ordinals of native missing XYZ slots; combine with finite vertex rows by exact indices. Not complete geometry; do not interpolate or bridge gaps.'
                if accepted[identity].get('blob_type') == 'WellTrace3':
                    flags = accepted[identity].get('native_flags', {})
                    metadata_warning = ('Stored legacy trace stations; angle/coordinate units and CRS unresolved; '
                                        'no well head inferred. Native flags: ' + json.dumps(flags, sort_keys=True))
                records.append(dict(object_id=identity,name=name,category=category,format='CSV',
                    metadata_warning=metadata_warning,
                    path=target.relative_to(destination).as_posix(),bytes=target.stat().st_size,status='exported',
                    numerical_qc=('native_schema_bound; row-preserving split; count and hash checked; external validation separate' if category=='well_tops' else
                                  'auxiliary_evidence_only; row-preserving split; count checked' if category in ('polygon_attributes','point_attributes','polygon_missing_vertices','point_missing_vertices') else
                                  'metadata_only; row-preserving split; count checked' if category=='polygon_parts' else
                                  'native_decoder; row-preserving split; count checked'),units='unknown',
                    sha256=digest,integrity='saved_split_CSV_bytes_sha256; verified_against_emitted_CSV_bytes; derived_from_validated_shared_CSV',storage='object_table',record_count=count))
                outcome(category,'exported')
            except (OSError,ValueError,csv.Error) as exc:split_failed(identity,exc)
    # A completed decoder receipt can claim a decoded primary spatial object
    # even when its shared table is absent or has no row for that object.
    # Auxiliary evidence and explicitly empty objects do not require geometry.
    primary_spatial_categories={
        'Points3':'points', 'Polygons3':'polygons',
        'ExplicitTrajectoryProviderData':'trajectories',
        'MdInclAzimTrajectoryProviderData':'trajectories',
        'XYZTrajectoryProviderData':'trajectories', 'WellTrace3':'trajectories'}
    delivered_primary={(row['category'],row['object_id']) for row in records}
    missing_primary=[]
    for item in spatial.get('objects',[]):
        category=primary_spatial_categories.get(item.get('blob_type'))
        if (item.get('status')=='decoded' and category and
                (category,item.get('object_id')) not in delivered_primary):
            missing_primary.append((category,item.get('object_id')))
    seismic_gap=False
    if not spatial_checkpoint:
        for category,identity in missing_primary:
            finding='Decoded spatial primary output missing for '+category+' object '+str(identity)
            findings.append(finding)
            event('delivery_failed',severity='error',category=category,object_id=identity,reason=finding)
    # Well-head tables are complete open tables; labels stay explicit if not a per-well export.
    heads=package/'02_wells/well_headers/native_well_heads.csv'
    heads_accepted = (allowed_tables is None or
        ('02_wells/well_headers/native_well_heads.csv' in allowed_tables and
         spatial.get('model_well_head_decode',{}).get('status') in ('decoded','decoded_with_failed_closed_subjects','passed')))
    if heads_accepted and heads.is_file() and spatial.get('model_well_head_decode',{}).get('status')!='failed_closed':
        # The completed table has no decoder receipt digest. Record its full
        # delivery-time byte identity without claiming receipt or semantic QC.
        heads=contained(package,heads)
        head_row=link(heads,'well_headers','well_headers','All_well_headers','CSV',
                      'native_model_cross_checks',digest=sha256(heads))
        if head_row is not None:
            head_row['integrity']='delivery_sha256_freshly_computed'
    workflows={} if spatial_checkpoint else read(package/'01_project_metadata/workflow_recovery.json')
    for item in workflows.get('objects',[]):
        if item.get('status')!='definition_recovered':continue
        for artifact in item['artifacts']:
            link(package/'native_workflows'/artifact['path'],'workflows',item['object_id'],item['name'],
                 'JSON definition (not executable)','typed_structure_only','not_applicable',artifact.get('sha256'))
    if not spatial_checkpoint:
        # Final publication has a fresh output integrity boundary. The exact-ZGY
        # route uses <run>/report; a project route uses <run>/extraction/package.
        has_seismic=any(item.get('status')=='converted' for item in inventory.get('objects',[]))
        if package.parent.name=='package' and package.parent.parent.name=='extraction':
            seismic_run=package.parent.parent.parent
        elif package.name=='report':
            seismic_run=package.parent
        else:
            seismic_run=None
        if has_seismic and seismic_run is None:
            seismic_candidates=[]
            seismic_findings=['Seismic delivery withheld: unsupported package/run layout']
        elif has_seismic:
            seismic_candidates,seismic_findings=validate_seismic_rows(inventory,seismic_run,fresh_hash=True)
        else:
            seismic_candidates,seismic_findings=[],[]
        findings.extend(seismic_findings)
        invalid_seismic={row['object_id'] for row in seismic_invalidations(checkpoint_index or {})}
        seismic_gap=bool(seismic_findings or invalid_seismic)
        for finding in seismic_findings:
            current('seismic','invalid_declared_seismic','receipt_and_artifact_validation')
            outcome('seismic','failed')
            event('delivery_failed',severity='error',category='seismic',reason=finding)
        for candidate in seismic_candidates:
            item=candidate['row']
            if candidate['identity'] in invalid_seismic:
                findings.append('Previously invalid seismic checkpoint withheld from final accepted index: '+candidate['identity'])
                current('seismic',candidate['identity'],'checkpoint_invalidation')
                outcome('seismic','failed')
                continue
            unit=candidate['receipt']['summary'].get('profile',{}).get('source_vertical_unit','unknown')
            for artifact in candidate['artifacts']:
                source=artifact['source']
                link(source,'seismic',item['id'],item['name'],source.suffix[1:].upper(),
                    'every_amplitude_and_trace_geometry',unit,artifact['sha256'])
    if not spatial_checkpoint:
        companion_candidates,companion_inventory_gap=_companion_candidates(package,findings)
        for source,category,identity,source_ref,owner,digest,relative,registration_scope,registration_format_warning in companion_candidates:
            row=link(source,category,identity,Path(source_ref).name,source.suffix[1:].upper(),
                     'companion_format_conversion_and_file_integrity_only', 'unknown',digest,
                     recovery_status='converted_and_preserved',
                     metadata_warning=('Companion top: historical well_top package-scan registration; exact source binding from companion inventory. '
                                       if registration_scope=='well_top_package_scan_inventory_bound' else 'Companion conversion; ')+
                                      'Native numerical readback, units, CRS and scientific/import acceptance not established. '+registration_format_warning,
                     source_lane='companion',source_reference=source_ref,
                     source_format=owner.get('extension') or 'unknown',derivative_reference=relative,
                     registration_scope=registration_scope,registration_format_warning=registration_format_warning)
            if row is not None:row['integrity']='manifest_sha256_freshly_verified; preserved_source_sha256_freshly_verified'
    # An interrupted category can have a checkpoint without aggregate evidence.
    # Such rows remain visible only with their partial validation scope.
    if spatial_checkpoint:
        # The caller merges these rows into its partial checkpoint atomically.
        # Never replace its accepted logs/seismic or imply final package approval.
        for row in records:
            row['integrity']+='; completed_decoder_receipt_and_table_row_count; package_QC_not_established'
            row['numerical_qc']+='; recorded_decoder_checks_only; package_QC_not_established'
            row['receipt_signature']=spatial_signature
        delivered={r['object_id'] for r in records if r['category'] not in ('point_identities','point_attributes','polygon_attributes','polygon_parts','point_missing_vertices','polygon_missing_vertices')}
        absent=[x['object_id'] for x in spatial.get('objects',[])
                if x.get('status')=='decoded' and x['object_id'] not in delivered]
        if absent:findings.append('Decoded spatial objects without retained table rows: '+', '.join(absent))
        return dict(partial=True, status='stopped_partial', files=records, findings=findings,
                    package_qc='not_established', source_integrity='not_rechecked')
    existing={(r['category'],r['object_id'],r['path']) for r in records}
    for row in (checkpoint_index or {}).get('files',[]):
        if row.get('source_lane')=='companion' or str(row.get('category','')).startswith('companion_'):
            # Final companion publication is rebuilt only from fresh inventory and hashes.
            continue
        key=(row['category'],row['object_id'],row['path'])
        if key in existing:continue
        checkpoint_gap=True
        try:
            raw_source=Path(os.path.abspath(destination/row['path']))
            for parent in (raw_source,*raw_source.parents):
                if parent.is_symlink() or parent.is_junction():
                    raise ValueError('Checkpoint path has linked ancestry')
            source=raw_source.resolve(strict=True)
            if row.get('status')!='exported' or not source.is_file() or source.stat().st_size!=row['bytes']:
                raise ValueError('Checkpoint file no longer matches its committed size')
        except (OSError,ValueError) as exc:
            if systemic(exc):raise
            findings.append(str(exc)+': '+row['path']);continue
        # Hardlinks stay below destination; filesystem fallback stays in package
        # or the verified seismic run sibling. Never accept an arbitrary path.
        if not source.is_relative_to(destination) and not source.is_relative_to(package):
            findings.append('Checkpoint fallback outside native package not merged: '+row['path']);continue
        declared=str(row.get('sha256') or '').lower()
        if not re.fullmatch(r'[0-9a-f]{64}',declared):
            findings.append('Checkpoint SHA-256 missing or invalid: '+row['path']);continue
        try:
            actual=sha256(source)
        except OSError as exc:
            if systemic(exc):raise
            findings.append('Checkpoint SHA-256 unavailable for '+row['path']+': '+str(exc));continue
        if actual.lower()!=declared:
            findings.append('Checkpoint SHA-256 mismatch: '+row['path']);continue
        partial={k:v for k,v in row.items() if k!='receipt_signature'}
        partial.setdefault('recovery_status','')
        partial.setdefault('metadata_warning','')
        partial['status']='checkpoint_only_partial'
        partial['integrity']='fresh_sha256_matched_checkpoint_receipt; package_QC_not_established'
        partial['numerical_qc']=(str(row.get('numerical_qc') or 'recorded_checkpoint_checks_only')+
                                 '; final_accepted_QC_binding_not_established')
        partial_files.append(partial);existing.add(key)
        findings.append('Checkpoint-only export retained as partial; final accepted QC binding not established: '+row['path'])
    findings.extend((checkpoint_index or {}).get('findings',[]))
    findings.extend((checkpoint_index or {}).get('worker_errors',[]))
    for row in records:
        row.setdefault('recovery_status', '')
        row.setdefault('metadata_warning', '')
    fields=['object_id','name','category','format','label','path','bytes','status','numerical_qc','units','sha256','integrity','storage','record_count','recovery_status','metadata_warning','source_lane','source_reference','source_format','derivative_reference','registration_scope','registration_format_warning']
    import io
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(records)
    atomic_text(destination/'FILE_INDEX.csv','\ufeff'+stream.getvalue())
    delivery_failure=any(stats['failed'] for stats in category_stats.values())
    is_partial=bool(checkpoint_gap or companion_inventory_gap or rejected_native or seismic_gap or
                    delivery_failure or missing_primary)
    result=dict(product=PRODUCT,version=VERSION,files=records,partial_files=partial_files,
        partial=is_partial,
        status='partial' if is_partial else 'delivery_indexed',
        findings=list(dict.fromkeys(findings)),
        scope='Accepted decoded/converted objects only. Full project inventory is a separate report section.',
        storage_note='Large accepted files use hard links: one physical copy with a shallow user path and validated package compatibility path. Copying the entire run to another filesystem may duplicate hard-linked bytes. Keep the report, EXPORTS and data directories together.')
    invalid_markers=seismic_invalidations(checkpoint_index or {})
    if invalid_markers:
        result['invalid_seismic']=invalid_markers
    atomic_json(destination/'FILE_INDEX.json',result)
    for category,stats in category_stats.items():
        event('delivery_category_completed',category=category,**stats)
    event('delivery_completed',category_count=len(category_stats),file_count=len(records),
          finding_count=len(result['findings']),duration_seconds=round(time.monotonic()-started,3))
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
    complete=context.get('seismic_inventory_complete',context.get('model_readable',False))
    seismic_count=str(len(seismic)) if complete else f'{len(seismic)} discovered; total unknown (metadata incomplete)'
    metadata_findings=context.get('findings',[])
    metadata_note=('<div class="note"><strong>Metadata limitations:</strong><ul>'+''.join(
        '<li>'+escape(str(item))+'</li>' for item in metadata_findings)+'</ul></div>') if metadata_findings else ''
    record_errors=context.get('model_record_errors',[])
    metadata_note+=('<details><summary>'+str(len(record_errors))+' unresolved model records — reasons and candidate identities</summary><pre>'+
                    escape(json.dumps(record_errors,indent=2))+'</pre></details>') if record_errors else ''
    base=sum(not x.get('virtual') for x in seismic);virtual=sum(bool(x.get('virtual')) for x in seismic)
    rows=''.join('<tr><td>'+escape(k)+'</td><td>'+escape(str(v))+'</td></tr>' for k,v in [
        ('Latest saved Petrel version',value('saved_version')),('Original Petrel version',value('original_version')),
        ('Recorded build label',context.get('build_label') or 'unknown'),
        ('Native last save',str(saved.get('value','unknown'))+'; timezone '+str(saved.get('timezone','unknown'))),
        ('Filesystem last write (different evidence)',context.get('filesystem_last_write_utc','unknown')),
        ('Storage family',context.get('layout','unknown')),('Saved by (recorded)',context.get('saved_by') or 'unknown'),
        ('Project hierarchy inventory','complete' if context.get('object_inventory_complete') else 'incomplete / unavailable'),
        ('Model metadata','complete' if context.get('model_metadata_complete') else 'incomplete / unavailable')])
    workflow_parts=[]
    for w in context.get('workflows',{}).get('objects',[]):
        recovered=w.get('status')=='definition_recovered'
        outcome=(str(w.get('command_count',0))+' serialized entries recovered' if recovered
                 else 'not recovered ('+str(w.get('status','unknown'))+')')
        identity='; registry slot '+str(w['registry_slot']) if 'registry_slot' in w else ''
        reason='<p>'+escape(str(w['reason']))+'</p>' if w.get('reason') else ''
        commands=('<ol>'+''.join('<li>'+escape(str(c))+'</li>' for c in w.get('command_types',[]))+'</ol>') if recovered else ''
        workflow_parts.append('<details><summary>'+escape(str(w.get('name','Workflow')))+' — '+escape(outcome+identity)+'</summary>'+reason+commands+'</details>')
    workflow_rows=''.join(workflow_parts)
    dependency_rows=''.join('<tr><td>'+escape(str(d.get('name','')))+' '+escape(str(d.get('version','')))+'</td><td>'+escape(str(d.get('license','unspecified'))[:300])+'</td></tr>' for d in context.get('software_licenses',[]))
    return f'''<section id="project-identity"><h2>Project identity and compatibility</h2>
    <table>{rows}</table><p>Version values come from native metadata when present; unknown values are not inferred from file names. A detected release is not a claim of complete support for that release.</p>
    {metadata_note}<p>Native seismic objects: <b>{seismic_count}</b> ({base} base, {virtual} virtual discovered). These are object definitions, not a count of accessible ZGY files. File availability, previews and conversion outcomes are listed separately below.</p>
    <details><summary>Native payload types and recorded history</summary><pre>{escape(json.dumps(dict(payload_types=types,history=context.get('history',[]),findings=context.get('findings',[])),indent=2))}</pre></details>
    <h3>Licenses and attribution</h3><p>Field Viewer Data Extractor is licensed under the <a href="https://www.apache.org/licenses/LICENSE-2.0">Apache License, Version 2.0</a>. Copyright 2026 Ahmed Saher Nouh.</p>
    <p>Original project creator and principal author: <b>Ahmed Saher Nouh</b>. <a href="https://saherlabs.dev/">Website</a> · <a href="https://github.com/ahmedsahernouh/petrel-headless-extractor">Project repository</a>. See LICENSE and NOTICE in the distribution.</p>
    <p>Bundled dependencies retain their own licenses and attribution notices; see THIRD_PARTY_NOTICES.md. Petrel project data and extracted datasets retain their original ownership and permissions; the software license does not relicense them.</p>
    <p>Project license metadata: {'recorded fields below; these do not establish current entitlement' if has_license_value else 'no license value recorded in the inspected fields; current Petrel license and modules cannot be determined from this project' }.</p>
    <pre>{escape(json.dumps(licenses,indent=2)) if licenses else ''}</pre>
    <details><summary>Bundled dependency license metadata</summary><p>Display summaries; full license texts are retained in the installed distribution.</p><table>{dependency_rows or '<tr><td>Dependency inventory not available in this report context.</td></tr>'}</table></details>
    <p>FV / FieldViewer is a visual family affiliation only. No FieldViewer application code is required.</p>
    <h3>Saved workflow definitions and outcomes</h3><p>Readable typed definitions preserve serialized order and references. Unrecovered definitions retain their reasons below. Listed steps do not establish executable control flow. Execution and native re-import are not supported.</p>{workflow_rows or '<p>No saved workflow definition was recovered in this run.</p>'}</section>'''


def exports_section(index, destination, report):
    groups={}
    for item in index.get('files',[]):groups.setdefault((item['category'],item['object_id'],item['name']),[]).append(item)
    rows=[]
    unresolved_groups=0
    companion_files=sum(1 for item in index.get('files',[]) if item.get('source_lane')=='companion')
    native_files=len(index.get('files',[]))-companion_files
    for (category,identity,name),items in sorted(groups.items()):
        links=' · '.join('<a href="'+href(Path(destination)/x['path'],report)+'">'+escape(x.get('label',x['format']))+'</a>' for x in items)
        units=items[0].get('units')
        unresolved=units is None or str(units).strip().lower() in ('','unknown','unresolved','none')
        unresolved_groups+=int(unresolved)
        unit_text='Unresolved — exported values retained' if unresolved else str(units)
        recovery_status='; '.join(dict.fromkeys(str(x.get('recovery_status') or '') for x in items if x.get('recovery_status')))
        metadata_warning='; '.join(dict.fromkeys(str(x.get('metadata_warning') or '') for x in items if x.get('metadata_warning')))
        source_text='Companion source: '+items[0].get('source_reference','') if items[0].get('source_lane')=='companion' else ''
        rows.append('<tr><td>'+escape(category)+'</td><td>'+escape(name)+'<br><small>'+escape(identity)+'</small></td><td>'+links+'</td><td>'+escape(unit_text)+'</td><td>'+escape(items[0]['numerical_qc'])+'</td><td>'+escape(recovery_status)+'</td><td>'+escape(metadata_warning)+'<br>'+escape(source_text)+'</td></tr>')
    partial_rows=[]
    for item in index.get('partial_files',[]):
        partial_rows.append('<li>'+escape(str(item.get('category','')))+' — '+
            '<a href="'+href(Path(destination)/item['path'],report)+'">'+escape(str(item.get('name') or item['path']))+'</a>'+
            ' (checkpoint-only; fresh SHA-256 matched; final accepted QC binding unestablished)'+
            (' — '+escape(str(item['recovery_status'])) if item.get('recovery_status') else '')+
            (' — '+escape(str(item['metadata_warning'])) if item.get('metadata_warning') else '')+'</li>')
    partial_section=('<details><summary>Checkpoint-only outputs — '+str(len(partial_rows))+
                     ' partial, QC unestablished</summary><ul>'+''.join(partial_rows)+'</ul></details>') if partial_rows else ''
    status_note=('<p role="status"><strong>Delivery partial:</strong> Some outputs lack final evidence; '
                 'see checkpoint-only outputs and delivery findings below.</p>') if index.get('partial') else ''
    return f'''<section id="converted-data"><h2>Open the extracted data</h2><p><b>{len(groups)} export groups · {len(index.get('files',[]))} files ({native_files} native/spatial/workflow/seismic; {companion_files} companion derivatives).</b> This index contains accepted outputs, separate from the complete native project inventory. A group represents one recovered object or an explicitly labelled aggregate table, such as all well headers.</p>
    {status_note}
    <p><a href="{href(Path(destination)/'FILE_INDEX.csv',report)}">Download file index (CSV)</a> · <a href="{href(Path(destination),report)}/">Open EXPORTS folder</a></p>
    <p><b>{unresolved_groups} export groups have unresolved reported units.</b> Their files are available; unresolved units are a metadata limitation, not a failed numeric recovery. Consult each metadata sidecar for depth/XY units, CRS and remaining limitations. Numerical read-back does not establish physical units or scientific suitability.</p>
    <p><code>samples.csv</code> means the full recovered well-log sample table: sample index, measured depth, raw value and null flag. It is not a small preview. Seismic figures and their amplitude statistics are bounded previews, not full-volume statistics. Unknown units require user interpretation when loading the data.</p>
    <input aria-label="Search converted data" placeholder="Filter converted objects or formats…" oninput="document.querySelectorAll('#exports-table tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase()))">
    <div class="scroll"><table id="exports-table"><thead><tr><th>Category</th><th>Object</th><th>Files</th><th>Units</th><th>Validation scope</th><th>Recovery status</th><th>Metadata / computation limitations</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7">No accepted exports yet. Consult the inventory and per-object reasons.</td></tr>'}</tbody></table></div>
    {partial_section}<p>{escape(index.get('storage_note',''))}</p><details><summary>Delivery findings</summary><pre>{escape(json.dumps(index.get('findings',[]),indent=2))}</pre></details></section>'''


def decorate(report, context, index, destination, log_path=None, event_path=None, preparation_report=None):
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
    contract=Path(destination)/'INTEROPERABILITY_CURRENT.json'
    if contract.is_file():
        extra+='<p><a href="'+href(contract,report)+'">Interoperability catalogue</a>: source identities, available artifacts and unresolved prerequisites. OpendTect/PaleoScan import is not verified.</p>'
    if preparation_report is not None:
        extra+='<p><a href="'+href(preparation_report,report)+'">Search preparation evidence and blockers</a>: object identities, file availability and source/OpendTect/PaleoScan preparation assessments.</p>'
    extra+=metadata_section(context)
    text=text.replace('<nav>','<nav><a href="#converted-data">Extracted files</a><a href="#project-identity">Version and history</a>',1)
    text=text.replace('<main>','<main>'+extra,1)
    if '<main>' not in text:text=text.replace('<body>','<body>'+extra,1)
    text=text.replace('not_registered','not listed in package manifest (not a decode failure)')
    atomic_text(report,text)


def partial_report(report, context, status, reason, log_path=None, preserve=False):
    if preserve and Path(report).is_file():
        text=Path(report).read_text(encoding='utf-8')
        banner='<section role="alert" style="border:2px solid #ba6711;padding:16px"><h2>'+escape(status)+'</h2><p>'+escape(reason)+'</p><p>Previously generated content is retained; consult the final run result for completeness.</p></section>'
        atomic_text(report,text.replace('<main>','<main>'+banner,1))
        return
    groups={}
    for item in context.get('objects',[]):groups.setdefault(item['category'],[]).append(item)
    tree=''.join('<details><summary>'+escape(kind)+' ('+str(len(items))+')</summary><ul>'+''.join('<li>'+escape(x['name'])+' <small>'+escape(x['object_id'])+'</small></li>' for x in items)+'</ul></details>' for kind,items in sorted(groups.items()))
    text='<!doctype html><html><head><meta charset="utf-8"><title>'+PRODUCT+' report</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:20px;background:#f5f8fc;color:#172638}table{border-collapse:collapse}td{padding:8px;border-bottom:1px solid #ccc}pre{white-space:pre-wrap}details{margin:12px}</style></head><body><main><h1>'+PRODUCT+'</h1><h2>'+escape(status)+'</h2><p>'+escape(reason)+'</p>'+metadata_section(context)+'<h2>Native project inventory (metadata)</h2>'+tree
    if log_path:text+='<p><a href="'+href(log_path,report)+'">Process log</a></p>'
    text+='</main></body></html>'
    atomic_text(report,text)


def diagnostics_section(report, status, stages, summary, log_path, events_path):
    """Update status independently of figures; preserve any completed report content."""
    report=Path(report)
    rows=''.join('<tr><td>'+escape(str(r.get('category','')))+ '</td><td>'+escape(str(r.get('status','')))+ '</td><td>'+escape(str(r.get('reason') or 'See per-object outcomes'))+'</td></tr>' for r in stages)
    section='<section id="run-diagnostics"><h2>Run status: '+escape(status.replace('_',' '))+'</h2><p><a href="'+href(summary,report)+'">Readable diagnostic summary</a> · <a href="'+href(log_path,report)+'">Full process log</a> · <a href="'+href(events_path,report)+'">Structured events</a></p><p>Validated exports remain available when an independent object or optional figure fails. Pending files are excluded from the converted-data index. Receipt integrity and extraction coverage are separate checks.</p><table><tr><th>Category</th><th>Outcome</th><th>Reason</th></tr>'+rows+'</table></section>'
    text=report.read_text(encoding='utf-8')
    text=re.sub(r'<section id="run-diagnostics">.*?</section>','',text,flags=re.S)
    atomic_text(report,text.replace('<main>','<main>'+section,1))
