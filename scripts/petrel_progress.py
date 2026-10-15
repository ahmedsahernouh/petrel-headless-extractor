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
MAX_CHILD_FRAMES = 256
MAX_CHILD_LINE = 65536
_reporter = None
_batch = None
_phase_override = None


def child_frames(line):
    """Decode bounded prefixed JSON frames, including legacy joined writes.

    JSON decoding finds boundaries, so a prefix inside a JSON string is data.
    A malformed remainder raises after previously yielded valid frames.
    """
    from geoviewer_diagnostics import PREFIX
    prefixes = (PREFIX, EVENT_PREFIX, 'OBJECT_RESULT ')
    decoder = json.JSONDecoder()
    offset = count = 0
    if len(line) > MAX_CHILD_LINE:
        raise ValueError('Child event line exceeds framing limit')
    while offset < len(line):
        while offset < len(line) and line[offset].isspace(): offset += 1
        if offset == len(line): return
        if count >= MAX_CHILD_FRAMES:
            raise ValueError('Child event frame count exceeds framing limit')
        prefix = next((p for p in prefixes if line.startswith(p, offset)), None)
        if prefix is None:
            raise ValueError(f'Unexpected child event remainder at character {offset}')
        offset += len(prefix)
        while offset < len(line) and line[offset].isspace(): offset += 1
        data, offset = decoder.raw_decode(line, offset)
        if not isinstance(data, dict): raise ValueError('Child event must be a JSON object')
        count += 1
        yield prefix, data


def child_provenance(data, stream, pid):
    """Keep claimed metadata as evidence; transport/owner identity is trusted."""
    data = dict(data)
    reserved = ('schema', 'run_id', 'sequence', 'event_id', 'time_utc',
                'pid', 'parent_pid', 'stream')
    reported = {key: data.pop(key) for key in reserved if key in data}
    if reported:
        if 'child_reported_metadata' in data:
            reported['child_reported_metadata'] = data.pop('child_reported_metadata')
        data['child_reported_metadata'] = reported
    data.update(stream=stream, pid=pid, parent_pid=os.getpid())
    return data


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


def hash_file(path, on_bytes=None):
    """Streaming SHA-256, with optional byte progress and bounded memory."""
    if _reporter is not None and _batch is None:
        with hash_batch('Hashing file', [path]):
            return hash_file(path, on_bytes=on_bytes)
    metric = _batch
    if metric is not None:
        metric['file'] = path.name
        metric['file_bytes'] = path.stat().st_size
    digest = hashlib.sha256()
    bytes_read = 0
    started=time.monotonic()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
            bytes_read += len(block)
            if on_bytes is not None:
                on_bytes(len(block))
            if metric is not None:
                metric['done'] += len(block)
                _reporter.update(metric)
    if metric is not None:
        metric['files_done'] += 1
        _reporter.update(metric)
    observed_size = path.stat().st_size
    if max(bytes_read, observed_size)>=64*1024*1024:
        from geoviewer_diagnostics import event
        event('hash_completed',path=str(path),bytes=bytes_read,observed_size_bytes=observed_size,
              seconds=round(time.monotonic()-started,3),algorithm='SHA-256')
    return digest.hexdigest()


class HashFileEvents:
    """Bounded, file-local progress frames for an isolated source hash pass."""
    def __init__(self, path, total, stream=None, interval=5.0):
        self.path = str(path)
        self.total = total
        self.stream = stream
        self.interval = interval
        self.done = 0
        self.started = time.monotonic()
        self.last = self.started

    def emit(self, state, **extra):
        if state == 'completed' and self.done != self.total:
            raise ValueError(f'Hash byte count mismatch: expected {self.total}, read {self.done}')
        from geoviewer_diagnostics import write_child_frame
        elapsed = max(0.0, time.monotonic() - self.started)
        fraction = self.done / self.total if self.total else (1.0 if state == 'completed' else 0.0)
        eta = (self.total - self.done) * elapsed / self.done if self.done and self.done < self.total else None
        write_child_frame(EVENT_PREFIX, dict(kind='hash_file', state=state, source=self.path,
            total=self.total, done=self.done, elapsed=round(elapsed, 3),
            file_fraction=round(min(1.0, fraction), 6), file_eta_seconds=round(eta, 3) if eta is not None else None,
            **extra), self.stream)

    def advance(self, count):
        self.done += count
        now = time.monotonic()
        if now - self.last >= self.interval and self.done < self.total:
            self.last = now
            self.emit('progress')


class ChildEvents:
    """Throttled ASCII events through the already-owned pipeline stdout pipe."""
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.last = 0.0

    def update(self, metric):
        from geoviewer_diagnostics import _child_output_lock
        with _child_output_lock:
            self._update(metric)

    def _update(self, metric):
        now = time.monotonic()
        # Small files need no separate event; the parent still shows stage time.
        if (now - self.last < 0.5 and not metric['complete']) or metric['total'] < 64 * 1024 * 1024:
            return
        self.last = now
        data = {k: v for k, v in metric.items() if k != 'started'}
        data['elapsed'] = now - metric['started']
        from geoviewer_diagnostics import write_child_frame
        write_child_frame(EVENT_PREFIX, data, self.stream)


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
                if metric.get('kind') == 'hash_file':
                    metric = dict(kind='hash_file', label='Hash failed' if metric['state']=='failed' else 'Hashing source file', file=Path(metric['source']).name,
                        file_bytes=metric['total'], total=metric['total'], done=metric['done'],
                        files_total=1, files_done=int(metric['state'] == 'completed'),
                        elapsed=metric['elapsed'], complete=metric['state'] in ('completed', 'failed'))
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


def run_pipeline(command, cwd, log_path, timeout=None):
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
                line = pipe.readline(MAX_CHILD_LINE)
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
                    if line.startswith((PREFIX, EVENT_PREFIX, 'OBJECT_RESULT ')):
                        for prefix, data in child_frames(line):
                            if prefix == EVENT_PREFIX:
                                if data.get('kind') == 'hash_file':
                                    state = data.get('state')
                                    event('hash_file_'+state if state in ('started','progress','completed','failed') else 'hash_file_progress',
                                          **child_provenance({k:v for k,v in data.items() if k != 'kind'}, stream_name, proc.pid))
                                if isinstance(_reporter, ConsoleProgress):
                                    _reporter.child_line(EVENT_PREFIX + json.dumps(data))
                                continue
                            kind = data.pop('event') if prefix == PREFIX else 'native_object_outcome'
                            if not isinstance(kind, str): raise ValueError('Event name must be text')
                            event(kind, **child_provenance(data, stream_name, proc.pid))
                    else: event('child_output',line=line.rstrip(),stream=stream_name,pid=proc.pid)
                except (ValueError,TypeError,KeyError,RecursionError) as exc:
                    event('malformed_child_event',severity='warning',reason=str(exc),line=line.rstrip(),stream=stream_name,pid=proc.pid)
                if isinstance(_reporter,ConsoleProgress) and not line.startswith((PREFIX, EVENT_PREFIX, 'OBJECT_RESULT ')):
                    _reporter.child_line(line.rstrip())
        except OSError as exc:
            event('child_pipe_error',severity='error',reason=str(exc),stream=stream_name,pid=proc.pid)
        finally: pipe.close()
    readers=[threading.Thread(target=drain,args=(pipe,name),daemon=True) for pipe,name in
             ((proc.stdout,'stdout'),(proc.stderr,'stderr'))]
    for reader in readers: reader.start()
    try:
        # ``None`` is subprocess' documented unlimited wait.  It is used only
        # by the explicit run-to-completion path; ordinary callers retain their
        # finite timeout.
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
