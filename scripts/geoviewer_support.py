# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Offline diagnostic ZIPs. Website: https://saherlabs.dev/

Only diagnostic files are collected, never source stores, exports or figures.
The sharing copy replaces recognized identifiers; it is not guaranteed anonymous.
Original local evidence is unchanged. No network operations are performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = {
    'RUN_RESULT.json', 'request.json', 'preflight.json', 'receipt.json',
    'result.json', 'pipeline_stages.json', 'native_recovery_report.json',
    'native_spatial_decode_report.json', 'native_compatibility.json',
    'extraction_receipt.json', 'qc_receipt.json',
}
LOGS = {'extraction.log', 'report_build.log', 'RUN_LOG.txt', 'BOOTSTRAP_LOG.txt'}
# These folders contain actual project data or generated deliverables. Never walk them.
EXCLUDED_DIRS = {'08_native_project', 'native_data', 'assets', 'figures',
                 'runtime', 'originals', 'source_files', 'source', 'exports'}
SENSITIVE_KEYS = {'name', 'well', 'well_name', 'project_name', 'label', 'saved_by',
                  'author', 'username', 'hostname', 'computer_name', 'machine'}
SECRET_KEY = re.compile(r'password|passwd|secret|token|authorization|credential|api[_-]?key', re.I)
PAYLOAD_KEYS = {'raw_values', 'samples', 'vertices', 'coordinates', 'payload', 'blob',
                'raw_payload', 'native_payload', 'record_bytes', 'image', 'base64', 'project_context'}


def environment_info(output=None):
    """Allowlist only: no environment dump, host name, registry or credentials."""
    info = dict(python=sys.version, implementation=platform.python_implementation(),
                os=platform.system(), os_release=platform.release(), os_version=platform.version(),
                architecture=platform.machine(), pointer_bits=64 if sys.maxsize > 2**32 else 32,
                cpu_count=os.cpu_count(), filesystem_encoding=sys.getfilesystemencoding(),
                stdout_encoding=getattr(sys.stdout, 'encoding', None), executable=sys.executable,
                dependencies={})
    for name in ('numpy', 'lasio', 'openpyxl', 'pandas', 'pyshp', 'zmapio', 'zfpy',
                 'pyzgy', 'segyio', 'matplotlib', 'Pillow', 'lz4'):
        try: info['dependencies'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: info['dependencies'][name] = 'not_installed'
    try:
        toolkit = json.loads((ROOT/'toolkit.json').read_text(encoding='utf-8'))
        info['toolkit_version'] = toolkit['version']
        info['build_revision'] = toolkit.get('build_revision', 'unknown')
    except (OSError, ValueError, KeyError): info['toolkit_version'] = 'unknown'
    if output:
        try:
            disk = shutil.disk_usage(output)
            info['output_disk_bytes'] = dict(total=disk.total, used=disk.used, free=disk.free)
        except OSError as exc: info['output_disk_error'] = str(exc)
    return info


def register_run(logs, run, report):
    """The outer launcher packages once, after the bootstrap transcript closes."""
    descriptor = dict(run=str(run), report=str(report), logs=logs.paths(), run_id=logs.run_id,
                      status=logs.status, environment=environment_info(run))
    session = os.environ.get('GEOVIEWER_SUPPORT_SESSION')
    if session:
        try:
            path = Path(session)/'run.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(descriptor, ensure_ascii=True), encoding='utf-8')
            temporary.replace(path)
        except OSError as exc:
            print('Support descriptor could not be written: '+str(exc), file=sys.stderr)
    return descriptor


def finish_run(logs, run, report):
    logs.close()
    descriptor = register_run(logs, run, report)
    if not os.environ.get('GEOVIEWER_SUPPORT_SESSION'):
        try: build_bundle(descriptor)
        except Exception as exc: print('Support ZIP unavailable; original logs retained: '+str(exc), file=sys.stderr)


class Scrubber:
    """Consistent per-bundle pseudonyms, with no reverse mapping in the archive."""
    def __init__(self):
        self.salt = os.urandom(32)
        self.counts = Counter()
        self.replacements = {}
        for value in (str(Path.home()), str(ROOT), os.environ.get('USERNAME'), os.environ.get('COMPUTERNAME')):
            self.add(value)

    def token(self, value, kind='identifier'):
        self.counts[kind] += 1
        return '['+kind+'_'+hashlib.sha256(self.salt+str(value).casefold().encode('utf-8')).hexdigest()[:12]+']'

    def add(self, value):
        if isinstance(value, str) and len(value) >= 3 and value.lower() not in ('unknown', 'none'):
            self.replacements[value] = self.token(value)

    def learn(self, value, key=''):
        if key.lower() in PAYLOAD_KEYS or SECRET_KEY.search(key): return
        if isinstance(value, dict):
            for k, v in value.items(): self.learn(v, k)
        elif isinstance(value, list):
            for v in value: self.learn(v, key)
        elif isinstance(value, str):
            if key.lower() in SENSITIVE_KEYS: self.add(value)
            if key.lower().replace('_','') in ('projectfile', 'input', 'source', 'outputroot', 'run', 'report'):
                path = Path(value)
                if path.is_absolute():
                    self.add(str(path.parent)); self.add(path.stem)
                    if key.lower() in ('run','report'):
                        self.add(path.stem.removesuffix('_data').removesuffix('_REPORT'))

    def text(self, value):
        # Normalize JSON-escaped backslashes before matching Windows paths.
        if not hasattr(self, 'pattern'):
            variants = {v.casefold(): target for source, target in self.replacements.items()
                        for v in (source, source.replace('\\', '\\\\'), source.replace('\\', '/'))}
            self.variants = variants
            self.pattern = re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(v) for v in sorted(variants, key=len, reverse=True))+r')(?!\w)', re.I) if variants else None
        if self.pattern: value = self.pattern.sub(lambda m: self.variants[m[0].casefold()], value)
        value = re.sub(r'(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b', lambda m: self.token(m[0], 'object'), value)
        value = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\s<>"\'|,;]+', lambda m: self.token(m[0], 'path'), value)
        value = re.sub(r'(?i)[\w.+-]+@[\w.-]+\.[a-z]{2,}', lambda m: self.token(m[0], 'email'), value)
        value = re.sub(r'(?i)https?://[^\s<>"\']+', lambda m: self.token(m[0], 'url'), value)
        value = re.sub(r'(?i)\b(password|passwd|secret|token|authorization|api[_-]?key)(\s*[=:]\s*)[^\s,;]+', r'\1\2[redacted]', value)
        return value

    def data(self, value, key=''):
        if SECRET_KEY.search(key): return '[redacted]'
        if key.lower() in PAYLOAD_KEYS: return '[omitted: project content]'
        if isinstance(value, dict):
            # Preserve schema keys such as depth_range and package names. Only
            # path/UUID dictionary keys (e.g. source_hashes_before) need scrubbing.
            return {(self.text(str(k)) if re.match(r'^(?:[A-Za-z]:[\\/]|\\\\|[0-9a-fA-F]{8}-)', str(k)) else str(k)):
                    self.data(v, str(k)) for k, v in value.items()}
        if isinstance(value, list): return [self.data(v, key) for v in value]
        if isinstance(value, str):
            if key.lower() in SENSITIVE_KEYS: return self.token(value)
            return self.text(value)
        return value


def diagnostic_files(run):
    """Explicit filename allowlist; symlink/junction traversal is prohibited."""
    if not run: return
    root = Path(run).resolve()
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d.lower() not in EXCLUDED_DIRS
                   and not d.lower().endswith(('.ptd', '_exports'))
                   and not Path(directory, d).is_symlink()
                   and not (hasattr(Path(directory, d), 'is_junction') and Path(directory, d).is_junction())]
        for name in names:
            path = Path(directory, name)
            if name in RECEIPTS | LOGS and not path.is_symlink() and path.resolve().is_relative_to(root):
                yield path


def attach_link(report, bundle):
    from html import escape
    from urllib.parse import quote
    report = Path(report)
    if not report.is_file(): return
    try: link = quote(os.path.relpath(bundle, report.parent).replace('\\', '/'), safe='/')
    except ValueError: link = Path(bundle).resolve().as_uri()  # Fallback on another Windows drive.
    section = '<section id="support-bundle"><h2>Debugging and support</h2><p><a href="'+escape(link, quote=True)+'">Download one compressed support ZIP</a></p><p>Logs, errors, settings and recovery outcomes; source data and exports excluded. Recognized identifiers are replaced in this sharing copy. Review before sharing; automatic redaction is not guaranteed anonymity.</p></section>'
    text = report.read_text(encoding='utf-8')
    text = re.sub(r'<section id="support-bundle">.*?</section>', '', text, flags=re.S)
    text = text.replace('<main>', '<main>'+section, 1) if '<main>' in text else text.replace('</body>', section+'</body>')
    temporary = report.with_suffix('.support.tmp')
    temporary.write_text(text, encoding='utf-8'); temporary.replace(report)


def build_bundle(descriptor=None, session=None, destination=None, *, allow_fallback=True):
    descriptor = descriptor or {}
    session = Path(session) if session else None
    run = descriptor.get('run'); report = descriptor.get('report')
    report_path = Path(report) if report else None
    if destination is None:
        destination = (report_path.with_name(report_path.name.replace('_REPORT.html', '_SUPPORT.zip'))
                       if report_path else session/'SUPPORT.zip')
    destination = Path(destination)
    if destination.exists(): raise FileExistsError('Support ZIP already exists; choose a new name')
    paths = [Path(p) for p in descriptor.get('logs', [])]
    paths.extend(diagnostic_files(run) or [])
    if session:
        paths.extend(session/name for name in ('BOOTSTRAP_LOG.txt', 'launcher_result.json', 'session.json'))
    repair = ROOT/'build/dependencies/last_check.json'
    if session and repair.is_file(): paths.append(repair)
    paths = list(dict.fromkeys(paths))
    scrub = Scrubber(); scrub.learn(descriptor)
    # Learn identifiers from structured diagnostic receipts before transforming text logs.
    for path in paths:
        if path.suffix == '.json':
            try: scrub.learn(json.loads(path.read_text(encoding='utf-8-sig')))
            except (OSError, ValueError): pass
    missing = []; members = []
    metadata = dict(schema='geoviewer.support/1', created_utc=datetime.now(timezone.utc).isoformat(),
                    run_id=descriptor.get('run_id'), status=descriptor.get('status', 'launcher_only'),
                    source_data_included=False, network_used=False,
                    environment=descriptor.get('environment') or environment_info(run or (str(session) if session else None)))
    if session:
        try:
            launcher = json.loads((session/'launcher_result.json').read_text(encoding='utf-8-sig'))
            metadata['launcher_exit_code'] = launcher['exit_code']
            if metadata['status'] in ('running', 'launcher_only'):
                metadata['status'] = {0:'completed', 10:'completed_with_gaps', 130:'cancelled'}.get(launcher['exit_code'], 'failed')
        except (OSError, ValueError, KeyError): pass
    try: destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError: destination = Path(tempfile.mkdtemp(prefix='GeoViewer_support_'))/'SUPPORT.zip'
    temporary = destination.with_name(destination.name+'.'+uuid.uuid4().hex[:6]+'.tmp')
    try:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            archive.writestr('README.txt', 'GeoViewer support bundle\nWebsite: https://saherlabs.dev/\n\nSend this single ZIP to the developer after reviewing it. No automatic upload.\nLogs retain stages, timing, OS errors, tracebacks, retries and object outcomes.\nRecognized paths/names/IDs are pseudonymized consistently within this ZIP.\nNo reverse mapping is included. Original local logs are unchanged.\nFree text may still contain sensitive information: this is not guaranteed anonymity.\nNo source project stores, samples, exports, figures or workflow definitions are collected.\nCONTENTS.json lists collected files, omissions and redaction counts.\nInterrupted/forcibly killed processes can leave incomplete logs; missing evidence is listed.\n')
            archive.writestr('ENVIRONMENT.json', json.dumps(scrub.data(metadata), indent=2))
            for index, path in enumerate(paths):
                suffix = '.json' if path.suffix == '.json' else ('.jsonl' if path.suffix == '.jsonl' else '.txt')
                name = f'diagnostics/{index+1:03}{suffix}'
                try:
                    if not path.is_file(): raise FileNotFoundError(str(path))
                    with archive.open(name, 'w', force_zip64=True) as output:
                        if suffix == '.json':
                            raw = path.read_text(encoding='utf-8-sig', errors='replace')
                            try: text = json.dumps(scrub.data(json.loads(raw)), ensure_ascii=False, indent=2)
                            except ValueError: text = scrub.text(raw)
                            output.write(text.encode('utf-8'))
                        else:
                            # Stream large process/event logs; never read seismic or arrays.
                            with path.open(encoding='utf-8-sig', errors='replace') as source:
                                for line in source:
                                    if suffix == '.jsonl':
                                        try: line = json.dumps(scrub.data(json.loads(line)), ensure_ascii=False)+'\n'
                                        except ValueError: line = scrub.text(line)
                                    else: line = scrub.text(line)
                                    output.write(line.encode('utf-8'))
                    members.append(dict(member=name, role=scrub.text(path.name), original_bytes=path.stat().st_size))
                except OSError as exc:
                    missing.append(dict(role=scrub.text(path.name), error=scrub.text(str(exc))))
            archive.writestr('CONTENTS.json', json.dumps(dict(files=members, unavailable=missing,
                redaction_counts=dict(scrub.counts), excluded='Source stores, numeric data, exports, figures, workflow definitions, environment variables, credentials',
                privacy='Best-effort replacement; review before sharing. No anonymity guarantee.'), indent=2))
        temporary.replace(destination)
    except OSError:
        if not allow_fallback: raise
        fallback = Path(tempfile.mkdtemp(prefix='GeoViewer_support_'))/'SUPPORT.zip'
        return build_bundle(descriptor, session, fallback, allow_fallback=False)
    if report:
        try: attach_link(report, destination)
        except OSError as exc: print('Support ZIP ready, but report link could not be updated: '+str(exc), file=sys.stderr)
    print('SUPPORT ZIP (send this one file for debugging): '+str(destination), flush=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    args = parser.parse_args()
    descriptor_path = args.session/'run.json'
    descriptor = json.loads(descriptor_path.read_text(encoding='utf-8-sig')) if descriptor_path.is_file() else {}
    build_bundle(descriptor, args.session)
    return 0


if __name__ == '__main__': raise SystemExit(main())
