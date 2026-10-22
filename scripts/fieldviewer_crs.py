#!/usr/bin/env python3
# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read exact PET/Model metadata and export typed CRS declarations only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

import petrel_crs_datum as parser
import petrel_native_binary as native

SCHEMA = 'fieldviewer.crs-export/1'
RESERVE_BYTES = 256 * 1024 * 1024
MAX_INPUT_BYTES = native.MAX_CONTAINER
CHUNK = 8 * 1024 * 1024
CODE_FILES = ('fieldviewer_crs.py', 'petrel_crs_datum.py',
              'petrel_native_binary.py', 'petrel_native_well_evidence.py')
MODULES = {'petrel_crs_datum.py': parser, 'petrel_native_binary.py': native,
           'petrel_native_well_evidence.py': parser.wells}


def utc():
    return datetime.now(timezone.utc).isoformat()


def _json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                     allow_nan=False) + '\n', encoding='utf-8')


def _hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def _absolute(path):
    p = Path(path)
    if not p.is_absolute():
        raise ValueError('Explicit absolute paths are required')
    return Path(os.path.abspath(p))


def _no_links(path):
    for component in (path, *path.parents):
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if component.is_symlink() or (getattr(info, 'st_file_attributes', 0) &
                                           getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
                raise ValueError('Path traverses a link or reparse point: ' + str(component))


def _inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _source(path, role, *, retain=False):
    _no_links(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT_BYTES:
        raise ValueError(f'{role} must be a regular file within the {MAX_INPUT_BYTES}-byte bound')
    chunks = [] if retain else None
    h = hashlib.sha256()
    length = 0
    with path.open('rb') as stream:
        while True:
            block = stream.read(CHUNK)
            if not block:
                break
            length += len(block)
            if length > MAX_INPUT_BYTES:
                raise ValueError(f'{role} grew beyond input bound')
            h.update(block)
            if retain:
                chunks.append(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino) or length != before.st_size:
        raise ValueError('Source changed while reading: ' + str(path))
    evidence = dict(role=role, path=str(path), bytes=length, sha256=h.hexdigest(),
                    mtime_ns=before.st_mtime_ns)
    return (b''.join(chunks) if retain else None), evidence


def _events(path, stage, number):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(event='stage', time_utc=utc(), stage=stage,
                                     number=number)) + '\n')


def _bound_code():
    scripts = Path(__file__).absolute().parent
    if parser.native is not native or sys.modules.get('petrel_crs_datum') is not parser:
        raise ValueError('Imported CRS parser dependency identity changed')
    if sys.modules.get('petrel_native_binary') is not native or (
            sys.modules.get('petrel_native_well_evidence') is not parser.wells):
        raise ValueError('Imported native parser dependency identity changed')
    bound = []
    for name in CODE_FILES:
        module = MODULES.get(name)
        path = Path(module.__file__ if module is not None else __file__).absolute()
        if path != scripts / name:
            raise ValueError('Imported parser path is outside entrypoint directory: ' + str(path))
        _no_links(path)
        bound.append(dict(path=str(path), sha256=_hash(path)))
    return bound


def _xml_bytes(xml):
    match = re.match(r'^\s*<\?xml\s+[^?]*\bencoding\s*=\s*["\']([^"\']+)["\']', xml, re.I)
    encoding = match.group(1).lower() if match else 'utf-8'
    if encoding not in ('utf-8', 'utf-16', 'utf-16le', 'utf-16-le',
                        'utf-16be', 'utf-16-be'):
        raise ValueError('Unsupported declared projection XML encoding: ' + encoding)
    return xml.encode(encoding)


def _placeholder(reason, locator='Model.ptd'):
    return dict(schema=parser.SCHEMA, status='unresolved', saved_version=None,
                project_records=[], datum_records=[], well_reference_records=[],
                gaps=[dict(locator=locator, reason=reason)],
                counts=dict(ProjectSubject_seen=0, stream_complete=False))


def _classification(metadata):
    if not metadata:
        return False
    records = metadata.get('project_records') or []
    counts = metadata.get('counts') or {}
    if counts.get('ProjectSubject_seen') != 1 or len(records) != 1 or not counts.get('stream_complete'):
        return False
    projection = records[0].get('projection') or {}
    return (projection.get('declaration_state') == 'stored_project_declaration'
            and projection.get('status') == 'parsed_stored_declaration'
            and metadata.get('status') == 'parsed')


def _report(receipt, metadata, root):
    esc = lambda value: html.escape(str(value), quote=True)
    records = (metadata or {}).get('project_records') or []
    project = records[0] if len(records) == 1 else {}
    projection = project.get('projection') or {}
    gaps = (metadata or {}).get('gaps') or []
    links = ['CRS_DATUM.json', 'FILE_MANIFEST.json', 'EVENTS.jsonl']
    for optional in ('CRS_DECLARATION.xml',):
        if (root / optional).exists():
            links.append(optional)
    summary = [f'<p>Status: <strong>{esc(receipt["status"])}</strong>. Stored declaration only; physical CRS and datum remain unverified.</p>',
               f'<p>Project: {esc(receipt["request"]["project_file"])}</p>',
               f'<p>Saved version: {esc((metadata or {}).get("saved_version"))}; '
               f'projection state: {esc(projection.get("declaration_state", "unresolved"))}.</p>',
               f'<p>Late CRS: {esc((projection.get("late_bound") or {}).get("name", "unknown"))}; '
               f'authority: {esc((projection.get("late_bound") or {}).get("authority", "unknown"))}.</p>',
               f'<p>Datum records: {len((metadata or {}).get("datum_records") or [])}; '
               f'well reference records: {len((metadata or {}).get("well_reference_records") or [])}; '
               f'gaps: {len(gaps)}.</p>',
               '<p>Coordinates, vertical units, selected datum, and transforms have not been verified or applied.</p>']
    if receipt.get('error'):
        summary.append('<p>Error: ' + esc(receipt['error']['message']) + '</p>')
    if gaps:
        summary.append('<h2>Unresolved evidence</h2><ul>' + ''.join(
            '<li>' + esc(g.get('locator')) + ': ' + esc(g.get('reason')) + '</li>'
            for g in gaps[:100]) + '</ul>')
    summary.append('<h2>Evidence files</h2><ul>' + ''.join(
        '<li><a href="' + esc(name) + '">' + esc(name) + '</a></li>' for name in links) + '</ul>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>CRS and datum metadata</title><style>body{font:16px Segoe UI, sans-serif;'
            'max-width:900px;margin:32px auto;padding:0 20px;overflow-wrap:anywhere}'
            'li{margin:8px 0}</style><h1>CRS and datum metadata</h1>' + ''.join(summary) + '</html>')


def export(project_file, output_root):
    project = _absolute(project_file)
    root = _absolute(output_root)
    request = dict(project_file=str(project), output_root=str(root))
    if root.exists():
        raise FileExistsError('Exclusive new output directory required: ' + str(root))
    _no_links(root)
    _no_links(project)
    if _inside(root, project.parent) or _inside(project.parent, root):
        raise ValueError('Output and source project directory overlap')
    if not root.parent.is_dir():
        raise ValueError('Output parent directory does not exist')
    if shutil.disk_usage(root.parent).free < RESERVE_BYTES:
        raise OSError('Insufficient free space for 256 MiB metadata reserve')
    root.mkdir(exist_ok=False)
    events = root / 'EVENTS.jsonl'
    number = 0
    def mark(stage):
        nonlocal number
        number += 1
        _events(events, stage, number)
    receipt = dict(schema=SCHEMA, status='failed', request=request, started_utc=utc(),
                   source_integrity_unchanged=False, source_files=[],
                   physical_crs_verified=False, physical_datum_verified=False,
                   petrel_launched=False, data_store_read=False,
                   full_report=str(root / 'CRS_REPORT.html'),
                   metadata_file=str(root / 'CRS_DATUM.json'),
                   export_directory=str(root), manifest_file=str(root / 'FILE_MANIFEST.json'),
                   structured_events=str(events),
                   access_scope='Exact PET and matching PTD/Model.ptd only')
    metadata = None
    mark('started')
    try:
        receipt['parser_code'] = _bound_code()
        if project.suffix.lower() != '.pet' or not project.is_file():
            raise ValueError('Exact existing .pet file required')
        store = project.with_suffix('.ptd')
        _no_links(store)
        if not store.is_dir():
            raise ValueError('Matching same-stem .ptd directory required')
        model = store / 'Model.ptd'
        _, pet_info = _source(project, 'PET')
        receipt['source_files'].append(pet_info)
        mark('pet_hashed')
        if model.exists():
            raw, model_info = _source(model, 'MODEL', retain=True)
            receipt['source_files'].append(model_info)
            mark('model_hashed')
            metadata = parser.inspect_model(raw)
            _json(root / 'CRS_DATUM.json', metadata)
            mark('model_parsed')
            receipt['status'] = 'completed' if _classification(metadata) else 'completed_with_gaps'
            records = metadata.get('project_records') or []
            if len(records) == 1 and metadata.get('counts', {}).get('ProjectSubject_seen') == 1:
                projection = records[0].get('projection') or {}
                if projection.get('declaration_state') == 'stored_project_declaration':
                    raw_projection = projection.get('raw') or ''
                    if ': ' in raw_projection:
                        (root / 'CRS_DECLARATION.xml').write_bytes(
                            _xml_bytes(raw_projection.partition(': ')[2]))
        else:
            receipt['status'] = 'completed_with_gaps'
            receipt['gaps'] = [dict(locator=str(model), reason='Model.ptd missing; CRS unknown')]
            metadata = _placeholder('Model.ptd missing; CRS unknown', str(model))
            _json(root / 'CRS_DATUM.json', metadata)
        for source in receipt['source_files']:
            path = Path(source['path'])
            _no_links(path)
            info = path.stat()
            if (info.st_size != source['bytes'] or info.st_mtime_ns != source['mtime_ns']
                    or _hash(path) != source['sha256']):
                raise ValueError('Source changed after inspection: ' + str(path))
        if _bound_code() != receipt['parser_code']:
            raise ValueError('Parser code or imported module changed during inspection')
        receipt['source_integrity_unchanged'] = True
        mark('source_reverified')
    except Exception as exc:
        receipt['status'] = 'failed'
        receipt['error'] = dict(type=type(exc).__name__, message=str(exc))
        if metadata is None:
            metadata = _placeholder(str(exc), str(project))
        mark('failed')
    if not (root / 'CRS_DATUM.json').exists():
        _json(root / 'CRS_DATUM.json', metadata)
    (root / 'CRS_REPORT.html').write_text(_report(receipt, metadata, root), encoding='utf-8')
    mark('report_written')
    receipt['ended_utc'] = utc()
    artifacts = []
    for path in sorted(root.iterdir()):
        if path.name not in ('FILE_MANIFEST.json', 'CRS_EXPORT.json') and path.is_file():
            artifacts.append(dict(path=str(path), bytes=path.stat().st_size, sha256=_hash(path)))
    _json(root / 'FILE_MANIFEST.json', dict(schema='fieldviewer.file-manifest/1',
                                           artifacts=artifacts))
    _json(root / 'CRS_EXPORT.json', receipt)
    return receipt


def main(argv=None):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--project', required=True)
    args.add_argument('--output', required=True)
    parsed = args.parse_args(argv)
    try:
        result = export(parsed.project, parsed.output)
    except Exception as exc:
        result = dict(schema=SCHEMA, status='failed',
                      request=dict(project_file=parsed.project, output_root=parsed.output),
                      error=dict(type=type(exc).__name__, message=str(exc)))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return {'completed': 0, 'completed_with_gaps': 10, 'failed': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
