# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Headless, source-bound SEG-Y preparation; never launches or writes PaleoScan."""
from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
import struct

from fieldviewer_connectors import _digest_file, _write_json
from fieldviewer_handoff_intake import MAX_HANDOFF_BYTES, _read_json, _value_hash
from geoviewer_contract import _unlinked, _resolve
from validate_geoviewer_contract import _recheck_bindings

PROFILE = 'paleoscan-2018.1.0-segy-scalar1-preparation/1'
SCHEMA = 'fieldviewer.paleoscan-segy-preparation/1'
COORDINATES = (73, 77, 81, 85, 181, 185)
PAYLOAD = 'volume.segy'
MARKER = 'RECEIVER.json'


def _transcode(source, expected_sha256, destination=None):
    """Stream fixed-length, generated rev1 IEEE SEG-Y with exact scalar +1 XY.

    A bounded trace buffer preserves all sample bytes, including null bit patterns.
    Coordinates must be integral and int32-representable. No rounding is allowed.
    Without destination this computes the same derivative digest without writes.
    """
    before, after, samples = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    traces = 0
    def emit(raw):
        after.update(raw)
        if destination is not None:
            destination.write(raw)
    with source.open('rb') as stream:
        header = stream.read(3600)
        if len(header) != 3600:
            raise ValueError('Truncated SEG-Y header')
        before.update(header)
        encoding = 'ascii' if header[:1] == b'C' else 'cp500'
        text = header[:3200].decode(encoding)
        if not text.startswith('C 1 Field Viewer'):
            raise ValueError('Only FieldViewer-generated SEG-Y is supported')
        interval, _, ns, _, code = struct.unpack_from('>HHHHH', header, 3216)
        rev, fixed, extended = struct.unpack_from('>HHh', header, 3500)
        if (code != 5 or rev != 256 or fixed != 1 or extended != 0 or not ns or not interval
                or struct.unpack_from('>H', header, 3254)[0] != 1):
            raise ValueError('Require rev1 fixed-length IEEE float32 time samples and metres; no extended headers')
        lines = [text[i:i+80] for i in range(0, 3200, 80)]
        lines[4] = 'C 5 XY UNITS m; COORDINATE SCALAR +1; LOSSLESS RECEIVER COPY'.ljust(80)
        emit(''.join(lines).encode(encoding) + header[3200:])
        while raw := stream.read(240 + 4*ns):
            if len(raw) != 240 + 4*ns:
                raise ValueError('Truncated or variable-length SEG-Y trace')
            before.update(raw)
            if (struct.unpack_from('>HH', raw, 114) != (ns, interval)
                    or struct.unpack_from('>H', raw, 88)[0] != 1):
                raise ValueError('Trace sampling or coordinate units differ from supported profile')
            changed = bytearray(raw[:240])
            scalar = struct.unpack_from('>h', raw, 70)[0]
            factor = Fraction(scalar) if scalar > 0 else Fraction(1, -scalar) if scalar < 0 else Fraction(1)
            for position in COORDINATES:
                value = struct.unpack_from('>i', raw, position-1)[0] * factor
                if value.denominator != 1 or not -(2**31) <= value.numerator < 2**31:
                    raise ValueError('Scalar +1 would lose coordinate precision or overflow int32')
                struct.pack_into('>i', changed, position-1, value.numerator)
            struct.pack_into('>h', changed, 70, 1)
            emit(changed)
            emit(raw[240:])
            samples.update(raw[240:])
            traces += 1
    if traces == 0 or before.hexdigest() != expected_sha256:
        raise ValueError('Empty SEG-Y or source hash changed during preparation')
    return dict(source_sha256=before.hexdigest(), derivative_sha256=after.hexdigest(),
        traces=traces, samples=traces*ns, samples_per_trace=ns, sample_interval_us=interval,
        sample_bytes_sha256=samples.hexdigest(), effective_coordinates_exact=True,
        sample_bytes_unchanged=True, coordinate_scalar=1, coordinate_fields=list(COORDINATES))


def _context(bundle, contract, entity_key, artifact_key, decisions, retention_view):
    from fieldviewer_seismic_native import seismic_context
    return seismic_context(bundle, contract, entity_key, artifact_key, decisions, retention_view, target='paleoscan')


def _receipt(intake, entity_key, artifact_key, transformation):
    return dict(schema=SCHEMA, adapter_profile=PROFILE, target_version='2018.1.0',
        runtime_boundary='zero_gui_python_stdlib', operation='prepare_segy_scalar1',
        generation=intake['generation'], manifest_sha256=intake['manifest_sha256'],
        upstream_handoff_sha256=intake['handoff_sha256'],
        upstream_profile_revision=intake['profile_revision'],
        upstream_counts=intake['counts'],
        upstream_entity_preparation=intake['selected_upstream_preparation'],
        entity_key=entity_key, artifact_key=artifact_key,
        payload=PAYLOAD, transformation=transformation, execution_allowed=False,
        imports_executed=0, receiving_qualified=False, runtime_probed=False,
        state='prepared_file_only',
        scope='Lossless representation preparation only. Upstream 2023 mapping is not 2018 qualification; source/CRS/axis/mapping blockers remain unresolved. No native import, scientific acceptance or runtime availability claim.')


def prepare_paleoscan_segy(bundle, contract, entity_key, artifact_key, output, *,
                           decisions=None, retention_view=None):
    """New independent derivative plus final source-bound marker; no GUI or SDK."""
    intake, catalogue, bindings, artifact, source = _context(
        bundle, contract, entity_key, artifact_key, decisions, retention_view)
    output = Path(output).absolute()
    _unlinked(output)
    output = output.resolve(strict=False)
    protected = [*catalogue['roots'].values(), Path(bundle).resolve()]
    protected += [Path(p).resolve() for p in (decisions, retention_view) if p]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise ValueError('Receiver output must be separate from source, handoff and review inputs')
    expected = _transcode(source, artifact['recorded_sha256'])
    output.mkdir(parents=True, exist_ok=False)
    with (output / PAYLOAD).open('xb') as stream:
        written = _transcode(source, artifact['recorded_sha256'], stream)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    if written != expected or _digest_file(output / PAYLOAD) != expected['derivative_sha256']:
        raise ValueError('Derivative integrity mismatch; no committed receiver marker')
    _recheck_bindings(bindings)
    final, _, _, _, _ = _context(bundle, contract, entity_key, artifact_key, decisions, retention_view)
    if _value_hash(final) != _value_hash(intake):
        raise ValueError('Handoff/source changed during preparation; no receiver marker')
    _recheck_bindings([(output / PAYLOAD, written['derivative_sha256'], artifact['expected_bytes'])])
    if {p.name for p in output.iterdir()} != {PAYLOAD}:
        raise ValueError('Receiver output changed during preparation; no receiver marker')
    result = _receipt(intake, entity_key, artifact_key, written)
    _write_json(output / MARKER, result)
    return result


def verify_paleoscan_segy(prepared, bundle, contract, *, decisions=None, retention_view=None):
    """Recompute derivative identity from current source, never trust a rehashed receipt."""
    prepared = Path(prepared).absolute()
    _unlinked(prepared)
    stored, marker_hash = _read_json(prepared / MARKER, MAX_HANDOFF_BYTES)
    if {p.name for p in prepared.iterdir()} != {MARKER, PAYLOAD}:
        raise ValueError('Unexpected receiver preparation members')
    payload = _resolve(prepared, PAYLOAD)
    intake, _, bindings, artifact, source = _context(bundle, contract,
        stored['entity_key'], stored['artifact_key'], decisions, retention_view)
    transformation = _transcode(source, artifact['recorded_sha256'])
    expected = _receipt(intake, stored['entity_key'], stored['artifact_key'], transformation)
    if (_value_hash(expected) != _value_hash(stored)
            or _digest_file(payload) != transformation['derivative_sha256']):
        raise ValueError('Receiver preparation differs from current source and adapter policy')
    _recheck_bindings(bindings + [(prepared / MARKER, marker_hash, MAX_HANDOFF_BYTES)])
    final, _, _, _, _ = _context(bundle, contract, stored['entity_key'], stored['artifact_key'], decisions, retention_view)
    if _value_hash(final) != _value_hash(intake):
        raise ValueError('Handoff/source changed during receiver verification')
    _recheck_bindings([(payload, transformation['derivative_sha256'], artifact['expected_bytes']),
        (prepared / MARKER, marker_hash, MAX_HANDOFF_BYTES)])
    if {p.name for p in prepared.iterdir()} != {MARKER, PAYLOAD}:
        raise ValueError('Receiver members changed during verification')
    return dict(schema='fieldviewer.paleoscan-segy-intake/1', adapter_profile=PROFILE,
        state='verified_file_preparation', generation=intake['generation'],
        manifest_sha256=intake['manifest_sha256'], receiver_marker_sha256=marker_hash,
        entity_key=stored['entity_key'], artifact_key=stored['artifact_key'],
        transformation=transformation, upstream_entity_preparation=expected['upstream_entity_preparation'],
        scope=expected['scope'], execution_allowed=False, imports_executed=0,
        receiving_qualified=False, runtime_probed=False, runtime_boundary='zero_gui_python_stdlib')
