# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Write source-bound OpendTect 7 native surveys and CBVS offline, without OpendTect."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import re
import struct

from fieldviewer_connectors import _digest_file, _write_json
from fieldviewer_handoff_intake import _read_json, _value_hash
from fieldviewer_seismic_native import seismic_context, _geometry, _little_samples
from geoviewer_contract import _unlinked
from geoviewer_receiving_profiles import _bounded_json, MAX_DECISIONS_BYTES
from validate_geoviewer_contract import _recheck_bindings

PROFILE='opendtect-7.0.10-native-cbvs-survey/1'
PROJECTED_PROFILE='opendtect-7.0.10-projected-cbvs-survey/1'
# Deliberately bounded to the metric projected CRS checked against a saved native
# 7.0.10 survey and independent native reader. This is not an EPSG resolver.
PROJECTED_CRS={'EPSG:20439':'Ain el Abd / UTM zone 39N'}
SCHEMA='fieldviewer.opendtect-native-survey/1'
GROUPS=(('Seismics',100010,'Seismics','Seismic directory','Seismics'),
    ('Surfaces',100020,'Surfaces','Surfaces directory','Surfaces directory'),
    ('Locations',100030,'Locations','Locations directory','Locations'),
    ('Features',100040,'Analysis features','Features directory','Features'),
    ('WellInfo',100050,'WellInfo','Well Info directory','WellInfo'),
    ('NLAs',100060,'Non-Linear Analyses','NLA directory','NLAs'),
    ('Misc',100070,'Misc','Miscellaneous directory','Misc'),
    ('Attribs',100080,'Attribs','Atribute Definitions','Attribs'),
    ('Models',100090,'Models','Earth Models directory','Models'),
    ('Geometry',100100,'Geometry','Survey Geometry Directory','Geometry'))
FOOTER=struct.pack('<i',0)+b'\x01BGd'
UNDEFINED_RANGE=tuple(struct.unpack('<f',struct.pack('<f',v))[0] for v in (9.99999e29,1.00001e30))


def _name(value):
    if (not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value)
            or value.upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(10)),*(f'LPT{i}' for i in range(10))}):
        raise ValueError('Use a safe ASCII survey/dataset/component name, at most 64 characters')
    return value


def _coefficients(g):
    if g['di']<=0 or g['dc']<=0:
        raise ValueError('This CBVS profile requires increasing inline/crossline axes')
    result=[]
    for axis in (0,1):
        b=g['inline_basis'][axis]/g['di'];c=g['crossline_basis'][axis]/g['dc']
        result += [g['origin'][axis]-b*g['il0']-c*g['xl0'],b,c]
    return result


def _cbvs_header(g,component):
    """CBVS v2: one IEEE float component, regular grid, no per-trace aux data."""
    name=_name(component).encode('ascii')
    first=bytearray(b'dGB\x01\x02\x00\x02\x00'+b'\0'*8)
    first.extend(struct.pack('<iii',0,1,len(name))) # stdtext length, components, name length
    first.extend(name)
    first.extend(struct.pack('<i',0)+b'\x12\x01\x00\x00') # unknown data type, signed IEEE float32 LE
    first.extend(struct.pack('<ffiff',g['time0']/1000,g['dt_ms']/1000,g['ns'],0.,1.))
    first.extend(struct.pack('<8i',1,1,g['il0'],g['xl0'],g['il0']+(g['ni']-1)*g['di'],
        g['xl0']+(g['nx']-1)*g['dc'],g['di'],g['dc']))
    first.extend(struct.pack('<6d',*_coefficients(g)))
    first.extend(struct.pack('<i',0)) # sequence number
    padding=(-len(first)-4)%4
    first.extend(struct.pack('<i',padding)+b' '*padding)
    struct.pack_into('<i',first,8,len(first))
    return bytes(first)


def _stamp(value=None):
    if value is None:return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):
        raise ValueError('Invalid native survey creation timestamp')
    datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ')
    return value


def _document(kind,body,stamp):
    return ('dTect V7.0.10\n'+kind+'\n'+stamp+'\n!\n'+body).encode('utf-8')


def _metadata(survey,dataset,g,stamp):
    def f(v):return format(v,'.17g')
    coeff=_coefficients(g)
    il1=g['il0']+(g['ni']-1)*g['di'];xl1=g['xl0']+(g['nx']-1)*g['dc']
    x,y=g['origin'];ix,iy=g['inline_basis'];cx,cy=g['crossline_basis']
    if g.get('crs'):
        crs=g['crs']
        coordinate_system=(f'Coordinate System.Projection.Name: {PROJECTED_CRS[crs]}\n'
            'Coordinate System.System name: ProjectionBased System\n'
            f'Coordinate System.Projection.ID: EPSG`{crs.split(":")[1]}\n')
    else:
        coordinate_system='Coordinate System.XY in Feet: No\nCoordinate System.System name: Unlocated XY\n'
    body=(f'Name: {survey}\nSurvey Data Type: Only 3D\n'
        f"In-line range: {g['il0']}`{il1}`{g['di']}\nCross-line range: {g['xl0']}`{xl1}`{g['dc']}\n"
        f"Z range: {f(g['time0']/1000)}`{f((g['time0']+(g['ns']-1)*g['dt_ms'])/1000)}`{f(g['dt_ms']/1000)}`T\n"
        'Coord-X-BinID: '+'`'.join(map(f,coeff[:3]))+'\nCoord-Y-BinID: '+'`'.join(map(f,coeff[3:]))+'\n'
        f"Set Point.1: {g['il0']}/{g['xl0']}`({x},{y})\n"
        f"Set Point.2: {il1}/{g['xl0']}`({x+(g['ni']-1)*ix},{y+(g['ni']-1)*iy})\n"
        f"Set Point.3: {g['il0']}/{xl1}`({x+(g['nx']-1)*cx},{y+(g['nx']-1)*cy})\n"
        +coordinate_system+'Seismic Reference Datum: 0\n!\n')
    files={'.omf':_document('Object Management file','ID: -1\n!\nAppl dir: 1\nAppl: dGB`Stream\n$Name: appl\n!\n',stamp),
        survey+'/.survey':_document('Survey Info',body,stamp)}
    root='ID: 0`1\n!\nSurvey root: 1\nSurvey Info: dGB`Stream\n$Name: .survey\n!\n'
    for folder,group,label,translator,selfname in GROUPS:
        root+=f'@{group}: {folder}\n!\n'
        child=f'ID: {group}'+('`2' if folder=='Seismics' else '')+'\n!\n'
        child+=f'{label}: 1\n{translator}: dGB`Stream\n$Name: {selfname}\n!\n'
        if folder=='Seismics':
            child+=f'{dataset}: 2\nSeismic Data: CBVS`Stream\n$Name: {dataset}.cbvs\n#Version: 7.0.10\n!\n'
        files[survey+'/'+folder+'/.omf']=_document('Object Management file',child,stamp)
    files[survey+'/.omf']=_document('Object Management file',root,stamp)
    return files


def _context(bundle,contract,entity,artifact,decisions,retention_view):
    proof,catalogue,bindings,selected,source=seismic_context(
        bundle,contract,entity,artifact,decisions,retention_view,target='opendtect')
    # Collect all declarations, including reviewed ones. Never reproject or let
    # a planned receiving CRS override a contradictory source declaration.
    unknown=(None,'','unknown','unresolved','Unlocated XY')
    declarations=[]
    horizontal_units=[]
    planned=None
    for row in catalogue['tables']['metadata_evidence']:
        if row['entity_key']!=entity:continue
        value=row['raw_value']
        crs=value.get('crs') if row['field'] in ('seismic_conversion','seismic_source') and isinstance(value,dict) else value if row['field']=='crs' else None
        if crs not in unknown:
            declarations.append(crs)
        unit=value.get('horizontal_unit') if row['field'] in ('seismic_conversion','seismic_source') and isinstance(value,dict) else value if row['field']=='horizontal_unit' else None
        if unit not in unknown:horizontal_units.append(unit)
        convention=value.get('time_convention') if row['field'] in ('seismic_conversion','seismic_source') and isinstance(value,dict) else value if row['field']=='time_convention' else None
        if convention not in unknown and convention!='TWT':
            raise ValueError('Known source time convention cannot be relabelled as native TWT')
    if decisions:
        record=_bounded_json(Path(decisions),MAX_DECISIONS_BYTES)['entities'].get(entity,{})
        asserted=record.get('source_assertions',{}).get('crs',{}).get('value')
        planned=record.get('survey',{}).get('crs')
        if asserted not in unknown:declarations.append(asserted)
        horizontal_units.extend(value for value in (
            record.get('source_assertions',{}).get('horizontal_unit',{}).get('value'),
            record.get('survey',{}).get('horizontal_unit')) if value not in unknown)
        conventions=(record.get('source_assertions',{}).get('time_convention',{}).get('value'),
                     record.get('survey',{}).get('time_convention'))
        if any(value not in unknown and value!='TWT' for value in conventions):
            raise ValueError('Reviewed time convention cannot be relabelled as native TWT')
    if planned=='Unlocated XY' and declarations:
        raise ValueError('Conflicting source/review CRS declarations; projected source cannot become Unlocated XY')
    if planned not in unknown:
        if not declarations:
            raise ValueError('Planned survey CRS requires source evidence or a reviewed source CRS assertion')
        if any(value!=planned for value in declarations):
            raise ValueError('Conflicting source/review CRS declarations; reprojection is not supported')
    geometry=_geometry(source,selected['recorded_sha256'],require_scalar_one=False,undefined_range=UNDEFINED_RANGE)
    if declarations:
        if any(value!=declarations[0] for value in declarations):
            raise ValueError('Conflicting source/review CRS declarations; reprojection is not supported')
        if any(not isinstance(value,str) or value not in PROJECTED_CRS for value in declarations):
            raise ValueError('Known source CRS is unsupported by the bounded projected-survey profile')
        if any(value!='m' for value in horizontal_units):
            raise ValueError('Projected CRS requires consistent metre horizontal-unit declarations (m)')
        geometry['crs']=declarations[0]
    _coefficients(geometry)
    return proof,catalogue,bindings,selected,source,geometry


def _write_cbvs(source,target,g,component,sha):
    digest=hashlib.sha256()
    with source.open('rb') as reader,target.open('xb') as writer:
        writer.write(_cbvs_header(g,component))
        digest.update(reader.read(3600))
        while raw:=reader.read(240+4*g['ns']):
            if len(raw)!=240+4*g['ns']:raise ValueError('Source changed during CBVS write')
            digest.update(raw);writer.write(_little_samples(raw[240:]))
        writer.write(FOOTER)
    if digest.hexdigest()!=sha:raise ValueError('Source changed during CBVS write; no completion marker')


def _verify_cbvs(source,target,g,component):
    with source.open('rb') as reader,target.open('rb') as cbvs:
        header=_cbvs_header(g,component)
        if cbvs.read(len(header))!=header:raise ValueError('CBVS header/geometry mismatch')
        reader.seek(3600);count=0
        while raw:=reader.read(240+4*g['ns']):
            if len(raw)!=240+4*g['ns'] or cbvs.read(4*g['ns'])!=_little_samples(raw[240:]):
                raise ValueError('CBVS sample bit mismatch')
            count+=1
        if count!=g['ni']*g['nx'] or cbvs.read(8)!=FOOTER or cbvs.read(1):
            raise ValueError('CBVS trace count/trailer mismatch')


def _receipt(proof,selected,g,survey,dataset,component,stamp):
    return dict(schema=SCHEMA,profile=PROJECTED_PROFILE if g.get('crs') else PROFILE,state='offline_verified_candidate',
        survey_directory=survey,dataset_name=dataset,component_name=component,
        created_at=stamp,generation=proof['generation'],manifest_sha256=proof['manifest_sha256'],
        upstream_handoff_sha256=proof['handoff_sha256'],upstream_profile_revision=proof['profile_revision'],
        entity_key=proof['selected_entity_key'],artifact_key=selected['artifact_key'],
        source_sha256=selected['recorded_sha256'],upstream_entity_preparation=proof['selected_upstream_preparation'],
        geometry=g,crs=g.get('crs','Unlocated XY; no georeferencing assigned'),seismic_reference_datum_policy='Unresolved; zero native default, not a surveyed datum',
        native_samples_verified=g['ni']*g['nx']*g['ns'],native_sample_bits_exact=True,
        native_z_unit='s',source_z_unit='ms',application_used=False,application_required=False,
        native_libraries_used=False,receiving_open_verified=False,
        runtime_boundary='zero_gui_python_stdlib_no_opendtect_installation',
        limits=('Single finite IEEE float32 component, regular increasing 3D grid, integral metre coordinates, time axis; '+
            ('EPSG:20439 declarations preserved without reprojection or independent source CRS qualification. ' if g.get('crs') else 'local XY only. ')+
            'No wells, horizons, null semantics or desktop-open/session qualification.'))


def create_survey(bundle,contract,entity_key,artifact_key,output,*,survey_name='Prepared_OpendTect',
                   dataset_name='Seismic',component_name='amplitude',decisions=None,retention_view=None):
    survey,dataset,component=map(_name,(survey_name,dataset_name,component_name))
    proof,catalogue,bindings,selected,source,g=_context(bundle,contract,entity_key,artifact_key,decisions,retention_view)
    proof['selected_entity_key']=entity_key
    output=Path(output).absolute();_unlinked(output);output=output.resolve(strict=False)
    protected=[*catalogue['roots'].values(),Path(bundle).resolve()]
    protected += [Path(p).resolve() for p in (decisions,retention_view) if p]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise ValueError('Native survey output must be separate from source, handoff and review inputs')
    output.mkdir(parents=True,exist_ok=False)
    stamp=_stamp()
    for relative,raw in _metadata(survey,dataset,g,stamp).items():
        path=output/relative;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(raw)
    target=output/survey/'Seismics'/(dataset+'.cbvs')
    _write_cbvs(source,target,g,component,selected['recorded_sha256'])
    _verify_cbvs(source,target,g,component)
    native_sha=_digest_file(target)
    _recheck_bindings(bindings)
    final,_,_,_,_,final_g=_context(bundle,contract,entity_key,artifact_key,decisions,retention_view)
    final['selected_entity_key']=entity_key
    if _value_hash(final)!=_value_hash(proof) or _value_hash(g)!=_value_hash(final_g) or _digest_file(target)!=native_sha:
        raise ValueError('Native survey inputs/output changed; no completion marker')
    metadata=_metadata(survey,dataset,g,stamp)
    for relative,raw in metadata.items():
        path=output/relative;_unlinked(path)
        if path.read_bytes()!=raw:raise ValueError('Survey metadata changed; no completion marker')
    expected=set(metadata)|{target.relative_to(output).as_posix()}
    actual=set()
    for p in output.rglob('*'):
        _unlinked(p)
        if p.is_file():actual.add(p.relative_to(output).as_posix())
    if actual!=expected:raise ValueError('Unexpected survey output; no completion marker')
    result=_receipt(proof,selected,g,survey,dataset,component,stamp)
    result['files']=[dict(path=p.relative_to(output).as_posix(),bytes=p.stat().st_size,sha256=_digest_file(p))
        for p in sorted(output.rglob('*')) if p.is_file()]
    _write_json(output/'OPENDTECT_PROJECT.json',result)
    return result


def verify_survey(output,bundle,contract,*,decisions=None,retention_view=None):
    output=Path(output).absolute();_unlinked(output)
    stored,marker_sha=_read_json(output/'OPENDTECT_PROJECT.json',2*1024*1024)
    survey,dataset,component=map(_name,(stored['survey_directory'],stored['dataset_name'],stored['component_name']))
    proof,_,bindings,selected,source,g=_context(bundle,contract,stored['entity_key'],stored['artifact_key'],decisions,retention_view)
    proof['selected_entity_key']=stored['entity_key']
    stamp=_stamp(stored['created_at'])
    expected=_receipt(proof,selected,g,survey,dataset,component,stamp)
    if _value_hash({k:v for k,v in stored.items() if k!='files'})!=_value_hash(expected):
        raise ValueError('Survey receipt differs from current source/profile')
    metadata=_metadata(survey,dataset,g,stamp)
    target=output/survey/'Seismics'/(dataset+'.cbvs')
    names=set(metadata)|{target.relative_to(output).as_posix()}
    if not isinstance(stored.get('files'),list) or len(stored['files'])!=len(names) or {f['path'] for f in stored['files']}!=names:
        raise ValueError('Native survey file inventory mismatch')
    actual=set()
    for p in output.rglob('*'):
        _unlinked(p)
        if p.is_file():actual.add(p.relative_to(output).as_posix())
    if actual!=names|{'OPENDTECT_PROJECT.json'}:raise ValueError('Unexpected/missing survey member')
    for item in stored['files']:
        p=output/item['path']
        if p.stat().st_size!=item['bytes'] or _digest_file(p)!=item['sha256']:
            raise ValueError('Native survey member hash/size mismatch')
    for relative,raw in metadata.items():
        if (output/relative).read_bytes()!=raw:raise ValueError('Native survey registration/geometry mismatch')
    _verify_cbvs(source,target,g,component)
    _recheck_bindings(bindings)
    final,_,_,_,_,final_g=_context(bundle,contract,stored['entity_key'],stored['artifact_key'],decisions,retention_view)
    final['selected_entity_key']=stored['entity_key']
    if _value_hash(final)!=_value_hash(proof) or g!=final_g or _digest_file(output/'OPENDTECT_PROJECT.json')!=marker_sha:
        raise ValueError('Survey inputs changed during verification')
    _recheck_bindings([(output/f['path'],f['sha256'],f['bytes']) for f in stored['files']]
        +[(output/'OPENDTECT_PROJECT.json',marker_sha,2*1024*1024)])
    return dict(schema='fieldviewer.opendtect-native-survey-verification/1',profile=expected['profile'],
        state='offline_verified_candidate',native_sample_bits_exact=True,
        native_samples_verified=g['ni']*g['nx']*g['ns'],application_used=False,receiving_open_verified=False)
