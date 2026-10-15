# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only validation of a committed source contract and receiving prerequisites."""
from __future__ import annotations

import argparse
import hashlib
import json
from geoviewer_io import read_up_to, ContractProgress, contract_rows
from pathlib import Path
import sys
import re
import heapq
from contextlib import contextmanager

from geoviewer_contract import POINTER, SCHEMA, SHARDED_SCHEMA, SHARDED_ENCODING, MAX_SIDECAR_BYTES, MAX_HEAD_BYTES, _resolve, _unlinked, canonical, digest
from geoviewer_contract import MAX_TABLE_BYTES, MAX_TABLE_ROWS, MAX_ROW_BYTES, MAX_TABLE_SHARDS, MAX_SHARDED_TABLE_BYTES, MAX_SHARDED_TABLE_ROWS
from geoviewer_native_receipt import MAX_NATIVE_RECEIPT_BYTES
from geoviewer_contract_retention import load_retention_view
from geoviewer_receiving_profiles import PROFILE_REVISION, PROFILES as RECEIVING_PROFILES, assess_mapping, load_decisions, metadata_object
from geoviewer_receiving_profiles import _grid, _single
from geoviewer_receiving_profiles import exact_relationship_target, CORE_BLOCKERS
from geoviewer_metadata_evidence import resolve_values, status_is, summarize_rows


PROFILES = ("source", "opendtect", "paleoscan")
_FULL_BY_CATEGORY = {"surface_geometry": {"geometry_values"}, "surface_property": {"property_values"},
                     "qa_scalar": {"property_values"}, "seismic": {"full_payload"},
                     "well_log": {"data"}, "trajectory": {"data"}, "well": {"data"},
                     "culture_polygon": {"data"}, "culture_point": {"data"}}
_RECORDED_NUMERICAL_QC = frozenset(('exact_readback', 'every_amplitude_and_trace_geometry',
    'native_decoder; row-preserving split; count checked', 'native_model_cross_checks'))
_METADATA_REASONS = frozenset(('UNRESOLVED_VALUE_UNIT', 'UNRESOLVED_CRS',
    'SEISMIC_METADATA_UNBOUND', 'SEISMIC_GEOMETRY_CONFLICT', 'SEISMIC_AXIS_CONFLICT',
    'SEISMIC_DOMAIN_UNRESOLVED', 'SEGY_HEADER_PROFILE_UNSPECIFIED', 'TIME_CONVENTION_UNRESOLVED',
    'VERTICAL_REFERENCE_UNRESOLVED', 'SEISMIC_METADATA_STALE', 'NODE_REGISTRATION_UNVERIFIED',
    'VERTICAL_UNIT_UNRESOLVED', 'HORIZONTAL_UNIT_UNRESOLVED', 'HEAD_METADATA_STALE'))
_RELATIONSHIP_REASONS = frozenset(('PARENT_GEOMETRY_UNRESOLVED', 'SOURCE_WELL_UNRESOLVED',
    'DEPENDENCY_CYCLE', 'REQUIRED_DEPENDENCY_UNAVAILABLE', 'NODE_MASK_UNAVAILABLE'))


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with ContractProgress("artifact_hash", unit="bytes", path=str(path)) as progress, path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            hasher.update(block)
            progress.advance(len(block))
    return hasher.hexdigest()


def _metadata_digest(path: Path, limit: int | None = None) -> str:
    """Hash bounded catalogue/receipt bytes; bulk artifact hashing stays separate."""
    _unlinked(path)
    limit = MAX_TABLE_BYTES if limit is None else limit
    hasher = hashlib.sha256()
    total = 0
    with ContractProgress('metadata_hash', unit='bytes', path=str(path)) as progress, path.open('rb') as handle:
        while block := handle.read(min(1024 * 1024, limit + 1 - total)):
            total += len(block)
            progress.advance(len(block))
            if total > limit:
                raise ValueError('Contract metadata exceeds explicit byte bound')
            hasher.update(block)
    return hasher.hexdigest()


def _dependency_order(entities: dict, relations: list[dict]) -> tuple[list[str], list[str], list[str]]:
    # A folder parent is ancestry, not a payload dependency.
    required = {key: set() for key in entities}
    unresolved = []
    for row in relations:
        if row["relation"] not in ("source_well", "trajectory_provider", "materialized_payload", "preparation_dependency") or row["status"] != "exact_id_reference":
            continue
        source, target = row["source_key"], row["target_key"]
        if target not in entities:
            unresolved.append(target)
        else:
            required[source].add(target)
    dependents = {key: set() for key in entities}
    for key, dependencies in required.items():
        for dependency in dependencies:
            dependents[dependency].add(key)
    pending = {key: len(value) for key, value in required.items()}
    ready = [key for key, count in pending.items() if not count]
    heapq.heapify(ready)
    order = []
    while ready:
        key = heapq.heappop(ready)
        order.append(key)
        for child in dependents[key]:
            pending[child] -= 1
            if not pending[child]:
                heapq.heappush(ready, child)
    return order, sorted(key for key, count in pending.items() if count), sorted(set(unresolved))


def _json_with_digest(path: Path) -> tuple[dict, str]:
    _unlinked(path)
    with path.open('rb') as handle:
        raw = read_up_to(handle, MAX_TABLE_BYTES + 1)
    if len(raw) > MAX_TABLE_BYTES:
        raise ValueError('Contract metadata exceeds explicit byte bound')
    data = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data, digest(raw)


def _json(path: Path) -> dict:
    return _json_with_digest(path)[0]


def _table_rows(root: Path, name: str, spec: dict, schema: str, *, collect: bool = True):
    """Verify old single files or explicitly ordered, bounded evidence shards."""
    if not isinstance(spec, dict):
        raise ValueError('Invalid contract table descriptor')
    sharded = spec.get('encoding') == SHARDED_ENCODING
    if sharded:
        if schema != SHARDED_SCHEMA or name != 'metadata_evidence':
            raise ValueError('Unsupported sharded contract table')
        shards = spec.get('shards')
        if not isinstance(shards, list) or not 2 <= len(shards) <= MAX_TABLE_SHARDS:
            raise ValueError('Invalid contract shard count')
        if (type(spec.get('rows')) is not int or not 0 < spec['rows'] <= MAX_SHARDED_TABLE_ROWS
                or type(spec.get('bytes')) is not int or not 0 < spec['bytes'] <= MAX_SHARDED_TABLE_BYTES):
            raise ValueError('Sharded contract aggregate bound exceeded')
    else:
        if spec.get('encoding') is not None or schema == SHARDED_SCHEMA and name == 'metadata_evidence':
            raise ValueError('Unsupported contract table encoding')
        shards = [spec]
    rows = []
    bindings = []
    aggregate = hashlib.sha256()
    aggregate_rows = aggregate_bytes = 0
    seen_paths = set()
    for ordinal, part in enumerate(shards, 1):
        if not isinstance(part, dict):
            raise ValueError('Invalid contract shard descriptor')
        if (type(part.get('rows')) is not int or not 0 <= part['rows'] <= MAX_TABLE_ROWS
                or sharded and (part['rows'] == 0 or type(part.get('bytes')) is not int
                                or not 0 < part['bytes'] <= MAX_TABLE_BYTES or type(part.get('index')) is not int
                                or part['index'] != ordinal
                                or part.get('path') != f'metadata_evidence.{ordinal:04d}.jsonl')):
            raise ValueError('Contract table row bound or byte/shard bound exceeded')
        path = _resolve(root, part['path'])
        if not path.is_relative_to(root) or path in seen_paths:
            raise ValueError('Duplicate or escaping contract table path')
        seen_paths.add(path)
        part_hash = hashlib.sha256(); part_bytes = part_rows = 0
        with ContractProgress('catalogue_table_read', total=part['rows'], table=name,
                              shard=ordinal if sharded else None) as progress, path.open('rb') as handle:
            while line := handle.readline(MAX_ROW_BYTES + 1):
                part_bytes += len(line)
                part_rows += 1
                if (len(line) > MAX_ROW_BYTES or part_bytes > MAX_TABLE_BYTES
                        or part_rows > MAX_TABLE_ROWS or not line.endswith(b'\n')):
                    raise ValueError('Contract table exceeds explicit byte/row bounds')
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('Contract table row must be an object')
                part_hash.update(line); aggregate.update(line)
                if collect:
                    rows.append(row)
                progress.advance()
        if (part_hash.hexdigest() != part.get('sha256') or part_rows != part['rows']
                or sharded and part_bytes != part['bytes']):
            raise ValueError(f'Table hash/count mismatch: {name}' if not sharded
                             else f'Table shard hash/count/bytes mismatch: {name}/{ordinal}')
        aggregate_rows += part_rows; aggregate_bytes += part_bytes
        if aggregate_rows > MAX_SHARDED_TABLE_ROWS or aggregate_bytes > MAX_SHARDED_TABLE_BYTES:
            raise ValueError('Sharded contract aggregate bound exceeded')
        bindings.append((path, part['sha256'], MAX_TABLE_BYTES))
    if sharded and (aggregate_rows != spec['rows'] or aggregate_bytes != spec['bytes']
                    or aggregate.hexdigest() != spec.get('sha256')):
        raise ValueError('Sharded contract aggregate identity mismatch')
    return rows, bindings, aggregate_rows


def _diagnostic(entity: str, artifact: str | None, code: str, field: str, reason: str,
                *, severity: str = "blocker", evidence: object = None, scope: str = "", next_action: str = "") -> dict:
    return dict(entity_key=entity, artifact_key=artifact, code=code, severity=severity,
                affected_mapping="source_preparation", field_or_link=field, reason=reason,
                current_evidence=evidence, searched_scope=scope, next_action=next_action)


def validate_contract(contract: Path, profile: str = "source", *, hash_artifacts: bool = False,
                      decisions: Path | None = None, retention_view: Path | None = None) -> dict:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    if decisions is not None and profile == 'source':
        raise ValueError('Explicit preparation decisions require a receiving profile; source truth is never overridden')
    catalogue, _ = _load_contract(contract)
    return assess_catalogue(**catalogue, profile=profile, hash_artifacts=hash_artifacts,
                            decisions=decisions, retention_view=retention_view)


def _load_contract(contract: Path, *, bind_missing_receipts: bool = False) -> tuple[dict, list]:
    """Parse one bounded, hash-verified generation; no global cache or bulk reads."""
    _unlinked(Path(contract).absolute())
    contract = Path(contract).resolve(strict=True)
    if contract.name == POINTER:
        destination = contract.parent
        pointer, pointer_digest = _json_with_digest(contract)
    elif contract.name == "manifest.json":
        destination = contract.parents[3]
        pointer, pointer_digest = _json_with_digest(destination / POINTER)
        if (destination / pointer.get("manifest", "")).resolve() != contract:
            raise ValueError("Requested generation is stale; current pointer differs")
    else:
        raise ValueError("Select INTEROPERABILITY_CURRENT.json or its current manifest.json")
    bindings = [(destination / POINTER, pointer_digest, MAX_TABLE_BYTES)]
    if pointer.get("schema") not in (SCHEMA, SHARDED_SCHEMA):
        raise ValueError("Unknown pointer schema")
    manifest_path = _resolve(destination, pointer.get("manifest", ""))
    if not manifest_path.is_relative_to(destination / "INTEROPERABILITY" / "generations"):
        raise ValueError("Manifest outside generation area")
    manifest, manifest_digest = _json_with_digest(manifest_path)
    if manifest_digest != pointer.get("manifest_sha256"):
        raise ValueError("Manifest hash differs from committed pointer")
    if manifest.get("schema") != pointer['schema'] or manifest.get("generation") != pointer.get("generation"):
        raise ValueError("Generation identity mismatch")
    bindings.append((manifest_path, manifest_digest, MAX_TABLE_BYTES))
    run = _resolve(destination, manifest["roots"]["run"], allow_parent=True)
    package = _resolve(destination, manifest["roots"]["package"], allow_parent=True)
    if run == Path(run.anchor) or not destination.is_relative_to(run) or not package.is_relative_to(run):
        raise ValueError("Declared run/package roots are not bounded")
    roots = dict(exports=destination, package=package, run=run)
    index_path = _resolve(destination, manifest["file_index"]["path"])
    index, index_digest = _json_with_digest(index_path)
    if digest(canonical(index)) != manifest["file_index"]["canonical_sha256"]:
        raise ValueError("FILE_INDEX changed since generation commit")
    # Independently enforce current intake boundaries for older committed
    # catalogues as well as generations made by the current writer.
    from geoviewer_contract import validate_seismic_index, _base_and_path
    validate_seismic_index(index)
    for row in index.get('files', []) + index.get('partial_files', []):
        _base_and_path(package, destination, run, row)
    bindings.append((index_path, index_digest, MAX_TABLE_BYTES))
    for receipt_name, recorded in manifest["receipts"].items():
        receipt_limit = MAX_NATIVE_RECEIPT_BYTES if receipt_name == 'native_recovery' and recorded.get('path', '').endswith('/native_recovery_report.json') else MAX_TABLE_BYTES
        if not recorded.get("sha256"):
            if bind_missing_receipts:
                path = _resolve(package, recorded['path'])
                if path.exists():
                    raise ValueError(f'Source receipt appeared since generation commit: {receipt_name}')
                bindings.append((path, None, receipt_limit))
            continue
        path = _resolve(package, recorded["path"])
        if not path.is_file() or _metadata_digest(path, receipt_limit) != recorded["sha256"]:
            raise ValueError(f"Source receipt changed or missing: {receipt_name}")
        bindings.append((path, recorded['sha256'], receipt_limit))
    if set(manifest.get('tables', {})) != set(('entities', 'artifacts', 'relationships', 'metadata_evidence', 'gaps')):
        raise ValueError('Contract table set mismatch')
    tables = {}
    for name, spec in manifest['tables'].items():
        rows, table_bindings, _ = _table_rows(manifest_path.parent, name, spec, pointer['schema'])
        tables[name] = rows
        bindings.extend(table_bindings)
    if bind_missing_receipts:
        # Bind the observed small metadata copy, including absence. Already stale
        # bytes still produce the normal profile diagnostic; changing them during
        # publication prevents one bundle from combining different observations.
        artifacts = {a['artifact_key']: a for a in tables['artifacts']}
        for binding in manifest.get('metadata_bindings', []):
            artifact = artifacts.get(binding.get('artifact_key'))
            if not artifact or any(binding.get(k) != artifact.get(k) for k in ('path_base', 'relative_path')):
                raise ValueError('Dangling or conflicting metadata binding')
            path = _resolve(roots[artifact['path_base']], artifact['relative_path'])
            limit = MAX_HEAD_BYTES if binding.get('kind') == 'native_head_rows' else MAX_SIDECAR_BYTES
            actual = _metadata_digest(path, limit) if path.exists() else None
            bindings.append((path, actual, limit))
    return dict(tables=tables, roots=roots, manifest=manifest, pointer=pointer, index=index), bindings


def _recheck_bindings(bindings: list) -> None:
    for path, expected, limit in contract_rows(bindings, "reference_validation", pass_name="publication_recheck"):
        try:
            _unlinked(path)
            valid = not path.exists() if expected is None else _metadata_digest(path, limit) == expected
        except (OSError, ValueError) as exc:
            raise ValueError(f'Contract snapshot changed or unreadable: {path}') from exc
        if not valid:
            raise ValueError(f'Contract snapshot changed: {path}')


@contextmanager
def verified_profile_assessments(contract: Path, profiles=PROFILES, *, hash_artifacts: bool = False):
    """Share parsed source metadata, not mutable assessments or file availability.

    Each profile assesses an independent copy and freshly checks artifact state
    and bound metadata sidecars. The caller may write reports inside this context,
    but must not accept/publish their bundle until normal context exit verifies
    every source binding again. The final pass streams hashes, without reparsing
    tables. It is a bounded consistency check, not filesystem locking or a claim
    that artifact bytes cannot change after their individual check.
    """
    profiles = tuple(profiles)
    if not profiles or len(set(profiles)) != len(profiles) or any(p not in PROFILES for p in profiles):
        raise ValueError('Select unique supported profiles')
    catalogue, bindings = _load_contract(contract, bind_missing_receipts=True)
    # Assessment mutates only artifact dictionaries with current availability,
    # retention and hash state. Source evidence (potentially millions of rows)
    # remains read-only and is shared across profile passes.
    def profile_catalogue():
        tables = dict(catalogue['tables'])
        tables['artifacts'] = [dict(row) for row in tables['artifacts']]
        return dict(catalogue, tables=tables)
    reports = {profile: assess_catalogue(**profile_catalogue(), profile=profile,
                                        hash_artifacts=hash_artifacts) for profile in profiles}
    del catalogue
    yield reports
    _recheck_bindings(bindings)


def assess_catalogue(*, tables: dict, roots: dict[str, Path], manifest: dict,
                     pointer: dict, index: dict, profile: str = "source",
                     hash_artifacts: bool = False, decisions: Path | None = None,
                     retention_view: Path | None = None,
                     committed_generation: bool = True) -> dict:
    """Assess normalized tables in memory with the normal conservative source rules.

    A derived legacy view must pass ``committed_generation=False``. That inserts
    an unwaivable publication blocker before any source/receiving assessment.
    """
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    if not committed_generation and (decisions is not None or retention_view is not None):
        raise ValueError("Legacy assessment does not accept generation-bound decisions or retention views")
    entities = {row["entity_key"]: row for row in tables["entities"]}
    if len(entities) != len(tables['entities']):
        raise ValueError('Duplicate entity identity')
    artifacts = tables["artifacts"]
    retained = (load_retention_view(retention_view, generation=manifest['generation'],
                manifest_sha256=pointer['manifest_sha256'], artifacts=artifacts) if retention_view else {})
    chosen_decisions = load_decisions(decisions, generation=manifest['generation'],
                        manifest_sha256=pointer['manifest_sha256'], profile=profile, entity_keys=set(entities))
    diagnostics = []
    by_entity = {key: [] for key in entities}
    seen_artifacts = set()
    for artifact in contract_rows(artifacts, "reference_validation", profile=profile):
        key = artifact["artifact_key"]
        owners = artifact.get('owner_keys', [artifact['entity_key']])
        if (key in seen_artifacts or not isinstance(owners, list) or not owners or
                len(set(owners)) != len(owners) or artifact['entity_key'] not in owners or
                any(owner not in entities for owner in owners)):
            raise ValueError("Duplicate or dangling artifact")
        seen_artifacts.add(key)
        for owner in owners:
            by_entity[owner].append(artifact)
        base = artifact["path_base"]
        if base not in roots or (base == "run" and entities[artifact["entity_key"]]["category"] != "seismic"):
            raise ValueError("Invalid path base")
        path = _resolve(roots[base], artifact["relative_path"])
        if not path.is_relative_to(roots[base]):
            raise ValueError("Artifact escapes declared base")
        artifact["current_path"] = str(path)
        physical_present = path.is_file()
        size_matches = physical_present and path.stat().st_size == artifact["expected_bytes"]
        artifact['physical_present'] = physical_present
        artifact['current_retention'] = retained.get(key, {}).get('retention', {'status': 'not_declared', 'reason': 'No retention view supplied'})
        retention_status = artifact['current_retention']['status']
        retention_conflict = (retention_status == 'pruned' and physical_present or retention_status == 'retained' and not size_matches)
        artifact["available"] = size_matches and retention_status not in ('pruned', 'unavailable', 'external_reference')
        if retention_conflict:
            diagnostics.append(_diagnostic(artifact['entity_key'], key, 'RETENTION_STATE_CONFLICT', 'retention',
                'Current file state differs from declared retention view', evidence=artifact['current_retention'],
                next_action='Reconcile current copy without modifying the original extraction receipt'))
        artifact["current_identity_scope"] = "recorded_size_only" if artifact["available"] else "unavailable"
        if artifact["available"] and hash_artifacts:
            actual = _file_digest(path)
            expected = artifact.get("recorded_sha256")
            artifact["current_identity_scope"] = "fresh_sha256" if expected and actual == expected else "fresh_hash_without_matching_receipt"
            if expected and actual != expected:
                artifact["available"] = False
                diagnostics.append(_diagnostic(artifact["entity_key"], key, "HASH_MISMATCH", "sha256",
                                               "Current bytes differ from recorded digest", evidence=expected,
                                               scope=artifact["source_locator"], next_action="Restore verified retained artifact"))
        if not artifact["available"]:
            code = ('ARTIFACT_PRUNED' if retention_status == 'pruned' else
                    'ARTIFACT_EXTERNAL_REFERENCE' if retention_status == 'external_reference' else
                    'ARTIFACT_SIZE_MISMATCH' if physical_present and not size_matches else 'ARTIFACT_UNAVAILABLE')
            diagnostics.append(_diagnostic(artifact["entity_key"], key, "ARTIFACT_UNAVAILABLE", "path",
                                           "Current copy cannot supply this artifact; original outcome is unchanged", evidence=artifact["relative_path"],
                                           scope=base, next_action="Restore artifact from original accepted delivery"))
            if code != 'ARTIFACT_UNAVAILABLE':
                diagnostics.append(_diagnostic(artifact['entity_key'], key, code, 'retention',
                    artifact['current_retention']['reason'], evidence=artifact['current_retention'], scope=base))
        if artifact["index_section"] != "files" or artifact["original_status"] != "exported":
            diagnostics.append(_diagnostic(artifact["entity_key"], key, "PARTIAL_ARTIFACT", "publication",
                                           "Checkpoint-only or unaccepted row", evidence=artifact["original_status"],
                                           scope=artifact["source_locator"], next_action="Require final accepted publication"))
    stale_metadata = set()
    stale_heads = set()
    by_artifact = {a['artifact_key']: a for a in artifacts}
    for binding in manifest.get('metadata_bindings', []):
        artifact = by_artifact.get(binding.get('artifact_key'))
        if not artifact or any(binding.get(k) != artifact.get(k) for k in ('path_base', 'relative_path')):
            raise ValueError('Dangling or conflicting metadata binding')
        path = _resolve(roots[artifact['path_base']], artifact['relative_path'])
        head_binding = binding.get('kind') == 'native_head_rows'
        limit = MAX_HEAD_BYTES if head_binding else MAX_SIDECAR_BYTES
        try:
            with path.open('rb') as handle:
                raw = read_up_to(handle, limit + 1)
            if len(raw) > limit or digest(raw) != binding.get('sha256'):
                raise ValueError('Bound metadata changed since generation commit')
        except (OSError, ValueError) as exc:
            for owner in artifact.get('owner_keys', [artifact['entity_key']]):
                (stale_heads if head_binding else stale_metadata).add(owner)
                diagnostics.append(_diagnostic(owner, artifact['artifact_key'], 'HEAD_METADATA_STALE' if head_binding else 'SEISMIC_METADATA_STALE',
                                               'metadata_binding', str(exc), scope=artifact['relative_path']))
    for relation in tables["relationships"]:
        if relation["source_key"] not in entities or relation["target_key"] not in entities:
            raise ValueError("Dangling relationship")
    evidence = {key: {} for key in entities}
    for row in tables["metadata_evidence"]:
        if row["entity_key"] not in entities:
            raise ValueError("Dangling metadata evidence")
        evidence[row["entity_key"]].setdefault(row["field"], []).append(row["raw_value"])
    metadata_resolution = summarize_rows(tables['metadata_evidence'])
    for row in tables["gaps"]:
        if row["entity_key"] not in entities:
            raise ValueError("Dangling gap")
    order, cycles, unresolved_dependencies = _dependency_order(entities, tables["relationships"])
    preparing_relations = [dict(r, relation='preparation_dependency') if
        (r['relation'] == 'native_parent' and entities[r['source_key']]['category'] == 'surface_property') or
        r['relation'] == 'reference_seismic' else r for r in tables['relationships']]
    preparation_order, preparation_cycles, _ = _dependency_order(entities, preparing_relations)
    # Planned well creation must be assessed before its dependent log/track.
    # Existing destination wells are an identity-only receiving requirement;
    # their source head payload is not required for the supported log write.
    relations_by_source = {key: {} for key in entities}
    for relation in tables['relationships']:
        relations_by_source[relation['source_key']].setdefault(relation['relation'], []).append(relation)
    cycle_keys = set(cycles)
    preparation_cycle_keys = set(preparation_cycles)
    entity_keys = set(entities)
    entity_categories = {k: n['category'] for k, n in entities.items()}

    def dependency_payload_ready(target, kind=None):
        expected = {'source_well': 'well', 'trajectory_provider': 'trajectory',
                    'materialized_payload': 'seismic', 'reference_seismic': 'seismic'}
        category = entity_categories.get(target)
        if kind in expected and category != expected[kind]:
            return False
        full = [a for a in by_entity.get(target, [])
                if a['role'] in _FULL_BY_CATEGORY.get(category, set())
                and a['index_section'] == 'files' and a['original_status'] == 'exported'
                and a['available']]
        return bool(full) and all(a['numerical_qc'] in _RECORDED_NUMERICAL_QC and
            re.fullmatch(r'[0-9a-f]{64}', str(a.get('recorded_sha256') or '')) for a in full)
    assessments = []
    for key, node in contract_rows(sorted(entities.items()), "profile_assessment", profile=profile):
        group = by_entity[key]
        accepted = [a for a in group if a["index_section"] == "files" and a["original_status"] == "exported"]
        category = node["category"]
        relations = relations_by_source[key]
        accepted_full = [a for a in accepted if a["role"] in _FULL_BY_CATEGORY.get(category, set())]
        full = [a for a in accepted_full if a["available"]]
        reasons = []
        if key in stale_heads:
            reasons.append('HEAD_METADATA_STALE')
        if not full:
            reasons.append("NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD")
        if not committed_generation:
            reasons.append("PUBLICATION_NOT_FINAL")
        if not manifest.get('project_identity_scope', '').startswith('verified_pet_model_data_receipt_hashes'):
            reasons.append('SOURCE_REVISION_IDENTITY_UNRESOLVED')
        if index.get('status') not in ('delivery_indexed', 'partial'):
            reasons.append("PUBLICATION_NOT_FINAL")
        fields = evidence[key]
        if (node.get("source_status") not in ("decoded", "converted", "definition_recovered", "missing_metadata") and
                not (category == 'seismic' and node.get('materialization_status') == 'converted')):
            reasons.append("SOURCE_RECOVERY_NOT_COMPLETE")
        if any(a["index_section"] != "files" or a["original_status"] != "exported" for a in group):
            reasons.append("PARTIAL_PUBLICATION_PRESENT")
        if accepted_full and any(a["numerical_qc"] not in _RECORDED_NUMERICAL_QC for a in accepted_full):
            reasons.append("NUMERICAL_QC_UNVALIDATED")
        if full and any(not re.fullmatch(r"[0-9a-f]{64}", str(a.get("recorded_sha256") or "")) for a in full):
            reasons.append("RECORDED_PAYLOAD_IDENTITY_MISSING")
        if full and any(a["current_identity_scope"] == "unavailable" for a in full):
            reasons.append("FILE_INTEGRITY_UNAVAILABLE")
        for field in ("unit", "measurement", "well_id"):
            if resolve_values(fields.get(field, []))['status'] == 'conflict':
                reasons.append("CONFLICTING_" + field.upper())
        if fields.get("unit") and fields.get("index_declared_units"):
            if set(map(str, fields["unit"])) != set(map(str, fields["index_declared_units"])):
                reasons.append("CONFLICTING_UNIT_DECLARATION")
        if category in ("surface_geometry", "surface_property", "qa_scalar"):
            if not _single(fields, 'unit') or not status_is(fields, 'unit_status', 'validated_metric_profile'):
                reasons.append("UNRESOLVED_VALUE_UNIT")
            if not status_is(fields, 'crs_status', 'verified'):
                reasons.append("UNRESOLVED_CRS")
        if category == 'seismic':
            plans = fields.get('seismic_conversion', [])
            sources = fields.get('seismic_source', [])
            qcs = fields.get('conversion_qc', [])
            if len(plans) != 1 or len(sources) != 1:
                reasons.append('SEISMIC_METADATA_UNBOUND')
            else:
                plan = metadata_object(plans[0], 'seismic_conversion')
                source = metadata_object(sources[0], 'seismic_source')
                axis = metadata_object(plan.get('native_axis'), 'native_axis')
                source_size = source.get('size')
                if (not isinstance(source_size, list) or len(source_size) != 3 or
                        not all(type(v) is int and v > 0 for v in source_size) or
                        plan.get('size') != source_size or axis.get('count') != source_size[-1]):
                    reasons.append('SEISMIC_GEOMETRY_CONFLICT')
                if axis.get('origin') != source.get('zstart') or axis.get('increment') != source.get('zinc'):
                    reasons.append('SEISMIC_AXIS_CONFLICT')
                if plan.get('domain') not in ('time', 'depth', 'length'):
                    reasons.append('SEISMIC_DOMAIN_UNRESOLVED')
                if plan.get('source_vertical_unit') in (None, '', 'unknown'):
                    reasons.append('VERTICAL_UNIT_UNRESOLVED')
                if plan.get('horizontal_unit') in (None, '', 'unknown'):
                    reasons.append('HORIZONTAL_UNIT_UNRESOLVED')
                if plan.get('crs_verified') is not True:
                    reasons.append('UNRESOLVED_CRS')
                if not plan.get('segy_header_profile'):
                    reasons.append('SEGY_HEADER_PROFILE_UNSPECIFIED')
                if plan.get('domain') == 'time' and not plan.get('time_convention'):
                    reasons.append('TIME_CONVENTION_UNRESOLVED')
                if plan.get('domain') in ('depth', 'length') and not plan.get('vertical_reference'):
                    reasons.append('VERTICAL_REFERENCE_UNRESOLVED')
            qc_record = metadata_object(qcs[0], 'conversion_qc') if len(qcs) == 1 else {}
            if qc_record.get('all_decoded_samples_exact') is not True or qc_record.get('headers_verified') is not True:
                reasons.append('SEISMIC_NUMERICAL_QC_UNVALIDATED')
            if key in stale_metadata:
                reasons.append('SEISMIC_METADATA_STALE')
        if category == "surface_property":
            links = relations.get('native_parent', [])
            parents = {r['target_key'] for r in links if r.get('status') == 'exact_id_reference'}
            parent = next(iter(parents)) if len(parents) == 1 else None
            if not parent or entity_categories.get(parent) != 'surface_geometry':
                reasons.append("PARENT_GEOMETRY_UNRESOLVED")
            registration = _single(fields, 'node_registration')
            grid = _single(fields, 'grid_registration')
            if (not isinstance(registration, dict) or registration.get('validated') is not True
                    or not parent or entity_categories.get(parent) != 'surface_geometry'
                    or registration.get('parent_entity_key') != parent
                    or not _grid(grid) or type(registration.get('count')) is not int
                    or registration['count'] != grid['dimensions'][0]*grid['dimensions'][1]):
                reasons.append("NODE_REGISTRATION_UNVERIFIED")
            # Every property join needs both native definition masks, including
            # joins supplied by reviewed source assertions. A method label or
            # validated flag must not waive current accepted mask availability.
            for owner in (key, parent):
                if not any(a['role'] == 'node_mask' and a['available']
                           and a['index_section'] == 'files' and a['original_status'] == 'exported'
                           for a in by_entity.get(owner, [])):
                    reasons.append('NODE_MASK_UNAVAILABLE')
                    break
        if category in ('culture_point', 'culture_polygon'):
            if any(type(v) is int and v > 0 for v in fields.get('missing_vertex_slots', [])):
                reasons.append('NATIVE_VERTEX_GAPS_UNRESOLVED')
        if category == "well_log" and exact_relationship_target(
                relations.get('source_well', []), entity_keys, entity_categories, 'well') is None:
            reasons.append("SOURCE_WELL_UNRESOLVED")
        if category in ("well", "trajectory", "well_log", "culture_point", "culture_polygon"):
            if not status_is(fields, 'crs_status', 'verified'):
                reasons.append("UNRESOLVED_CRS")
        if category == "well_log" and (not _single(fields, 'unit') or not status_is(fields, 'unit_status', 'validated_metric_profile')):
            reasons.append("UNRESOLVED_VALUE_UNIT")
        if category in ("surface_geometry", "surface_property", "qa_scalar", "well", "trajectory", "well_log", "culture_point", "culture_polygon"):
            if not fields.get("depth_unit") and category != "qa_scalar":
                reasons.append("VERTICAL_UNIT_UNRESOLVED")
        if category in ('surface_geometry', 'surface_property', 'well', 'trajectory', 'culture_point', 'culture_polygon') and not fields.get('horizontal_unit'):
            reasons.append('HORIZONTAL_UNIT_UNRESOLVED')
        if key in cycle_keys:
            reasons.append("DEPENDENCY_CYCLE")
        required = [(kind, r) for kind in ('source_well', 'trajectory_provider', 'materialized_payload')
                    for r in relations.get(kind, [])]
        if any(r['status'] != 'exact_id_reference' or
               not dependency_payload_ready(r['target_key'], kind) for kind, r in required):
            reasons.append("REQUIRED_DEPENDENCY_UNAVAILABLE")
        for reason in reasons:
            diagnostics.append(_diagnostic(key, None, reason, "source_prerequisite", reason.replace("_", " ").lower(),
                                           scope="metadata_evidence and relationships", next_action="Resolve from exact source evidence"))
        reasons = sorted(set(reasons))
        metadata_reasons = [r for r in reasons if r in _METADATA_REASONS or r.startswith('CONFLICTING_')]
        relationship_reasons = [r for r in reasons if r in _RELATIONSHIP_REASONS]
        has_partial = any(a['index_section'] != 'files' or a['original_status'] != 'exported' for a in group)
        recorded_qc = ('not_established' if not accepted_full else
                       'recorded_unvalidated' if any(r in reasons for r in
                           ('NUMERICAL_QC_UNVALIDATED', 'SEISMIC_NUMERICAL_QC_UNVALIDATED')) else 'recorded_validated')
        source_state = "source_prerequisites_satisfied" if not reasons else "source_blocked"
        receiving = None
        if profile != 'source':
            receiving = assess_mapping(profile=profile, node=node, fields=fields, relations=relations,
                artifacts=group, core_reasons=reasons + (['DEPENDENCY_CYCLE'] if key in preparation_cycle_keys else []),
                decision=chosen_decisions.get(key), entity_keys=entity_keys, entity_categories=entity_categories)
            diagnostics.extend(receiving.pop('diagnostics'))
        receiving_state = 'source_only' if receiving is None else receiving['receiving_state']
        assessments.append(dict(entity_key=key, category=category, source_state=source_state,
                                receiving_state=receiving_state, reason_codes=reasons,
                                receiving_assessment=receiving,
                                metadata_resolution=metadata_resolution.get(key, {}),
                                state_axes=dict(recovery=node.get('source_status'),
                                    availability='accepted_full_available' if full else 'no_accepted_full_available',
                                    metadata=('requirements_unresolved' if metadata_reasons else
                                              'requirements_satisfied' if category in _FULL_BY_CATEGORY else 'not_assessed'),
                                    metadata_reason_codes=metadata_reasons,
                                    relationships='requirements_unresolved' if relationship_reasons else 'no_reported_relationship_gap',
                                    relationship_reason_codes=relationship_reasons,
                                    source_revision_identity=('unresolved' if 'SOURCE_REVISION_IDENTITY_UNRESOLVED' in reasons
                                                              else 'recorded_verified_hashes'),
                                    publication=('accepted_and_partial_artifacts' if accepted and has_partial else
                                                 'accepted_artifacts_present' if accepted else
                                                 'partial_artifacts_only' if has_partial else 'no_accepted_artifacts'),
                                    original_run_publication=index.get('status'),
                                    numerical_qc=recorded_qc,
                                    numerical_qc_scope='Historical accepted artifact declarations and bound conversion summary; no new numeric readback',
                                    integrity=full[0]['current_identity_scope'] if full else 'unavailable'),
                                preferred_artifact_key=full[0]["artifact_key"] if full else None,
                                file_integrity_state=full[0]["current_identity_scope"] if full else "no_full_payload",
                                artifact_count=len(group)))
    if profile != 'source':
        # Qualify actual capability dependencies, not any file of their owner.
        # An explicitly bound existing well retains the log-only exemption.
        prepared = {a['entity_key']: a for a in assessments}
        for key in preparation_order:
            assessment = prepared[key]
            if assessment['receiving_state'] != 'qualified_for_planned_mapping':
                continue
            category = assessment['category']
            decision = chosen_decisions.get(key, {})
            failures = []
            if category == 'surface_property':
                parents = relations_by_source[key].get('native_parent', [])
                if any(prepared[p['target_key']]['receiving_state'] != 'qualified_for_planned_mapping' for p in parents):
                    failures.append(('PARENT_MAPPING_NOT_QUALIFIED', 'native_parent'))
            if category in ('well_log', 'trajectory', 'marker', 'checkshot'):
                target = decision.get('well_target', {})
                if target.get('state') == 'planned':
                    parent = target.get('source_entity_key')
                    if (prepared.get(parent, {}).get('receiving_state') != 'qualified_for_planned_mapping'
                            or chosen_decisions.get(parent, {}).get('target_object_id') != target.get('target_id')):
                        failures.append(('PLANNED_WELL_MAPPING_NOT_QUALIFIED', 'well_target'))
            if profile == 'paleoscan' and category in ('surface_geometry', 'surface_property'):
                target = decision.get('reference_volume_binding', {}).get('source_entity_key')
                if (not dependency_payload_ready(target, 'reference_seismic') or
                        any(code in CORE_BLOCKERS or code.startswith('CONFLICTING_')
                            for code in prepared.get(target, {}).get('reason_codes', []))):
                    failures.append(('REFERENCE_VOLUME_PAYLOAD_UNAVAILABLE', 'reference_volume_binding'))
            if failures:
                assessment['receiving_state'] = 'blocked_relationship'
                assessment['receiving_assessment']['receiving_state'] = 'blocked_relationship'
                for code, relation in failures:
                    assessment['receiving_assessment']['reason_codes'].append(code)
                    diagnostics.append(_diagnostic(key, None, code, relation,
                        'The selected capability requires its exact accepted dependency payload or qualified planned mapping'))
    return dict(schema="geoviewer.contract-validation/2" if committed_generation else "geoviewer.legacy-assessment/1", profile=profile,
                generation=manifest["generation"], manifest_sha256=pointer['manifest_sha256'], project_revision=manifest["project_revision"],
                profile_revision=PROFILE_REVISION[profile],
                receiving_profile=RECEIVING_PROFILES.get(profile),
                retention_scope='Explicit current-copy view' if retention_view else 'Current physical inspection; no pruning intent inferred',
                count_scope={'qualified_for_planned_mapping': 'Specified preparation profile only',
                             'receiving_qualified': 'Legacy runtime/import qualification count; remains zero because no receiving execution is performed'},
                artifact_availability=[{k: a[k] for k in ('artifact_key', 'original_status', 'index_section', 'available', 'physical_present', 'current_identity_scope', 'current_retention')} for a in artifacts],
                requirements={"source": "available accepted full payload, verified provenance/QC and category semantics",
                              "target_capability": "unknown until receiving runtime/version is tested",
                              "destination_decisions": "survey, coordinate transform and object mapping unselected"},
                dependency_order=order, dependency_cycles=cycles, unresolved_dependencies=unresolved_dependencies,
                preparation_dependency_order=preparation_order, preparation_dependency_cycles=preparation_cycles,
                preparation_stages=['inspect/select survey context; no survey creation', 'source geometry and well/track dependencies',
                                    'dependent logs, markers, time-depth, surface properties and seismic as linked'],
                counts=dict(entities=len(entities), artifacts=len(artifacts), source_ready=sum(a["source_state"] == "source_prerequisites_satisfied" for a in assessments),
                            source_blocked=sum(a["source_state"] == "source_blocked" for a in assessments),
                            qualified_for_planned_mapping=sum(a['receiving_state'] == 'qualified_for_planned_mapping' for a in assessments),
                            receiving_qualified=0, receiving_imports_executed=0), assessments=assessments, diagnostics=diagnostics,
                integrity_policy="Fresh artifact hashes only when --hash-artifacts is selected; no target runtime or import tested")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path, help="Current pointer or current generation manifest")
    parser.add_argument("--profile", choices=PROFILES, default="source")
    parser.add_argument("--output", type=Path, required=True, help="New validation report outside the package/run")
    parser.add_argument("--hash-artifacts", action="store_true", help="Opt in to current payload hashing")
    parser.add_argument('--decisions', type=Path, help='Generation/profile-bound explicit preparation decisions with small local evidence')
    parser.add_argument('--retention-view', type=Path, help='Generation-bound current-copy retention view; never changes original results')
    args = parser.parse_args(argv)
    try:
        destination = args.contract.resolve().parent
        if args.contract.name == "manifest.json":
            destination = args.contract.resolve().parents[3]
        _unlinked(args.output.absolute())
        output = args.output.resolve(strict=False)
        pointer = _json(destination / POINTER)
        manifest = _json(_resolve(destination, pointer["manifest"]))
        run = _resolve(destination, manifest["roots"]["run"], allow_parent=True)
        if output.is_relative_to(run) or run.is_relative_to(output):
            raise ValueError("Validation output must be separate from source run")
        _unlinked(output)
        if output.exists():
            raise ValueError("Validation output already exists")
        result = validate_contract(args.contract, args.profile, hash_artifacts=args.hash_artifacts,
                                   decisions=args.decisions, retention_view=args.retention_view)
        # Publication imports this validator, so load its shared bounded writer
        # only after the assessment is built (also for direct CLI execution).
        from geoviewer_contract_publication import write_bounded_assessment
        write_bounded_assessment(output, result)
        print(f"{result['counts']['entities']} entities; {result['counts']['source_ready']} source-ready; "
              f"{result['counts']['qualified_for_planned_mapping']} prepared for specified mapping; 0 imports executed")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Contract validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
