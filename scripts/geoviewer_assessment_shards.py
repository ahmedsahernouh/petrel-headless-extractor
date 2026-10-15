# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Lossless bounded representation for complete large profile assessments."""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import os
from pathlib import Path
import re
import uuid

from geoviewer_contract import MAX_ROW_BYTES, MAX_TABLE_BYTES, _resolve, _unlinked, digest

SCHEMA = 'geoviewer.contract-validation-shards/1'
ENCODING = 'ordered-canonical-framed-jsonl/1'
LIST_FIELDS = tuple(sorted(('artifact_availability', 'dependency_order', 'dependency_cycles',
                            'unresolved_dependencies', 'preparation_dependency_order',
                            'preparation_dependency_cycles', 'preparation_stages',
                            'assessments', 'diagnostics')))
MAX_MEMBERS = 24
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_TOTAL_ROWS = 2_000_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_LOGICAL_BYTES = 32 * 1024 * 1024
FRAGMENT_BYTES = 1024 * 1024
_SHA = re.compile(r'^[0-9a-f]{64}$')


def _canonical(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode('utf-8')


def _canonical_bounded(value, limit):
    """Encode canonical JSON in small pieces, stopping before a large allocation.

    Strings are escaped in independent slices: JSON string escaping is local to
    each code point, so concatenating the slice interiors preserves json.dumps
    bytes. Other scalar spellings are delegated to json.dumps for parity.
    """
    output = bytearray()

    def pieces(item, depth):
        if depth > 128:
            raise ValueError('Assessment logical item nesting bound exceeded')
        if isinstance(item, str):
            yield b'"'
            for start in range(0, len(item), 8192):
                escaped = json.dumps(item[start:start+8192], ensure_ascii=False,
                                     allow_nan=False)[1:-1]
                yield escaped.encode('utf-8')
            yield b'"'
        elif isinstance(item, (list, tuple)):
            yield b'['
            for index, child in enumerate(item):
                if index:
                    yield b','
                yield from pieces(child, depth+1)
            yield b']'
        elif isinstance(item, dict):
            yield b'{'
            for index, key in enumerate(sorted(item)):
                if type(key) is not str:
                    raise TypeError('Assessment JSON object keys must be strings')
                if index:
                    yield b','
                yield from pieces(key, depth+1)
                yield b':'
                yield from pieces(item[key], depth+1)
            yield b'}'
        else:
            yield json.dumps(item, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')

    for chunk in pieces(value, 0):
        if len(output) + len(chunk) + 1 > limit:
            raise ValueError('Assessment logical item byte bound exceeded')
        output.extend(chunk)
    output.extend(b'\n')
    return bytes(output)


def write_sharded_assessment(path: Path, report: dict) -> dict:
    """Write all list items in order, then commit a small binding manifest last."""
    path = Path(path).absolute(); _unlinked(path)
    if path.exists() or not path.parent.is_dir() or set(k for k, v in report.items() if isinstance(v, list)) != set(LIST_FIELDS):
        raise ValueError('Assessment destination or list-field shape is invalid')
    base = {key: value for key, value in report.items() if key not in LIST_FIELDS}
    if base.get('schema') != 'geoviewer.contract-validation/2':
        raise ValueError('Only complete committed assessments may be sharded')
    lists = {}
    all_hash = hashlib.sha256()
    total_bytes = total_rows = total_items = member_count = 0
    logical_hash = hashlib.sha256()
    for field in LIST_FIELDS:
        values = report[field]
        if not isinstance(values, list):
            raise ValueError('Assessment list field is missing')
        parts = []
        field_hash = hashlib.sha256()
        field_bytes = field_rows = field_items = 0
        field_logical_hash = hashlib.sha256()
        stream = None
        member_hash = None
        member_bytes = member_rows = 0
        member_path = None

        def close_member():
            nonlocal stream, member_hash, member_bytes, member_rows, member_path
            if stream is None:
                return
            stream.flush(); os.fsync(stream.fileno()); stream.close()
            parts.append(dict(index=len(parts) + 1, path=member_path.name,
                              rows=member_rows, bytes=member_bytes,
                              sha256=member_hash.hexdigest()))
            stream = None

        try:
            for item in values:
                logical = _canonical_bounded(item, MAX_LOGICAL_BYTES)
                field_items += 1; total_items += 1
                field_logical_hash.update(logical); logical_hash.update(logical)
                complete = b'["item",' + logical[:-1] + b']\n'
                if len(complete) <= MAX_ROW_BYTES:
                    frames = (complete,)
                else:
                    count = (len(logical) + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
                    frames = (_canonical(['fragment', field_items, index + 1, count,
                                          digest(logical), base64.b64encode(
                                              logical[index*FRAGMENT_BYTES:(index+1)*FRAGMENT_BYTES]
                                          ).decode('ascii')]) for index in range(count))
                for raw in frames:
                    size = len(raw)
                    if size > MAX_ROW_BYTES:
                        raise ValueError('Assessment physical row byte bound exceeded')
                    if total_rows >= MAX_TOTAL_ROWS:
                        raise ValueError(f'Assessment aggregate row bound exceeded: '
                                         f'{total_rows + 1} > {MAX_TOTAL_ROWS}')
                    if total_bytes + size > MAX_TOTAL_BYTES:
                        raise ValueError(f'Assessment aggregate byte bound exceeded: '
                                         f'{total_bytes + size} > {MAX_TOTAL_BYTES}')
                    if stream is None or member_bytes + size > MAX_TABLE_BYTES or member_rows >= 1_000_000:
                        close_member()
                        if member_count >= MAX_MEMBERS:
                            raise ValueError('Assessment member count bound exceeded')
                        member_path = path.with_name(f'{path.stem}.{field}.{len(parts) + 1:04d}.jsonl')
                        _unlinked(member_path)
                        stream = member_path.open('xb')
                        member_count += 1
                        member_hash = hashlib.sha256(); member_bytes = member_rows = 0
                    if stream.write(raw) != size:
                        raise OSError('Incomplete assessment member write')
                    member_hash.update(raw); field_hash.update(raw); all_hash.update(raw)
                    member_bytes += size; member_rows += 1
                    field_bytes += size; field_rows += 1
                    total_bytes += size; total_rows += 1
            close_member()
        finally:
            if stream is not None:
                stream.close()
        lists[field] = dict(items=field_items, rows=field_rows, bytes=field_bytes,
                            sha256=field_hash.hexdigest(), logical_sha256=field_logical_hash.hexdigest(),
                            members=parts)
    envelope = dict(schema=SCHEMA, encoding=ENCODING, base=base,
                    list_order=list(LIST_FIELDS), lists=lists,
                    aggregate=dict(items=total_items, rows=total_rows, bytes=total_bytes,
                                   sha256=all_hash.hexdigest(), logical_sha256=logical_hash.hexdigest(),
                                   members=member_count),
                    generation=base['generation'], manifest_sha256=base['manifest_sha256'],
                    profile=base['profile'], profile_revision=base['profile_revision'],
                    counts=base['counts'])
    raw = _canonical_bounded(envelope, MAX_MANIFEST_BYTES)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex[:12] + '.partial')
    try:
        with pending.open('xb') as stream:
            if stream.write(raw) != len(raw):
                raise OSError('Incomplete assessment manifest write')
            stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            raise ValueError('Assessment manifest destination appeared')
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return dict(format=ENCODING, bytes=len(raw), sha256=digest(raw),
                members=[part for field in LIST_FIELDS for part in lists[field]['members']])


def verify_sharded_assessment(path: Path, envelope: dict, *, entities: set[str],
                              artifacts: set[str], expected_report: dict | None = None) -> dict:
    """Stream every member, verify ordering and exact coverage without collecting rows."""
    path = Path(path).absolute(); _unlinked(path)
    if (not isinstance(envelope, dict) or envelope.get('schema') != SCHEMA
            or envelope.get('encoding') != ENCODING or envelope.get('list_order') != list(LIST_FIELDS)
            or set(envelope.get('lists', {})) != set(LIST_FIELDS)):
        raise ValueError('Unsupported or incomplete assessment shard manifest')
    base = envelope.get('base')
    if (not isinstance(base, dict) or base.get('schema') != 'geoviewer.contract-validation/2'
            or any(base.get(key) != envelope.get(key) for key in
                   ('generation', 'manifest_sha256', 'profile', 'profile_revision', 'counts'))
            or any(isinstance(value, list) for value in base.values())):
        raise ValueError('Assessment shard base binding mismatch')
    if expected_report is not None:
        if ({key: value for key, value in expected_report.items() if key not in LIST_FIELDS} != base
                or set(key for key, value in expected_report.items() if isinstance(value, list)) != set(LIST_FIELDS)):
            raise ValueError('Assessment readback base differs from intended report')
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('Assessment manifest byte bound exceeded')
    declared = envelope.get('aggregate')
    if (not isinstance(declared, dict)
            or any(type(declared.get(name)) is not int or not 0 <= declared[name] <= bound
                   for name, bound in (('items', MAX_TOTAL_ROWS), ('rows', MAX_TOTAL_ROWS),
                                       ('bytes', MAX_TOTAL_BYTES), ('members', MAX_MEMBERS)))):
        raise ValueError('Assessment declared aggregate bound invalid')
    total_hash = hashlib.sha256()
    total_bytes = total_rows = total_items = member_count = 0
    logical_hash = hashlib.sha256()
    seen_paths = set()
    seen_entities = set(); seen_artifacts = set()
    ready = blocked = qualified = 0
    for field in LIST_FIELDS:
        spec = envelope['lists'][field]
        if (not isinstance(spec, dict) or type(spec.get('items')) is not int
                or not 0 <= spec['items'] <= MAX_TOTAL_ROWS
                or type(spec.get('rows')) is not int or type(spec.get('bytes')) is not int
                or not 0 <= spec['rows'] <= MAX_TOTAL_ROWS or not 0 <= spec['bytes'] <= MAX_TOTAL_BYTES
                or not isinstance(spec.get('members'), list) or not _SHA.fullmatch(str(spec.get('sha256')))
                or not _SHA.fullmatch(str(spec.get('logical_sha256')))):
            raise ValueError('Assessment list descriptor invalid')
        field_hash = hashlib.sha256(); field_logical_hash = hashlib.sha256()
        field_bytes = field_rows = field_items = 0
        pending = bytearray(); pending_count = pending_sha = None

        def consume_item(item, logical):
            nonlocal field_items, ready, blocked, qualified
            if expected_report is not None:
                intended = expected_report[field]
                if field_items >= len(intended) or item != intended[field_items]:
                    raise ValueError('Assessment readback item differs from intended report')
            field_items += 1
            if total_items + field_items > MAX_TOTAL_ROWS:
                raise ValueError('Assessment logical item count bound exceeded')
            field_logical_hash.update(logical); logical_hash.update(logical)
            if field == 'assessments':
                key = item.get('entity_key') if isinstance(item, dict) else None
                if key not in entities or key in seen_entities:
                    raise ValueError('Assessment entity coverage duplicate or unknown')
                seen_entities.add(key)
                state = item.get('source_state')
                if state == 'source_prerequisites_satisfied': ready += 1
                elif state == 'source_blocked': blocked += 1
                else: raise ValueError('Assessment source state unknown')
                qualified += item.get('receiving_state') == 'qualified_for_planned_mapping'
            elif field == 'artifact_availability':
                key = item.get('artifact_key') if isinstance(item, dict) else None
                if key not in artifacts or key in seen_artifacts:
                    raise ValueError('Assessment artifact coverage duplicate or unknown')
                seen_artifacts.add(key)
        for ordinal, member in enumerate(spec['members'], 1):
            if (not isinstance(member, dict) or type(member.get('index')) is not int or member['index'] != ordinal
                    or member.get('path') != f'{path.stem}.{field}.{ordinal:04d}.jsonl'
                    or type(member.get('rows')) is not int or not 0 < member['rows'] <= 1_000_000
                    or type(member.get('bytes')) is not int or not 0 < member['bytes'] <= MAX_TABLE_BYTES
                    or not _SHA.fullmatch(str(member.get('sha256')))):
                raise ValueError('Assessment member descriptor/order/bound invalid')
            member_path = _resolve(path.parent, member['path'])
            if not member_path.is_relative_to(path.parent) or member_path in seen_paths:
                raise ValueError('Assessment member path duplicates or escapes')
            seen_paths.add(member_path)
            member_count += 1
            if member_count > MAX_MEMBERS:
                raise ValueError('Assessment member count bound exceeded')
            hasher = hashlib.sha256(); byte_count = row_count = 0
            with member_path.open('rb') as stream:
                while line := stream.readline(MAX_ROW_BYTES + 1):
                    byte_count += len(line); row_count += 1
                    if (len(line) > MAX_ROW_BYTES or byte_count > MAX_TABLE_BYTES
                            or row_count > 1_000_000 or not line.endswith(b'\n')
                            or total_bytes + field_bytes + byte_count > MAX_TOTAL_BYTES
                            or total_rows + field_rows + row_count > MAX_TOTAL_ROWS):
                        raise ValueError('Assessment member byte/row bound exceeded')
                    frame = json.loads(line)
                    if _canonical(frame) != line or not isinstance(frame, list):
                        raise ValueError('Assessment member noncanonical or ambiguous JSON row')
                    if len(frame) == 2 and frame[0] == 'item':
                        if pending:
                            raise ValueError('Assessment fragment sequence incomplete')
                        logical = _canonical_bounded(frame[1], MAX_LOGICAL_BYTES)
                        consume_item(frame[1], logical)
                    elif len(frame) == 6 and frame[0] == 'fragment':
                        _, ordinal, index, count, claimed_sha, payload = frame
                        if (type(ordinal) is not int or ordinal != field_items + 1
                                or type(index) is not int or type(count) is not int
                                or not 1 <= index <= count <= MAX_LOGICAL_BYTES // FRAGMENT_BYTES
                                or count < 2
                                or index != (len(pending) + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES + 1
                                or not _SHA.fullmatch(str(claimed_sha)) or not isinstance(payload, str)):
                            raise ValueError('Assessment fragment order/count invalid')
                        if pending and (count != pending_count or claimed_sha != pending_sha):
                            raise ValueError('Assessment fragment identity changed')
                        try:
                            chunk = base64.b64decode(payload, validate=True)
                        except (ValueError, binascii.Error) as exc:
                            raise ValueError('Assessment fragment base64 invalid') from exc
                        if not 0 < len(chunk) <= FRAGMENT_BYTES or len(pending) + len(chunk) > MAX_LOGICAL_BYTES:
                            raise ValueError('Assessment fragment byte bound exceeded')
                        if index < count and len(chunk) != FRAGMENT_BYTES:
                            raise ValueError('Assessment nonterminal fragment is short')
                        pending.extend(chunk); pending_count = count; pending_sha = claimed_sha
                        if index == count:
                            logical = bytes(pending)
                            if digest(logical) != claimed_sha:
                                raise ValueError('Assessment logical item digest mismatch')
                            item = json.loads(logical)
                            if (_canonical_bounded(item, MAX_LOGICAL_BYTES) != logical
                                    or len(b'["item",' + logical[:-1] + b']\n') <= MAX_ROW_BYTES):
                                raise ValueError('Assessment logical item noncanonical')
                            consume_item(item, logical)
                            pending.clear(); pending_count = pending_sha = None
                    else:
                        raise ValueError('Assessment physical row frame invalid')
                    hasher.update(line); field_hash.update(line); total_hash.update(line)
            if (byte_count != member['bytes'] or row_count != member['rows']
                    or hasher.hexdigest() != member['sha256']):
                raise ValueError('Assessment member bytes/count/hash mismatch')
            field_bytes += byte_count; field_rows += row_count
        if (pending or field_items != spec['items'] or field_rows != spec['rows'] or field_bytes != spec['bytes']
                or field_hash.hexdigest() != spec['sha256']
                or field_logical_hash.hexdigest() != spec['logical_sha256']
                or bool(spec['members']) != bool(spec['rows'])):
            raise ValueError('Assessment list aggregate identity mismatch')
        if expected_report is not None and field_items != len(expected_report[field]):
            raise ValueError('Assessment readback list length differs from intended report')
        total_bytes += field_bytes; total_rows += field_rows; total_items += field_items
        if total_rows > MAX_TOTAL_ROWS or total_items > MAX_TOTAL_ROWS or total_bytes > MAX_TOTAL_BYTES:
            raise ValueError('Assessment aggregate byte/row bound exceeded')
    aggregate = envelope.get('aggregate', {})
    if (type(aggregate.get('items')) is not int or aggregate['items'] != total_items
            or type(aggregate.get('rows')) is not int or aggregate['rows'] != total_rows
            or type(aggregate.get('bytes')) is not int or aggregate['bytes'] != total_bytes
            or type(aggregate.get('members')) is not int or aggregate['members'] != member_count
            or aggregate.get('sha256') != total_hash.hexdigest()
            or aggregate.get('logical_sha256') != logical_hash.hexdigest()):
        raise ValueError('Assessment aggregate identity mismatch')
    counts = base['counts']
    if (seen_entities != entities or seen_artifacts != artifacts
            or any(type(counts.get(key)) is not int or counts[key] != value for key, value in
                   dict(entities=len(entities), artifacts=len(artifacts), source_ready=ready,
                        source_blocked=blocked, qualified_for_planned_mapping=qualified,
                        receiving_qualified=0, receiving_imports_executed=0).items())):
        raise ValueError('Assessment manifest counts or exact coverage mismatch')
    return dict(status='verified', members=member_count, rows=total_rows, bytes=total_bytes)
