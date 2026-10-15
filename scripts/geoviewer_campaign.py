# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Read-only project discovery and sequential Field Viewer extraction campaigns.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Standard library only. Never opens Petrel, deletes source data or uploads logs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
import zipfile

# The embedded interpreter omits the script directory from sys.path. Only this
# add-on's fixed directory is added; child extractor search paths remain isolated.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = 'geoviewer.campaign/1'
SKIP_NAMES = {'$recycle.bin', 'system volume information', '$windows.~bt'}
TERMINAL = {'completed', 'completed_with_gaps', 'failed', 'cancelled', 'interrupted',
            'source_changed', 'source_unavailable'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path = Path(path)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with pending.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def inside(path, parent):
    return Path(path).is_relative_to(Path(parent))


def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def powershell(script):
    return ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)]


def connected_roots():
    """Visible filesystem drives plus already mapped UNC shares; no host probing."""
    script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$rows=@(Get-PSDrive -PSProvider FileSystem | ForEach-Object {
    [pscustomobject]@{path=$_.Root; canonical=$(if ($_.DisplayRoot) {$_.DisplayRoot} else {$_.Root}); kind='filesystem'}
})
try { $rows+=@(Get-SmbMapping -ErrorAction Stop | Where-Object Status -eq 'OK' | ForEach-Object {
    [pscustomobject]@{path=$_.RemotePath;canonical=$_.RemotePath;kind='connected_share'}
}) } catch {}
ConvertTo-Json -InputObject @($rows) -Compress
"""
    import base64
    result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand',
                             base64.b64encode(script.encode('utf-16le')).decode()],
                            capture_output=True, check=True, timeout=60)
    return json.loads(result.stdout.decode('utf-8-sig', errors='replace'))


def canonical(path, roots):
    value = os.path.normcase(os.path.abspath(path))
    for row in sorted(roots, key=lambda r: len(r['path']), reverse=True):
        prefix = os.path.normcase(os.path.abspath(row['path'])).rstrip('\\/')
        if value == prefix or value.startswith(prefix + os.sep):
            return os.path.normcase(os.path.abspath(row['canonical'])).rstrip('\\/') + value[len(prefix):]
    return value


def collapse_roots(rows):
    selected, aliases = [], []
    for row in sorted(rows, key=lambda r: len(os.path.abspath(r['canonical']))):
        key = os.path.normcase(os.path.abspath(row['canonical']))
        previous = next((x for x in selected if inside(key, os.path.normcase(os.path.abspath(x['canonical'])))), None)
        if previous:
            aliases.append(dict(**row, covered_by=previous['path']))
        else:
            selected.append(row)
    return selected, aliases


class Events:
    def __init__(self, campaign):
        self.path = campaign / 'LOGS' / 'campaign.jsonl'
        self.path.parent.mkdir(exist_ok=True)
        self.last = 0.0

    def emit(self, event, **fields):
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(at=utc(), event=event, **fields), ensure_ascii=True) + '\n')
        if event != 'progress' or time.monotonic() - self.last > 10:
            print(event + ': ' + json.dumps(fields, ensure_ascii=True), flush=True)
            self.last = time.monotonic()


def generated_directory(path):
    return ((path / 'CAMPAIGN.json').is_file() or
            (path / 'STANDALONE.txt').is_file() or
            (path / 'RUN_RESULT.json').is_file() or
            (path / '00_manifest' / 'export_manifest.csv').is_file())


def access_error(path, operation, exc):
    return dict(path=str(path), operation=operation, error=str(exc),
                error_type=type(exc).__name__, errno=getattr(exc, 'errno', None),
                winerror=getattr(exc, 'winerror', None),
                filename=str(exc.filename) if getattr(exc, 'filename', None) else None)


def walk_files(root, events, excluded=(), *, discovery=False, problems=None):
    """Iterative streaming traversal. Reparse points are recorded, not followed."""
    problems = problems if problems is not None else []
    stack = [Path(root)]
    visited = 0
    while stack:
        folder = stack.pop()
        try:
            if linked(folder):
                raise OSError('Reparse-point traversal excluded')
            if any(inside(folder, p) for p in excluded):
                continue
            with os.scandir(folder) as entries:
                entries = list(entries)
                names = {entry.name.lower() for entry in entries}
                if discovery and ('campaign.json' in names or 'standalone.txt' in names or
                                  'run_result.json' in names or
                                  ('00_manifest' in names and generated_directory(folder))):
                    events.emit('excluded_generated_output', path=str(folder))
                    continue
                for entry in entries:
                    path = Path(entry.path)
                    try:
                        if linked(path):
                            raise OSError('Reparse-point traversal excluded')
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() in SKIP_NAMES:
                                continue
                            if discovery and entry.name.lower().endswith('.ptd'):
                                continue
                            stack.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            yield path, entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        problems.append(access_error(path, 'inspect_entry', exc))
                        events.emit('scan_entry_error', path=str(path), error=str(exc))
            visited += 1
            events.emit('progress', directories=visited, path=str(folder)) if time.monotonic() - events.last > 10 else None
        except OSError as exc:
            problems.append(access_error(folder, 'scan_directory', exc))
            events.emit('scan_directory_error', path=str(folder), error=str(exc))


def snapshot(pet, events):
    """Size the exact .pet/.ptd pair, never the shared parent or other projects."""
    store = pet.with_suffix('.ptd')
    problems = []
    if not store.is_dir() or linked(store) or linked(pet):
        raise ValueError('Exact regular .pet and matching non-linked .ptd directory required')
    s = pet.stat()
    rows = [dict(relative='PROJECT.pet', size=s.st_size, mtime_ns=s.st_mtime_ns)]
    for path, stat in walk_files(store, events, problems=problems):
        rows.append(dict(relative='STORE/' + path.relative_to(store).as_posix(),
                         size=stat.st_size, mtime_ns=stat.st_mtime_ns))
    return sorted(rows, key=lambda r: r['relative']), problems


def source_preflight(campaign, project, events, source_check=None):
    """Retain every check independently; incomplete reads cannot prove removals."""
    baseline = campaign / 'inventories' / (project['id'] + '.json')
    previous, current, errors = None, None, []
    operation, inspected_path = 'read_baseline_inventory', baseline
    try:
        previous = read_json(baseline)
        operation, inspected_path = 'inspect_source_pair', Path(project['pet'])
        current, errors = (source_check(project, previous, events) if source_check else
                           snapshot(inspected_path, events))
    except (OSError, ValueError) as exc:
        errors = [access_error(inspected_path, operation, exc)]
    before = {r['relative']: r for r in previous or []}
    after = {r['relative']: r for r in current or []}
    complete = previous is not None and current is not None and not errors
    added = [after[k] for k in sorted(after.keys() - before.keys())]
    missing = [before[k] for k in sorted(before.keys() - after.keys())]
    changed = [dict(relative=k, before=before[k], after=after[k])
               for k in sorted(before.keys() & after.keys()) if before[k] != after[k]]
    differs = bool(added or missing or changed)
    status = 'source_unavailable' if not complete else ('source_changed' if differs else 'unchanged')
    reason = ('Source inventory could not be completely inspected; see preflight evidence' if not complete else
              'Source inventory metadata differs from the saved baseline; start a new scan' if differs else
              'Source inventory metadata matches the saved baseline')
    receipt = dict(schema='geoviewer.source-preflight/1', checked_utc=utc(), project_id=project['id'],
                   source=project['pet'], baseline_path=str(baseline), status=status, reason=reason,
                   comparison_scope=('selected_metadata_files' if project.get('lab_scenario') == 'metadata_profile'
                                     else 'exact_pet_ptd_inventory_metadata'),
                   content_hashes_checked=False, inventory_complete=complete,
                   inventory_changed=(differs if complete else None), before=previous, after=current,
                   access_errors=errors, diff=dict(added=added, changed=changed,
                   removed=missing if complete else [], unobserved=missing if not complete else []))
    folder = campaign / 'LOGS' / project['id'] / 'preflight'
    folder.mkdir(parents=True, exist_ok=True)
    evidence = folder / (uuid.uuid4().hex + '.json')
    write_json(evidence, receipt)
    project['latest_preflight'] = str(evidence)
    events.emit('source_preflight', project=project['id'], status=status, evidence=str(evidence),
                inventory_complete=complete, added=len(added), changed=len(changed),
                removed=len(receipt['diff']['removed']), unobserved=len(receipt['diff']['unobserved']),
                access_errors=len(errors))
    return receipt


def row_path(pet, row):
    return pet if row['relative'] == 'PROJECT.pet' else pet.with_suffix('.ptd') / row['relative'][6:]


def digest(path, events=None):
    before = path.stat()
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(8 * 1024 * 1024):
            sha.update(block)
            if events and time.monotonic() - events.last > 10:
                events.emit('progress', action='duplicate verification: full file read', file=str(path), bytes=stream.tell())
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError('Source changed during hashing: ' + str(path))
    return sha.hexdigest()


def full_fingerprint(pet, rows, events):
    hashes = []
    for row in rows:
        path = row_path(pet, row)
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime_ns']):
            raise OSError('Source changed since inventory: ' + str(path))
        hashes.append((row['relative'], row['size'], digest(path, events)))
    latest, errors = snapshot(pet, events)
    if errors or latest != rows:
        raise OSError('Native file inventory changed during duplicate verification')
    return hashlib.sha256(json.dumps(hashes).encode()).hexdigest()


def companion_fingerprint(pet, events, excluded):
    """Conservative duplicate check: different adjacent data keeps both trials."""
    parent = pet.parent
    omitted = list(excluded)
    for item in parent.iterdir():
        if item.suffix.lower() in ('.pet', '.ptd'):
            omitted.append(item)
    errors, files = [], []
    for path, stat in walk_files(parent, events, omitted, discovery=True, problems=errors):
        if any(inside(path, p) for p in omitted):
            continue
        files.append((path.relative_to(parent).as_posix(), stat.st_size, digest(path, events)))
    if errors:
        raise OSError('Adjacent companion coverage incomplete; retain duplicate candidate')
    return hashlib.sha256(json.dumps(sorted(files)).encode()).hexdigest()


def deduplicate(projects, campaign, events, excluded):
    groups = defaultdict(list)
    for project in projects:
        if project['status'] != 'queued':
            continue
        rows = read_json(campaign / 'inventories' / (project['id'] + '.json'))
        try:
            signature = [(r['relative'], r['size']) for r in rows]
            key = (digest(Path(project['pet'])), json.dumps(signature))
            groups[key].append(project)
        except OSError as exc:
            project['status'] = 'source_unavailable'
            project['reason'] = str(exc)
    for group in groups.values():
        if len(group) < 2:
            continue
        confirmed = {}
        for project in group:
            try:
                pet = Path(project['pet'])
                rows = read_json(campaign / 'inventories' / (project['id'] + '.json'))
                native = full_fingerprint(pet, rows, events)
                companions = companion_fingerprint(pet, events, excluded)
                project['native_sha256'] = native
                project['companion_sha256'] = companions
                key = (native, companions)
                if key in confirmed:
                    project.update(status='duplicate', duplicate_of=confirmed[key])
                    events.emit('duplicate_verified', project=project['id'], duplicate_of=confirmed[key])
                else:
                    confirmed[key] = project['id']
            except OSError as exc:
                # Uncertainty is not permission to discard a test case.
                project['deduplication_warning'] = str(exc)
                events.emit('deduplication_incomplete', project=project['id'], error=str(exc))


def indexes(campaign, state):
    write_json(campaign / 'CAMPAIGN.json', state)
    columns = ['id', 'pet', 'store', 'pet_bytes', 'store_bytes', 'total_bytes', 'file_count',
               'status', 'duplicate_of', 'saved_version', 'reason', 'latest_output', 'latest_report', 'latest_preflight']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(state['projects'])
    target = campaign / 'PROJECTS.csv'
    tmp = target.with_suffix('.csv.tmp')
    tmp.write_text(stream.getvalue(), encoding='utf-8-sig')
    tmp.replace(target)
    import html
    rows = []
    for p in state['projects']:
        link = Path(p['latest_report']).as_uri() if p.get('latest_report') else ''
        preflight = Path(p['latest_preflight']).as_uri() if p.get('latest_preflight') else ''
        evidence_links = []
        if link:
            evidence_links.append('<a href="' + html.escape(link, quote=True) + '">Report</a>')
        if preflight:
            evidence_links.append('<a href="' + html.escape(preflight, quote=True) + '">Source preflight evidence</a>')
        rows.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in
                    [p['id'], p['pet'], round(p.get('total_bytes', 0) / 1024**3, 3),
                     p['status'], p.get('saved_version', 'unknown'), p.get('duplicate_of', ''), p.get('reason', '')]) +
                    '<td>' + ' | '.join(evidence_links) + '</td></tr>')
    page = '<!doctype html><meta charset="utf-8"><title>Field Viewer project trials</title><style>body{font:15px system-ui;margin:24px}td,th{padding:8px;border:1px solid #ccc;text-align:left}table{border-collapse:collapse}td{overflow-wrap:anywhere}</style>'
    page += '<h1>Field Viewer project trials</h1><p>Website: <a href="https://saherlabs.dev/">saherlabs.dev</a></p>'
    page += '<p>' + html.escape(str(dict(Counter(p['status'] for p in state['projects'])))) + '</p>'
    page += '<p>Discovery access errors: ' + str(sum(r.get('scan_errors', 0) for r in state.get('roots', []))) + '; alias roots collapsed: ' + str(len(state.get('root_aliases', []))) + '. See the campaign log for skipped directories and exclusions.</p>'
    page += '<p><a href="PROJECTS.csv">Inventory CSV</a> | <a href="LOGS/">Collected logs</a> | <a href="CAMPAIGN.json">Full queue and attempts</a></p>'
    if (campaign / 'LOGS/ANALYSIS/REPORT.html').is_file():
        page += '<p><a href="LOGS/ANALYSIS/REPORT.html">Cross-project diagnostics and improvement evidence</a></p>'
    page += '<p>GiB means .pet plus matching .ptd only. Linked external data is additional. Completion does not establish full recovery or cross-version support.</p>'
    page += '<table><tr><th>ID</th><th>Source</th><th>GiB</th><th>Outcome</th><th>Saved version</th><th>Duplicate of</th><th>Reason</th><th>Files and evidence</th></tr>' + ''.join(rows) + '</table>'
    tmp = campaign / 'INDEX.html.tmp'
    tmp.write_text(page, encoding='utf-8')
    tmp.replace(campaign / 'INDEX.html')


def discover(campaign, state, events):
    config = state['config']
    roots = connected_roots() if config.get('scan_connected_drives', True) else []
    roots += [dict(path=str(Path(p).absolute()), canonical=str(Path(p).absolute()), kind='explicit') for p in config.get('extra_roots', [])]
    # Convert explicitly supplied drive subdirectories to their mapped UNC identities.
    for row in roots:
        row['canonical'] = canonical(row['canonical'], roots)
    selected, aliases = collapse_roots(roots)
    state.update(roots=selected, root_aliases=aliases, discovery_complete=False)
    excluded = [Path(p).absolute() for p in config.get('exclude_roots', []) + config['output_roots']]
    excluded += [campaign.absolute(), Path(config['extractor_directory']).absolute()]
    excluded_keys = [canonical(p, roots) for p in excluded]
    # Cover exclusions through alternate mapped letters as well as UNC paths.
    for root in selected:
        for key in excluded_keys:
            base = root['canonical'].rstrip('\\/')
            if inside(key, base):
                excluded.append(Path(root['path']) / Path(key).relative_to(base))
    seen = set()
    for root in selected:
        errors = []
        events.emit('scan_root', **root)
        for pet, _ in walk_files(Path(root['path']), events, excluded, discovery=True, problems=errors):
            if pet.suffix.lower() != '.pet':
                continue
            key = canonical(pet, roots)
            if key in seen:
                continue
            seen.add(key)
            pid = 'P' + str(len(state['projects']) + 1).zfill(5)
            row = dict(id=pid, pet=str(pet), store=str(pet.with_suffix('.ptd')), status='queued',
                       saved_version='unknown', attempts=[], filesystem_modified_utc=utc())
            try:
                files, incomplete = snapshot(pet, events)
                write_json(campaign / 'inventories' / (pid + '.json'), files)
                row.update(pet_bytes=files[[r['relative'] for r in files].index('PROJECT.pet')]['size'],
                           total_bytes=sum(r['size'] for r in files), file_count=len(files),
                           filesystem_modified_utc=datetime.fromtimestamp(pet.stat().st_mtime, timezone.utc).isoformat())
                row['store_bytes'] = row['total_bytes'] - row['pet_bytes']
                if incomplete:
                    row.update(status='inventory_incomplete', reason='Some native entries could not be inspected; see campaign log')
            except (OSError, ValueError) as exc:
                row.update(status='invalid_pair', reason=str(exc))
            state['projects'].append(row)
            events.emit('project_found', **{k: row.get(k) for k in ('id', 'pet', 'total_bytes', 'status')})
            indexes(campaign, state)
        root['scan_errors'] = len(errors)
    deduplicate(state['projects'], campaign, events, excluded)
    state.update(discovery_complete=True, discovery_finished_utc=utc())
    indexes(campaign, state)


@contextmanager
def campaign_lock(campaign):
    with (campaign / '.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def stop_child(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
        else:
            proc.kill()
        proc.wait(timeout=20)


def process(command, logfile, *, env=None, timeout_hours=0, events=None, on_started=None):
    """Child gets no stdin: batch execution cannot block on a hidden prompt."""
    with logfile.open('xb') as output:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        started = time.monotonic()
        try:
            if on_started:
                on_started(proc.pid)
            with logfile.open('rb') as reader:
                while proc.poll() is None:
                    chunk = reader.read()
                    if chunk:
                        print(chunk.decode('utf-8', errors='replace'), end='', flush=True)
                    if timeout_hours and time.monotonic() - started > timeout_hours * 3600:
                        if events:
                            events.emit('process_timeout', pid=proc.pid, timeout_seconds=timeout_hours * 3600,
                                        elapsed_seconds=round(time.monotonic() - started, 2), console=str(logfile))
                        raise TimeoutError(f'Configured per-project time limit exceeded ({timeout_hours * 3600:g} seconds; PID {proc.pid})')
                    if events and time.monotonic() - events.last > 30:
                        events.emit('progress', action='extractor running', pid=proc.pid, seconds=round(time.monotonic() - started))
                    time.sleep(0.25)
                print(reader.read().decode('utf-8', errors='replace'), end='', flush=True)
            return proc.returncode
        except BaseException:
            stop_child(proc)
            raise


def process_alive(pid):
    """A surviving child must not overlap a retried extraction after runner death."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only.
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # No such PID.
                return False
            raise OSError(error, 'Cannot determine whether prior child is still running')
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def choose_output(config, project, campaign_name):
    required = int(config.get('reserve_free_gib', 50) * 1024**3 + project['total_bytes'] * config.get('space_multiplier', 3))
    candidates, findings = [], []
    for root in config['output_roots']:
        root = Path(root).absolute()
        try:
            if not Path(root.anchor).is_dir():
                raise OSError('Output drive/share is unavailable')
            if inside(root, Path(project['pet']).parent):
                raise ValueError('Output is inside source directory')
            root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(root).free
            findings.append(dict(root=str(root), free_bytes=free, required_bytes_estimate=required))
            if free >= required:
                candidates.append((free, root / campaign_name))
        except (OSError, ValueError) as exc:
            findings.append(dict(root=str(root), error=str(exc)))
    return (max(candidates, key=lambda x: x[0])[1] if candidates else None), findings


def collect_attempt(campaign, project, attempt, events):
    """Only known diagnostic artifacts; never recursive data-file collection."""
    destination = campaign / 'LOGS' / project['id'] / ('attempt_' + str(attempt['number']).zfill(3))
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(attempt['output'])
    # A 1.0.0 launcher can retain only STARTUP_SUPPORT.zip. The batch can rerun
    # the package's verified, standard-library collector after the child stops.
    if not list(root.glob('*_SUPPORT.zip')) and attempt.get('extractor_directory'):
        for descriptor in (root / '_scratch').glob('GeoViewer_support_*/run.json'):
            if attempt.get('child_pid') and process_alive(attempt['child_pid']):
                break
            try:
                toolkit = Path(attempt['extractor_directory'])
                builder = toolkit / 'scripts' / 'geoviewer_support.py'
                manifest = read_json(toolkit / '00_manifest' / 'toolkit_files.json')
                expected = [r for r in manifest['files'] if r['path'] == 'scripts/geoviewer_support.py']
                if len(expected) != 1 or digest(builder) != expected[0]['sha256'].lower():
                    raise ValueError('Support collector integrity verification failed')
                logfile = destination / ('support_recovery_' + uuid.uuid4().hex[:8] + '.txt')
                code = process([str(toolkit / 'runtime' / 'python.exe'), '-B', str(builder),
                                '--session', str(descriptor.parent)], logfile, events=events)
                events.emit('support_recovery', project=project['id'], exit_code=code, log=str(logfile))
                if code != 0:
                    attempt['support_recovery_warning'] = 'Collector failed; raw logs retained'
            except (OSError, ValueError) as exc:
                attempt['support_recovery_warning'] = str(exc)
                events.emit('support_recovery_failed', project=project['id'], error=str(exc))
    files = list(root.glob('*_SUPPORT.zip'))
    files += list((root / '_scratch').glob('GeoViewer_support_*/*SUPPORT.zip'))
    files += list(root.glob('*_data/RUN_RESULT.json'))
    files += list(root.glob('*_data/RUN_LOG.txt'))
    files += list(root.glob('*_LOG.txt')) + list(root.glob('*_EVENTS.jsonl'))
    # Bootstrap can return a fallback path outside the requested output volume.
    console = Path(attempt['console'])
    if console.exists():
        for line in console.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            if line.startswith('SUPPORT ZIP (') and '): ' in line:
                path = Path(line.split('): ', 1)[1].strip())
                if path.is_absolute() and path.name.lower().endswith('support.zip') and path.is_file():
                    files.append(path)
    collected = []
    for index, source in enumerate(dict.fromkeys(files), 1):
        target = destination / (f'{index:03}_' + source.name)
        try:
            shutil.copy2(source, target)
            collected.append(dict(source=str(source), copy=str(target), sha256=digest(target)))
        except OSError as exc:
            events.emit('log_collection_failed', source=str(source), error=str(exc))
    attempt['collected'] = collected
    receipts = list(root.glob('*_data/RUN_RESULT.json'))
    if len(receipts) == 1:
        receipt = read_json(receipts[0])
        attempt['receipt_status'] = receipt.get('status', 'unknown')
        project['saved_version'] = receipt.get('project_context', {}).get('saved_version', {}).get('value') or 'unknown'
        project['latest_report'] = receipt.get('full_report', '')
        attempt['category_outcomes'] = receipt.get('category_outcomes', [])
    if not project.get('latest_report'):
        reports = list(root.glob('*_REPORT.html'))
        if len(reports) == 1:
            project['latest_report'] = str(reports[0])
    try:
        from geoviewer_case_diagnostics import analyze_case
        attempt['case_diagnostics'] = analyze_case(campaign, project, attempt, destination)
    except Exception as exc:
        attempt['case_diagnostics_error'] = str(exc)
        events.emit('case_diagnostics_failed', project=project['id'], error=str(exc), traceback=traceback.format_exc())
    write_json(destination / 'ATTEMPT.json', attempt)


def outcome(code, receipt_status):
    if code == 0 and receipt_status == 'completed':
        return 'completed'
    if code in (0, 10) and receipt_status == 'completed_with_gaps':
        return 'completed_with_gaps'
    if code == 130:
        return 'cancelled'
    return 'failed'


def run_queue(campaign, state, events, *, retry_failed=False, max_projects=0, execution=None):
    config = state['config']
    toolkit = Path(config['extractor_directory'])
    launcher = toolkit / 'scripts' / 'launch_standalone_petrel.ps1'
    if not launcher.is_file():
        raise ValueError('Use a complete standalone Field Viewer package')
    if not execution:
        doctorlog = campaign / 'LOGS' / ('doctor_' + uuid.uuid4().hex[:8] + '.txt')
        code = process(powershell(toolkit / 'scripts' / 'doctor_portable_petrel_toolkit.ps1') +
                       ['-PythonPath', str(toolkit / 'runtime' / 'python.exe')], doctorlog, events=events)
        if code != 0:
            raise RuntimeError('Toolkit doctor failed; see ' + str(doctorlog))
    elif not execution.get('verified_session'):
        raise ValueError('A dedicated execution adapter requires a verified session')
    attempted = 0
    queue_started = time.monotonic()
    for project in state['projects']:
        if execution and not execution['select'](project):
            continue
        if execution and execution['session_seconds'] is not None and time.monotonic() - queue_started >= execution['session_seconds']:
            events.emit('session_budget_reached', seconds=execution['session_seconds'])
            break
        if (campaign / 'STOP_AFTER_CURRENT.txt').exists():
            events.emit('stop_requested', path=str(campaign / 'STOP_AFTER_CURRENT.txt'))
            break
        eligible = project['status'] in ('queued', 'deferred_space') or (retry_failed and project['status'] in TERMINAL - {'completed', 'completed_with_gaps'})
        if not eligible:
            continue
        if project['attempts']:
            prior_pid = project['attempts'][-1].get('child_pid')
            if prior_pid and process_alive(prior_pid):
                raise RuntimeError('Prior child PID ' + str(prior_pid) + ' still exists; refusing overlapping retry')
        if max_projects and attempted >= max_projects:
            break
        preflight = source_preflight(campaign, project, events,
                                    execution.get('source_check') if execution else None)
        if preflight['status'] != 'unchanged':
            project.update(status=preflight['status'], reason=preflight['reason'])
            indexes(campaign, state)
            continue
        admission = execution['admission'](project) if execution else project
        output, capacity = choose_output(config, admission, campaign.name)
        project['capacity_checks'] = capacity
        if output is None:
            project.update(status='deferred_space', reason='No output root passed availability/space admission check')
            indexes(campaign, state)
            continue
        number = len(project['attempts']) + 1
        output = output / (project['id'] + '_' + str(number).zfill(3))
        output.mkdir(parents=True, exist_ok=False)
        scratch = output / '_scratch'; scratch.mkdir()
        logs = campaign / 'LOGS' / project['id']; logs.mkdir(exist_ok=True)
        attempt = dict(number=number, output=str(output), started_utc=utc(),
                       extractor_directory=str(toolkit), console=str(logs / (f'console_{number:03}.txt')))
        attempt['extractor_manifest_sha256'] = state.get('extractor_manifest_sha256')
        project['attempts'].append(attempt)
        project.update(status='running', latest_output=str(output), latest_report='')
        command = powershell(launcher) + ['-ProjectFile', project['pet'], '-OutputRoot', str(output),
                                          '-CompanionMode', 'convert', '-NoPause']
        command.append('-FullHash' if config.get('full_hash', True) else '-NoFullHash')
        if config.get('run_to_completion', False):
            command.append('-RunToCompletion')
        if execution:
            command = execution['command'](project, output)
        attempt['command'] = command
        indexes(campaign, state)
        env = dict(os.environ, TEMP=str(scratch), TMP=str(scratch), PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        if execution:
            for key in ('GEOVIEWER_SUPPORT_SESSION', 'GEOVIEWER_BOOTSTRAP_LOG'):
                env.pop(key, None)
            # Trace on the local campaign disk, independent of the output share.
            trace_dir = logs / (f'trace_{number:03}')
            trace_dir.mkdir(exist_ok=False)
            attempt['trace_directory'] = str(trace_dir)
            env['FIELDVIEWER_TRACE_DIRECTORY'] = str(trace_dir)
        started = time.monotonic()
        events.emit('extraction_started', project=project['id'], output=str(output))
        cancelled = False
        monitor = None
        case_timeout_hours = (execution['timeout_hours'](project) if execution else
                              None if config.get('run_to_completion', False) else config.get('timeout_hours', 0))
        try:
            def child_started(pid):
                nonlocal monitor
                attempt['child_pid'] = pid
                indexes(campaign, state)
                try:
                    from geoviewer_process_metrics import ProcessSampler
                    metrics = logs / (f'process_metrics_{number:03}.jsonl')
                    attempt['metrics_file'] = str(metrics)
                    monitor = ProcessSampler(metrics, output, config.get('diagnostic_sample_seconds', 10))
                    monitor.start(pid)
                except Exception as exc:
                    attempt['metrics_error'] = str(exc)
                    events.emit('process_metrics_unavailable', project=project['id'], error=str(exc))
            attempt['exit_code'] = process(command, Path(attempt['console']), env=env,
                                            timeout_hours=case_timeout_hours, events=events,
                                            on_started=child_started)
        except KeyboardInterrupt:
            cancelled = True
            attempt.update(exit_code=130, error='User interrupted the campaign')
        except Exception as exc:
            attempt.update(exit_code=1, error=str(exc), exception_type=type(exc).__name__,
                           timeout_seconds=None if case_timeout_hours is None else case_timeout_hours * 3600,
                           traceback=traceback.format_exc())
            events.emit('attempt_failed', project=project['id'], attempt=number, exception_type=type(exc).__name__,
                        error=str(exc), console=attempt['console'], child_pid=attempt.get('child_pid'))
        finally:
            if monitor:
                try:
                    attempt['metrics_collection'] = monitor.close()
                except Exception as exc:
                    attempt['metrics_error'] = str(exc)
        attempt.update(finished_utc=utc(), elapsed_seconds=round(time.monotonic() - started, 2))
        try:
            collect_attempt(campaign, project, attempt, events)
        except Exception as exc:
            attempt['collection_error'] = str(exc)
            events.emit('collection_failed', project=project['id'], error=str(exc))
        project['status'] = outcome(attempt['exit_code'], attempt.get('receipt_status'))
        events.emit('extraction_finished', project=project['id'], status=project['status'], seconds=attempt['elapsed_seconds'])
        indexes(campaign, state)
        attempted += 1
        if execution:
            execution['after_case'](campaign, state, project)
            if attempt['exit_code'] in execution.get('stop_exit_codes', []):
                raise RuntimeError('Testing session stopped: runtime/session validation failed; inspect case console')
        if cancelled:
            break


def collect_all(campaign, state, events):
    for project in state['projects']:
        for attempt in project['attempts']:
            try:
                attempt.setdefault('extractor_directory', state['config']['extractor_directory'])
                collect_attempt(campaign, project, attempt, events)
            except (OSError, ValueError) as exc:
                events.emit('collection_failed', project=project['id'], error=str(exc))
    indexes(campaign, state)


def bundle_logs(campaign):
    # Rebuild the comparison from recorded cases only; never starts an extraction.
    state = read_json(campaign / 'CAMPAIGN.json')
    analysis_error = None
    try:
        from geoviewer_case_diagnostics import campaign_analysis
        state['diagnostic_analysis'] = campaign_analysis(campaign, state)
        indexes(campaign, state)
    except Exception as exc:
        analysis_error = str(exc)
        Events(campaign).emit('campaign_analysis_failed', error=str(exc), traceback=traceback.format_exc())
    target = campaign / 'CAMPAIGN_LOGS.zip'
    pending = campaign / ('CAMPAIGN_LOGS.' + uuid.uuid4().hex[:8] + '.zip.tmp')
    receipt = dict(schema='fieldviewer.log-archive/1', created_utc=utc(), status='building',
                   archive=str(target), included=0, excluded=[], unreadable=[], originals_retained=True,
                   analysis_error=analysis_error)
    def add(z, file):
        name = file.relative_to(campaign).as_posix()
        if file.suffix.lower() in ('.lock', '.tmp') or file.name == '.lock':
            receipt['excluded'].append(dict(path=name, reason='transient lock or incomplete temporary file'))
            return
        # Open before adding a ZIP header, so a denied open leaves no empty member.
        try:
            source = file.open('rb')
        except OSError as exc:
            receipt['unreadable'].append(dict(path=name, error=str(exc), exception_type=type(exc).__name__))
            return
        with source:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if file.suffix.lower() == '.zip' else zipfile.ZIP_DEFLATED
            with z.open(info, 'w', force_zip64=True) as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
        receipt['included'] += 1
    try:
      with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        z.writestr('README.txt', 'Local debugging campaign. Contains source paths/project names and original logs; UNREDACTED. Review before sharing. No project stores, converted datasets or automatic upload. Per-run SUPPORT.zip files retain their own privacy notices.\n')
        for name in ('CAMPAIGN.json', 'PROJECTS.csv', 'INDEX.html'):
            add(z, campaign / name)
        for name in ('CANDIDATES.json', 'CANDIDATES.csv', 'TEST_PLAN.html', 'TEST_PLAN.md'):
            file = campaign / name
            if file.is_file() and not linked(file):
                add(z, file)
        # Saved file inventories are metadata, useful when the source share is offline.
        for file in (campaign / 'inventories').glob('*.json'):
            if file.is_file() and not linked(file):
                add(z, file)
        for folder, dirs, files in os.walk(campaign / 'LOGS', followlinks=False):
            dirs[:] = [d for d in dirs if not linked(Path(folder) / d)]
            for name in files:
                file = Path(folder) / name
                if file.is_file() and not linked(file):
                    add(z, file)
        receipt['status'] = 'partial' if receipt['unreadable'] or analysis_error else 'complete'
        z.writestr('ARCHIVE_CONTENTS.json', json.dumps(receipt, indent=2))
      pending.replace(target)
      receipt['archive_bytes'] = target.stat().st_size
    except Exception as exc:
        receipt.update(status='failed', error=str(exc), exception_type=type(exc).__name__,
                       previous_archive_retained=target.exists())
        try: pending.unlink(missing_ok=True)
        except OSError: pass
    write_json(campaign / 'ARCHIVE_STATUS.json', receipt)
    return receipt


def validate_config(config):
    if not isinstance(config.get('run_to_completion', False), bool):
        raise ValueError('run_to_completion must be true or false')
    if not config.get('output_roots') or not isinstance(config['output_roots'], list):
        raise ValueError('At least one output root is required')
    for name in ('output_roots', 'extra_roots', 'exclude_roots'):
        for p in config.get(name, []):
            if not Path(p).is_absolute():
                raise ValueError(name + ' requires absolute paths: ' + p)
    if config.get('reserve_free_gib', 50) < 0 or config.get('space_multiplier', 3) < 1 or config.get('timeout_hours', 0) < 0:
        raise ValueError('Invalid space reserve, multiplier or timeout')
    if not config.get('scan_connected_drives', True) and not config.get('extra_roots'):
        raise ValueError('No scan roots selected')
    if config.get('campaign_root') and not Path(config['campaign_root']).is_absolute():
        raise ValueError('campaign_root must be an absolute path or empty')
    if not 1 <= config.get('diagnostic_sample_seconds', 10) <= 3600:
        raise ValueError('diagnostic_sample_seconds must be between 1 and 3600')


def select_saved_campaign(config_path, selected=None):
    """Load an explicit listing or a completed-crawl pointer; never discover here."""
    if selected is None:
        pointer = config_path.resolve().parent / 'LATEST_CRAWL.json'
        if not pointer.is_file():
            raise ValueError('No completed crawler list. Run the crawler first or supply --resume CAMPAIGN.json')
        selected = Path(read_json(pointer)['campaign'])
    selected = Path(selected).resolve(strict=True)
    if selected.is_file():
        if selected.name not in ('CAMPAIGN.json', 'PROJECTS.csv'):
            raise ValueError('Select CAMPAIGN.json, PROJECTS.csv or their campaign directory')
        selected = selected.parent
    return selected


def publish_crawl_pointer(config_path, campaign, state):
    if not state.get('discovery_complete'):
        raise ValueError('Cannot publish an incomplete crawl')
    pointer = config_path.resolve().parent / 'LATEST_CRAWL.json'
    write_json(pointer, dict(schema='geoviewer.crawl-pointer/1', campaign=str(campaign),
                            list=str(campaign / 'CAMPAIGN.json'), csv=str(campaign / 'PROJECTS.csv'),
                            finished_utc=state['discovery_finished_utc']))
    print('CRAWLER COMPLETE: ' + str(campaign / 'PROJECTS.csv'))
    print('SAVED RUN LIST: ' + str(campaign / 'CAMPAIGN.json'))
    print('Start GeoViewer_run_extractions.bat later to process this list.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--extractor-directory', type=Path, required=True)
    parser.add_argument('--mode', choices=['all', 'scan', 'run', 'collect'], default='scan')
    parser.add_argument('--resume', '--list', dest='resume', type=Path)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--max-projects', type=int, default=0)
    args = parser.parse_args(argv)
    if args.mode == 'scan' and args.resume:
        raise ValueError('Crawler mode creates a new list; use run mode for a saved list')
    if args.mode in ('run', 'collect'):
        args.resume = select_saved_campaign(args.config, args.resume)
    if args.resume:
        campaign = args.resume.resolve(strict=True)
        state = read_json(campaign / 'CAMPAIGN.json')
        if state.get('schema') != SCHEMA:
            raise ValueError('Unsupported campaign file')
        if not state.get('discovery_complete') and args.mode != 'collect':
            raise ValueError('Interrupted discovery: start a new scan; partial findings remain available')
        if Path(state['config']['extractor_directory']) != args.extractor_directory.resolve():
            raise ValueError('Resume requires the same extractor location; use a new campaign for another build')
    else:
        if args.mode in ('run', 'collect'):
            raise ValueError('--resume is required for run/collect')
        config = read_json(args.config)
        config['extractor_directory'] = str(args.extractor_directory.resolve(strict=True))
        validate_config(config)
        parent = Path(config.get('campaign_root') or args.config.resolve().parent / 'Campaigns').absolute()
        campaign = parent / ('Campaign_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
        campaign.mkdir(parents=True, exist_ok=False)
        (campaign / 'inventories').mkdir()
        state = dict(schema=SCHEMA, created_utc=utc(), config=config, projects=[], discovery_complete=False)
        manifest = args.extractor_directory / '00_manifest' / 'toolkit_files.json'
        state['extractor_manifest_sha256'] = digest(manifest)
        indexes(campaign, state)
    with campaign_lock(campaign):
        events = Events(campaign)
        print('CAMPAIGN: ' + str(campaign), flush=True)
        for p in state['projects']:
            if p['status'] == 'running':
                last = p['attempts'][-1]
                if last.get('child_pid') and process_alive(last['child_pid']):
                    if args.mode == 'collect':
                        events.emit('collecting_live_partial_logs', project=p['id'], pid=last['child_pid'])
                        continue
                    raise RuntimeError('Prior child PID ' + str(last['child_pid']) +
                                       ' still exists; wait for it to finish before resuming this campaign')
                p.update(status='interrupted', reason='Previous runner stopped; collect evidence, then explicitly retry if desired')
        indexes(campaign, state)
        try:
            manifest = args.extractor_directory / '00_manifest' / 'toolkit_files.json'
            if args.mode != 'collect' and digest(manifest) != state['extractor_manifest_sha256']:
                raise ValueError('Extractor build changed; start a new campaign')
            if args.mode in ('scan', 'all') and not args.resume:
                discover(campaign, state, events)
                if args.mode == 'scan':
                    publish_crawl_pointer(args.config, campaign, state)
            if args.mode in ('run', 'all'):
                run_queue(campaign, state, events, retry_failed=args.retry_failed, max_projects=args.max_projects)
            if args.mode == 'collect':
                collect_all(campaign, state, events)
        except BaseException as exc:
            events.emit('campaign_stopped', error=str(exc), traceback=traceback.format_exc())
            raise
        finally:
            state['updated_utc'] = utc()
            indexes(campaign, state)
            bundle_logs(campaign)
            print('INDEX: ' + str(campaign / 'INDEX.html'))
            print('COLLECTED LOGS: ' + str(campaign / 'CAMPAIGN_LOGS.zip'))
            print('PROJECT OUTCOMES: ' + json.dumps(dict(Counter(p['status'] for p in state['projects']))))
    # Campaign completion is not evidence that every project recovered cleanly.
    gaps = any(p['status'] not in ('completed', 'duplicate', 'queued') for p in state['projects'])
    access_errors = any(r.get('scan_errors') for r in state.get('roots', []))
    return 10 if gaps or access_errors else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
