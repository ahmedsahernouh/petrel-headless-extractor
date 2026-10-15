# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded testing waves from saved crawler inventories; no drive scanning.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
import argparse
from collections import Counter
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_campaign as c
from geoviewer_case_diagnostics import csv_file

SCHEMA = 'geoviewer.test-lab/1'
DEFAULTS = dict(profile_cases=32, extraction_cases=8, extraction_native_budget_gib=40,
                max_project_gib=20, profile_timeout_seconds=180, extraction_timeout_hours=2,
                session_hours=4, reserve_free_gib=50, space_multiplier=3, full_hash=True,
                run_to_completion=False,
                diagnostic_sample_seconds=10, output_roots=[],
                exclude_path_patterns=['\\appdata\\local\\temp\\', '\\geoviewer_campaign_test_',
                    '\\geoviewer_diagnostics_test_', '\\build\\campaign_acceptance_',
                    '\\build\\two_stage_acceptance_', '\\build\\test_lab_acceptance_',
                    '\\petrel-headless-extractor\\tests\\', '\\petrel-headless-extractor\\build\\'],
                priority_path_patterns=[])


def read(path):
    return c.read_json(Path(path))


def validate_config(config):
    if not isinstance(config.get('run_to_completion', False), bool):
        raise ValueError('run_to_completion must be true or false')
    for key in ('profile_cases', 'extraction_cases'):
        if not isinstance(config[key], int) or not 1 <= config[key] <= 1000:
            raise ValueError(key + ' must be an integer between 1 and 1000')
    for key in ('extraction_native_budget_gib', 'max_project_gib', 'profile_timeout_seconds',
                'extraction_timeout_hours', 'session_hours'):
        if not isinstance(config[key], (int, float)) or config[key] <= 0:
            raise ValueError(key + ' must be positive')
    c.validate_config(dict(config, scan_connected_drives=True, extra_roots=[], exclude_roots=[]))


def features(project, rows, roots, config):
    path = project['pet']
    drive = Path(path).anchor
    root = next((r for r in roots if r['path'].casefold() == drive.casefold()), {})
    storage = 'shared' if str(root.get('canonical', '')).startswith('\\\\') or path.startswith('\\\\') else 'drive_type_unverified'
    n = project.get('total_bytes', 0)
    size = 'under_100MiB' if n < 100 * 1024**2 else '100MiB_to_1GiB' if n < 1024**3 else '1_to_10GiB' if n < 10 * 1024**3 else 'over_10GiB'
    extensions = {Path(r['relative']).suffix.casefold() for r in rows}
    names = {Path(r['relative']).name.casefold() for r in rows}
    result = {'drive:' + drive, 'storage:' + storage, 'size:' + size,
              'native_files:' + ('model_and_data' if {'model.ptd', 'data.ptd'} <= names else 'other_layout')}
    for suffix, label in [('.zgy', 'ZGY'), ('.zhz', 'surface_tiles'), ('.sgy', 'SEG-Y'), ('.segy', 'SEG-Y')]:
        if suffix in extensions:
            result.add('file_evidence:' + label)
    if any(ord(x) > 127 for x in path): result.add('path:unicode')
    if ' ' in path: result.add('path:spaces')
    if len(path) > 180: result.add('path:long')
    if any(x in path for x in '&()'): result.add('path:shell_characters')
    if re.search(r'202[3-9]', path): result.add('name_hint:recent_year_unverified')
    for pattern in config['priority_path_patterns']:
        if pattern.casefold() in path.casefold(): result.add('priority_path:' + pattern)
    version = project.get('saved_version')
    if version and version != 'unknown': result.add('recorded_version:' + str(version))
    return sorted(result)


def choose_wave(candidates, limit, budget=None, maximum=None):
    remaining = [r for r in candidates if r['eligibility'] == 'eligible' and (maximum is None or r['total_bytes'] <= maximum)]
    selected, covered, groups, used = [], set(), set(), 0
    while remaining and len(selected) < limit:
        allowed = [r for r in remaining if r['candidate_group'] not in groups and (budget is None or used + r['total_bytes'] <= budget)]
        if not allowed: break
        def rank(row):
            fresh = set(row['features']) - covered
            score = sum(10 if f.startswith('priority_path:') else 7 if f.startswith(('recorded_version:', 'payload_type:', 'profile_layout:'))
                        else 3 if f.startswith(('drive:', 'file_evidence:', 'native_files:')) else 1 for f in fresh)
            return (-score, row['total_bytes'], row['id'])
        winner = min(allowed, key=rank)
        selected.append(winner)
        covered.update(winner['features'])
        groups.add(winner['candidate_group'])
        used += winner['total_bytes']
        remaining.remove(winner)
    return selected


def job(row, scenario):
    suffix = '_P' if scenario == 'metadata_profile' else '_X'
    return dict(id=row['id'] + suffix, source_project_id=row['id'], pet=row['pet'], store=str(Path(row['pet']).with_suffix('.ptd')),
                total_bytes=row['total_bytes'], file_count=row['file_count'], status='queued', attempts=[],
                saved_version=row.get('saved_version', 'unknown'), lab_scenario=scenario,
                selection_features=row['features'], candidate_group=row['candidate_group'])


def review_candidates(lab):
    """Read optional review metadata; never reconstruct eligibility from saved jobs."""
    path = lab / 'CANDIDATES.json'
    try:
        candidates = read(path)
        if not isinstance(candidates, list):
            raise ValueError('Candidate catalogue must be a list')
        for index, row in enumerate(candidates):
            if not isinstance(row, dict):
                raise ValueError(f'Candidate {index} must be an object')
            for key in ('id', 'pet', 'eligibility', 'reason', 'candidate_group'):
                if not isinstance(row.get(key), str):
                    raise ValueError(f'Candidate {index}: {key} must be a string')
            for key in ('total_bytes', 'file_count'):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise ValueError(f'Candidate {index}: {key} must be a nonnegative integer')
            if not isinstance(row.get('features'), list) or not all(isinstance(v, str) for v in row['features']):
                raise ValueError(f'Candidate {index}: features must be a list of strings')
    except (OSError, ValueError, UnicodeError) as exc:
        # Only ancillary input errors are recoverable. Report/index writes and
        # extraction failures retain their ordinary error handling.
        diagnostic = dict(status='warning', candidate_metadata='unavailable', path=str(path),
                          exception_type=type(exc).__name__, error=str(exc),
                          message='Candidate metadata unavailable; candidate eligibility and full catalogue coverage cannot be reviewed. '
                                  'Saved CAMPAIGN jobs, results and selection evidence are retained; no discovery or reselection performed. '
                                  'Any existing CANDIDATES.csv is retained but is not refreshed or verified.')
        return None, diagnostic
    return candidates, dict(status='complete', candidate_metadata='available', path=str(path),
                            candidate_count=len(candidates))


def review(lab, state):
    candidates, diagnostic = review_candidates(lab)
    if candidates is not None:
        csv_file(lab / 'CANDIDATES.csv', candidates, ['id', 'pet', 'total_bytes', 'file_count', 'eligibility', 'reason', 'candidate_group', 'features'])
    c.write_json(lab / 'REVIEW_STATUS.json', diagnostic)
    if diagnostic['status'] == 'warning':
        print('TEST PLAN REVIEW WARNING: ' + diagnostic['message'] + ' ' + diagnostic['exception_type'] + ': ' + diagnostic['error'], flush=True)
    c.indexes(lab, state)
    profiles = [p for p in state['projects'] if p['lab_scenario'] == 'metadata_profile']
    extracts = [p for p in state['projects'] if p['lab_scenario'] == 'full_extract']
    esc = lambda v: html.escape(str(v))
    page = '<!doctype html><meta charset="utf-8"><title>Field Viewer testing plan</title><style>body{font:15px system-ui;margin:30px;line-height:1.5}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #bbb;text-align:left;overflow-wrap:anywhere}</style>'
    page += '<h1>Field Viewer testing plan</h1><p><a href="https://saherlabs.dev/">saherlabs.dev</a></p>'
    page += '<p>Frozen catalogue: ' + esc(state['source_catalogue']) + '. Discovery-complete flag at snapshot: ' + esc(state['source_catalogue_complete']) + '.</p>'
    page += '<p>Metadata profiling does not test numerical recovery. Full extraction uses the pinned local main-launcher engine and numerical QC. No Petrel/Ocean. No archive unpacking.</p>'
    if state['config'].get('run_to_completion', False):
        page += '<p><strong>Run to completion:</strong> no elapsed-time cutoff for case extraction; disk-space and parser checks remain active. One verified runtime session is reused. No automatic retries.</p>'
    page += '<p>' + ('<a href="CANDIDATES.csv">All candidates and selection reasons</a> | ' if candidates is not None else '')
    page += '<a href="INDEX.html">Jobs and reports</a> | <a href="LOGS/ANALYSIS/REPORT.html">Diagnostic comparisons (after runs)</a> | <a href="TEST_PLAN.md">Detailed testing plan</a></p>'
    if candidates is not None:
        page += '<p>Candidate statuses: ' + esc(dict(Counter(p['eligibility'] for p in candidates))) + '.</p>'
    else:
        page += '<p><strong>Review warning:</strong> ' + esc(diagnostic['message']) + ' ' + esc(diagnostic['exception_type'] + ': ' + diagnostic['error']) + ' <a href="REVIEW_STATUS.json">Review diagnostic</a>.</p>'
    page += '<p>Selected metadata profiles: ' + str(len(profiles)) + '; full extraction jobs: ' + str(len(extracts)) + '.'
    if candidates is not None:
        page += ' Suspected duplicates are selection groups only; unselected copies remain in the catalogue.'
    page += '</p>'
    page += '<table><tr><th>Case</th><th>Scenario</th><th>Source</th><th>GiB</th><th>Status</th><th>Selection evidence</th></tr>'
    for p in state['projects']:
        page += '<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in [p['id'], p['lab_scenario'], p['pet'], round(p['total_bytes']/1024**3, 3), p['status'], ', '.join(p['selection_features'])]) + '</tr>'
    page += '</table><p>Unknown versions stay unknown. File extensions/layout names are selection clues, not proof of decoded types or supported Petrel versions.</p>'
    (lab / 'TEST_PLAN.html').write_text(page, encoding='utf-8')


def prepare(catalogue, lab, config, toolkit):
    catalogue = catalogue.resolve()
    if catalogue.is_file(): catalogue = catalogue.parent
    source = read(catalogue / 'CAMPAIGN.json')
    if source.get('schema') != c.SCHEMA: raise ValueError('Expected crawler CAMPAIGN.json')
    validate_config(config)
    # Frozen existing metadata only. Never contact the source projects here.
    lab.mkdir(parents=True, exist_ok=False)
    (lab / 'inventories').mkdir()
    (lab / 'LOGS').mkdir()
    (lab / 'snapshots').mkdir()
    c.write_json(lab / 'snapshots/CRAWLER_STATE.json', source)
    manifest = toolkit / '00_manifest/toolkit_files.json'
    current_build = c.digest(manifest)
    if current_build != source['extractor_manifest_sha256']:
        raise ValueError('Catalogue belongs to a different extractor build')
    verified_duplicates = {p['id']: p.get('duplicate_of') for p in source['projects'] if p['status'] == 'duplicate'}
    log = catalogue / 'LOGS/campaign.jsonl'
    log_size = log.stat().st_size
    # Read only the bytes present at snapshot time, preserving live crawler ownership.
    with log.open('rb') as stream:
        for raw in stream.read(log_size).splitlines():
            try: event = json.loads(raw)
            except ValueError: continue
            if event.get('event') == 'duplicate_verified':
                verified_duplicates[event['project']] = event['duplicate_of']
    candidates = []
    for p in source['projects']:
        if not re.fullmatch(r'P\d+', p['id']): raise ValueError('Unexpected crawler project ID')
        row = dict(id=p['id'], pet=p['pet'], total_bytes=p.get('total_bytes', 0) or 0, file_count=p.get('file_count', 0),
                   saved_version=p.get('saved_version', 'unknown'), eligibility='eligible', reason='', features=[], candidate_group='unknown')
        inventory = catalogue / 'inventories' / (p['id'] + '.json')
        if p['status'] not in ('queued', 'duplicate', 'completed', 'completed_with_gaps') or not inventory.is_file():
            row.update(eligibility='ineligible', reason=p.get('reason') or p['status'])
        elif p['id'] in verified_duplicates:
            row.update(eligibility='verified_duplicate', reason='Crawler confirmed duplicate of ' + str(verified_duplicates[p['id']]))
        elif any(pattern.casefold() in p['pet'].casefold() for pattern in config['exclude_path_patterns']):
            row.update(eligibility='excluded_by_path_rule', reason='Temporary/development fixture path; retained for review')
        if inventory.is_file():
            rows = read(inventory)
            c.write_json(lab / 'snapshots' / (p['id'] + '.json'), rows)
            signature = sorted((r['relative'], r['size']) for r in rows)
            row['candidate_group'] = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:20]
            row['features'] = features(p, rows, source.get('roots', []), config)
        candidates.append(row)
    c.write_json(lab / 'CANDIDATES.json', candidates)
    selected = choose_wave(candidates, config['profile_cases'])
    jobs = [job(p, 'metadata_profile') for p in selected]
    for p in jobs:
        shutil.copy2(lab / 'snapshots' / (p['source_project_id'] + '.json'), lab / 'inventories' / (p['id'] + '.json'))
    state = dict(schema=SCHEMA, created_utc=c.utc(), source_catalogue=str(catalogue), source_catalogue_complete=source['discovery_complete'],
                 catalogue_snapshot_bytes=(catalogue / 'CAMPAIGN.json').stat().st_size, config=dict(config, extractor_directory=str(toolkit.resolve())),
                 extractor_manifest_sha256=current_build, projects=jobs, roots=source.get('roots', []), root_aliases=source.get('root_aliases', []),
                 selection_method='Greedy diversity across saved file metadata; no source read or companion hashing; possible duplicates retained')
    guide = Path(__file__).resolve().parent / 'TEST_LAB.md'
    if not guide.is_file(): guide = Path(__file__).resolve().parents[1] / 'docs/TEST_LAB.md'
    shutil.copy2(guide, lab / 'TEST_PLAN.md')
    review(lab, state)
    print('TEST PLAN: ' + str(lab / 'TEST_PLAN.html'), flush=True)
    print('Selected ' + str(len(jobs)) + ' metadata profiles. No extraction or source scan started.', flush=True)
    return state


def refresh_extraction_wave(lab, state):
    if any(p['lab_scenario'] == 'full_extract' for p in state['projects']): return
    candidates = read(lab / 'CANDIDATES.json')
    candidates_by_id = {p['id']: p for p in candidates}
    attempted = []
    for p in state['projects']:
        if p['lab_scenario'] != 'metadata_profile' or not p['attempts']: continue
        row = copy.deepcopy(candidates_by_id[p['source_project_id']])
        result = Path(p['attempts'][-1]['output']) / 'profile_data/PROFILE_RESULT.json'
        if result.is_file():
            context = read(result).get('context', {})
            version = context.get('saved_version', {}).get('value')
            if version:
                row['saved_version'] = str(version)
                row['features'].append('recorded_version:' + str(version))
            row['features'].append('profile_layout:' + str(context.get('layout', 'unknown')))
            for kind in context.get('payload_type_counts', {}): row['features'].append('payload_type:' + kind)
        else:
            row['features'].append('profile_outcome:' + p['status'])
        attempted.append(row)
    if not attempted: raise ValueError('Run the selected metadata profiles before selecting full extraction cases')
    config = state['config']
    chosen = choose_wave(attempted, config['extraction_cases'], config['extraction_native_budget_gib'] * 1024**3,
                         config['max_project_gib'] * 1024**3)
    for row in chosen:
        p = job(row, 'full_extract')
        state['projects'].append(p)
        shutil.copy2(lab / 'snapshots' / (p['source_project_id'] + '.json'), lab / 'inventories' / (p['id'] + '.json'))
    if not chosen: raise ValueError('No profiled cases fit the configured native-size budget; revise the plan settings')
    review(lab, state)


def metadata_source_check(project, previous, events):
    if project['lab_scenario'] == 'full_extract': return c.snapshot(Path(project['pet']), events)
    # Profiling checks only files it inspects. No recursive native inventory walk.
    current = []
    for row in previous:
        if row['relative'] == 'PROJECT.pet' or row['relative'].casefold() in ('store/model.ptd', 'store/data.ptd'):
            s = c.row_path(Path(project['pet']), row).stat()
            row = dict(row, size=s.st_size, mtime_ns=s.st_mtime_ns)
        current.append(row)
    return current, []


def selected_case(project, scenario, case_ids=()):
    return project['lab_scenario'] == scenario and (not case_ids or project['id'] in case_ids)


def elapsed_limits(config, scenario, run_to_completion=False):
    """Return outer session/case limits; ``None`` is the explicit unlimited sentinel."""
    enabled = bool(run_to_completion or config.get('run_to_completion', False))
    if enabled:
        return None, None
    timeout = config['profile_timeout_seconds'] / 3600 if scenario == 'metadata_profile' else config['extraction_timeout_hours']
    return config['session_hours'] * 3600, timeout


def run(lab, scenario, limit=0, retry=False, case_ids=(), run_to_completion=False):
    state = read(lab / 'CAMPAIGN.json')
    if state['schema'] != SCHEMA: raise ValueError('Not a test-lab plan')
    config = state['config']
    validate_config(config)
    run_to_completion = bool(run_to_completion or config.get('run_to_completion', False))
    toolkit = Path(config['extractor_directory'])
    known = {p['id'] for p in state['projects'] if p['lab_scenario'] == scenario}
    if set(case_ids) - known:
        raise ValueError('Unknown case ID for this scenario: ' + ', '.join(sorted(set(case_ids) - known)))
    with c.campaign_lock(lab):
        if scenario == 'full_extract': refresh_extraction_wave(lab, state)
        for p in state['projects']:
            if p['status'] == 'running':
                if c.process_alive(p['attempts'][-1]['child_pid']): raise RuntimeError('A prior case is still running')
                p['status'] = 'interrupted'
        selected = [p for p in state['projects'] if selected_case(p, scenario, case_ids) and
                    (p['status'] in ('queued', 'deferred_space') or retry and p['status'] in c.TERMINAL - {'completed', 'completed_with_gaps'})]
        if not selected:
            print('No eligible cases remain in this testing wave. Prior results retained.')
            return 0
        if c.digest(toolkit / '00_manifest/toolkit_files.json') != state['extractor_manifest_sha256']:
            raise RuntimeError('Extractor build changed; prepare a new plan')
        events = c.Events(lab)
        session_dir = lab / 'LOGS' / ('session_' + uuid.uuid4().hex[:8])
        session_dir.mkdir()
        os.environ['FIELDVIEWER_TRACE_DIRECTORY'] = str(session_dir)
        import geoviewer_trace
        geoviewer_trace.start()
        worker = Path(__file__).resolve().parent / 'geoviewer_test_worker.py'
        python = str(toolkit / 'runtime/python.exe')
        session = session_dir / 'RUNTIME_SESSION.json'
        print('Checking the runtime once for this session...', flush=True)
        doctor = c.process(c.powershell(toolkit / 'scripts/doctor_portable_petrel_toolkit.ps1') + ['-PythonPath', python],
                           session_dir / 'doctor.log', events=events, timeout_hours=.25)
        if doctor != 0: raise RuntimeError('Toolkit doctor failed; inspect ' + str(session_dir))
        check = c.process([python, '-B', str(worker), '--toolkit', str(toolkit), '--session', str(session),
                           '--prepare', '--owner-pid', str(os.getpid())] + (['--run-to-completion'] if run_to_completion else []),
                          session_dir / 'preflight.log', events=events, timeout_hours=.5)
        if check != 0: raise RuntimeError('Full runtime preflight failed; inspect ' + str(session_dir))
        verified = read(session)
        if verified.get('status') != 'ready': raise RuntimeError('Invalid verification receipt')
        def command(project, output):
            cmd = [python, '-B', str(worker), '--toolkit', str(toolkit), '--session', str(session),
                   '--scenario', project['lab_scenario'], '--project', project['pet'], '--output', str(output)]
            if not config['full_hash']: cmd.append('--no-full-hash')
            if run_to_completion: cmd.append('--run-to-completion')
            return cmd
        def after_case(campaign, current, project):
            review(campaign, current)
            try:
                result = c.bundle_logs(campaign)
                if result['status'] != 'complete':
                    events.emit('test_archive_incomplete', archive_status=result['status'], receipt=str(campaign / 'ARCHIVE_STATUS.json'))
            except Exception as exc:
                events.emit('test_archive_update_failed', error=str(exc), originals_retained=True)
        session_seconds, _ = elapsed_limits(config, scenario, run_to_completion)
        execution = dict(verified_session=True, select=lambda p: selected_case(p, scenario, case_ids),
                         command=command, session_seconds=session_seconds,
                         timeout_hours=lambda p: elapsed_limits(config, p['lab_scenario'], run_to_completion)[1],
                         source_check=metadata_source_check, after_case=after_case, stop_exit_codes=[75],
                         admission=lambda p: dict(p, total_bytes=0) if p['lab_scenario'] == 'metadata_profile' else p)
        try:
            c.run_queue(lab, state, events, max_projects=limit, retry_failed=retry, execution=execution)
        finally:
            state['updated_utc'] = c.utc()
            review(lab, state)
            final_archive = {}
            try:
                final_archive = c.bundle_logs(lab)
            except Exception as exc:
                events.emit('test_archive_update_failed', error=str(exc), originals_retained=True)
        print('TEST PLAN: ' + str(lab / 'TEST_PLAN.html'))
        print('LOG ARCHIVE STATUS: ' + str(lab / 'ARCHIVE_STATUS.json'))
        return 10 if final_archive.get('status') != 'complete' or any(p['status'] not in ('completed', 'queued') for p in state['projects'] if selected_case(p, scenario, case_ids)) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'profile', 'extract', 'collect'])
    parser.add_argument('--list', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--toolkit', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--max-projects', type=int, default=0)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--run-to-completion', action='store_true',
                        help='Opt in to no elapsed-time termination for selected cases and their extraction stages')
    parser.add_argument('--case', action='append', default=[], help='Run only this saved case ID; may be repeated. Does not select new projects.')
    args = parser.parse_args()
    home = Path(__file__).resolve().parent
    pointer = home / 'LATEST_TEST_PLAN.json'
    if args.mode == 'prepare':
        if not args.list or not args.toolkit: raise ValueError('Preparation requires --list and --toolkit')
        config = dict(DEFAULTS, **(read(args.config) if args.config else {}))
        lab = args.plan or home / 'Plans' / ('TestLab_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
        prepare(args.list, lab.resolve(), config, args.toolkit.resolve())
        c.write_json(pointer, dict(plan=str(lab.resolve()), created_utc=c.utc()))
        return 0
    lab = args.plan or Path(read(pointer)['plan'])
    if lab.is_file(): lab = lab.parent
    lab = lab.resolve()
    if args.mode == 'collect':
        with c.campaign_lock(lab):
            state = read(lab / 'CAMPAIGN.json')
            if state.get('schema') != SCHEMA: raise ValueError('Not a testing plan')
            c.collect_all(lab, state, c.Events(lab))
            review(lab, state)
            c.bundle_logs(lab)
        return 0
    return run(lab, 'metadata_profile' if args.mode == 'profile' else 'full_extract', args.max_projects,
               args.retry_failed, args.case, args.run_to_completion)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
