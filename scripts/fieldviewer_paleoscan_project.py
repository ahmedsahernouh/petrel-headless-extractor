# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Experimental offline writer for the observed PaleoScan 2018 seismic layout.

No PaleoScan installation, application process, SDK or native library is used.
Native opening of generated projects is deliberately a separate acceptance gate.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET

from fieldviewer_connectors import _digest_file, _write_json
from fieldviewer_handoff_intake import _read_json, _value_hash
from fieldviewer_paleoscan_segy import verify_paleoscan_segy
from fieldviewer_seismic_native import _geometry, _little_samples
from geoviewer_contract import _unlinked

PROFILE = 'paleoscan-2018.1.0-native-project-prototype/1'
TEMPLATE_SCHEMA = 'fieldviewer.paleoscan2018-project-template/1'
MAX_TEMPLATE = 2 * 1024 * 1024
XML_FILES = ('descriptor.xml', 'projectClassification.xml', 'projectExtraData.xml')
DATABASES = {
    'Log/wellQt4Paleoscan.sql': {
        'LOG_TABLE': 'ID integer primary key unique, NAME TEXT NOT NULL, SET_ID INTEGER NOT NULL, NOVALUE REAL, UNIT TEXT, LOGTYPE INTEGER',
        'MARKERINFOS_TABLE': 'ID integer primary key unique, NAME TEXT NOT NULL UNIQUE, COLOR TEXT NOT NULL',
        'MARKERSELECTIONWELLSRELATION_TABLE': 'ID integer primary key unique, MARKERSELECTION_ID INTEGER NOT NULL, WELLS_ID INTEGER NOT NULL',
        'MARKERSELECTION_TABLE': 'ID integer primary key unique, NAME TEXT NOT NULL UNIQUE',
        'MARKERTOPBOTTOM_TABLE': 'ID integer primary key unique, MARKERINFO_ID INTEGER NOT NULL, MARKERSET_ID INTEGER NOT NULL, TOP REAL NOT NULL, BOTTOM REAL',
        'SET_TABLE': 'ID integer primary key unique, NAME TEXT NOT NULL, SET_TYPE INTEGER NOT NULL, WELL_ID INTEGER NOT NULL, SHIFT_DEPTH REAL, TWT_SHIFTING REAL, DEPTH1_TYPE INTEGER, DEPTH2_TYPE INTEGER, DEVIATION_TYPE INTEGER, CHECKSHOT_TYPE INTEGER, CRS TEXT',
        'WELL_TABLE': 'ID integer primary key unique, NAME TEXT NOT NULL UNIQUE, XUTM REAL NOT NULL, YUTM REAL NOT NULL, REFSET_ID INTEGER, KB REAL NOT NULL, KBUnit INTEGER NOT NULL, CRS TEXT'},
    'History/HistoryDatabase.sql': {
        'OBJECTHISTORYDATASOURCES_TABLE': 'ID integer primary key unique, DATA_ID INTEGER NOT NULL, INDEXSOURCE_ID INTEGER NOT NULL',
        'OBJECTHISTORYDATA_TABLE': 'ID integer primary key unique, OBJECT_ID INTEGER NOT NULL, DATE INTEGER NOT NULL, DATA TEXT',
        'OBJECTHISTORYINDEX_TABLE': 'ID integer primary key unique, NAME TEXT NOT NULL, OBJECT_TYPE INTEGER NOT NULL'}}


def _name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value)
            or value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}):
        raise ValueError('Use a safe ASCII project/volume name, at most 64 characters')
    return value


def _xml(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_TEMPLATE or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('Unsupported template XML')
    root = ET.fromstring(text)
    if sum(1 for _ in root.iter()) > 20000:
        raise ValueError('Template XML node bound exceeded')
    return root


def _template(path):
    value, sha = _read_json(Path(path).absolute(), MAX_TEMPLATE)
    directories = _validate_template(value)
    return value, sha, directories


def _validate_template(value):
    if value.get('schema') != TEMPLATE_SCHEMA or set(value.get('xml', {})) != set(XML_FILES):
        raise ValueError('Unsupported project template schema/members')
    roots = {name: _xml(value['xml'][name]) for name in XML_FILES}
    if any(re.match(r'(?i)^(?:[a-z]:[/\\]|\\\\|file:|https?://|/)', val)
           for root in roots.values() for node in root.iter() for val in node.attrib.values()):
        raise ValueError('Template contains an external absolute reference')
    descriptor = roots['descriptor.xml']
    if (descriptor.tag != 'project' or descriptor.get('version') != '71'
            or descriptor.find('type') is None or descriptor.find('type').get('value') != 'geophysicProjectBis'
            or descriptor.find('path') is None or descriptor.find('path').get('value') != '.'
            or descriptor.find('MDCrs') is None or len(descriptor.find('MDCrs')) or descriptor.find('MDCrs').attrib):
        raise ValueError('Require observed version-71 no-CRS project template')
    for name, tag in (('projectClassification.xml', 'ProjectClassificationManager'),
                      ('projectExtraData.xml', 'ProjectExtraDataManager')):
        root = roots[name]
        if root.tag != tag or root.find('ObjectList') is None or len(root.find('ObjectList')):
            raise ValueError('Template must not contain existing object registrations')
    structure = descriptor.find('theFileStructure/fileStructure')
    if structure is None:
        raise ValueError('Project template has no physical file structure')
    directories = []
    def visit(parent, prefix=Path()):
        for node in parent.findall('Directory'):
            part = node.get('value', '')
            if not part or part in ('.', '..') or re.search(r'[\\/:\x00-\x1f]', part) or part.endswith((' ', '.')):
                raise ValueError('Unsafe project directory in template')
            relative = prefix / part
            if node.get('isVirtual') == '0':
                directories.append(relative.as_posix())
                visit(node, relative)
    visit(structure)
    if 'Volume/Attribute' not in directories:
        raise ValueError('Template does not define Volume/Attribute')
    return directories


def compile_template(reference_project, output):
    """Extract only empty project settings, never object data/DB rows or binaries."""
    reference_project, output = Path(reference_project).absolute(), Path(output).absolute()
    _unlinked(reference_project); _unlinked(output)
    if output.resolve(strict=False).is_relative_to(reference_project.resolve()):
        raise ValueError('Template output must be outside reference project')
    xml, before = {}, {}
    for name in XML_FILES:
        path = reference_project / name
        _unlinked(path)
        with path.open('rb') as stream:
            raw = stream.read(MAX_TEMPLATE + 1)
        if len(raw) > MAX_TEMPLATE:
            raise ValueError('Reference metadata exceeds bound')
        before[name] = hashlib.sha256(raw).hexdigest()
        root = _xml(raw.decode('utf-8-sig'))
        if name == 'descriptor.xml':
            root.find('path').set('value', '.')
        xml[name] = ET.tostring(root, encoding='unicode')
    result = dict(schema=TEMPLATE_SCHEMA, xml=xml, reference_metadata_sha256=before,
        scope='Reusable project settings only; no native payload, history, locks, sessions or application libraries')
    _validate_template(result)
    if any(_digest_file(reference_project / n) != sha for n, sha in before.items()):
        raise ValueError('Reference metadata changed during template compilation')
    _write_json(output, result)
    return dict(schema=TEMPLATE_SCHEMA, template_sha256=_digest_file(output),
        reference_metadata_sha256=before, application_used=False)


def _native_header(geometry):
    g = geometry
    root = ET.Element('globalObject')
    obj = ET.SubElement(ET.SubElement(root, 'object', type='MDSeismicBloc'), 'MDSeismicBloc')
    def value(parent, key, val):
        ET.SubElement(parent, key, value=str(val))
    for key, val in dict(dimx=g['nx'],dimy=g['ns'],dimz=g['ni'],dataSize='FLOAT_SIZE',
                         Version=1,WithTransposition=1).items():
        value(obj,key,val)
    x,y = g['origin']; cx,cy = g['crossline_basis']; ix,iy = g['inline_basis']
    matrices = {'fromBlocToWorldMatrix': [[cx,0,ix,x],[0,g['dt_ms'],0,g['time0']],[cy,0,iy,y],[0,0,0,1]],
        'fromBlocToSurveyMatrix': [[g['dc'],0,0,g['xl0']],[0,g['dt_ms'],0,g['time0']],[0,0,g['di'],g['il0']],[0,0,0,1]]}
    for name, rows in matrices.items():
        parent = ET.SubElement(obj,name)
        for i, row in enumerate(rows):
            ET.SubElement(parent,'vect'+str(i),**{k:format(v,'.6f') for k,v in zip(('x','y','z','t'),row)})
    value(obj,'info',''); value(obj,'SRDValue','0.000000')
    units=ET.SubElement(obj,'unitsObject')
    for key, val in (('initialSpatialUnit','Meter'),('initialVerticalUnit','Millisecond'),
                     ('initialCrs',None),('spatialUnit','Meter'),('verticalUnit','Millisecond'),('crs',None)):
        ET.SubElement(units,key,**({} if val is None else {'value':val}))
    value(obj,'attribute',0)
    ET.indent(root, space=' ')
    header = ET.tostring(root,encoding='utf-8')+b'\n'
    data_offset = 2500+len(header)+10000
    global_header=ET.Element('GlobalHeader')
    for key,val in dict(headerOffset=2500,headerSize=len(header),Version='WithTampon',
                        DataOffset=data_offset,VersionNumber=1).items():
        value(global_header,key,val)
    ET.indent(global_header,space=' ')
    prefix=ET.tostring(global_header,encoding='utf-8')+b'\n'
    return prefix+b'\0'*(2500-len(prefix))+header+b'\0'*10000


def _write_volume(source, target, geometry, sha):
    digest=hashlib.sha256()
    with source.open('rb') as reader, target.open('xb') as writer:
        binary=reader.read(3600); digest.update(binary)
        writer.write(_native_header(geometry))
        while raw:=reader.read(240+4*geometry['ns']):
            if len(raw)!=240+4*geometry['ns']:
                raise ValueError('Source trace changed during native write')
            digest.update(raw); writer.write(_little_samples(raw[240:]))
    if digest.hexdigest()!=sha:
        raise ValueError('Source changed during native write; no project marker')


def _verify_volume(source, target, geometry):
    prefix=_native_header(geometry)
    with source.open('rb') as reader, target.open('rb') as native:
        if native.read(len(prefix))!=prefix:
            raise ValueError('Native header/geometry mismatch')
        reader.seek(3600)
        count=0
        while raw:=reader.read(240+4*geometry['ns']):
            if len(raw)!=240+4*geometry['ns'] or native.read(4*geometry['ns'])!=_little_samples(raw[240:]):
                raise ValueError('Native sample bit readback mismatch')
            count+=1
        if native.read(1) or count!=geometry['ni']*geometry['nx']:
            raise ValueError('Native sample count/extent mismatch')


def create_project(prepared, bundle, contract, template, output, *,
                   project_name='Prepared_PaleoScan', volume_name='Seismic', decisions=None, retention_view=None):
    """Write only a new candidate project; no in-place native editing or app calls."""
    project_name,volume_name=_name(project_name),_name(volume_name)
    proof=verify_paleoscan_segy(prepared,bundle,contract,decisions=decisions,retention_view=retention_view)
    settings,template_sha,directories=_template(template)
    from validate_geoviewer_contract import _load_contract
    catalogue,_=_load_contract(Path(contract),bind_missing_receipts=True)
    output=Path(output).absolute();_unlinked(output);output=output.resolve(strict=False)
    protected=[*catalogue['roots'].values(),Path(prepared).resolve(),Path(bundle).resolve(),Path(template).resolve()]
    protected += [Path(p).resolve() for p in (decisions,retention_view) if p]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise ValueError('New project output must be separate from all source/preparation inputs')
    source=Path(prepared)/'volume.segy'
    source_sha=proof['transformation']['derivative_sha256']
    geometry=_geometry(source,source_sha)
    output.mkdir(parents=True,exist_ok=False)
    project=output/project_name;project.mkdir()
    for name in directories:
        (project/name).mkdir(parents=True,exist_ok=True)
    for name,text in settings['xml'].items():
        root=_xml(text)
        if name=='descriptor.xml':
            root.find('path').set('value',project.as_posix())
        (project/name).write_bytes(ET.tostring(root,encoding='utf-8'))
    for relative,tables in DATABASES.items():
        path=project/relative;path.parent.mkdir(parents=True,exist_ok=True)
        connection=sqlite3.connect(path)
        try:
            connection.execute('PRAGMA page_size=1024')
            for name,columns in tables.items():
                connection.execute('CREATE TABLE '+name+' ('+columns+')')
            connection.commit()
            if connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise ValueError('Empty project database integrity failure')
        except sqlite3.Error as exc:
            raise ValueError('Native project database creation failed: '+str(exc)) from exc
        finally:
            connection.close()
    for folder,major,minor in (('Log',6,2),('History',1,0)):
        (project/folder/'wellDbInfo.xml').write_text(
            f'<GlobalHeader><VersionNumber value="{major}"/><VersionNumberMinor value="{minor}"/></GlobalHeader>',encoding='utf-8')
    target=project/'Volume/Attribute'/volume_name
    _write_volume(source,target,geometry,source_sha)
    _verify_volume(source,target,geometry)
    native_sha=_digest_file(target)
    final=verify_paleoscan_segy(prepared,bundle,contract,decisions=decisions,retention_view=retention_view)
    if _value_hash(proof)!=_value_hash(final) or _digest_file(template)!=template_sha:
        raise ValueError('Native writer inputs changed; no completion marker')
    if _digest_file(target)!=native_sha:
        raise ValueError('Native output changed during finalization; no completion marker')
    files=[dict(path=p.relative_to(output).as_posix(),bytes=p.stat().st_size,sha256=_digest_file(p))
        for p in sorted(project.rglob('*')) if p.is_file()]
    result=dict(schema='fieldviewer.paleoscan-native-project/1',profile=PROFILE,
        state='offline_verified_candidate',project_directory=project_name,volume_name=volume_name,
        runtime_boundary='zero_gui_python_stdlib_no_paleoscan_installation',
        application_used=False,application_required=False,native_libraries_used=False,
        receiving_open_verified=False,source_preparation=proof,template_sha256=template_sha,
        geometry=geometry,native_samples_verified=geometry['ni']*geometry['nx']*geometry['ns'],
        native_sample_bits_exact=True,native_volume_sha256=native_sha,files=files,
        limits='Observed 2018 version-71 finite single-component affine 3D FLOAT_SIZE profile only; no assigned CRS, wells, horizons, Survey, native-open, relocation or other-version qualification. Empty databases contain no copied history or well data.')
    _write_json(output/'NATIVE_PROJECT.json',result)
    return result


def verify_project(output, prepared, bundle, contract, template, *, decisions=None, retention_view=None):
    """Read-only structural/sample check of a generated candidate, before app use."""
    output=Path(output).absolute();_unlinked(output)
    stored,marker_sha=_read_json(output/'NATIVE_PROJECT.json',MAX_TEMPLATE)
    if (stored.get('schema')!='fieldviewer.paleoscan-native-project/1' or stored.get('profile')!=PROFILE
            or stored.get('state')!='offline_verified_candidate'
            or stored.get('receiving_open_verified') is not False
            or stored.get('application_used') is not False or stored.get('application_required') is not False
            or stored.get('native_sample_bits_exact') is not True):
        raise ValueError('Unsupported native project receipt')
    project_name=_name(stored['project_directory']);volume_name=_name(stored['volume_name'])
    project=output/project_name
    proof=verify_paleoscan_segy(prepared,bundle,contract,decisions=decisions,retention_view=retention_view)
    settings,sha,_=_template(template)
    source=Path(prepared)/'volume.segy'
    geometry=_geometry(source,proof['transformation']['derivative_sha256'])
    if (_value_hash(proof)!=_value_hash(stored['source_preparation']) or sha!=stored['template_sha256']
            or _value_hash(geometry)!=_value_hash(stored['geometry'])
            or type(stored.get('native_samples_verified')) is not int
            or stored['native_samples_verified']!=geometry['ni']*geometry['nx']*geometry['ns']):
        raise ValueError('Native project source/template/geometry binding mismatch')
    expected={f'{project_name}/{name}' for name in (*XML_FILES,*DATABASES,
        'Log/wellDbInfo.xml','History/wellDbInfo.xml','Volume/Attribute/'+volume_name)}
    files=stored.get('files')
    if not isinstance(files,list) or len(files)!=len(expected) or {f['path'] for f in files}!=expected:
        raise ValueError('Native project file inventory mismatch')
    actual=set()
    for path in output.rglob('*'):
        _unlinked(path)
        if path.is_file():actual.add(path.relative_to(output).as_posix())
    if actual!=expected|{'NATIVE_PROJECT.json'}:
        raise ValueError('Unexpected/missing native project member')
    for item in files:
        path=output/item['path']
        if path.stat().st_size!=item['bytes'] or _digest_file(path)!=item['sha256']:
            raise ValueError('Native project member hash/size mismatch')
    for name,text in settings['xml'].items():
        expected_root=_xml(text)
        if name=='descriptor.xml':expected_root.find('path').set('value',project.as_posix())
        if (project/name).read_bytes()!=ET.tostring(expected_root,encoding='utf-8'):
            raise ValueError('Native project settings differ from template/current location')
    for relative,tables in DATABASES.items():
        connection=sqlite3.connect((project/relative).as_uri()+'?mode=ro',uri=True)
        try:
            connection.execute('PRAGMA trusted_schema=OFF')
            objects=connection.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
            expected_sql={name:re.sub(r'\s+','',f'CREATE TABLE {name} ({columns})').upper() for name,columns in tables.items()}
            if (len(objects)!=len(tables) or any(kind!='table' or name not in expected_sql
                    or re.sub(r'\s+','',sql).upper()!=expected_sql[name] for kind,name,sql in objects)):
                raise ValueError('Unexpected native project database schema')
            if connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise ValueError('Native project database integrity failure')
            if any(connection.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in tables):
                raise ValueError('Generated project database must contain no copied history/well rows')
        except sqlite3.Error as exc:
            raise ValueError('Native project database verification failed: '+str(exc)) from exc
        finally:
            connection.close()
    _verify_volume(source,project/'Volume/Attribute'/volume_name,geometry)
    if _digest_file(project/'Volume/Attribute'/volume_name)!=stored['native_volume_sha256']:
        raise ValueError('Native volume identity mismatch')
    final=verify_paleoscan_segy(prepared,bundle,contract,decisions=decisions,retention_view=retention_view)
    if _value_hash(final)!=_value_hash(proof) or _digest_file(template)!=sha or _digest_file(output/'NATIVE_PROJECT.json')!=marker_sha:
        raise ValueError('Native project verification inputs changed')
    return dict(schema='fieldviewer.paleoscan-native-project-verification/1',profile=PROFILE,
        state='offline_verified_candidate',native_samples_verified=geometry['ni']*geometry['nx']*geometry['ns'],
        native_sample_bits_exact=True,application_used=False,receiving_open_verified=False,
        scope='Current source-bound native file structure and sample bytes only; no PaleoScan application-open acceptance')
