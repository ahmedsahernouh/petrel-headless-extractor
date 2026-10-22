#!/usr/bin/env python3
# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Discover Petrel pairs and audit their CRS through the packaged CRS-only tool."""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

SCHEMA = 'fieldviewer.crs-batch/1'
CODE = ('fieldviewer_crs_batch.py', 'fieldviewer_crs.py', 'petrel_crs_datum.py',
        'petrel_native_binary.py', 'petrel_native_well_evidence.py')
FIELDS = ('project', 'store', 'status', 'saved_version', 'crs_names', 'crs_authorities',
          'project_record_count', 'project_subject_count', 'stream_complete',
          'projection_states', 'datum_definitions', 'project_units_and_datum_tags',
          'well_reference_count', 'source_unchanged', 'error', 'receipt')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Use an absolute drive or folder path')
    return Path(os.path.abspath(path))


def inside(path, parent):
    return path == parent or parent in path.parents


def no_links(path):
    for p in (path, *path.parents):
        if p.exists() or p.is_symlink():
            info = p.lstat()
            if p.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
                raise ValueError('Link/reparse path is excluded: ' + str(p))


def read(path):
    path = Path(path)
    no_links(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Batch control file exceeds 32 MiB: ' + str(path))
    return json.loads(path.read_text('utf-8-sig'))


def write(path, value):
    path = Path(path)
    no_links(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    os.replace(temp, path)


def code_binding():
    scripts = Path(__file__).absolute().parent
    return {n: digest(scripts / n) for n in CODE}


def identity(project):
    return hashlib.sha256(os.path.normcase(str(project)).encode('utf-8')).hexdigest()[:24]


def discover(scan_root, output):
    scan_root, output = absolute(scan_root), absolute(output)
    no_links(scan_root); no_links(output)
    if not scan_root.is_dir() or inside(scan_root, output):
        raise ValueError('Existing scan folder and separate output directory required')
    if output.exists() or not output.parent.is_dir():
        raise ValueError('Choose a new campaign directory under an existing parent')
    output.mkdir()
    issues, entries = [], []
    pending = [scan_root]
    package = Path(__file__).absolute().parents[1]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as iterator:
                children = sorted(iterator, key=lambda e: e.name.casefold())
        except OSError as exc:
            issues.append(dict(path=str(folder), status='inaccessible_directory', error=str(exc)))
            continue
        dirs = {}
        pets = {}
        for child in children:
            path = Path(child.path)
            try:
                info = child.stat(follow_symlinks=False)
                if child.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
                    issues.append(dict(path=str(path), status='skipped_link_or_reparse', error='Not traversed'))
                elif stat.S_ISDIR(info.st_mode):
                    dirs[child.name.casefold()] = path
                elif stat.S_ISREG(info.st_mode) and path.suffix.lower() == '.pet':
                    pets[path.stem.casefold()] = path
            except OSError as exc:
                issues.append(dict(path=str(path), status='inaccessible_entry', error=str(exc)))
        for stem, pet in pets.items():
            store = dirs.get(stem + '.ptd')
            status = 'pending' if store else 'missing_pair'
            if inside(output, pet.parent) or inside(pet.parent, output):
                status = 'output_overlap'
            entries.append(dict(id=identity(pet), project=str(pet),
                                store=str(store) if store else '', status=status))
        for name, folder in dirs.items():
            if folder == output or inside(folder, output) or folder == package:
                issues.append(dict(path=str(folder), status='excluded_output_or_package', error='Not traversed'))
            elif name.endswith('.ptd'):
                # Native stores contain payloads, not project-discovery roots.
                if name[:-4] not in pets:
                    issues.append(dict(path=str(folder), status='orphan_store', error='No same-stem PET in this directory'))
            else:
                pending.append(folder)
    entries.sort(key=lambda r: os.path.normcase(r['project']))
    if len({r['id'] for r in entries}) != len(entries):
        raise ValueError('Duplicate canonical project identity')
    plan = dict(schema=SCHEMA, created_utc=now(), scan_root=str(scan_root), output=str(output),
                code=code_binding(), entries=entries, discovery_issues=issues,
                discovery_scope='Same-directory PET/PTD pairs; no links, reparse points or descent into native PTD stores; no archive or external-reference search')
    write(output / 'DISCOVERY.json', plan)
    write(output / 'BINDING.json', dict(discovery_sha256=digest(output / 'DISCOVERY.json'), code=plan['code']))
    (output / 'projects').mkdir()
    return summarize(output)


def plan_for(campaign):
    campaign = absolute(campaign); no_links(campaign)
    binding = read(campaign / 'BINDING.json')
    if binding['discovery_sha256'] != digest(campaign / 'DISCOVERY.json') or binding['code'] != code_binding():
        raise ValueError('Discovery or tool code changed; preserve this campaign and start a new one')
    plan = read(campaign / 'DISCOVERY.json')
    if plan['schema'] != SCHEMA or plan['output'] != str(campaign):
        raise ValueError('Campaign path/schema mismatch; use the original campaign path')
    for entry in plan['entries']:
        if entry['id'] != identity(entry['project']):
            raise ValueError('Project identity mismatch')
    return plan


def verify_export(directory, project, *, recheck_source=False):
    directory = absolute(directory)
    receipt = read(directory / 'CRS_EXPORT.json')
    if (receipt.get('schema') != 'fieldviewer.crs-export/1' or
            receipt.get('request') != dict(project_file=str(project), output_root=str(directory)) or
            receipt.get('status') not in ('completed', 'completed_with_gaps', 'failed')):
        raise ValueError('CRS receipt does not belong to this project/run')
    manifest = read(directory / 'FILE_MANIFEST.json')
    paths = set()
    for artifact in manifest['artifacts']:
        p = absolute(artifact['path']); no_links(p)
        if p.parent != directory or p.name in paths or p.stat().st_size != artifact['bytes'] or digest(p) != artifact['sha256']:
            raise ValueError('CRS artifact binding/hash mismatch')
        paths.add(p.name)
    if not {'CRS_DATUM.json', 'CRS_REPORT.html', 'EVENTS.jsonl'} <= paths:
        raise ValueError('Required CRS artifacts missing from manifest')
    if recheck_source:
        for source in receipt.get('source_files', []):
            p = absolute(source['path']); no_links(p)
            expected = {Path(project), Path(project).with_suffix('.ptd') / 'Model.ptd'}
            if p not in expected or p.stat().st_size != source['bytes'] or digest(p) != source['sha256']:
                raise ValueError('Source metadata changed since the recorded inspection')
        model = Path(project).with_suffix('.ptd') / 'Model.ptd'
        recorded = {s['path'] for s in receipt.get('source_files', [])}
        if receipt['status'] != 'failed' and model.exists() != (str(model) in recorded):
            raise ValueError('Model metadata presence changed since inspection')
    return receipt, read(directory / 'CRS_DATUM.json')


def completed(directory, project, *, recheck_source=False):
    done = read(directory / 'BATCH_RESULT.json')
    if done['project'] != str(project):
        raise ValueError('Batch result project mismatch')
    if done.get('receipt_sha256'):
        for name, key in [('CRS_EXPORT.json', 'receipt_sha256'), ('FILE_MANIFEST.json', 'manifest_sha256')]:
            if digest(directory / 'CRS' / name) != done[key]:
                raise ValueError('Previously registered CRS receipt changed')
        receipt, metadata = verify_export(directory / 'CRS', project, recheck_source=recheck_source)
        if receipt['status'] != done['status']:
            raise ValueError('Batch status/receipt mismatch')
        return done, receipt, metadata
    if recheck_source and done['status'] != 'failed':
        raise ValueError('Completion without a CRS receipt')
    return done, {}, {}


def summarize(campaign):
    campaign = absolute(campaign)
    plan = plan_for(campaign)
    rows = []
    for entry in plan['entries']:
        row = dict(project=entry['project'], store=entry['store'], status=entry['status'])
        directory = campaign / 'projects' / entry['id']
        if (directory / 'BATCH_RESULT.json').exists():
            done, receipt, metadata = completed(directory, Path(entry['project']))
            records = metadata.get('project_records') or []
            projections = [r.get('projection') or {} for r in records]
            row.update(status=done['status'], saved_version=metadata.get('saved_version'),
                       crs_names=[(p.get('late_bound') or {}).get('name') for p in projections],
                       crs_authorities=[(p.get('late_bound') or {}).get('authority') for p in projections],
                       project_record_count=len(records),
                       project_subject_count=metadata.get('counts', {}).get('ProjectSubject_seen'),
                       stream_complete=metadata.get('counts', {}).get('stream_complete'),
                       projection_states=[p.get('declaration_state') for p in projections],
                       datum_definitions=metadata.get('datum_records', []),
                       project_units_and_datum_tags=[r.get('units_style') for r in records],
                       well_reference_count=len(metadata.get('well_reference_records') or []),
                       source_unchanged=receipt.get('source_integrity_unchanged'),
                       error=done.get('error') or receipt.get('error'),
                       receipt=str((directory / 'CRS' / 'CRS_EXPORT.json').relative_to(campaign)) if receipt else '')
        elif directory.exists():
            row['status'] = 'interrupted_requires_review'
        rows.append(row)
    counts = {s: sum(r['status'] == s for r in rows) for s in sorted({r['status'] for r in rows})}
    summary = dict(schema=SCHEMA, updated_utc=now(), scan_root=plan['scan_root'], projects=len(rows),
                   status_counts=counts, discovery_issue_count=len(plan['discovery_issues']),
                   physical_crs_or_datum_verified=False, csv='RESULTS.csv', discovery='DISCOVERY.json',
                   inspection_scope='PET/Model metadata only; all definitions retained, no automatic selected datum or coordinate transformation')
    buf = io.StringIO(newline=''); writer = csv.DictWriter(buf, FIELDS); writer.writeheader()
    for row in rows:
        values = {}
        for key in FIELDS:
            value = row.get(key)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False, allow_nan=False)
            # Prevent spreadsheet formula interpretation of names/paths; JSON is unchanged.
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            values[key] = value
        writer.writerow(values)
    csv_path = campaign / 'RESULTS.csv'; no_links(csv_path)
    tmp = campaign / 'RESULTS.csv.tmp'
    with tmp.open('x', encoding='utf-8-sig', newline='') as stream:
        stream.write(buf.getvalue())
    os.replace(tmp, csv_path)
    write(campaign / 'SUMMARY.json', summary)
    return summary


def run(campaign, max_projects=None):
    campaign = absolute(campaign); plan = plan_for(campaign)
    if max_projects is not None and max_projects < 1:
        raise ValueError('max-projects must be positive')
    lock = campaign / 'RUNNING.lock'
    with lock.open('x', encoding='utf-8') as stream:
        json.dump(dict(pid=os.getpid(), started_utc=now(), executable=sys.executable), stream)
    try:
        count = 0
        for entry in plan['entries']:
            if entry['status'] != 'pending':
                continue
            project = Path(entry['project']); directory = campaign / 'projects' / entry['id']
            if (directory / 'BATCH_RESULT.json').exists():
                completed(directory, project, recheck_source=True)
                continue
            if directory.exists():
                raise ValueError('Incomplete prior project requires review; no automatic retry: ' + str(directory))
            if max_projects is not None and count >= max_projects:
                break
            plan_for(campaign)
            no_links(project); no_links(directory)
            directory.mkdir()
            script = Path(__file__).absolute().with_name('fieldviewer_crs.py')
            with (directory / 'stdout.json').open('x', encoding='utf-8') as stdout, (directory / 'stderr.txt').open('x', encoding='utf-8') as stderr:
                process = subprocess.run([sys.executable, '-B', str(script), '--project', str(project),
                                          '--output', str(directory / 'CRS')], stdout=stdout, stderr=stderr)
            done = dict(project=str(project), status='failed', exit_code=process.returncode, ended_utc=now())
            if (directory / 'CRS' / 'CRS_EXPORT.json').exists():
                receipt, _ = verify_export(directory / 'CRS', project, recheck_source=True)
                expected = {'completed': 0, 'completed_with_gaps': 10, 'failed': 2}[receipt['status']]
                if process.returncode != expected:
                    raise ValueError('CRS exit code/receipt mismatch')
                done.update(status=receipt['status'], receipt_sha256=digest(directory / 'CRS' / 'CRS_EXPORT.json'),
                            manifest_sha256=digest(directory / 'CRS' / 'FILE_MANIFEST.json'), error=receipt.get('error'))
            else:
                done['error'] = dict(message='CRS tool did not write a receipt; inspect retained stdout/stderr')
            write(directory / 'BATCH_RESULT.json', done)
            summarize(campaign)
            count += 1
            if done['status'] == 'failed':
                raise ValueError('Project failed; inspect evidence before continuing. This recorded failure will not be retried.')
        return summarize(campaign)
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    scan = commands.add_parser('discover'); scan.add_argument('--root', required=True); scan.add_argument('--output', required=True)
    for name in ('run', 'status'):
        sub = commands.add_parser(name); sub.add_argument('--campaign', required=True)
        if name == 'run': sub.add_argument('--max-projects', type=int)
    args = parser.parse_args(argv)
    try:
        result = discover(args.root, args.output) if args.action == 'discover' else (
            run(args.campaign, args.max_projects) if args.action == 'run' else summarize(args.campaign))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if any(result['status_counts'].get(s, 0) for s in ('failed', 'interrupted_requires_review')):
            return 2
        gaps = result['discovery_issue_count'] or any(s != 'completed' for s in result['status_counts'])
        return 10 if gaps else 0
    except Exception as exc:
        print(json.dumps(dict(schema=SCHEMA, status='requires_review', error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
