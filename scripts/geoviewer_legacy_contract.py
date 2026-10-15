# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only, local assessment of an older committed FieldViewer FILE_INDEX.

API: assess_legacy(package, exports, *, file_index=None,
                   expected_index_sha256=None, expected_receipt_sha256=None, profile='source',
                   hash_artifacts=False) -> report.

Only an exact ``run/EXPORTS`` (or ``run/*_EXPORTS``) plus a package beneath the
same run is read. Known small receipts are resolved beneath that explicit package.
No recorded absolute source/native-project path is followed. This derives an
assessment, not a historical generation or RUN_RESULT binding. The CLI writes
one new report outside the run and refuses to overwrite it.
"""
from __future__ import annotations

import argparse
import json
from geoviewer_io import read_up_to
from pathlib import Path
import re
import sys

from geoviewer_contract import (POINTER, MAX_SIDECAR_BYTES, _resolve, _unlinked, build_catalogue,
                                canonical, digest)
from validate_geoviewer_contract import PROFILES, assess_catalogue


SCHEMA = 'geoviewer.legacy-assessment/1'
MAX_INDEX_BYTES = 32 * 1024 * 1024
MAX_RECEIPT_BYTES = 32 * 1024 * 1024
MAX_INDEX_ROWS = 1_000_000
_SHA = re.compile(r'^[0-9a-f]{64}$')
_RECEIPTS = {
    'project_context': '01_project_metadata/project_context.json',
    'project_seismic_inventory': '01_project_metadata/project_seismic_inventory.json',
    'native_recovery': '07_workflows_reports/native_recovery/native_recovery_report.json',
    'native_recovery_fallback': '07_workflows_reports/native_recovery/native_recovery.json',
    'native_spatial': '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
}


def _bounded_json(path: Path, limit: int) -> tuple[dict, str]:
    _unlinked(path)
    with path.open('rb') as handle:
        raw = read_up_to(handle, limit + 1)
    if len(raw) > limit:
        raise ValueError(f'Legacy input exceeds {limit} byte bound: {path.name}')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'Legacy input must be a JSON object: {path.name}')
    return value, digest(raw)


def _small_input_state(path: Path, limit: int) -> dict:
    """Bind exact bytes when bounded, or an explicit missing/oversize state."""
    _unlinked(path)
    if not path.is_file():
        return dict(state='missing', raw_sha256=None)
    with path.open('rb') as handle:
        raw = read_up_to(handle, limit + 1)
    if len(raw) > limit:
        return dict(state='oversize', raw_sha256=None)
    return dict(state='bounded', raw_sha256=digest(raw))


def _check_index(index: dict) -> None:
    schema = index.get('schema')
    status = index.get('status')
    if schema not in (None, 'geoviewer.inventory-index/1'):
        raise ValueError(f'Unknown legacy FILE_INDEX schema: {schema}')
    if index.get('product', 'Field Viewer Data Extractor') != 'Field Viewer Data Extractor':
        raise ValueError('Unknown legacy FILE_INDEX product')
    if schema == 'geoviewer.inventory-index/1':
        if status != 'inventory_only' or index.get('dataset_conversion_enabled') is not False:
            raise ValueError('Malformed inventory-only FILE_INDEX')
    elif status not in (None, 'delivery_indexed', 'partial'):
        raise ValueError(f'Unknown legacy FILE_INDEX status: {status}')
    elif status is None and index.get('product') != 'Field Viewer Data Extractor':
        raise ValueError('Status-free legacy FILE_INDEX requires the FieldViewer product marker')
    files, partial = index.get('files'), index.get('partial_files', [])
    if not isinstance(files, list) or not isinstance(partial, list) or len(files) + len(partial) > MAX_INDEX_ROWS:
        raise ValueError('Malformed or oversized legacy FILE_INDEX row lists')
    if schema == 'geoviewer.inventory-index/1' and (files or partial):
        raise ValueError('Inventory-only FILE_INDEX cannot contain payload rows')
    for section, rows in (('files', files), ('partial_files', partial)):
        for number, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('object_id'), str) or not row['object_id']:
                raise ValueError(f'Malformed {section}[{number}] object identity')
            if not isinstance(row.get('path'), str) or not row['path']:
                raise ValueError(f'Malformed {section}[{number}] path')
            if type(row.get('bytes')) is not int or row['bytes'] < 0:
                raise ValueError(f'Malformed {section}[{number}] byte count')
            if section == 'files' and row.get('status') != 'exported':
                raise ValueError(f'Unaccepted row in files[{number}]')
            if section == 'partial_files' and row.get('status') == 'exported':
                raise ValueError(f'Accepted row in partial_files[{number}]')


def assess_legacy(package: Path, exports: Path, *, file_index: dict | None = None,
                  expected_index_sha256: str | None = None,
                  expected_receipt_sha256: dict[str, str] | None = None,
                  profile: str = 'source', hash_artifacts: bool = False) -> dict:
    """Derive a local view from exact existing inputs without writing inside the run.

    ``file_index`` and expected receipt hashes are optional caller assertions. A
    stale assertion fails before normalization; it cannot replace on-disk input.
    Receipts are optional, but an asserted missing or altered receipt is fatal.
    """
    if profile not in PROFILES:
        raise ValueError(f'Unknown profile: {profile}')
    package, exports = Path(package).absolute(), Path(exports).absolute()
    _unlinked(package); _unlinked(exports)
    package, exports = package.resolve(strict=True), exports.resolve(strict=True)
    run = exports.parent
    if (not package.is_dir() or not exports.is_dir() or
            not (exports.name == 'EXPORTS' or exports.name.endswith('_EXPORTS')) or
            package == run or not package.is_relative_to(run) or
            package.is_relative_to(exports)):
        raise ValueError('Require a bounded package and EXPORTS under the same explicit run')
    if (exports / POINTER).exists():
        raise ValueError('A committed contract pointer exists; use normal validation')
    index_path = exports / 'FILE_INDEX.json'
    index, index_raw_hash = _bounded_json(index_path, MAX_INDEX_BYTES)
    bound_inputs = {index_path: (index, index_raw_hash)}
    if expected_index_sha256 is not None and (not _SHA.fullmatch(expected_index_sha256) or
                                              expected_index_sha256 != index_raw_hash):
        raise ValueError('Supplied FILE_INDEX digest is stale or mismatched')
    _check_index(index)
    if file_index is not None and canonical(file_index) != canonical(index):
        raise ValueError('Supplied FILE_INDEX differs from the on-disk index')
    if expected_receipt_sha256 is not None and (not isinstance(expected_receipt_sha256, dict) or
            set(expected_receipt_sha256) - set(_RECEIPTS) or
            any(not isinstance(v, str) or not _SHA.fullmatch(v) for v in expected_receipt_sha256.values())):
        raise ValueError('Unknown receipt assertion or invalid SHA-256')
    receipt_hashes = {}
    inventory = None
    for name, relative in _RECEIPTS.items():
        path = package / relative
        _unlinked(path)
        if path.exists():
            receipt, receipt_hashes[name] = _bounded_json(path, MAX_RECEIPT_BYTES)
            bound_inputs[path] = (receipt, receipt_hashes[name])
            if 'objects' in receipt and not isinstance(receipt['objects'], list):
                raise ValueError(f'Malformed {name} receipt objects list')
            if any(not isinstance(row, dict) for row in receipt.get('objects', [])):
                raise ValueError(f'Malformed {name} receipt object record')
            if name == 'project_seismic_inventory':
                if 'native_objects' in receipt and not isinstance(receipt['native_objects'], list):
                    raise ValueError('Malformed seismic inventory native_objects list')
                if any(not isinstance(row, dict) for row in receipt.get('native_objects', [])):
                    raise ValueError('Malformed seismic inventory native object record')
                inventory = receipt
        else:
            bound_inputs[path] = ({}, None)
        if name in (expected_receipt_sha256 or {}) and receipt_hashes.get(name) != expected_receipt_sha256[name]:
            raise ValueError(f'Supplied receipt is stale or missing: {name}')
    heads = package / '02_wells/well_headers/native_well_heads.csv'
    _unlinked(heads)
    head_bytes = None
    if heads.is_file():
        with heads.open('rb') as handle:
            head_bytes = read_up_to(handle, MAX_RECEIPT_BYTES + 1)
        if len(head_bytes) > MAX_RECEIPT_BYTES:
            raise ValueError('Aggregate well-head table exceeds 32 MiB legacy read bound')
    head_state = dict(state='bounded', raw_sha256=digest(head_bytes)) if head_bytes is not None else dict(state='missing', raw_sha256=None)
    normalized = build_catalogue(package, exports, index, inventory,
                                 bound_inputs=bound_inputs, bound_heads=head_bytes,
                                 bound_heads_known=True)
    script_dir = Path(__file__).resolve().parent
    adapter_hashes = {name: digest((script_dir / name).read_bytes()) for name in
                      ('geoviewer_legacy_contract.py', 'geoviewer_contract.py',
                       'validate_geoviewer_contract.py', 'geoviewer_receiving_profiles.py',
                       'geoviewer_contract_retention.py', 'geoviewer_metadata_evidence.py')}
    inputs = dict(index=dict(path=exports.name + '/FILE_INDEX.json', raw_sha256=index_raw_hash,
                             canonical_sha256=normalized['index_hash']),
                  receipts={name: {'path': relative,
                                   'state': 'bounded' if name in receipt_hashes else 'missing',
                                   'raw_sha256': receipt_hashes.get(name)}
                            for name, relative in _RECEIPTS.items()},
                  aggregate_well_heads=dict(path='02_wells/well_headers/native_well_heads.csv', **head_state),
                  consumed_metadata=normalized['consumed_metadata'],
                  adapter_script_sha256=adapter_hashes)
    fingerprint = digest(canonical(inputs))
    roots = dict(exports=exports, package=package, run=run)
    manifest = dict(generation=None, project_revision=normalized['project_key'],
                    project_identity_scope=normalized['revision_scope'],
                    metadata_bindings=normalized['metadata_bindings'])
    result = assess_catalogue(tables=normalized['tables'], roots=roots, manifest=manifest,
                              pointer={'manifest_sha256': None}, index=index, profile=profile,
                              hash_artifacts=hash_artifacts, committed_generation=False)
    # All asserted JSON inputs were passed to the builder as parsed snapshots.
    # Refuse a report if any small on-disk input changes during assessment.
    if _small_input_state(index_path, MAX_INDEX_BYTES)['raw_sha256'] != index_raw_hash:
        raise ValueError('FILE_INDEX changed during legacy assessment')
    for name, relative in _RECEIPTS.items():
        expected_state = dict(state='bounded' if name in receipt_hashes else 'missing',
                              raw_sha256=receipt_hashes.get(name))
        if _small_input_state(package / relative, MAX_RECEIPT_BYTES) != expected_state:
            raise ValueError(f'{name} receipt changed during legacy assessment')
    if _small_input_state(heads, MAX_RECEIPT_BYTES) != head_state:
        raise ValueError('Aggregate well-head table changed during legacy assessment')
    for item in normalized['consumed_metadata']:
        path = _resolve(roots[item['path_base']], item['relative_path'])
        current = _small_input_state(path, MAX_SIDECAR_BYTES)
        if current['state'] != item['state'] or current['raw_sha256'] != item['raw_sha256']:
            raise ValueError('Consumed metadata changed during legacy assessment')
    result.update(schema=SCHEMA, committed_generation=None,
                  catalogue_input_fingerprint=fingerprint, derived_local_fingerprint=fingerprint,
                  fingerprint_inputs=inputs, normalized_catalogue=normalized['tables'],
                  publication_binding='absent; this assessment cannot satisfy RUN_RESULT or generation binding',
                  qualification_scope='Read-only legacy source assessment; no receiving import or committed generation',
                  source_roots=dict(exports=exports.name, package=package.relative_to(run).as_posix()),
                  fingerprint_scope='Exact bounded index, package receipts, aggregate heads, consumed sidecars and assessment scripts; excludes bulk payload bytes, current availability, profile/options and receiving execution',
                  assessment_options=dict(profile=profile, hash_artifacts=hash_artifacts),
                  limits=dict(index_bytes=MAX_INDEX_BYTES, receipt_bytes_each=MAX_RECEIPT_BYTES,
                              index_rows=MAX_INDEX_ROWS, sidecar_bytes=1048576),
                  integrity_policy='Current size/state only unless --hash-artifacts; historical numeric/file QC is never upgraded')
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path, help='Existing package below the source run')
    parser.add_argument('exports', type=Path, help='Existing sibling EXPORTS or *_EXPORTS')
    parser.add_argument('--output', type=Path, required=True, help='New report outside the source run')
    parser.add_argument('--profile', choices=PROFILES, default='source')
    parser.add_argument('--hash-artifacts', action='store_true')
    parser.add_argument('--expected-index-sha256', help='Optional exact raw FILE_INDEX byte digest')
    parser.add_argument('--expected-receipt-sha256', action='append', default=[], metavar='NAME=SHA256')
    args = parser.parse_args(argv)
    try:
        _unlinked(args.output.absolute())
        output = args.output.resolve(strict=False)
        run = args.exports.resolve(strict=True).parent
        if output.is_relative_to(run) or run.is_relative_to(output) or output.exists():
            raise ValueError('Output must be a new file outside the source run')
        expected = {}
        for item in args.expected_receipt_sha256:
            name, sep, value = item.partition('=')
            if not sep or name in expected:
                raise ValueError('Receipt assertion must be unique NAME=SHA256')
            expected[name] = value
        result = assess_legacy(args.package, args.exports, profile=args.profile,
                               hash_artifacts=args.hash_artifacts,
                               expected_index_sha256=args.expected_index_sha256,
                               expected_receipt_sha256=expected)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as handle:
            handle.write(canonical(result))
        print(f"{result['counts']['entities']} entities; {result['counts']['source_ready']} source-ready; 0 imports executed")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'Legacy assessment failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
