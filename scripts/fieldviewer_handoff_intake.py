# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Verify a receiving-preparation bundle against explicit current source inputs."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from fieldviewer_connectors import SCHEMA, _entity_plans, _review_bindings
from geoviewer_contract import _resolve, _unlinked, MAX_ROW_BYTES, MAX_TABLE_BYTES, MAX_TABLE_ROWS
from geoviewer_assessment_shards import (
    SCHEMA as SHARD_SCHEMA, MAX_MEMBERS, MAX_LOGICAL_BYTES, LIST_FIELDS,
    _canonical_bounded, verify_sharded_assessment)
from geoviewer_receiving_profiles import PROFILES, PROFILE_REVISION
from validate_geoviewer_contract import _load_contract, _recheck_bindings, assess_catalogue

MAX_HANDOFF_BYTES = 16 * 1024 * 1024
SHA = re.compile(r'^[0-9a-f]{64}$')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON member: ' + key)
        result[key] = value
    return result


def _parse(raw):
    return json.loads(raw, object_pairs_hook=_unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON constant')))


def _read_json(path, maximum):
    _unlinked(path)
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Handoff JSON exceeds explicit byte bound')
    result = _parse(raw.decode('utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('Expected handoff JSON object')
    return result, hashlib.sha256(raw).hexdigest()


def _value_hash(value):
    """Typed equality without true/1 equivalence or a second giant JSON string."""
    hasher = hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':')).iterencode(value):
        hasher.update(part.encode('utf-8'))
    return hasher.hexdigest()


def _member_bindings(bundle, handoff):
    files = handoff.get('files')
    if not isinstance(files, list) or not 2 <= len(files) <= MAX_MEMBERS + 2:
        raise ValueError('Invalid handoff member inventory')
    bindings = []
    names = set()
    for item in files:
        if (not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}
                or not isinstance(item['path'], str) or '/' in item['path']
                or type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_TABLE_BYTES
                or not SHA.fullmatch(str(item['sha256']))):
            raise ValueError('Invalid handoff member descriptor')
        path = _resolve(bundle, item['path'])
        if path.name.casefold() in names or path.name == 'HANDOFF.json' or not path.is_file():
            raise ValueError('Duplicate, missing or reserved handoff member')
        names.add(path.name.casefold())
        if path.stat().st_size != item['bytes']:
            raise ValueError('Handoff member size mismatch: ' + path.name)
        bindings.append((path, item['sha256'], MAX_TABLE_BYTES))
    actual = set()
    for path in bundle.iterdir():
        _unlinked(path)
        if not path.is_file():
            raise ValueError('Unexpected handoff directory or special member')
        actual.add(path.name.casefold())
    if actual != names | {'handoff.json'} or not {'assessment.json', 'entities.jsonl'}.issubset(names):
        raise ValueError('Missing or unexpected handoff members')
    _recheck_bindings(bindings)
    return bindings


def _verify_assessment(path, expected, catalogue):
    stored, _ = _read_json(path, MAX_TABLE_BYTES)
    members = {'assessment.json', 'entities.jsonl'}
    if stored.get('schema') == SHARD_SCHEMA:
        verify_sharded_assessment(path, stored,
            entities={row['entity_key'] for row in catalogue['tables']['entities']},
            artifacts={row['artifact_key'] for row in catalogue['tables']['artifacts']},
            expected_report=expected)
        # Existing shard equality uses Python values. Independently bind typed JSON.
        if _value_hash(stored['base']) != _value_hash({k: v for k, v in expected.items() if k not in LIST_FIELDS}):
            raise ValueError('Assessment typed base differs from current source')
        for field in LIST_FIELDS:
            hasher = hashlib.sha256()
            for item in expected[field]:
                hasher.update(_canonical_bounded(item, MAX_LOGICAL_BYTES))
            if hasher.hexdigest() != stored['lists'][field]['logical_sha256']:
                raise ValueError('Assessment typed list differs from current source')
            members.update(item['path'] for item in stored['lists'][field]['members'])
    elif _value_hash(stored) != _value_hash(expected):
        raise ValueError('Stored assessment differs from current source or review inputs')
    return members


def verify_handoff(bundle, contract, *, decisions=None, retention_view=None, hash_artifacts=True):
    """Read-only intake. Explicit source/review paths override no historical hints.

    Returns fresh preparation counts and blockers, never receiving acceptance.
    """
    bundle = Path(bundle).absolute()
    _unlinked(bundle)
    handoff, marker_hash = _read_json(bundle / 'HANDOFF.json', MAX_HANDOFF_BYTES)
    if (handoff.get('schema') != SCHEMA or handoff.get('target') not in PROFILES
            or handoff.get('execution_allowed') is not False
            or handoff.get('receiving_qualified') is not False
            or type(handoff.get('imports_executed')) is not int or handoff['imports_executed'] != 0
            or type(handoff.get('artifact_hashing_requested')) is not bool):
        raise ValueError('Invalid handoff schema, target or execution claims')
    target = handoff['target']
    members = _member_bindings(bundle, handoff)
    decisions = Path(decisions).absolute() if decisions else None
    retention_view = Path(retention_view).absolute() if retention_view else None
    catalogue, source_bindings = _load_contract(Path(contract), bind_missing_receipts=True)
    for key, expected in dict(generation=catalogue['manifest']['generation'],
            manifest_sha256=catalogue['pointer']['manifest_sha256'],
            project_revision=catalogue['manifest']['project_revision'],
            profile_revision=PROFILE_REVISION[target]).items():
        if handoff.get(key) != expected:
            raise ValueError('Stale or incompatible handoff binding: ' + key)
    reviews = _review_bindings(decisions, retention_view)
    recorded_reviews = handoff.get('review_inputs')
    if (not isinstance(recorded_reviews, list) or any(not isinstance(row, dict)
            or not SHA.fullmatch(str(row.get('sha256'))) for row in recorded_reviews)
            or Counter(row['sha256'] for row in recorded_reviews) != Counter(sha for _, sha, _ in reviews)):
        raise ValueError('Supply the exact reviewed decisions/retention inputs explicitly')
    original = assess_catalogue(**catalogue, profile=target,
        hash_artifacts=handoff['artifact_hashing_requested'], decisions=decisions, retention_view=retention_view)
    if (_value_hash(handoff.get('counts')) != _value_hash(original['counts'])
            or _value_hash(handoff.get('receiving_profile')) != _value_hash(original['receiving_profile'])):
        raise ValueError('Handoff counts/profile differ from current preparation')
    expected_state = 'inventory_empty' if not original['assessments'] else 'prepared_with_blockers' if any(not (
        row['source_state'] == 'source_prerequisites_satisfied'
        and row['receiving_state'] == 'qualified_for_planned_mapping')
        for row in original['assessments']) else 'prepared_for_review'
    if handoff.get('state') != expected_state:
        raise ValueError('Handoff state differs from current preparation')
    allowed = _verify_assessment(bundle / 'assessment.json', original, catalogue)
    if {path.name for path, _, _ in members} != allowed:
        raise ValueError('Handoff member list differs from assessment representation')
    with (bundle / 'entities.jsonl').open('rb') as stream:
        total_bytes = row_count = 0
        for expected in _entity_plans(catalogue, original):
            raw = stream.readline(MAX_ROW_BYTES + 1)
            total_bytes += len(raw)
            row_count += 1
            if (not raw.endswith(b'\n') or len(raw) > MAX_ROW_BYTES
                    or total_bytes > MAX_TABLE_BYTES or row_count > MAX_TABLE_ROWS
                    or _value_hash(_parse(raw)) != _value_hash(expected)):
                raise ValueError('Handoff entity plan differs from current preparation')
        if stream.read(1):
            raise ValueError('Unexpected extra handoff entity row')
    current = original
    if hash_artifacts and not handoff['artifact_hashing_requested']:
        current = assess_catalogue(**catalogue, profile=target, hash_artifacts=True,
                                   decisions=decisions, retention_view=retention_view)
    if any(row['code'] == 'HASH_MISMATCH' for row in current['diagnostics']):
        raise ValueError('Current payload hash mismatch; handoff rejected')
    ready = sum(row['source_state'] == 'source_prerequisites_satisfied'
                and row['receiving_state'] == 'qualified_for_planned_mapping'
                for row in current['assessments'])
    total = len(current['assessments'])
    _recheck_bindings(source_bindings + reviews + members + [(bundle / 'HANDOFF.json', marker_hash, MAX_HANDOFF_BYTES)])
    if {path.name for path in bundle.iterdir()} != allowed | {'HANDOFF.json'}:
        raise ValueError('Handoff member inventory changed during intake')
    hashed = bool(hash_artifacts or handoff['artifact_hashing_requested'])
    return dict(schema='fieldviewer.handoff-intake/1', target=target,
        generation=handoff['generation'], manifest_sha256=handoff['manifest_sha256'],
        profile_revision=PROFILE_REVISION[target], handoff_sha256=marker_hash,
        state='inventory_empty' if not total else 'verified_metadata_only' if not hashed
            else 'verified_with_blockers' if ready < total else 'verified_for_receiving_test',
        counts=dict(entities=total, qualified_for_planned_mapping=ready, blocked=total-ready,
                    imports_executed=0, receiving_qualified=0),
        artifact_hashing_requested=hashed,
        payload_integrity_counts=dict(
            fresh_matches=sum(a['current_identity_scope'] == 'fresh_sha256' for a in current['artifact_availability']),
            unbound_hashes=sum(a['current_identity_scope'] == 'fresh_hash_without_matching_receipt' for a in current['artifact_availability']),
            unavailable=sum(not a['available'] for a in current['artifact_availability'])),
        execution_allowed=False, runtime_probed=False,
        integrity_scope='Fresh hashes of currently available artifacts against recorded receipts' if
            hash_artifacts or handoff['artifact_hashing_requested'] else 'Recorded size/state only; no fresh payload hashes',
        scope='Source-bound preparation intake only; no target runtime, import or numerical readback')
