# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Evidence-only diagnostics for multi-project extraction trials.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Reads existing receipts/events, not native stores or converted sample arrays.
Original logs remain authoritative. Missing evidence is never counted as success.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import heapq
import html
import io
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

SCHEMA = 'geoviewer.case-diagnostics/1'
PIPELINE_TIMING_NAME = re.compile(r'pipeline_timings_[0-9a-f]{32}\.json\Z')
SPATIAL_FAILURE_NAME = re.compile(r'native_spatial_decode_failure_[0-9a-f]{32}\.json\Z')
JSON_LIMIT = 64 * 1024 * 1024
LINE_LIMIT = 4 * 1024 * 1024
PAYLOAD = {'samples', 'raw_values', 'vertices', 'coordinates', 'payload', 'blob', 'raw_payload',
           'native_payload', 'record_bytes', 'image', 'base64', 'blob_data', 'raw_hex', 'commands'}
SECRETS = re.compile(r'password|passwd|secret|authorization|credential|api[_-]?key', re.I)
GOOD = {'decoded', 'converted', 'completed', 'empty_supported_object', 'already_open_format'}
INFORMATIONAL = GOOD | {'ready', 'inventoried', 'available', 'supported', 'not_attempted', 'not_requested'}
OBJECT_EVENTS = {'native_object_outcome', 'object_committed', 'object_failed', 'seismic_outcome'}
MAX_IO_RETRY_ATTEMPT = 32  # Conservative ceiling; retry_io uses a small fixed delay sequence.


def atomic(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
    with tmp.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text); stream.flush(); os.fsync(stream.fileno())
    tmp.replace(path)


def clean(value, key='', depth=0):
    if SECRETS.search(key):
        return '[redacted]'
    if key.casefold() in PAYLOAD:
        return '[omitted: data payload]'
    if depth > 24:
        return '[omitted: diagnostic nesting limit]'
    if isinstance(value, dict):
        return {str(k): clean(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 200_000:
            return {'omitted': 'diagnostic list limit', 'length': len(value)}
        return [clean(v, key, depth + 1) for v in value]
    if isinstance(value, str):
        if len(value) > 1_000_000:
            return {'omitted': 'diagnostic string limit', 'characters': len(value)}
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def save_json(path, value):
    atomic(path, json.dumps(clean(value), indent=2, ensure_ascii=True, allow_nan=False) + '\n')


def safe_file(path, root):
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root):
        return False
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            return False
        if part == root:
            break
    return path.is_file()


def read_json(path, issues, root):
    try:
        if not safe_file(path, root):
            raise OSError('Linked or missing diagnostic file')
        if path.stat().st_size > JSON_LIMIT:
            raise ValueError('Diagnostic JSON exceeds 64 MiB limit; original retained')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if PIPELINE_TIMING_NAME.fullmatch(Path(path).name):
            if not isinstance(value, list):
                raise ValueError('Expected pipeline timing journal array')
            return {'stages': value}
        if Path(path).name == 'pipeline_stages.json' and isinstance(value, list):
            return {'stages': value}
        if not isinstance(value, dict):
            raise ValueError('Expected diagnostic JSON object')
        return value
    except (OSError, ValueError) as exc:
        issues.append({'file': str(path), 'reason': str(exc)})
        return {}


def lines(path, issues, root):
    try:
        if not safe_file(path, root):
            raise OSError('Linked or missing event file')
        with path.open('rb') as stream:
            number = 0
            while True:
                raw = stream.readline(LINE_LIMIT + 1)
                if not raw:
                    break
                number += 1
                if len(raw) > LINE_LIMIT:
                    while raw and not raw.endswith(b'\n'):
                        raw = stream.readline(LINE_LIMIT + 1)
                    issues.append({'file': str(path), 'line': number, 'reason': 'Oversized event line; original retained'})
                    continue
                try:
                    row = json.loads(raw.decode('utf-8-sig'))
                    if not isinstance(row, dict):
                        raise ValueError('Event is not an object')
                    yield number, row
                except (ValueError, UnicodeError) as exc:
                    issues.append({'file': str(path), 'line': number, 'reason': str(exc)})
    except OSError as exc:
        issues.append({'file': str(path), 'reason': str(exc)})


def normalized_reason(message):
    message = str(message)
    message = re.sub(r'(?i)[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', '<object>', message)
    message = re.sub(r'''["'][A-Za-z]:[\\/][^"'\r\n]*["']''', '<path>', message)
    message = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\s,;\r\n]+', '<path>', message)
    message = re.sub(r'\b0x[0-9a-fA-F]+\b', '<hex>', message)
    message = re.sub(r'\b\d+\b', '<n>', message)
    return ' '.join(message.split())[:2000]


def issue_signature(row):
    error = row.get('error') if isinstance(row.get('error'), dict) else {}
    kind = row.get('blob_type') or row.get('type') or row.get('category') or 'unknown'
    reason = row.get('reason') or error.get('message') or (row.get('error') if isinstance(row.get('error'), str) else None) or row.get('message') or row.get('status') or 'unspecified'
    identity = dict(type=kind, operation=error.get('operation') or row.get('operation'),
                    profile=row.get('decoder_profile'), serialization_version=row.get('object_version'),
                    attempted_decoder_profile=row.get('attempted_decoder_profile'),
                    exception_type=error.get('exception_type') or row.get('exception_type'),
                    winerror=error.get('winerror', row.get('winerror')),
                    errno=error.get('errno', row.get('errno')), reason=normalized_reason(reason))
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:20]
    return signature, identity


def decoder_evidence(record, lane):
    """Normalize recorded dimensions without promoting a version/envelope to a decoder."""
    error = record.get('error') if isinstance(record.get('error'), dict) else {}
    result = record.get('result') if isinstance(record.get('result'), dict) else {}
    family = record.get('decoder_family') or result.get('operation')
    basis = 'recorded' if family else None
    if not family and lane in ('native', 'spatial') and record.get('blob_type'):
        family = lane + ':' + record['blob_type']
        basis = 'report lane and object type; applicable family only'
    envelope = record.get('envelope')
    return dict(decoder_family=family, decoder_family_basis=basis,
                registry_version=record.get('registry_version', record.get('version') if lane == 'spatial' else None),
                serialization_version=record.get('object_version'),
                envelope=envelope.get('envelope') if isinstance(envelope, dict) else envelope,
                attempted_decoder_profile=record.get('attempted_decoder_profile', error.get('attempted_decoder_profile')),
                decoder_profile=record.get('decoder_profile') or (record.get('decoder') if record.get('status') in GOOD else None),
                failure_checkpoint=record.get('failure_checkpoint') or error.get('operation'))


def valid_seconds(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def io_retry_identity(event):
    """Return an exact retry identity and attempt, or None when correlation is unsafe."""
    text_fields = ('operation', 'source', 'destination')
    if any(not isinstance(event.get(name), str) or not event[name] for name in text_fields):
        return None
    pid = event.get('pid')
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return None
    attempt = event.get('attempt')
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= MAX_IO_RETRY_ATTEMPT:
        return None
    return tuple(event[name] for name in text_fields) + (pid,), attempt


def diagnostic_documents(output):
    """Fixed metadata locations: never recurse through native_data or copied stores."""
    for run in output.glob('*_data'):
        for name in ('RUN_RESULT.json', 'PROFILE_RESULT.json', 'request.json', 'preflight.json', 'seismic_results.json', 'qc/result.json', 'extraction/result.json'):
            path = run / name
            if path.is_file():
                yield path
        yield from (path for path in (run / 'extraction/package').glob('pipeline_timings_*.json')
                    if PIPELINE_TIMING_NAME.fullmatch(path.name))
        for package in (run / 'extraction/package').glob('*'):
            for relative in (
                '01_project_metadata/project_context.json',
                '01_project_metadata/native_compatibility.json',
                '01_project_metadata/project_seismic_inventory.json',
                '01_project_metadata/native_crs_summary.json',
                '01_project_metadata/extraction_options.json',
                '01_project_metadata/pipeline_stages.json',
                '01_project_metadata/stage_budgets.json',
                '07_workflows_reports/native_recovery/native_recovery_report.json',
                '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
                '07_workflows_reports/portable_extractor/companion_capability_report.json'):
                path = package / relative
                if path.is_file():
                    yield path
            spatial_reports = package / '07_workflows_reports/native_spatial_zero_gui'
            yield from (path for path in spatial_reports.glob('native_spatial_decode_failure_*.json')
                        if SPATIAL_FAILURE_NAME.fullmatch(path.name))
    yield from output.glob('*_EXPORTS/FILE_INDEX.json')


def diagnostic_event_paths(output, documents, issues):
    """Only exact receipt references to known event sinks inside this output."""
    paths = list(sorted(output.glob('*_EVENTS.jsonl')))
    for relative, receipt in documents.items():
        if Path(relative).name != 'RUN_RESULT.json' or 'structured_events' not in receipt:
            continue
        value = receipt['structured_events']
        try:
            if not isinstance(value, str) or not value:
                raise ValueError('Invalid structured_events reference')
            path = Path(value)
            if not path.is_absolute():
                path = output / path
            rel = path.resolve().relative_to(output.resolve())
            allowed = (len(rel.parts) == 1 and rel.name.endswith('_EVENTS.jsonl')) or (
                len(rel.parts) == 3 and rel.parts[0] == '_scratch' and
                rel.parts[1].startswith('GeoViewer_diagnostics_') and rel.name == 'EVENTS.jsonl')
            if not allowed:
                raise ValueError('Untrusted diagnostic event path; only known in-output sinks are allowed')
            if not safe_file(path, output):
                raise ValueError('Referenced diagnostic event file missing or linked')
            path = output / rel  # Keep locators in the caller's output spelling (mapped drive or UNC).
            if path.resolve() not in {p.resolve() for p in paths}:
                paths.append(path)
        except (ValueError, OSError) as exc:
            issues.append({'file': relative, 'reference': value,
                           'reason': 'Rejected or missing structured_events reference: ' + str(exc)})
    return paths


def event_scope(event, path):
    run, pid = event.get('run_id'), event.get('pid')
    if isinstance(run, str) and run and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        scope = ('run', run, pid)
        # Transport identity stays authoritative. Forwarded origin claims are a
        # separate discriminator, never proof that a process exists or is alive.
        # Without it, different grandchildren sharing one pipe merge stage/retry
        # clocks. Collapse redundant wrappers, retaining the actual origin chain.
        origins = []
        reported = event.get('child_reported_metadata')
        for _ in range(16):
            if not isinstance(reported, dict):
                break
            claimed = reported.get('pid')
            if isinstance(claimed, int) and not isinstance(claimed, bool) and claimed > 0:
                if claimed != (origins[-1] if origins else pid):
                    origins.append(claimed)
            reported = reported.get('child_reported_metadata')
        if isinstance(reported, dict):
            # Excessively nested claims cannot safely be joined across events.
            return scope + ('unresolved_origin', str(path), event.get('event_id'), event.get('sequence'))
        return scope + ('reported_origin', tuple(origins)) if origins else scope
    return ('file', str(path), str(run), str(pid))


def diagnostic_events(paths, issues, output):
    """Merge ordered writer segments by sequence, retaining legacy file order."""
    def segment(path, index):
        for number, event in lines(path, issues, output):
            sequence = event.get('sequence')
            ordered = isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > 0
            yield (sequence if ordered else float('inf'), index, number, path, event)
    seen = {}
    for _, _, number, path, event in heapq.merge(*(segment(p, i) for i, p in enumerate(paths)),
                                                key=lambda row: row[:3]):
        event_id = event.get('event_id')
        if isinstance(event_id, str) and event_id:
            identity = (event_scope(event, path), event_id)
            fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            if identity in seen:
                if seen[identity] != fingerprint:
                    issues.append({'file': str(path), 'line': number,
                                   'reason': 'Conflicting duplicate event_id; first event retained'})
                continue
            seen[identity] = fingerprint
        yield path, number, event


def metrics_summary(path, issues):
    if not path or not Path(path).is_file():
        return {'status': 'not_recorded', 'reason': 'No live process metrics in this attempt'}
    maximums = {}
    samples = 0
    peak_private = peak_working = 0
    free = []
    unavailable = 0
    for _, row in lines(Path(path), issues, Path(path).parent):
        samples += 1
        processes = row.get('processes', [])
        peak_private = max(peak_private, sum(p.get('private_bytes', 0) for p in processes))
        peak_working = max(peak_working, sum(p.get('working_set_bytes', 0) for p in processes))
        unavailable += len(row.get('unavailable', []))
        if isinstance(row.get('output_free_bytes'), int):
            free.append(row['output_free_bytes'])
        for p in processes:
            # PID + creation time avoids combining recycled process identifiers.
            key = (p['pid'], p.get('created_ticks'))
            item = maximums.setdefault(key, {})
            for field in ('cpu_seconds', 'read_bytes', 'write_bytes', 'read_operations', 'write_operations'):
                if field in p:
                    item[field] = max(item.get(field, 0), p[field])
    return dict(status='sampled' if maximums else 'unavailable', sample_count=samples,
                observed_processes=len(maximums), unavailable_process_samples=unavailable,
                sampled_peak_tree_private_bytes=peak_private if maximums else None,
                sampled_peak_tree_working_set_bytes=peak_working if maximums else None,
                sampled_process_totals={field: sum(r.get(field, 0) for r in maximums.values())
                                        for field in ('cpu_seconds', 'read_bytes', 'write_bytes', 'read_operations', 'write_operations')},
                minimum_output_free_bytes=min(free) if free else None,
                output_free_change_bytes=free[-1] - free[0] if len(free) > 1 else None,
                caveat='Sampled lower bounds; short children/peaks may be missed. Working sets can share pages. Logical I/O is not disk/network throughput. Free-space change includes other applications.')


def analyze_case(campaign, project, attempt, destination):
    started = time.monotonic()
    output, destination = Path(attempt['output']), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    issues, evidence, documents = [], [], {}
    objects = {}
    failures = {}
    hashes = {}
    stages, prior_stage = [], {}
    incomplete_timings, seen_timings = [], set()
    timing_journal_count = 0
    environment = {}
    context = {}
    event_counts = Counter()
    event_objects = {}
    pending_io_retries = {}
    context_path = None
    artifact_summary = {'status': 'not_recorded'}
    category_reports = []

    def add_failure(row, locator, owner=''):
        signature, identity = issue_signature(row)
        key = (signature, owner or str(row.get('object_id', '')), str(locator))
        failures[key] = dict(signature=signature, normalized=identity, object_id=row.get('object_id'),
                             owner=owner, evidence=locator, record=clean(row), resolution='unresolved')
        return failures[key]

    def track_io_event(kind, event, locator, occurrence):
        identified = io_retry_identity(event)
        if identified is None:
            return
        identity, attempt_number = identified
        identity = (current_event_scope, identity)
        if kind in ('io_error', 'retry_scheduled') and occurrence is not None:
            state = pending_io_retries.get(identity)
            if state is None or attempt_number < state['last_attempt'] or attempt_number > state['last_attempt'] + 1:
                state = {'first_attempt': attempt_number, 'last_attempt': attempt_number,
                         'attempts': defaultdict(list), 'kinds': defaultdict(set), 'ambiguous': False}
                pending_io_retries[identity] = state
            elif kind in state['kinds'][attempt_number]:
                # A repeated retry marker can be a later operation with the same paths and PID.
                state['ambiguous'] = True
            state['last_attempt'] = max(state['last_attempt'], attempt_number)
            state['attempts'][attempt_number].append(occurrence)
            state['kinds'][attempt_number].add(kind)
        elif kind == 'io_recovered':
            state = pending_io_retries.pop(identity, None)
            if not state or state['ambiguous'] or state['first_attempt'] != 1 or attempt_number != state['last_attempt'] + 1:
                return
            if set(state['attempts']) != set(range(1, attempt_number)):
                return
            recovery = clean(event)
            for records in state['attempts'].values():
                for record in records:
                    record.update(resolution='recovered', recovery_evidence=locator, recovery_record=recovery)
        elif kind == 'io_failed':
            pending_io_retries.pop(identity, None)

    def add_objects(rows, lane, locator):
        if not isinstance(rows, list):
            issues.append({'file': locator, 'reason': 'Expected object list'})
            return
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                issues.append({'file': locator, 'index': index, 'reason': 'Malformed object record'})
                continue
            identity = str(row.get('object_id') or row.get('id') or ('unidentified_' + str(index)))
            kind = row.get('blob_type') or row.get('type') or row.get('category') or ('seismic_file' if lane == 'seismic_physical' else 'unknown')
            key = (lane, identity, str(kind))
            objects[key] = dict(lane=lane, object_id=identity, object_type=kind, status=row.get('status', 'unknown'),
                                evidence=locator + '#objects[' + str(index) + ']', record=clean(row))

    for path in diagnostic_documents(output):
        data = read_json(path, issues, output)
        relative = path.relative_to(output).as_posix()
        evidence.append({'path': relative, 'bytes': path.stat().st_size if path.is_file() else None,
                         'loaded': bool(data)})
        documents[relative] = data
        if path.name == 'project_context.json' and data:
            context, context_path = data, relative
        elif path.name == 'RUN_RESULT.json' and not context:
            context = data.get('project_context') if isinstance(data.get('project_context'), dict) else {}
            context_path = relative + '#project_context'
        elif path.name == 'native_recovery_report.json':
            add_objects(data.get('objects', []), 'native', relative)
            category_reports.append(dict(lane='native', evidence=relative,
                                         summary=clean({k: v for k, v in data.items() if k != 'objects'})))
        elif path.name == 'native_spatial_decode_report.json':
            add_objects(data.get('objects', []), 'spatial', relative)
            category_reports.append(dict(lane='spatial', evidence=relative,
                                         summary=clean({k: v for k, v in data.items() if k != 'objects'})))
        elif SPATIAL_FAILURE_NAME.fullmatch(path.name) and data:
            if (data.get('tool') == 'export_petrel_native_spatial_zero_gui.py'
                    and data.get('status') == 'failed'):
                category_reports.append(dict(lane='spatial', evidence=relative,
                    evidence_kind='invocation_failure',
                    summary=clean({k: v for k, v in data.items() if k != 'objects'})))
                issues.append({'file': relative,
                    'reason': 'Spatial invocation failed; preserved object outcomes are separate evidence '
                              'and do not prove this invocation completed or its outputs passed final QC'})
            else:
                issues.append({'file': relative, 'reason': 'Invalid spatial failure receipt identity/status'})
        elif path.name in ('seismic_results.json', 'project_seismic_inventory.json'):
            # Prefer final seismic_results over the earlier discovery snapshot.
            if path.name == 'seismic_results.json' or not any(k[0] == 'seismic_physical' for k in objects):
                add_objects(data.get('objects', []), 'seismic_physical', relative)
        elif path.name == 'FILE_INDEX.json':
            files = data.get('files', [])
            artifact_summary = dict(status='recorded', evidence=relative, file_count=len(files),
                logical_bytes=sum(r.get('bytes', 0) or 0 for r in files),
                formats=dict(Counter(str(r.get('format', 'unknown')) for r in files)),
                categories=dict(Counter(str(r.get('category', 'unknown')) for r in files)),
                numerical_qc=dict(Counter(str(r.get('numerical_qc', 'unknown')) for r in files)),
                delivery_modes=dict(Counter(str(r.get('storage', 'unknown')) for r in files)),
                findings=clean(data.get('findings', [])),
                caveat='Logical indexed bytes, not physical allocation; hard links may share storage')
        is_timing_journal = PIPELINE_TIMING_NAME.fullmatch(path.name) is not None
        if is_timing_journal:
            timing_journal_count += 1
            if not data.get('stages'):
                issues.append({'file': relative, 'reason': 'Pipeline timing journal is empty or invalid; no stage timing evidence'})
        if path.name == 'stage_budgets.json' or is_timing_journal:
            key = 'attempts' if path.name == 'stage_budgets.json' else 'stages'
            for i, timing in enumerate(data.get(key, [])):
                if not isinstance(timing, dict):
                    issues.append({'file': relative, 'reason': 'Invalid stage timing row; expected object', 'row': i})
                    continue
                locator = relative + '#' + key + '[' + str(i) + ']'
                identity = (timing.get('pipeline_run_id'), timing.get('stage_id'))
                if all(identity):
                    if identity in seen_timings:
                        continue
                    seen_timings.add(identity)
                common = dict(stage=timing.get('category', timing.get('lane', 'unknown')),
                              evidence=locator, start_evidence=locator,
                              timing_basis='explicit stage receipt; monotonic elapsed',
                              **{k: timing.get(k) for k in ('started_at_utc','ended_at_utc','status',
                                  'stage_id','run_id','pipeline_run_id','runner_pid','parent_pid','exit_code')})
                duration = timing.get('elapsed_seconds')
                if (timing.get('started_at_utc') and timing.get('ended_at_utc') and
                        timing.get('status') in ('completed','completed_with_gaps','failed','interrupted','interrupted_budget') and
                        valid_seconds(duration)):
                    stages.append(dict(common, seconds=duration))
                elif timing.get('status') != 'not_attempted_budget':
                    incomplete_timings.append(dict(common, label=common['stage'], seconds=None))
        if path.name == 'pipeline_stages.json':
            for i, stage in enumerate(data.get('stages', [])):
                if isinstance(stage, dict) and stage.get('status') in ('failed', 'partial'):
                    add_failure(stage, relative + '#stages[' + str(i) + ']', 'stage_receipt')
        if data.get('error') or data.get('reason') and data.get('status') not in INFORMATIONAL:
            add_failure(data, relative, 'receipt')

    # Consume each authoritative event file once, never console reprints of it.
    event_paths = diagnostic_event_paths(output, documents, issues)
    for path in event_paths:
        evidence.append({'path': path.relative_to(output).as_posix(), 'kind': 'structured_events'})
    if not timing_journal_count:
        issues.append({'reason': 'Explicit pipeline timing journal missing; unbounded stage timing evidence is unavailable'})
    if not event_paths:
        issues.append({'reason': 'Structured event log missing; event-derived stage/hash timing is unavailable'})
    for path, number, event in diagnostic_events(event_paths, issues, output):
        current_event_scope = event_scope(event, path)
        kind = event.get('event', 'unknown')
        event_counts[kind] += 1
        locator = path.relative_to(output).as_posix() + ':' + str(number)
        if kind == 'environment':
            environment = {k: v for k, v in event.items() if k not in ('event', 'schema', 'sequence', 'event_id')}
        if kind == 'native_preflight' and not context:
            context = {'layout': event.get('layout'), 'saved_version': event.get('saved_version'),
                       'findings': event.get('findings')}
            context_path = locator
        if kind in OBJECT_EVENTS and event.get('object_id'):
            event_objects[str(event['object_id'])] = (event, locator)
        occurrence = None
        if event.get('severity') in ('warning', 'error'):
            # Each event remains locatable; campaign ranking uses distinct cases, not event counts.
            occurrence = add_failure(event, locator, 'event')
        track_io_event(kind, event, locator, occurrence)
        if kind == 'stage':
            pid = event.get('pid')
            clock_key = current_event_scope
            previous = prior_stage.get(clock_key) if pid is not None else None
            if previous:
                seconds = event.get('previous_stage_seconds')
                basis = 'reported previous stage duration'
                if not valid_seconds(seconds) and valid_seconds(previous['elapsed']) and valid_seconds(event.get('elapsed_seconds')):
                    seconds = event['elapsed_seconds'] - previous['elapsed']
                    basis = 'event-clock interval to next stage'
                if valid_seconds(seconds):
                    stages.append(dict(stage=previous['label'], seconds=seconds, start_evidence=previous['evidence'],
                                       evidence=locator, timing_basis=basis, pid=pid))
                else:
                    issues.append({'file': locator, 'reason': 'Stage interval timing invalid or missing; duration remains unknown'})
                    incomplete_timings.append(dict(previous, seconds=None))
            prior_stage[clock_key] = {'label': event.get('label', 'unknown'), 'elapsed': event.get('elapsed_seconds'), 'evidence': locator}
        if kind in ('completed', 'run_finished', 'failed', 'cancelled'):
            clock_key = current_event_scope
            previous = prior_stage.pop(clock_key, None) if event.get('pid') is not None else None
            if previous and valid_seconds(previous['elapsed']) and valid_seconds(event.get('elapsed_seconds')):
                seconds = event['elapsed_seconds'] - previous['elapsed']
                if valid_seconds(seconds):
                    stages.append(dict(stage=previous['label'], seconds=seconds, start_evidence=previous['evidence'],
                                       evidence=locator, timing_basis='event-clock interval to terminal event', pid=event['pid']))
                else:
                    issues.append({'file': locator, 'reason': 'Terminal stage interval timing invalid; duration remains unknown'})
                    incomplete_timings.append(dict(previous, seconds=None))
            elif previous:
                issues.append({'file': locator, 'reason': 'Terminal stage interval timing missing; duration remains unknown'})
                incomplete_timings.append(dict(previous, seconds=None))
        if kind == 'hash_completed':
            key = (str(event.get('path', 'unknown')), event.get('bytes'))
            row = hashes.setdefault(key, dict(path=key[0], bytes_per_pass=key[1], passes=0, seconds=0.0, evidence=locator))
            row['passes'] += 1
            row['seconds'] += event.get('seconds', 0) or 0

    ids_with_receipts = {row['object_id'] for row in objects.values()}
    for identity, (event, locator) in event_objects.items():
        if identity not in ids_with_receipts:
            lane = 'seismic_physical' if event.get('event') == 'seismic_outcome' else 'event_only'
            add_objects([event], lane, locator)
    for item in objects.values():
        if item['status'] not in INFORMATIONAL or item['record'].get('export_status') == 'partial':
            add_failure(item['record'], item['evidence'], 'object')

    # A parent timeout can stop a child before it writes its final receipt.
    if attempt.get('error'):
        kind = attempt.get('exception_type') or ('TimeoutError' if 'time limit exceeded' in attempt['error'] else 'RunnerError')
        add_failure(dict(category='runner', status='failed', exception_type=kind, error=attempt['error'],
                         traceback=attempt.get('traceback'), timeout_seconds=attempt.get('timeout_seconds'),
                         child_pid=attempt.get('child_pid'), active_stages=list(prior_stage.values()),
                         trace_directory=attempt.get('trace_directory')), 'ATTEMPT.json', 'runner')

    inventory = context.get('objects') if isinstance(context.get('objects'), list) else []
    inventory_counts = Counter(str(r.get('category') or r.get('type') or 'unknown') for r in inventory if isinstance(r, dict))
    native_counts = Counter((r['lane'], r['object_type'], str(r['status'])) for r in objects.values())
    coverage = [dict(lane=k[0], object_type=k[1], status=k[2], count=count) for k, count in sorted(native_counts.items())]
    profiles = Counter()
    timed_objects = []
    for item in objects.values():
        record = item['record']
        detail = item['decoder_evidence'] = decoder_evidence(record, item['lane'])
        dimensions = ('registry_version', 'decoder_profile', 'serialization_version', 'envelope',
                      'decoder_family', 'attempted_decoder_profile', 'failure_checkpoint')
        profiles[(item['lane'], str(item['object_type']), str(item['status']),
                  *(str(detail[k]) if detail[k] is not None else 'unknown' for k in dimensions))] += 1
        seconds = record.get('object_elapsed_seconds', record.get('elapsed_seconds'))
        if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
            timed_objects.append(dict(object_id=item['object_id'], object_type=item['object_type'], lane=item['lane'],
                                      seconds=seconds, status=item['status'], evidence=item['evidence']))
    profile_counts = [dict(lane=k[0], object_type=k[1], status=k[2], count=n, **dict(zip(dimensions, k[3:])))
                      for k, n in sorted(profiles.items())]
    for item in hashes.values():
        item['total_observed_bytes'] = (item['bytes_per_pass'] or 0) * item['passes']
        item['repeat_pass_bytes'] = (item['bytes_per_pass'] or 0) * max(0, item['passes'] - 1)
    inventory_file = Path(campaign) / 'inventories' / (project['id'] + '.json')
    file_stats = {'status': 'not_recorded'}
    try:
        rows = json.loads(inventory_file.read_text())
        ext = defaultdict(lambda: {'files': 0, 'bytes': 0})
        for r in rows:
            key = Path(r['relative']).suffix.lower() or '(none)'
            ext[key]['files'] += 1; ext[key]['bytes'] += r['size']
        file_stats = dict(status='recorded', extensions=dict(ext),
                          largest_native_files=sorted(rows, key=lambda r: r['size'], reverse=True)[:20],
                          longest_relative_path=max((len(r['relative']) for r in rows), default=0))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issues.append({'file': str(inventory_file), 'reason': str(exc)})
    recorded_version = context.get('saved_version') or {}
    version = recorded_version.get('value') if isinstance(recorded_version, dict) else None
    profile_missing = sum(r['decoder_evidence']['decoder_profile'] is None and r['decoder_evidence']['attempted_decoder_profile'] is None
                          for r in objects.values())
    if profile_missing:
        issues.append({'reason': 'Attempted/accepted decoder profile was not recorded for some outcomes; serialization and envelope evidence are separate', 'objects': profile_missing})
    if incomplete_timings:
        issues.append({'reason': 'Explicit stage receipt has no terminal timing proof; duration remains unknown', 'stages': len(incomplete_timings)})
    if prior_stage:
        issues.append({'reason': 'Stage start has no matching next-stage or terminal event; duration remains unknown', 'stages': len(prior_stage)})
    if not context:
        issues.append({'reason': 'Project metadata absent; saved version and complete inventory unknown'})
    terminal = attempt.get('receipt_status', 'unknown')
    if (attempt.get('exit_code') == 0 and terminal == 'completed') or (attempt.get('exit_code') in (0, 10) and terminal == 'completed_with_gaps'):
        status = terminal
    else:
        status = 'cancelled' if attempt.get('exit_code') == 130 else 'failed'
    campaign_state = read_json(Path(campaign) / 'CAMPAIGN.json', issues, Path(campaign))
    case = dict(schema=SCHEMA, created_utc=datetime.now(timezone.utc).isoformat(),
        project_id=project['id'], attempt=attempt['number'], status=status,
        scenario=project.get('lab_scenario', 'full_extract'), source_project_id=project.get('source_project_id', project['id']),
        conversion_tested=project.get('lab_scenario') != 'metadata_profile' and
                          bool(any(r['lane'] != 'seismic_physical' or r['status'] in ('converted', 'conversion_failed')
                                   for r in objects.values()) or event_counts.get('object_started') or artifact_summary.get('file_count')),
        conversion_requested=project.get('lab_scenario') != 'metadata_profile',
        selection_features=project.get('selection_features', []),
        receipt_status=terminal, exit_code=attempt.get('exit_code'),
        source=dict(pet=project['pet'], total_native_bytes=project.get('total_bytes'),
                    file_count=project.get('file_count'), path_characters=len(project['pet']),
                    saved_version=str(version) if version else 'unknown', saved_version_evidence=recorded_version,
                    layout=context.get('layout') or 'unknown', context_evidence=context_path,
                    file_statistics=file_stats),
        metadata=clean({k: context.get(k) for k in ('original_version', 'last_native_save', 'build_label',
                    'display_units', 'object_inventory_complete', 'model_metadata_complete', 'model_records_read',
                    'model_record_error_count', 'model_record_errors', 'findings', 'native_decoders_applicable')}),
        settings=clean({'command': attempt.get('command'), 'extractor_directory': attempt.get('extractor_directory'),
                       'extractor_manifest_sha256': attempt.get('extractor_manifest_sha256') or campaign_state.get('extractor_manifest_sha256'),
                       'diagnostic_analyzer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'discovery_roots': campaign_state.get('roots'), 'drive_aliases': campaign_state.get('root_aliases'),
                       'campaign_config': campaign_state.get('config', {})}),
        environment=clean(environment), source_inventory_counts=dict(inventory_counts),
        recorded_inventory_objects=len(inventory), decoder_record_counts=coverage,
        decoder_profile_counts=profile_counts,
        whole_project_recovery_percentage=None,
        coverage_boundary='Native metadata inventory and decoder-selected records are different denominators. No universal recovery percentage or cross-version support claim.',
        artifacts=artifact_summary, category_reports=category_reports,
        performance=dict(elapsed_seconds=attempt.get('elapsed_seconds'), stages=stages,
                         stages_without_terminal_event=list(prior_stage.values()) + incomplete_timings,
                         trace_directory=attempt.get('trace_directory'),
                         timed_object_count=len(timed_objects), slowest_objects=sorted(timed_objects, key=lambda r: r['seconds'], reverse=True)[:50],
                         completed_hash_calls=sum(r['passes'] for r in hashes.values()),
                         observed_hash_bytes=sum(r['total_observed_bytes'] for r in hashes.values()),
                         repeated_hash_bytes=sum(r['repeat_pass_bytes'] for r in hashes.values()),
                         hashes=sorted(hashes.values(), key=lambda r: r['seconds'], reverse=True),
                         process_metrics=metrics_summary(attempt.get('metrics_file'), issues),
                         capacity_checks=project.get('capacity_checks', []),
                         metrics_collection=attempt.get('metrics_collection'), metrics_error=attempt.get('metrics_error'),
                         caveat='Stage/hash intervals may overlap and are not additive. Repeated hashes may be required integrity checks, not redundant work.'),
        event_counts=dict(event_counts),
        error_occurrence_counts=dict(Counter(r.get('resolution', 'unresolved') for r in failures.values())),
        evidence_files=evidence, evidence_gaps=issues,
        report=project.get('latest_report'), output=str(output),
        collection_scope='Existing metadata, receipts, events and process counters only; no source payloads or converted sample arrays read',
        collector_seconds=round(time.monotonic() - started, 3))
    save_json(destination / 'CASE_DIAGNOSTICS.json', case)
    atomic(destination / 'OBJECT_OUTCOMES.jsonl', ''.join(json.dumps(clean(r), ensure_ascii=True) + '\n' for r in objects.values()))
    atomic(destination / 'INVENTORY_OBJECTS.jsonl', ''.join(json.dumps(clean(r), ensure_ascii=True) + '\n' for r in inventory))
    atomic(destination / 'ERROR_OCCURRENCES.jsonl', ''.join(json.dumps(clean(r), ensure_ascii=True) + '\n' for r in failures.values()))
    # Retain useful metadata that the original support ZIP omits, without arrays.
    save_json(destination / 'DIAGNOSTIC_RECEIPTS.json', documents)
    return dict(path=str(destination / 'CASE_DIAGNOSTICS.json'), objects=len(objects),
                error_occurrences=len(failures), evidence_gaps=len(issues), collector_seconds=case['collector_seconds'])


def csv_file(path, rows, columns):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        safe = {key: (json.dumps(value) if isinstance(value, (dict, list)) else value) for key, value in row.items()}
        for key, value in safe.items():
            if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
                safe[key] = "'" + value
        writer.writerow(safe)
    atomic(path, stream.getvalue())


def campaign_analysis(campaign, state):
    campaign = Path(campaign)
    destination = campaign / 'LOGS' / 'ANALYSIS'; destination.mkdir(parents=True, exist_ok=True)
    cases, warnings, groups = [], [], {}
    coverage, profiles = Counter(), Counter()
    occurrence_statuses = Counter()
    profile_dimensions = ('registry_version', 'decoder_profile', 'serialization_version', 'envelope',
                          'decoder_family', 'attempted_decoder_profile', 'failure_checkpoint')
    attempts_total = sum(len(p['attempts']) for p in state['projects'])
    for project in state['projects']:
        if not project['attempts']:
            continue
        attempt = project['attempts'][-1]  # Latest attempt per project, never inflate coverage with retries.
        folder = campaign / 'LOGS' / project['id'] / ('attempt_' + str(attempt['number']).zfill(3))
        case = read_json(folder / 'CASE_DIAGNOSTICS.json', warnings, campaign)
        if not case:
            continue
        cases.append(case)
        version = case['source']['saved_version']
        layout = case['source']['layout']
        for row in case['decoder_record_counts']:
            coverage[(version, layout, row['lane'], row['object_type'], row['status'])] += row['count']
        for row in case.get('decoder_profile_counts', []):
            profiles[(version, layout, row['lane'], row['object_type'], row['status'],
                      *(row.get(k, 'unknown') for k in profile_dimensions))] += row['count']
        per_case = set()
        for _, occurrence in lines(folder / 'ERROR_OCCURRENCES.jsonl', warnings, campaign):
            sig = occurrence['signature']
            resolution = occurrence.get('resolution')
            if resolution not in ('recovered', 'unresolved'):
                resolution = 'unresolved'
            occurrence_statuses[resolution] += 1
            group = groups.setdefault(sig, dict(signature=sig, normalized=occurrence['normalized'],
                occurrences=0, recovered_occurrences=0, unresolved_occurrences=0,
                cases=[], recovered_cases=[], unresolved_cases=[], versions=[], examples=[]))
            group['occurrences'] += 1
            group[resolution + '_occurrences'] += 1
            if project['id'] not in group['cases']:
                group['cases'].append(project['id'])
            status_cases = group[resolution + '_cases']
            if project['id'] not in status_cases:
                status_cases.append(project['id'])
            if version not in group['versions']:
                group['versions'].append(version)
            if len(group['examples']) < 3 and sig not in per_case:
                group['examples'].append({'project': project['id'], 'case_path': str(folder / 'CASE_DIAGNOSTICS.json'),
                                          'evidence': occurrence['evidence'], 'record': occurrence['record']})
            per_case.add(sig)
    issues = sorted(groups.values(), key=lambda r: (-len(r['unresolved_cases']), -r['unresolved_occurrences'],
                                                     -len(r['recovered_cases']), -r['recovered_occurrences'], r['signature']))
    matrix = [dict(saved_version=k[0], layout=k[1], lane=k[2], object_type=k[3], status=k[4], objects=n)
              for k, n in sorted(coverage.items())]
    profile_matrix = [dict(saved_version=k[0], layout=k[1], lane=k[2], object_type=k[3], status=k[4], objects=n,
                           **dict(zip(profile_dimensions, k[5:])))
                      for k, n in sorted(profiles.items())]
    case_rows = [dict(project_id=c['project_id'], attempt=c['attempt'], status=c['status'],
                      scenario=c.get('scenario', 'full_extract'), source_project_id=c.get('source_project_id', c['project_id']),
                      saved_version=c['source']['saved_version'], layout=c['source']['layout'],
                      native_bytes=c['source']['total_native_bytes'], elapsed_seconds=c['performance']['elapsed_seconds'],
                      hash_bytes=c['performance']['observed_hash_bytes'], repeated_hash_bytes=c['performance']['repeated_hash_bytes'],
                      output_formats=c['artifacts'].get('formats'), evidence_gaps=len(c['evidence_gaps']),
                      peak_private_bytes=c['performance']['process_metrics'].get('sampled_peak_tree_private_bytes')) for c in cases]
    stage_rows = [dict(project_id=c['project_id'], attempt=c['attempt'], **r)
                  for c in cases for r in c['performance']['stages']]
    actions = []
    for group in issues[:50]:
        if not group['unresolved_occurrences']:
            continue
        reason = group['normalized']['reason'].lower()
        if any(x in reason for x in ('space', 'permission', 'sharing violation', 'network', 'disk')):
            area, next_step = 'storage/reliability', 'Inspect OS errors, source/output storage and retry traces in the linked cases.'
        elif any(x in reason for x in ('read-back', 'readback', 'round-trip', 'numeric', 'mismatch')):
            area, next_step = 'numerical QC', 'Compare exact failed checks and decoder profile before changing conversion.'
        elif any(x in reason for x in ('metadata', 'ambiguous', 'unit', 'reference', 'missing')):
            area, next_step = 'metadata/linkage', 'Inspect recorded metadata and unresolved IDs/units; obtain independent reference evidence if needed.'
        elif any(x in reason for x in ('profile', 'version', 'layout', 'serialization', 'unsupported')):
            area, next_step = 'decoder coverage', 'Compare type, registry/serialization profile and trace locations; add a fixture and validated parser profile.'
        else:
            area, next_step = 'diagnostic review', 'Review the original linked evidence; this grouped signature is not a proven root cause.'
        actions.append(dict(area=area, affected_cases=len(group['cases']), signature=group['signature'], suggestion=next_step))
    for c in cases:
        if c['performance']['repeated_hash_bytes']:
            actions.append(dict(area='performance', project=c['project_id'],
                observed_repeat_hash_bytes=c['performance']['repeated_hash_bytes'],
                suggestion='Review repeated hash passes and timings; retain source-change detection and required QC.'))
    summary = dict(schema='geoviewer.campaign-analysis/1', created_utc=datetime.now(timezone.utc).isoformat(),
        discovered_projects=len(state['projects']), unique_queued_or_attempted=sum(p['status'] not in ('duplicate', 'invalid_pair', 'inventory_incomplete') for p in state['projects']),
        queue_statuses=dict(Counter(p['status'] for p in state['projects'])), attempts_total=attempts_total,
        latest_attempts_analyzed=len(cases), analysis_warnings=warnings,
        cases=case_rows, version_type_matrix=matrix, decoder_profile_matrix=profile_matrix,
        error_occurrence_counts=dict(occurrence_statuses), grouped_issues=issues, investigation_candidates=actions,
        boundaries=['Latest attempt per project only in comparisons; prior attempts remain in LOGS.',
                    'Missing evidence and unattempted projects are not successes.',
                    'Versions are recorded native metadata, never inferred from names.',
                    'Issue grouping and suggestions are review aids, not proven causes or automatic decoder changes.',
                    'Different datasets, storage and sizes confound cross-version/performance comparisons.',
                    'No universal recovery percentage; inventory and decoder records have different scopes.'])
    save_json(destination / 'CAMPAIGN_ANALYSIS.json', summary)
    csv_file(destination / 'CASES.csv', case_rows, ['project_id', 'source_project_id', 'scenario', 'attempt', 'status', 'saved_version', 'layout', 'native_bytes', 'elapsed_seconds', 'hash_bytes', 'repeated_hash_bytes', 'peak_private_bytes', 'output_formats', 'evidence_gaps'])
    csv_file(destination / 'STAGE_TIMINGS.csv', stage_rows, ['project_id', 'attempt', 'stage', 'status', 'seconds',
             'started_at_utc', 'ended_at_utc', 'run_id', 'pipeline_run_id', 'stage_id', 'runner_pid', 'timing_basis', 'evidence'])
    csv_file(destination / 'VERSION_TYPE_MATRIX.csv', matrix, ['saved_version', 'layout', 'lane', 'object_type', 'status', 'objects'])
    csv_file(destination / 'DECODER_PROFILES.csv', profile_matrix, ['saved_version', 'layout', 'lane', 'object_type', *profile_dimensions, 'status', 'objects'])
    csv_file(destination / 'ISSUE_GROUPS.csv', [dict(signature=g['signature'], affected_cases=len(g['cases']), occurrences=g['occurrences'],
             unresolved_cases=len(g['unresolved_cases']), unresolved_occurrences=g['unresolved_occurrences'],
             recovered_cases=len(g['recovered_cases']), recovered_occurrences=g['recovered_occurrences'],
             cases=g['cases'], versions=g['versions'], **g['normalized']) for g in issues],
             ['signature', 'affected_cases', 'occurrences', 'unresolved_cases', 'unresolved_occurrences',
              'recovered_cases', 'recovered_occurrences', 'cases', 'versions', 'type', 'operation', 'profile',
              'exception_type', 'winerror', 'errno', 'reason'])
    esc = lambda x: html.escape(str(x))
    def table(headers, rows):
        return '<table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table>'
    page = '<!doctype html><meta charset="utf-8"><title>Field Viewer campaign diagnostics</title><style>body{font:15px system-ui;margin:28px;line-height:1.45}td,th{border:1px solid #ccc;padding:7px;text-align:left;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}li{margin:6px 0}code{background:#eee}a{color:#075985}</style>'
    page += '<h1>Field Viewer campaign diagnostics</h1><p><a href="https://saherlabs.dev/">saherlabs.dev</a> | <a href="../../INDEX.html">Project index</a></p>'
    # ANALYSIS is under LOGS, so INDEX is two levels above.
    page += '<p>' + esc(len(cases)) + ' latest attempts analyzed; ' + esc(attempts_total) + ' attempts recorded; ' + esc(len(issues)) + ' issue signatures; ' + esc(occurrence_statuses['unresolved']) + ' unresolved and ' + esc(occurrence_statuses['recovered']) + ' recovered occurrences.</p>'
    page += '<p><a href="CAMPAIGN_ANALYSIS.json">Complete analysis JSON</a> | <a href="CASES.csv">Cases CSV</a> | <a href="STAGE_TIMINGS.csv">Stage timings CSV</a> | <a href="VERSION_TYPE_MATRIX.csv">Version/type CSV</a> | <a href="DECODER_PROFILES.csv">Decoder profiles CSV</a> | <a href="ISSUE_GROUPS.csv">Issues CSV</a></p>'
    page += '<h2>Evidence boundaries</h2><ul>' + ''.join('<li>' + esc(x) + '</li>' for x in summary['boundaries']) + '</ul>'
    page += '<p>Metadata-profile cases inspect metadata only; they do not test numerical decoding or conversion.</p>'
    page += '<h2>Case comparison</h2>' + table(['Case', 'Scenario', 'Petrel saved version', 'Layout', 'Outcome', 'Seconds', 'Evidence gaps'],
        ([r['project_id'], r['scenario'], r['saved_version'], r['layout'], r['status'], r['elapsed_seconds'], r['evidence_gaps']] for r in case_rows))
    page += '<h2>Recorded stage timings</h2><p>Intervals may overlap and are not additive. Missing terminal timing proof remains unknown in case diagnostics.</p>' + table(
        ['Case', 'Stage', 'Outcome', 'Seconds', 'Timing basis'],
        ([r['project_id'], r['stage'], r.get('status', 'unknown'), r['seconds'], r['timing_basis']] for r in stage_rows))
    page += '<h2>Case evidence</h2><ul>'
    for c in cases:
        base = '../' + c['project_id'] + '/attempt_' + str(c['attempt']).zfill(3) + '/'
        page += '<li>' + esc(c['project_id']) + ': ' + ' | '.join('<a href="' + base + name + '">' + label + '</a>' for name, label in
              [('CASE_DIAGNOSTICS.json', 'diagnostics'), ('OBJECT_OUTCOMES.jsonl', 'object outcomes'), ('ERROR_OCCURRENCES.jsonl', 'error evidence'), ('DIAGNOSTIC_RECEIPTS.json', 'receipts')]) + '</li>'
    page += '</ul><h2>Recurring issues</h2>' + table(['Unresolved cases / occurrences', 'Recovered cases / occurrences', 'Type', 'Exception / OS code', 'Grouped reason'],
        ([str(len(g['unresolved_cases'])) + ' / ' + str(g['unresolved_occurrences']),
          str(len(g['recovered_cases'])) + ' / ' + str(g['recovered_occurrences']),
          g['normalized']['type'], str(g['normalized']['exception_type']) + ' / ' + str(g['normalized']['winerror']),
          g['normalized']['reason']] for g in issues))
    page += '<h2>Investigations to consider</h2><ul>' + ''.join('<li>' + esc(a['area'] + ': ' + a['suggestion']) + '</li>' for a in actions) + '</ul>'
    page += '<h2>Missing analysis evidence</h2><pre>' + esc(json.dumps(warnings, indent=2)) + '</pre>'
    atomic(destination / 'REPORT.html', page)
    return {'path': str(destination / 'REPORT.html'), 'latest_attempts_analyzed': len(cases),
            'issue_groups': len(issues),
            'unresolved_issue_groups': sum(bool(g['unresolved_occurrences']) for g in issues),
            'recovered_only_issue_groups': sum(not g['unresolved_occurrences'] for g in issues)}
