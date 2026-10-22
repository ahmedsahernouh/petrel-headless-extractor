# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read bounded Model.ptd CRS and datum declarations without qualifying coordinates.

This module accepts exact Model.ptd bytes. It neither applies a transform nor
establishes which stored coordinates, wells, or grids use a declaration.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import re
import xml.etree.ElementTree as ET

import petrel_native_binary as native
import petrel_native_well_evidence as wells


SCHEMA = 'petrel.model-crs-datum-declarations/1'
MAX_PROJECTION_BYTES = 2 * 1024 * 1024
MAX_XML_DEPTH = 32
MAX_XML_ELEMENTS = 2048
FLOAT32_MAX = 3.4028234663852886e38
DATUM_FIELDS = ('unique_tag', 'name', 'kid_tag', 'datumInSI', 'isPredefined',
                'isWellSpecific', 'isTimeSpecific', 'hasVelocity', 'velocityInSI')


def _plain(value):
    """Keep native scalars, refusing nonfinite or non-JSON values."""
    if type(value) in (str, bool, int) or value is None:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError('scalar is not a finite JSON value')


def _field(node, name):
    child = node.child(name)
    if child.attrs or child.children or len(child.content) != 1:
        raise ValueError(f'{name}: not one typed scalar')
    return _plain(child.content[0])


def _optional_field(node, name):
    child = node.child(name, required=False)
    return None if child is None else _field(node, name)


def _xml_one(parent, name, required=True):
    found = [child for child in parent if child.tag == name]
    if len(found) > 1 or (required and not found):
        raise ValueError(f'projection XML requires {"one" if required else "at most one"} direct {name}; found {len(found)}')
    return found[0] if found else None


def _xml_text(parent, name):
    value = _xml_one(parent, name)
    if len(value) or value.attrib or value.text is None or not value.text.strip():
        raise ValueError(f'projection XML {name} is not a nonempty scalar')
    return value.text


def _projection(raw):
    if type(raw) is not str:
        raise ValueError('projection is not a text scalar')
    if len(raw.encode('utf-8')) > MAX_PROJECTION_BYTES:
        raise ValueError('projection exceeds 2 MiB scalar bound')
    if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', raw):
        return dict(status='unresolved_project_reference',
                    declaration_state='serialized_null_reference' if raw.lower()=='00000000-0000-0000-0000-000000000000' else 'serialized_reference_requires_resolution',
                    raw=raw, scope='Serialized projection UUID only; no CRS definition inferred')
    prefix, separator, xml = raw.partition(': ')
    if not separator or (prefix!='ESRI' and re.fullmatch(r'ESRI,PE_[0-9]+(?:_[0-9]+)+', prefix) is None):
        raise ValueError('unsupported projection scalar prefix')
    upper = xml.upper()
    if '<!DOCTYPE' in upper or '<!ENTITY' in upper:
        raise ValueError('projection XML DTD/entities are forbidden')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f'malformed projection XML: {exc}') from exc
    count = 0
    stack = [(root, 1)]
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > MAX_XML_ELEMENTS or depth > MAX_XML_DEPTH:
            raise ValueError('projection XML element/depth bound exceeded')
        stack.extend((child, depth + 1) for child in item)
    if root.tag == 'IEarlyBoundCoordinateReferenceSystem':
        early = root
        late = _xml_one(root, 'ILateBoundCoordinateReferenceSystem')
    elif root.tag == 'ILateBoundCoordinateReferenceSystem':
        early = None
        late = root
        if any(child.tag in ('IEarlyBoundCoordinateReferenceSystem',
                             'ILateBoundCoordinateReferenceSystem') for child in root):
            raise ValueError('conflicting nested CRS declaration')
    else:
        raise ValueError('unsupported projection XML root')
    transform = _xml_one(root, 'ISimpleTransform', required=False)
    typed_tags = {'IEarlyBoundCoordinateReferenceSystem',
                  'ILateBoundCoordinateReferenceSystem', 'ISimpleTransform'}
    allowed = {id(root), id(late)}
    if transform is not None:
        allowed.add(id(transform))
    if any(item.tag in typed_tags and id(item) not in allowed for item in root.iter()):
        raise ValueError('conflicting nested CRS/transform declaration')
    def name(element):
        value = element.attrib.get('name')
        if not value:
            raise ValueError(f'{element.tag}: missing name attribute')
        return value
    authorities = []
    if early is not None:
        authorities.append(dict(role='early_bound_crs', authority=_xml_text(early, 'AuthorityCode')))
    authorities.append(dict(role='late_bound_crs', authority=_xml_text(late, 'AuthorityCode')))
    if transform is not None:
        authorities.append(dict(role='transform', authority=_xml_text(transform, 'AuthorityCode')))
    transform_data = None
    if transform is not None:
        transform_data = dict(authority=_xml_text(transform, 'AuthorityCode'),
                              name=transform.attrib.get('name'), wkt=None,
                              name_present='name' in transform.attrib,
                              wkt_present=any(child.tag=='WKT' for child in transform))
        if transform_data['wkt_present']:
            transform_data['wkt'] = _xml_text(transform, 'WKT')
    return dict(status='parsed_stored_declaration', declaration_state='stored_project_declaration',
                prefix=prefix, raw=raw,
                **({'early_bound': dict(name=name(early), authority=_xml_text(early, 'AuthorityCode'))}
                   if early is not None else {}),
                late_bound=dict(name=name(late), authority=_xml_text(late, 'AuthorityCode')),
                **({'transform': transform_data} if transform is not None else {}),
                authority_records=authorities,
                scope='stored_project_declaration_only')


def _source_profile(node):
    version = node.attrs.get('Version')
    if isinstance(version, list):
        version = [_plain(value) for value in version]
    else:
        version = _plain(version)
    return dict(xmlns=_plain(node.attrs.get('xmlns')), Version=version)


def _project(node, ordinal):
    locator = f'Model.ptd#document={ordinal}/ProjectSubject'
    result = dict(document_ordinal=ordinal, locator=locator,
                  source_record_profile=_source_profile(node), units_style={})
    gaps = []
    fields = (
        ('unique_tag', lambda: _optional_field(node, 'unique_tag')),
        ('saved_version', lambda: _field(node, 'version_string')),
        ('units_style/is_used', lambda: _field(node.child('style').child('units_style'), 'is_used')),
        ('units_style/reference_datum', lambda: _field(node.child('style').child('units_style'), 'reference_datum')),
        ('projection', lambda: _projection(_field(node.child('style'), 'projection'))),
    )
    for path, read in fields:
        try:
            value = read()
            if path == 'saved_version' and (type(value) is not str or not value):
                raise ValueError('version_string is not nonempty text')
            if path == 'units_style/is_used' and type(value) is not bool:
                raise ValueError('is_used is not boolean')
            if path == 'units_style/reference_datum' and type(value) is not int:
                raise ValueError('reference_datum is not an integer tag')
            if path.startswith('units_style/'):
                result['units_style'][path.split('/', 1)[1]] = value
            else:
                result[path] = value
        except (native.NativeError, ValueError, TypeError, KeyError, OverflowError) as exc:
            gaps.append(dict(locator=f'{locator}/{path}', reason=str(exc)))
    return result, gaps


def _datum(node, ordinal):
    # Legacy records explicitly store datum/velocity instead of their SI-named
    # counterparts. Preserve those exact names; their units are not inferred.
    values = []
    for modern, legacy in (('datumInSI','datum'), ('velocityInSI','velocity')):
        names=[name for name in (modern,legacy) if node.child(name,required=False) is not None]
        if len(names)!=1:
            raise ValueError('Datum value field is absent or ambiguous: '+modern+'/'+legacy)
        values.append(names[0])
    row = {name: _field(node, name) for name in DATUM_FIELDS if name not in ('datumInSI','velocityInSI')}
    row.update({name:_field(node,name) for name in values})
    if type(row['unique_tag']) is not str or not row['unique_tag']:
        raise ValueError('DatumSubject unique_tag is not a nonempty string')
    if type(row['name']) is not str or type(row['kid_tag']) is not int:
        raise ValueError('DatumSubject name/kid_tag outside typed profile')
    for name in ('isPredefined', 'isWellSpecific', 'isTimeSpecific', 'hasVelocity'):
        if type(row[name]) is not bool:
            raise ValueError(f'DatumSubject {name} is not boolean')
    for name in values:
        value = row[name]
        if type(value) not in (int, float):
            raise ValueError(f'DatumSubject {name} is not finite numeric')
        row[name + '_usable'] = abs(value) != FLOAT32_MAX
    row['source_value_fields']=values
    row['value_units_status']='SI_named_field_declarations' if values==['datumInSI','velocityInSI'] else 'unresolved_legacy_field_units'
    row.update(document_ordinal=ordinal, locator=f'Model.ptd#document={ordinal}/DatumSubject',
               source_record_profile=_source_profile(node),
               scope='stored_datum_declaration_only')
    return row


def _well_reference_fields(node, ordinal):
    """Recover exact reference-field shape without requiring a head decoder.

    This does not relax the existing well identity/head/provider profiles. Older
    subjects may expose named references even when their head layout is unknown.
    """
    profile=_source_profile(node)
    if profile['xmlns']!=wells.NAMESPACE or not isinstance(profile['Version'],list) or not profile['Version'] or any(type(v) is not int for v in profile['Version']):
        raise ValueError('Unrecognized typed well namespace/version metadata')
    row=dict(object_id=wells.exact_uuid(_field(node,'unique_tag')),name=wells.text(_field(node,'name')),
             source_record_profile=profile,document_ordinal=ordinal,
             locator=f'Model.ptd#document={ordinal}/WellTraceSubject',
             metadata_status='source_recorded_reference_fields_only',field_gaps=[],
             reference_field_layout='bounded_name_offset_description',
             identity_scope='Typed source WellTraceSubject UUID only; no head, provider, industry identifier or trajectory binding',
             interpretation_boundary='Raw reference names/descriptions/offsets only. Units, sign, KB/head-Z equivalence and datum calibration unresolved.')
    for field in ('working_reference_level','reference_levels'):
        value=node.child(field,required=False)
        if value is None:
            row['field_gaps'].append(dict(field=field,reason='not_present'));continue
        if field=='working_reference_level':
            row[field]=wells.reference(value)
        else:
            if (set(value.attrs)!={'Size'} or type(value.attrs['Size']) is not int or not 0<=value.attrs['Size']<=256
                    or value.content or len(value.children)!=value.attrs['Size'] or any(v.name!='item' for v in value.children)):
                raise ValueError('Reference-level list outside exact bounded shape')
            row[field]=[wells.reference(v) for v in value.children]
    if not row.get('working_reference_level') and not row.get('reference_levels'):
        raise ValueError('No supported reference fields in this well subject')
    refs=([row['working_reference_level']] if row.get('working_reference_level') else [])+row.get('reference_levels',[])
    for value in refs:
        value['offset_usable']=abs(value['offset'])!=FLOAT32_MAX
        value['offset_unit_status']='unresolved'
    return row


def inspect_documents(nodes):
    """Inspect a one-pass native Node iterator; retain only relevant records."""
    result = dict(schema=SCHEMA, status='unresolved', saved_version=None,
                  project_records=[], datum_records=[], well_reference_records=[],
                  gaps=[], counts={}, coordinate_binding_verified=False)
    counts = Counter()
    stream_complete = True
    try:
        for ordinal, node in enumerate(nodes):
            counts['documents_seen'] += 1
            kind = node.name
            if kind not in ('ProjectSubject', 'DatumSubject', 'WellTraceSubject'):
                continue
            counts[kind + '_seen'] += 1
            locator = f'Model.ptd#document={ordinal}/{kind}'
            try:
                if kind == 'ProjectSubject':
                    project, gaps = _project(node, ordinal)
                    result['project_records'].append(project)
                    result['gaps'].extend(gaps)
                elif kind == 'DatumSubject':
                    result['datum_records'].append(_datum(node, ordinal))
                else:
                    evidence = wells.decode_subjects([node])
                    for row in evidence['subjects']:
                        row['locator'] = locator
                        row['document_ordinal'] = ordinal
                        row['source_record_profile'] = _source_profile(node)
                        result['well_reference_records'].append(row)
                    for rejected in evidence['rejected']:
                        counts['full_well_profile_rejected'] += 1
                        try:
                            raw_reference=_well_reference_fields(node,ordinal)
                            raw_reference['full_head_metadata_profile_status']='unsupported: '+rejected['reason']
                            result['well_reference_records'].append(raw_reference)
                            counts['reference_field_only_records'] += 1
                        except (native.NativeError,ValueError,TypeError,KeyError,OverflowError) as exc:
                            result['gaps'].append(dict(locator=locator,reason='unsupported_well_reference_fields: '+str(exc)))
                            counts['rejected_records'] += 1
            except (native.NativeError, ValueError, TypeError, KeyError, OverflowError) as exc:
                result['gaps'].append(dict(locator=locator, reason=str(exc)))
                counts['rejected_records'] += 1
    except Exception as exc:
        stream_complete = False
        result['gaps'].append(dict(locator='Model.ptd#document_stream',
                                   reason=f'parse_incomplete: {type(exc).__name__}: {exc}'))
    projects = result['project_records']
    if len(projects) != 1 or counts['ProjectSubject_seen'] != 1:
        result['gaps'].append(dict(locator='Model.ptd/ProjectSubject',
                                   reason=f'expected_one_valid_typed_project_record; seen={counts["ProjectSubject_seen"]}; valid={len(projects)}'))
    else:
        result['saved_version'] = projects[0].get('saved_version')
        if 'reference_datum' in projects[0]['units_style']:
            tag = projects[0]['units_style']['reference_datum']
            matches = [d for d in result['datum_records'] if d['kid_tag'] == tag]
            projects[0]['reference_datum_tag_candidate'] = (
                dict(kid_tag=tag, matching_datum_unique_tags=[d['unique_tag'] for d in matches],
                     unique_match=len(matches) == 1, selection_verified=False))
            if len(matches) != 1:
                result['gaps'].append(dict(locator='Model.ptd/ProjectSubject/style/units_style/reference_datum',
                                           reason=f'datum_tag_candidate_match_count={len(matches)}'))
    duplicate_wells = [tag for tag, count in Counter(
        row['object_id'] for row in result['well_reference_records']).items() if count > 1]
    if duplicate_wells:
        counts['rejected_records'] += sum(row['object_id'] in duplicate_wells
                                          for row in result['well_reference_records'])
        result['well_reference_records'] = [row for row in result['well_reference_records']
                                            if row['object_id'] not in duplicate_wells]
        for tag in duplicate_wells:
            result['gaps'].append(dict(locator='Model.ptd/WellTraceSubject',
                                       reason=f'duplicate_native_well_UUID: {tag}'))
    result['counts'] = dict(counts, project_records=len(projects),
                            datum_records=len(result['datum_records']),
                            well_reference_records=len(result['well_reference_records']),
                            rejected_records=counts['rejected_records'],
                            gap_count=len(result['gaps']),
                            stream_complete=stream_complete)
    result['status'] = ('partial' if result['gaps'] or not stream_complete else 'parsed') if (
        projects or result['datum_records'] or result['well_reference_records']) else 'unresolved'
    # This is also a runtime assertion against accidental NaN or native arrays.
    json.dumps(result, allow_nan=False)
    return result


def inspect_model(raw: bytes):
    """Inspect exact compressed Model.ptd bytes with native engine bounds."""
    if type(raw) is not bytes:
        raise TypeError('exact Model.ptd bytes required')
    if len(raw) > native.MAX_CONTAINER:
        raise native.NativeError('Model.ptd input exceeds native container bound')
    try:
        payload = native.decompress(raw)
    except (native.NativeError, ValueError) as exc:
        result = inspect_documents(iter(()))
        result['gaps'].append(dict(locator='Model.ptd#compression', reason=str(exc)))
        result['counts']['stream_complete'] = False
        return result
    def selected_documents():
        # BXML frames and root headers use the existing bounded reader. Only
        # the three requested metadata subject bodies are interpreted; grid,
        # history and rendering bodies are outside this tool's stated scope.
        for names, body in native.documents(payload):
            probe=native.NBFX(body,names)
            token=probe.byte()
            while token==2:
                probe.string();token=probe.byte()
            header=probe.start(token)
            if header.name in ('ProjectSubject','DatumSubject','WellTraceSubject'):
                yield native.NBFX(body,names).parse()
            else:
                yield native.Node(header.name)
    result=inspect_documents(selected_documents())
    result['body_validation_scope']='Complete container/framing and root headers; full bodies of ProjectSubject, DatumSubject, WellTraceSubject only. Other bodies are opaque and unvalidated.'
    return result
