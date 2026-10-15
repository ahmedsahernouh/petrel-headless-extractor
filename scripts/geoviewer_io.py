# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Owned output operations and bounded Windows finalization recovery.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Never changes source permissions or overwrites a dataset destination.
"""
from contextlib import contextmanager
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import time
import traceback
import uuid

RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0, 4.0)
_retry_wait = 0.0
RETRY_BUDGET_SECONDS = 30.0


def read_up_to(stream, maximum):
    """Read at most ``maximum`` bytes without allocating that allowance up front.

    The caller owns the open binary stream and its path checks. To enforce a file
    limit, request limit+1 and reject a result longer than limit. Short reads are
    not EOF; no stat-based size shortcut can miss bytes appended during reading.
    Returned bytes remain one snapshot for both parsing and hashing.
    """
    if type(maximum) is not int or maximum < 0:
        raise ValueError('Read allowance must be a nonnegative integer')
    remaining = maximum
    with io.BytesIO() as collected:
        while remaining:
            block = stream.read(min(64 * 1024, remaining))
            if block is None:
                raise BlockingIOError('Binary input read did not return bytes')
            if not block:
                break
            if len(block) > remaining:
                raise ValueError('Binary stream exceeded the requested byte allowance')
            collected.write(block)
            remaining -= len(block)
        return collected.getvalue()


def pending_output(path):
    parts = Path(path).parts
    return 'native_data' in parts and any(p.endswith('.partial') for p in parts[parts.index('native_data')+1:])


def error_details(exc, **context):
    return dict(exception_type=type(exc).__name__, message=str(exc),
                errno=getattr(exc, 'errno', None), winerror=getattr(exc, 'winerror', None),
                traceback=''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                **context)


def systemic(exc):
    return (isinstance(exc, MemoryError) or getattr(exc, 'errno', None) in
            (errno.ENOSPC, errno.EROFS, errno.ENOMEM, errno.EMFILE, errno.ENFILE)
            or getattr(exc, 'winerror', None) in (8, 14, 21, 39, 112))


def emit(kind, **fields):
    from geoviewer_diagnostics import event
    event(kind, **fields)


class ContractProgress:
    """Throttled actual-work counters; never a workflow percentage or ETA.

    An operation ID pairs the start and terminal event. Nested spans and repeated
    verification passes remain distinct and must not be summed as elapsed time.
    The existing diagnostic sink controls whether events are written at all.
    """
    interval_seconds = 2.0

    def __init__(self, phase, *, unit='records', total=None, **context):
        self.phase, self.unit, self.total = phase, unit, total
        self.context = context
        self.operation_id = uuid.uuid4().hex
        self.completed = 0

    def _emit(self, state, **extra):
        emit('contract_operation', phase=self.phase, operation_id=self.operation_id,
             unit=self.unit, total=self.total, completed=self.completed,
             scope='current_operation_only', state=state,
             elapsed_seconds=time.monotonic()-self.started, **self.context, **extra)

    def __enter__(self):
        self.started = self.last_emitted = time.monotonic()
        self._emit('started')
        return self

    def advance(self, amount=1):
        self.completed += amount
        now = time.monotonic()
        if now-self.last_emitted >= self.interval_seconds:
            self._emit('progress')
            self.last_emitted = now

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self._emit('completed')
        else:
            self._emit('interrupted' if kind is GeneratorExit else 'failed',
                       error_type=kind.__name__)
        return False


def contract_rows(rows, phase, **context):
    """Count consumed rows, including unresolved/rejected ones, not recovered data."""
    total = len(rows) if hasattr(rows, '__len__') else None
    with ContractProgress(phase, total=total, **context) as progress:
        for row in rows:
            yield row
            progress.advance()


def contained(root, path):
    root, path = Path(os.path.abspath(root)), Path(os.path.abspath(path))
    if path == root or not path.is_relative_to(root):
        raise ValueError('Output path is outside the owned directory')
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('Linked output paths are not accepted: '+str(parent))
        if parent == root:
            break
    return path


def retry_io(action, *, operation, source, destination, delays=RETRY_DELAYS, context=None):
    """Retry the identical finalization, never decoding/writing or replacing a dataset."""
    global _retry_wait
    context = dict(context or {})
    started = time.monotonic()
    for attempt in range(len(delays)+1):
        try:
            result = action()
            if attempt:
                emit('io_recovered', operation=operation, source=str(source), destination=str(destination),
                     attempt=attempt+1, duration_seconds=time.monotonic()-started, **context)
            return result
        except OSError as exc:
            details = error_details(exc, operation=operation, source=str(source), destination=str(destination),
                                    source_path_length=len(str(source)), destination_path_length=len(str(destination)),
                                    attempt=attempt+1, **context)
            exc.geoviewer_context = details
            transient = getattr(exc, 'winerror', None) in (5, 32, 33)
            if attempt < len(delays) and _retry_wait+delays[attempt] > RETRY_BUDGET_SECONDS:
                transient = False
            if not transient or attempt == len(delays):
                emit('io_failed', severity='error', **details)
                raise
            emit('retry_scheduled', severity='warning', delay_seconds=delays[attempt], **details)
            _retry_wait += delays[attempt]
            time.sleep(delays[attempt])


def finalize_directory(pending, destination, *, context=None):
    pending, destination = Path(pending), Path(destination)
    root = pending.parent
    contained(root, pending); contained(root, destination)
    if pending.parent != destination.parent or not pending.is_dir():
        raise ValueError('Finalization requires an owned sibling staging directory')
    def promote():
        contained(root, pending); contained(root, destination)
        if destination.exists():
            raise FileExistsError(errno.EEXIST, 'Refusing to overwrite existing output', str(destination))
        return pending.rename(destination)
    return retry_io(promote, operation='finalize_directory', source=pending,
                    destination=destination, context=context)


def atomic_text(path, text):
    """Replace a caller-owned summary, retaining the previous version on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    contained(path.parent, path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex[:12]+'.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        retry_io(lambda: temporary.replace(path), operation='replace_summary',
                 source=temporary, destination=path)
    finally:
        try: temporary.unlink(missing_ok=True)
        except OSError: pass


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, default=str)+'\n')


def output_probe(root):
    """Verify operations only on newly created probe files; never probes sources."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    probe = root/('.geoviewer_probe_'+uuid.uuid4().hex)
    contained(root, probe); probe.mkdir()
    first, second = probe/'write.tmp', probe/'renamed.tmp'
    try:
        with first.open('x', encoding='ascii') as stream:
            stream.write('GeoViewer output probe'); stream.flush(); os.fsync(stream.fileno())
        first.rename(second)
        second.unlink()
        return dict(status='passed', free_bytes=shutil.disk_usage(root).free)
    finally:
        for path in (first, second):
            try: path.unlink(missing_ok=True)
            except OSError: pass
        try: probe.rmdir()
        except OSError: pass


class Journal:
    """Append terminal object records durably without quadratic snapshot rewriting."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = self.path.open('a', encoding='utf-8', newline='\n')

    def append(self, value):
        self.stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False, default=str)+'\n')
        self.stream.flush(); os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


@contextmanager
def run_lock(directory):
    """OS releases this byte lock on process death; a stale file is not a stale lock."""
    path = Path(directory)/'.geoviewer.lock'
    contained(directory, path)
    stream = path.open('a+b')
    locked = False
    try:
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
