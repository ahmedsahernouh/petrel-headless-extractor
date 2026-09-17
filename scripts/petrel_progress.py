# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Opt-in console progress; hashing and generic JSON tool output stay unchanged.

Stage counts describe workflow completion, not equal amounts of work. ETA is
only for the current measured hash pass/file, never the entire extraction.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

EVENT_PREFIX = 'PETREL_PROGRESS:'
_reporter = None
_batch = None
_phase_override = None


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


def size(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


def bar(fraction, width=20):
    filled = min(width, max(0, int(fraction * width)))
    return '[' + '#' * filled + '-' * (width - filled) + ']'


def set_reporter(reporter):
    global _reporter
    _reporter = reporter


def phase(number, label):
    if isinstance(_reporter, ConsoleProgress):
        _reporter.phase(_phase_override if _phase_override is not None else number, label)


@contextmanager
def keep_stage(number):
    """Keep a nested converter within its parent project's final stage."""
    global _phase_override
    previous=_phase_override;_phase_override=number
    try:yield
    finally:_phase_override=previous


def items(label, done, total, started, unit='items'):
    """Report measurable conversion work without calling it a hash pass."""
    if _reporter is not None:
        _reporter.update(dict(kind='items', label=label, done=done, total=total,
                             started=started, unit=unit, complete=False))


@contextmanager
def hash_batch(label, paths):
    """Count actual read bytes across a known, sequential collection of files."""
    global _batch
    previous = _batch
    metric = None
    if _reporter is not None:
        paths = list(paths)
        metric = dict(label=label, total=sum(p.stat().st_size for p in paths),
                      done=0, files_total=len(paths), files_done=0, file='',
                      started=time.monotonic(), complete=False)
        _batch = metric
        _reporter.update(metric)
    try:
        yield
        if metric is not None:
            metric['complete'] = True
            _reporter.update(metric)
    finally:
        _batch = previous


def hash_file(path):
    """Streaming SHA-256, with optional byte progress and bounded memory."""
    if _reporter is not None and _batch is None:
        with hash_batch('Hashing file', [path]):
            return hash_file(path)
    metric = _batch
    if metric is not None:
        metric['file'] = path.name
        metric['file_bytes'] = path.stat().st_size
    digest = hashlib.sha256()
    started=time.monotonic()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
            if metric is not None:
                metric['done'] += len(block)
                _reporter.update(metric)
    if metric is not None:
        metric['files_done'] += 1
        _reporter.update(metric)
    if path.stat().st_size>=64*1024*1024:
        from geoviewer_diagnostics import event
        event('hash_completed',path=str(path),bytes=path.stat().st_size,seconds=round(time.monotonic()-started,3),algorithm='SHA-256')
    return digest.hexdigest()


class ChildEvents:
    """Throttled ASCII events through the already-owned pipeline stdout pipe."""
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.last = 0.0

    def update(self, metric):
        now = time.monotonic()
        # Small files need no separate event; the parent still shows stage time.
        if (now - self.last < 0.5 and not metric['complete']) or metric['total'] < 64 * 1024 * 1024:
            return
        self.last = now
        data = {k: v for k, v in metric.items() if k != 'started'}
        data['elapsed'] = now - metric['started']
        print(EVENT_PREFIX + json.dumps(data, ensure_ascii=True), file=self.stream, flush=True)


def enable_child_events():
    if os.environ.get('PETREL_PROGRESS_EVENTS') == '1':
        set_reporter(ChildEvents())


class ConsoleProgress:
    def __init__(self, stages=12, stream=None, interval=None):
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty()
        self.interval = interval if interval is not None else (1.0 if self.tty else 10.0)
        self.stages = stages
        self.number = 1
        self.label = 'Checking bundled runtime'
        self.started = self.phase_started = time.monotonic()
        self.metric = None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.line_width = 0
        self.last_large_file = None

    @property
    def elapsed(self):
        return time.monotonic() - self.started

    def start(self):
        set_reporter(self)
        self.phase(1, self.label)
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def _heartbeat(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                self._write(self.render(), redraw=self.tty)

    def _write(self, text, redraw=False):
        self._clear_line()
        # Prevent source filenames from injecting terminal control sequences.
        text = ''.join(c if c.isprintable() else ' ' for c in text)
        if redraw:
            import shutil
            text = text[:max(20, shutil.get_terminal_size((120, 25)).columns - 1)]
        self.stream.write(text + ('' if redraw else '\n'))
        self.line_width = len(text) if redraw else 0
        self.stream.flush()

    def _clear_line(self):
        if self.line_width:
            self.stream.write('\r' + ' ' * self.line_width + '\r')
            self.line_width = 0
            self.stream.flush()

    def message(self, *values, file=None, flush=True):
        """Keep normal launcher messages from colliding with the redraw line."""
        with self.lock:
            self._clear_line()
            print(*values, file=file or sys.stdout, flush=flush)

    def phase(self, number, label):
        from geoviewer_diagnostics import event
        event('stage',number=number,label=label,previous_stage_seconds=round(time.monotonic()-self.phase_started,3))
        with self.lock:
            self.number, self.label = number, label
            self.phase_started = time.monotonic()
            self.metric = None
            self._write(f'Overall {bar((number - 1) / self.stages)} '
                        f'{number - 1}/{self.stages} stages complete | Elapsed {duration(self.elapsed)}')
            self._write(f'Stage {number}/{self.stages}: {label}')

    def update(self, metric):
        with self.lock:
            file = metric.get('file', '')
            if file != self.last_large_file and metric.get('file_bytes', 0) >= 64 * 1024 * 1024:
                self._write(f'Hashing {file} ({size(metric["file_bytes"])})')
                self.last_large_file = file
            self.metric = metric.copy()

    def child_line(self, line):
        if line.startswith(EVENT_PREFIX):
            try:
                metric = json.loads(line[len(EVENT_PREFIX):])
                metric['started'] = time.monotonic() - float(metric.pop('elapsed'))
                self.update(metric)
            except (ValueError, KeyError, TypeError):
                pass  # Progress is advisory; malformed events remain in the log.
        else:
            match = re.match(r'^Stage ([1-6])/6: (.*)', line)
            if match:
                self.phase(int(match[1]) + 2, match[2])

    def render(self):
        now = time.monotonic()
        prefix = f'Elapsed {duration(now - self.started)} | '
        metric = self.metric
        if metric is None or metric['complete']:
            return prefix + f'Stage {self.number}/{self.stages} running | Stage time {duration(now - self.phase_started)} | {self.label}'
        total, done = metric['total'], metric['done']
        fraction = min(done / total, 0.999) if total else 0
        elapsed = now - metric['started']
        eta = duration((total - done) * elapsed / done) if done > 0 and done < total and elapsed >= 3 else 'calculating'
        if metric.get('kind') == 'items':
            return (prefix + f'{bar(fraction)} {100 * fraction:.1f}% | Phase ETA ~{eta}'
                    f' | {done}/{total} {metric["unit"]} | {metric["label"]}')
        return (prefix + f'{bar(fraction)} {100 * fraction:.1f}% | Hash ETA ~{eta}'
                f' | {size(done)}/{size(total)} | {metric["files_done"]}/{metric["files_total"]} files | {metric["label"]}')

    def close(self, success=False, status=None):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        with self.lock:
            completed = self.stages if success else self.number - 1
            self._write(f'Overall {bar(completed / self.stages)} {completed}/{self.stages} stages complete'
                        f' | {status or ("Complete" if success else "Stopped before completion")} | Elapsed {duration(self.elapsed)}')
        set_reporter(None)


def run_pipeline(command, cwd, log_path, timeout):
    """Drain both streams; diagnostic faults never break the producer's pipe."""
    env = os.environ.copy()
    if isinstance(_reporter, ConsoleProgress):
        env['PETREL_PROGRESS_EVENTS'] = '1'
    else:
        env.pop('PETREL_PROGRESS_EVENTS', None)
    env['PYTHONUTF8'] = '1'
    from geoviewer_diagnostics import event, PREFIX
    import tempfile
    event('child_start',command=command,cwd=str(cwd),log_path=str(log_path),timeout_seconds=timeout)
    log = None; log_lock = threading.Lock(); fallback_used = False
    try: log = log_path.open('w', encoding='utf-8')
    except OSError as exc: event('child_log_unavailable',severity='warning',reason=str(exc),path=str(log_path))
    try:
        proc = subprocess.Popen(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                text=True,encoding='utf-8',errors='replace')
    except OSError:
        if log is not None:log.close()
        raise
    def drain(pipe, stream_name):
        nonlocal log, fallback_used
        try:
            while True:
                line = pipe.readline(65536)
                if not line: break
                with log_lock:
                    try:
                        if log is not None:
                            log.write(('STDERR: ' if stream_name=='stderr' else '')+line); log.flush()
                    except (OSError,ValueError) as exc:
                        try: log.close()
                        except OSError: pass
                        log = None
                        event('child_log_write_failed',severity='warning',reason=str(exc),pid=proc.pid)
                    if log is None and not fallback_used:
                        fallback_used = True
                        try:
                            log = tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='GeoViewer_child_',suffix='.log',delete=False)
                            log.write(line); log.flush()
                            event('child_log_fallback',severity='warning',path=log.name,pid=proc.pid)
                        except OSError as exc: event('child_log_lost',severity='error',reason=str(exc))
                try:
                    if line.startswith(PREFIX):
                        data=json.loads(line[len(PREFIX):]); kind=data.pop('event')
                        if not isinstance(kind,str): raise ValueError('Event name must be text')
                        event(kind,**data)
                    elif line.startswith('OBJECT_RESULT '):
                        event('native_object_outcome',**json.loads(line[len('OBJECT_RESULT '):]))
                    else: event('child_output',line=line.rstrip(),stream=stream_name,pid=proc.pid)
                except (ValueError,TypeError,KeyError) as exc:
                    event('malformed_child_event',severity='warning',reason=str(exc),line=line.rstrip(),stream=stream_name,pid=proc.pid)
                if isinstance(_reporter,ConsoleProgress): _reporter.child_line(line.rstrip())
        except OSError as exc:
            event('child_pipe_error',severity='error',reason=str(exc),stream=stream_name,pid=proc.pid)
        finally: pipe.close()
    readers=[threading.Thread(target=drain,args=(pipe,name),daemon=True) for pipe,name in
             ((proc.stdout,'stdout'),(proc.stderr,'stderr'))]
    for reader in readers: reader.start()
    try:
        proc.wait(timeout=timeout)
    except BaseException as exc:
        event('child_interrupted',severity='error',exception_type=type(exc).__name__,reason=str(exc),pid=proc.pid)
        try: subprocess.run(['taskkill.exe','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=10)
        except (OSError,subprocess.TimeoutExpired) as cleanup: event('child_cleanup_failed',severity='error',reason=str(cleanup),pid=proc.pid)
        if proc.poll() is None:
            proc.kill()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: event('child_stop_unconfirmed',severity='error',pid=proc.pid)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=10)
            if reader.is_alive(): event('child_drain_timeout',severity='error',pid=proc.pid)
        with log_lock:
            if log is not None:
                try: log.close()
                except OSError as exc: event('child_log_close_failed',severity='warning',reason=str(exc))
                log = None
        event('child_exit',exit_code=proc.returncode,pid=proc.pid)
    return proc.returncode
