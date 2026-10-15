# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Typed native well identity and declared references for one evidenced profile.

Stored labels/offsets are evidence, not calibrated datums or industry identifiers.
Head rows bind through one exact provider UUID plus identical native name and XY;
neither proximity nor names alone establish identity.
"""
from collections import Counter
import math
import uuid
import petrel_native_binary as binary

NAMESPACE = 'http://www.slb.com/Petrel/2011/03/Serialization'
WELL_PROFILE = [33, 3, 14, 1, 2, 21, 0, 0, 1]
PROVIDER_STATUS = 'native_Model_ptd_definite_survey_provider_exact'
IDENTITY_STATUS = 'exact_typed_Model_WellTraceSubject_provider_name_XY'


def exact_uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise ValueError('Nonzero canonical native UUID required')
    return value


def text(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > 4096:
        raise ValueError('Native text outside bounded scalar profile')
    return value


def number(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('Finite native scalar required')
    return value


def reference(node):
    if node is None:
        return None
    if node.attrs or node.content or [x.name for x in node.children] != ['name','offset','description']:
        raise ValueError('Reference level structure outside observed profile')
    return dict(name=text(node.get('name')), offset=number(node.get('offset')), description=text(node.get('description')))


def decode_subjects(nodes):
    subjects=[]; rejected=[]
    for ordinal, node in enumerate(nodes):
        if node.name != 'WellTraceSubject':
            continue
        locator=f'08_native_project/ptd_store/Model.ptd#document={ordinal}/WellTraceSubject'
        try:
            if (node.attrs.get('xmlns') != NAMESPACE or node.attrs.get('Version') != WELL_PROFILE
                    or not all(type(x) is int for x in node.attrs['Version'])):
                raise ValueError('WellTraceSubject metadata profile not validated')
            tag=exact_uuid(node.get('unique_tag'))
            provider_raw=node.get('definite_survey_provider_tag_')
            provider=None if provider_raw == '' or (type(provider_raw) is int and provider_raw == 0) else exact_uuid(provider_raw)
            head=node.child('well_head_')
            xy=head.array('double')
            if head.attrs or head.content or len(head.children)!=1 or xy.shape!=(2,) or xy.dtype.kind!='f':
                raise ValueError('Well head must be the native two-double vector')
            values=[number(float(x)) for x in xy]
            if any(abs(x)>1e15 for x in values): raise ValueError('Native head coordinate outside supported bounds')
            row=dict(object_id=tag,subject_type='WellTraceSubject',model_profile=list(WELL_PROFILE),name=text(node.get('name')),
                definite_provider_id=provider,head_xy=values,locator=locator,
                identity_scope='Exact typed native Model unique_tag; source-revision scoped, not industry UWI',
                metadata_status='source_recorded_unresolved_units_and_datums',field_gaps=[])
            for field,read in [('uwi_',lambda n:text(n.scalar())),
                               ('working_reference_level',reference),
                               ('grid_convergence_factor',lambda n:number(n.scalar()))]:
                child=node.child(field,required=False)
                if child is None: row['field_gaps'].append(dict(field=field,reason='not_present'));continue
                try: row[field]=read(child)
                except (ValueError,TypeError,OverflowError,binary.NativeError) as exc:
                    row['field_gaps'].append(dict(field=field,reason=str(exc)))
            levels=node.child('reference_levels',required=False)
            try:
                if levels is None: raise ValueError('not_present')
                if (set(levels.attrs)!={'Size'} or type(levels.attrs['Size']) is not int
                        or not 0<=levels.attrs['Size']<=256 or levels.content
                        or len(levels.children)!=levels.attrs['Size'] or any(n.name!='item' for n in levels.children)):
                    raise ValueError('Reference-level list outside declared size/profile')
                row['reference_levels']=[reference(n) for n in levels.children]
            except (ValueError,TypeError,OverflowError,binary.NativeError) as exc:
                row['field_gaps'].append(dict(field='reference_levels',reason=str(exc)))
            row['interpretation_boundary']='Native uwi_ is a stored declaration, not verified external authority. Reference names/descriptions/offsets and convergence scalar retain raw values; no unit, sign, datum, azimuth reference or KB/head-Z equivalence inferred.'
            subjects.append(row)
        except (ValueError,TypeError,OverflowError,binary.NativeError) as exc:
            rejected.append(dict(locator=locator,reason=str(exc)))
    duplicates={tag for tag,count in Counter(x['object_id'] for x in subjects).items() if count>1}
    if duplicates:
        rejected.extend(dict(object_id=tag,reason='duplicate_native_well_UUID') for tag in sorted(duplicates))
        subjects=[x for x in subjects if x['object_id'] not in duplicates]
    return dict(schema='geoviewer.native-well-evidence/1',subjects=subjects,rejected=rejected,
        status='source_recorded' if subjects else 'unresolved',scope='Typed native metadata, no external identifier/datum calibration')


def bind_head_rows(rows, evidence):
    providers={}
    for well in evidence['subjects']:
        if well['definite_provider_id']:
            providers.setdefault(well['definite_provider_id'],[]).append(well)
    counts=Counter(row.get('trajectory_provider_object_id') for row in rows if row.get('trajectory_provider_object_id'))
    bound=[]
    for source in rows:
        row=dict(source);provider=row.get('trajectory_provider_object_id')
        candidates=providers.get(provider,[])
        row['native_well_identity_status']='unresolved_no_unique_exact_typed_provider'
        if row.get('trajectory_provider_link_status')==PROVIDER_STATUS and counts[provider]==1 and len(candidates)==1:
            well=candidates[0]
            if row.get('well_name')==well['name'] and [row.get('x'),row.get('y')]==well['head_xy']:
                row.update(native_well_object_id=well['object_id'],native_well_identity_status=IDENTITY_STATUS,
                           native_well_metadata_locator=well['locator'],native_uwi_declared=well.get('uwi_'))
            else:
                row['native_well_identity_status']='unresolved_typed_name_or_XY_conflict'
        bound.append(row)
    evidence['head_identity_counts']=dict(Counter(row['native_well_identity_status'] for row in bound))
    return bound


def enrich_heads(payload, rows):
    try:
        evidence=decode_subjects(binary.read_documents(payload))
        return bind_head_rows(rows,evidence),evidence
    except (ValueError,TypeError,OverflowError,binary.NativeError) as exc:
        return rows,dict(schema='geoviewer.native-well-evidence/1',status='unresolved',subjects=[],rejected=[],reason=str(exc))
