# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only, generation-bound retention view over source-contract artifacts.

The view describes a copy's current retention decision. It neither verifies
current payload bytes nor changes the original extraction/publication state.
"""
from __future__ import annotations

import json
from geoviewer_io import read_up_to
from pathlib import Path

from geoviewer_contract import _unlinked, canonical


SCHEMA = "geoviewer.retention-view/1"
SOURCE_FIELDS = ("artifact_key", "path_base", "relative_path", "expected_bytes",
                 "recorded_sha256", "index_section", "original_status")
STATUSES = ("retained", "pruned", "unavailable", "external_reference")
MAX_VIEW_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_ROWS = 200_000


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be a nonempty, trimmed string")
    return value


def _inventory(artifacts: list[dict]) -> dict[str, dict]:
    if not isinstance(artifacts, list) or len(artifacts) > MAX_ARTIFACT_ROWS:
        raise ValueError("Artifact inventory exceeds row bound or is not a list")
    by_key: dict[str, dict] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or any(field not in artifact for field in SOURCE_FIELDS):
            raise ValueError("Artifact lacks required source fields")
        source = {field: artifact[field] for field in SOURCE_FIELDS}
        key = _identity(source["artifact_key"], "artifact_key")
        if key in by_key:
            raise ValueError(f"Duplicate original artifact: {key}")
        by_key[key] = source
    return by_key


def _validate_view(view: object, *, generation: str, manifest_sha256: str,
                   artifacts: list[dict]) -> dict[str, dict]:
    originals = _inventory(artifacts)
    if not isinstance(view, dict) or set(view) != {"schema", "generation", "manifest_sha256", "scope", "counts", "artifacts"}:
        raise ValueError("Invalid retention view envelope")
    if view["schema"] != SCHEMA or view["generation"] != generation or view["manifest_sha256"] != manifest_sha256:
        raise ValueError("Retention view schema or source generation binding differs")
    if view["scope"] != "current_copy_retention_of_original_contract_inventory":
        raise ValueError("Invalid retention view scope")
    rows = view["artifacts"]
    if not isinstance(rows, list) or len(rows) != len(originals) or len(rows) > MAX_ARTIFACT_ROWS:
        raise ValueError("Retention view has missing or excessive artifact rows")
    by_key: dict[str, dict] = {}
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"source", "retention"}:
            raise ValueError("Invalid retention row")
        source, retention = row["source"], row["retention"]
        if not isinstance(source, dict) or set(source) != set(SOURCE_FIELDS):
            raise ValueError("Invalid bound source record")
        key = _identity(source["artifact_key"], "artifact_key")
        if (key in by_key or key not in originals or
                any(type(source[field]) is not type(originals[key][field]) or
                    source[field] != originals[key][field] for field in SOURCE_FIELDS)):
            raise ValueError(f"Duplicate, unknown or changed source artifact: {key}")
        if not isinstance(retention, dict) or set(retention) != {"status", "reason", "evidence_reference"}:
            raise ValueError(f"Invalid retention state for {key}")
        status = retention["status"]
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"Invalid retention status for {key}")
        _identity(retention["reason"], "reason")
        _identity(retention["evidence_reference"], "evidence_reference")
        counts[status] += 1
        by_key[key] = row
    if set(by_key) != set(originals):
        raise ValueError("Retention view omits original artifacts")
    if view["counts"] != {"original_artifacts": len(originals), **counts}:
        raise ValueError("Retention counts differ from rows")
    return by_key


def build_retention_view(*, generation: str, manifest_sha256: str,
                         artifacts: list[dict], dispositions: list[dict]) -> dict:
    """Build a complete, explicit disposition for one source-contract generation."""
    _identity(generation, "generation")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64 or any(c not in "0123456789abcdef" for c in manifest_sha256):
        raise ValueError("manifest_sha256 must be lowercase SHA-256")
    originals = _inventory(artifacts)
    if not isinstance(dispositions, list) or len(dispositions) != len(originals):
        raise ValueError("Every original artifact needs exactly one disposition")
    states: dict[str, dict] = {}
    for disposition in dispositions:
        if not isinstance(disposition, dict) or set(disposition) != {"artifact_key", "status", "reason", "evidence_reference"}:
            raise ValueError("Invalid disposition")
        key = _identity(disposition["artifact_key"], "artifact_key")
        if key not in originals or key in states:
            raise ValueError(f"Unknown or duplicate disposition: {key}")
        states[key] = {field: disposition[field] for field in ("status", "reason", "evidence_reference")}
    counts = {status: 0 for status in STATUSES}
    rows = []
    for key, source in originals.items():
        state = states[key]
        if not isinstance(state["status"], str) or state["status"] not in STATUSES:
            raise ValueError(f"Invalid retention status for {key}")
        _identity(state["reason"], "reason")
        _identity(state["evidence_reference"], "evidence_reference")
        counts[state["status"]] += 1
        rows.append({"source": source, "retention": state})
    view = {"schema": SCHEMA, "generation": generation, "manifest_sha256": manifest_sha256,
            "scope": "current_copy_retention_of_original_contract_inventory",
            "counts": {"original_artifacts": len(rows), **counts}, "artifacts": rows}
    if len(canonical(view)) > MAX_VIEW_BYTES:
        raise ValueError("Retention view exceeds byte bound")
    _validate_view(view, generation=generation, manifest_sha256=manifest_sha256, artifacts=artifacts)
    return view


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON member: {key}")
        result[key] = value
    return result


def load_retention_view(path: Path, *, generation: str, manifest_sha256: str,
                        artifacts: list[dict]) -> dict[str, dict]:
    """Validate a bounded view and return bound rows keyed by artifact_key.

    A retained claim is descriptive only; the caller must separately check
    current file existence and integrity before reporting availability.
    """
    _identity(generation, "generation")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64 or any(c not in "0123456789abcdef" for c in manifest_sha256):
        raise ValueError("manifest_sha256 must be lowercase SHA-256")
    path = Path(path).absolute()
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"Linked path is not permitted: {path}")
    _unlinked(path)
    with path.open("rb") as handle:
        raw = read_up_to(handle, MAX_VIEW_BYTES + 1)
    if len(raw) > MAX_VIEW_BYTES:
        raise ValueError("Retention view exceeds byte bound")
    view = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    return _validate_view(view, generation=generation, manifest_sha256=manifest_sha256, artifacts=artifacts)
