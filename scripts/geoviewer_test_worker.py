# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Same-machine test adapter for the unchanged 1.0.0 extraction engine.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
One full preflight per live session; child cases reuse its explicit receipt.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding='utf-8')
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(path):
    s = Path(path).stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def guarded_files(toolkit):
    # Small code/entrypoint checks, not a repeated dependency-tree scan.
    paths = [p for p in (toolkit / 'scripts').iterdir() if p.is_file() and p.suffix.lower() in ('.py', '.ps1')]
    paths += [toolkit / 'runtime/python.exe', toolkit / '00_manifest/toolkit_files.json', Path(__file__).resolve()]
    paths += list((toolkit / 'runtime').glob('python*._pth'))
    manifest = load(toolkit / '00_manifest/toolkit_files.json')
    if manifest.get('launcher'):
        paths.append(toolkit.parent / manifest['launcher']['path'])
    return {str(p.resolve()): identity(p) for p in paths}


def process_birth(pid):
    if os.name != 'nt':
        os.kill(pid, 0)
        return str(pid)
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    h = kernel.OpenProcess(0x1000, False, pid)
    if not h:
        raise RuntimeError('Testing session owner is unavailable')
    try:
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:
            raise RuntimeError('Testing session owner is no longer running')
        values = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(h, *(ctypes.byref(v) for v in values)):
            raise ctypes.WinError(ctypes.get_last_error())
        return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(h)


def engine(toolkit):
    toolkit = toolkit.resolve()
    if Path(sys.executable).resolve() != (toolkit / 'runtime/python.exe').resolve():
        raise RuntimeError('Use this toolkit\'s embedded Python')
    # Core preflight intentionally rejects all search paths outside its package.
    sys.path[:] = [str(toolkit / 'scripts')] + [p for p in sys.path if p and Path(p).resolve().is_relative_to(toolkit)]
    import standalone_petrel_extract
    return standalone_petrel_extract


def validate_session(receipt, toolkit, run_to_completion=None):
    if receipt.get('schema') != 'geoviewer.test-session/1' or receipt.get('status') != 'ready':
        raise RuntimeError('No verified testing session')
    if receipt['host'] != platform.node() or receipt['toolkit'] != str(toolkit.resolve()):
        raise RuntimeError('Session belongs to another machine/toolkit')
    if process_birth(receipt['owner_pid']) != receipt['owner_birth']:
        raise RuntimeError('Session owner changed')
    if receipt['python'] != str(Path(sys.executable).resolve()):
        raise RuntimeError('Runtime changed')
    if guarded_files(toolkit) != receipt['guarded_files']:
        raise RuntimeError('Code or runtime entrypoints changed; start a fresh verified session')
    if digest(toolkit / '00_manifest/toolkit_files.json') != receipt['manifest_sha256']:
        raise RuntimeError('Toolkit manifest changed')
    if run_to_completion is not None and bool(receipt.get('run_to_completion',False)) != bool(run_to_completion):
        raise RuntimeError('Session elapsed-time policy changed')


def profile(project, output, receipt):
    from geoviewer_metadata import inspect_project
    from geoviewer_diagnostics import Diagnostics
    from geoviewer_support import register_run, finish_run, environment_info
    from geoviewer_delivery import partial_report
    run = output / 'profile_data'
    run.mkdir(parents=True, exist_ok=False)
    report = output / 'profile_REPORT.html'
    logs = Diagnostics(output / 'profile_LOG.txt', output / 'profile_EVENTS.jsonl')
    started = time.monotonic()
    register_run(logs, run, report)
    logs.event('environment', **environment_info(run))
    logs.event('started', scenario='metadata_profile', conversion_attempted=False, preflight=receipt)
    code = 1
    try:
        paths = [project] + [p for p in (project.with_suffix('.ptd') / 'Model.ptd', project.with_suffix('.ptd') / 'Data.ptd') if p.is_file()]
        before = {str(p): identity(p) for p in paths}
        context = inspect_project(project)
        after = {str(p): identity(p) for p in paths}
        if before != after:
            raise RuntimeError('Metadata source files changed during inspection')
        write(run / 'PROFILE_RESULT.json', dict(context=context, scenario='metadata_profile', conversion_attempted=False,
              source_metadata_before=before, source_metadata_after=after,
              integrity_scope='Size/time checks on metadata files; no payload hash or conversion QC'))
        partial_report(report, context, 'Metadata profiling only', 'No data conversion attempted. Use full extraction tests to assess recovery.')
        status = 'completed_with_gaps' if context.get('findings') or not context.get('native_decoders_applicable') else 'completed'
        result = dict(status=status, project_context=context, full_report=str(report), scenario='metadata_profile',
                      conversion_attempted=False, elapsed_seconds=time.monotonic() - started)
        write(run / 'RUN_RESULT.json', result)
        write(run / 'preflight.json', receipt)
        logs.event(status, scenario='metadata_profile', conversion_attempted=False)
        code = 10 if status == 'completed_with_gaps' else 0
    except Exception as exc:
        write(run / 'RUN_RESULT.json', dict(status='failed', scenario='metadata_profile', conversion_attempted=False,
              error=str(exc), traceback=traceback.format_exc()))
        logs.event('failed', severity='error', reason=str(exc), traceback=traceback.format_exc())
    finally:
        finish_run(logs, run, report)
    return code


def standalone_argv(toolkit, source, output, no_full_hash=False, run_to_completion=False):
    argv=[str(toolkit / 'scripts/standalone_petrel_extract.py'), '--project-file', str(source),
          '--output-root', str(output), '--mode', 'convert',
          '--no-full-hash' if no_full_hash else '--full-hash']
    if run_to_completion:
        argv.append('--run-to-completion')
    else:
        argv.append('--bounded')
    return argv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolkit', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--owner-pid', type=int)
    parser.add_argument('--scenario', choices=['metadata_profile', 'full_extract'], default='full_extract')
    parser.add_argument('--project', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--no-full-hash', action='store_true')
    parser.add_argument('--run-to-completion', action='store_true')
    args = parser.parse_args()
    toolkit = args.toolkit.resolve()
    try:
        if args.prepare:
            core = engine(toolkit)
            started = time.monotonic()
            checked = core.preflight()
            write(args.session, dict(schema='geoviewer.test-session/1', status='ready',
                  created_utc=datetime.now(timezone.utc).isoformat(), host=platform.node(),
                  owner_pid=args.owner_pid, owner_birth=process_birth(args.owner_pid), toolkit=str(toolkit),
                  run_to_completion=args.run_to_completion,
                  python=str(Path(sys.executable).resolve()), manifest_sha256=digest(toolkit / '00_manifest/toolkit_files.json'),
                  guarded_files=guarded_files(toolkit), preflight=checked, preflight_seconds=time.monotonic() - started,
                  assurance='Full bundle/import/ZFP verification once; cases check small code/entrypoint metadata. Dependency files are not rehashed per case.'))
            print('Verified testing session ready; subsequent cases reuse it.', flush=True)
            return 0
        session = load(args.session)
        validate_session(session, toolkit, args.run_to_completion)
        core = engine(toolkit)
        receipt = dict(session['preflight'], verification_scope='reused live-session preflight; code and entrypoint metadata checked per case',
                       session_file=str(args.session), session_verified_utc=session['created_utc'],
                       per_case_dependency_scan=False, session_preflight_seconds=session['preflight_seconds'])
        if not args.project or not args.output:
            raise ValueError('Exact source and output required')
        source, output = args.project.resolve(strict=True), args.output.resolve()
        if source.suffix.lower() != '.pet' or not source.with_suffix('.ptd').is_dir() or output.is_relative_to(source.parent):
            raise ValueError('Require native project pair and output outside source')
        print('TEST SCENARIO: ' + args.scenario + '; verified runtime reused from ' + session['created_utc'], flush=True)
    except Exception as exc:
        print('TEST SESSION ERROR: ' + str(exc), file=sys.stderr, flush=True)
        traceback.print_exc()
        return 75
    if args.scenario == 'metadata_profile':
        return profile(source, output, receipt)
    # Dedicated adapter replaces only repeated preflight. The extraction, source
    # integrity, numerical QC, reports and failure policy are the released engine.
    core.preflight = lambda: receipt
    sys.argv = standalone_argv(toolkit,source,output,args.no_full_hash,args.run_to_completion)
    return core.main()


if __name__ == '__main__':
    raise SystemExit(main())
