# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Shared headless seismic source binding, regular-grid checks and byte ordering."""
from array import array
from fractions import Fraction
import hashlib
from pathlib import Path
import struct

from fieldviewer_handoff_intake import SHA, _parse, verify_handoff
from geoviewer_contract import MAX_ROW_BYTES, _resolve
from validate_geoviewer_contract import _load_contract

def seismic_context(bundle, contract, entity_key, artifact_key, decisions, retention_view, *, target):
    intake = verify_handoff(bundle, contract, decisions=decisions,
        retention_view=retention_view, hash_artifacts=True)
    if intake['target'] != target:
        raise ValueError('Require a '+target+' upstream handoff')
    catalogue, bindings = _load_contract(Path(contract), bind_missing_receipts=True)
    entities = [e for e in catalogue['tables']['entities'] if e['entity_key'] == entity_key]
    artifacts = [a for a in catalogue['tables']['artifacts'] if a['artifact_key'] == artifact_key]
    if len(entities) != 1 or entities[0]['category'] != 'seismic' or len(artifacts) != 1:
        raise ValueError('Select one exact seismic entity and artifact key')
    artifact = artifacts[0]
    if (entity_key not in artifact.get('owner_keys', [artifact['entity_key']])
            or artifact['role'] != 'full_payload' or artifact['format'] != 'SEGY'
            or artifact['original_status'] != 'exported' or artifact['index_section'] != 'files'
            or not SHA.fullmatch(str(artifact['recorded_sha256']))):
        raise ValueError('Require an owned, accepted full SEG-Y with a recorded SHA-256')
    source = _resolve(catalogue['roots'][artifact['path_base']], artifact['relative_path'])
    if source.stat().st_size != artifact['expected_bytes']:
        raise ValueError('Selected source size differs from its contract')
    # Reload and intake must describe the same generation even under concurrent publication.
    if (catalogue['manifest']['generation'] != intake['generation']
            or catalogue['pointer']['manifest_sha256'] != intake['manifest_sha256']):
        raise ValueError('Source generation changed during preparation')
    with (Path(bundle) / 'entities.jsonl').open('rb') as stream:
        while raw := stream.readline(MAX_ROW_BYTES + 1):
            if len(raw) > MAX_ROW_BYTES:
                raise ValueError('Handoff entity exceeds row bound')
            plan = _parse(raw)
            if plan['entity_key'] == entity_key:
                planned = [a for a in plan['artifacts'] if a['artifact_key'] == artifact_key]
                if len(planned) != 1 or planned[0]['available'] is not True:
                    raise ValueError('Selected payload is unavailable under current handoff/retention policy')
                intake['selected_upstream_preparation'] = {key: plan[key] for key in (
                    'source_state', 'receiving_state', 'source_reason_codes', 'receiving_reason_codes')}
                break
        else:
            raise ValueError('Selected entity missing from handoff')
    return intake, catalogue, bindings, artifact, source


def _geometry(source, expected_sha, *, require_scalar_one=True, undefined_range=None):
    """Exact regular 3D affine lattice in inline-major/crossline-minor order."""
    digest = hashlib.sha256()
    row = col = 0
    nx = di = dc = ib = cb = None
    with source.open('rb') as stream:
        header = stream.read(3600); digest.update(header)
        if len(header)!=3600:
            raise ValueError('Truncated native writer input binary header')
        text=header[:3200].decode('ascii' if header[:1]==b'C' else 'cp500')
        if not text.startswith('C 1 Field Viewer'):
            raise ValueError('Require FieldViewer-generated SEG-Y header mapping')
        ns = struct.unpack_from('>H', header, 3220)[0]
        dt = struct.unpack_from('>H', header, 3216)[0]
        if (not ns or not dt or struct.unpack_from('>H',header,3224)[0]!=5
                or struct.unpack_from('>HHh',header,3500)!=(256,1,0)
                or struct.unpack_from('>H',header,3254)[0]!=1):
            raise ValueError('Require fixed-length rev1 IEEE time SEG-Y in metres')
        for number in range(1 << 63):
            raw = stream.read(240 + 4*ns)
            if not raw:
                break
            if len(raw) != 240 + 4*ns:
                raise ValueError('Truncated native writer input trace')
            digest.update(raw)
            il, xl = struct.unpack_from('>ii', raw, 188)
            x, y = struct.unpack_from('>ii', raw, 180)
            delay = struct.unpack_from('>h', raw, 108)[0]
            scalar=struct.unpack_from('>h',raw,70)[0]
            factor=Fraction(scalar) if scalar>0 else Fraction(1,-scalar) if scalar<0 else Fraction(1)
            scaled=[v*factor for v in (x,y)]
            if any(v.denominator!=1 for v in scaled):
                raise ValueError('Fractional coordinate profile not yet qualified')
            x,y=(v.numerator for v in scaled)
            if ((require_scalar_one and scalar!=1) or struct.unpack_from('>H',raw,88)[0]!=1
                    or struct.unpack_from('>HH', raw, 114) != (ns, dt)):
                raise ValueError('Unsupported scalar/sampling for native writer')
            if any(bits & 0x7f800000 == 0x7f800000 for bits, in struct.iter_unpack('>I', raw[240:])):
                raise ValueError('Native null/nonfinite sample semantics are not qualified')
            if undefined_range is not None and any(undefined_range[0]<value<undefined_range[1]
                    for value, in struct.iter_unpack('>f',raw[240:])):
                raise ValueError('Source sample collides with target reserved undefined-value range')
            if number == 0:
                il0, xl0, x0, y0, time0 = il, xl, x, y, delay
                previous_il = il
            else:
                if il != previous_il:
                    if nx is None:
                        nx = col + 1
                        di, ib = il-il0, (x-x0, y-y0)
                    elif col + 1 != nx:
                        raise ValueError('Incomplete/nonrectangular inline')
                    row += 1; col = 0; previous_il = il
                else:
                    col += 1
                if row == 0 and col == 1:
                    dc, cb = xl-xl0, (x-x0, y-y0)
            if delay != time0 or (row and (il != il0+row*di)):
                raise ValueError('Irregular inline/time axis')
            if cb is not None and (xl != xl0+col*dc or x != x0+col*cb[0]+row*(ib[0] if ib else 0)
                    or y != y0+col*cb[1]+row*(ib[1] if ib else 0)):
                raise ValueError('Irregular trace coordinates or crossline order')
        else:
            raise ValueError('Trace count bound exceeded')
    if (nx is None or nx < 2 or row < 1 or col+1 != nx or not dc or not di
            or ib is None or cb is None or cb[0]*ib[1] == cb[1]*ib[0]):
        raise ValueError('Require a complete, nondegenerate regular 3D cube of at least 2x2 traces')
    if digest.hexdigest() != expected_sha:
        raise ValueError('Prepared SEG-Y changed during geometry validation')
    return dict(ni=row+1, nx=nx, ns=ns, dt_ms=dt/1000, time0=time0,
        il0=il0, xl0=xl0, di=di, dc=dc, origin=[x0,y0], inline_basis=list(ib), crossline_basis=list(cb))


def _little_samples(raw):
    words=array('I')
    if words.itemsize != 4:
        raise ValueError('Runtime uint32 width unsupported')
    words.frombytes(raw); words.byteswap()
    return words.tobytes()
