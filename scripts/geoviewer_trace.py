# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Opt-in local stack sampling for Field Viewer trials. https://saherlabs.dev/

No payloads, arbitrary local variables, credentials, or file contents are read.
The runner supplies a local diagnostic directory separate from network output.
"""
import atexit
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

_sampler = None
PATH_KEYS = {'path', 'source', 'destination', 'data_file', 'dbpath', 'export_package',
             'output', 'directory', 'filename', 'log_path', 'report_path'}
ID_KEYS = {'object_id', 'object_index', 'object_total', 'blob_type', 'operation', 'pk', 'tag'}


def snapshot():
    """Innermost frames first; only named scalar path and object identifiers."""
    names = {t.ident: t.name for t in threading.enumerate()}
    result = []
    for ident, frame in sys._current_frames().items():
        if ident == threading.get_ident():
            continue
        frames = []
        for _ in range(24):
            if frame is None: break
            values = dict(frame.f_locals)
            details = {}
            for key in PATH_KEYS | ID_KEYS:
                value = values.get(key)
                if type(value) in (str, int, float) or isinstance(value, Path):
                    details[key] = str(value)[:800]
            context = values.get('context')
            if type(context) is dict:
                for key in ID_KEYS:
                    if type(context.get(key)) in (str, int, float): details[key] = str(context[key])[:800]
            frames.append(dict(file=frame.f_code.co_filename, line=frame.f_lineno,
                               function=frame.f_code.co_name, context=details))
            frame = frame.f_back
        result.append(dict(thread=names.get(ident, 'unknown'), ident=ident, frames=frames))
    return result


class Sampler:
    def __init__(self, directory, interval=30):
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.interval = interval
        self.path = Path(directory) / (f'python_{os.getpid()}_{time.time_ns()}.jsonl')
        self.stream = self.path.open('x', encoding='utf-8')
        self.write('process_started', script=sys.argv[0], python=sys.executable)
        self.thread = threading.Thread(target=self.run, name='FV-local-trace', daemon=True)
        self.thread.start()

    def write(self, event, **fields):
        self.stream.write(json.dumps(dict(event=event, pid=os.getpid(), parent_pid=os.getppid(),
            time_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic()-self.started, 3),
            **fields), ensure_ascii=True) + '\n')
        self.stream.flush()

    def run(self):
        try:
            while not self.stop.is_set():
                self.write('stack_sample', threads=snapshot())
                if self.stream.tell() > 64 * 1024 * 1024:
                    self.write('trace_limit_reached', limit_bytes=64 * 1024 * 1024)
                    return
                self.stop.wait(self.interval)
        except Exception as exc:
            print('FV local trace stopped: ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr, flush=True)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive(): return
        try:
            self.write('process_finished')
            self.stream.close()
        except Exception: pass


def start():
    global _sampler
    directory = os.environ.get('FIELDVIEWER_TRACE_DIRECTORY')
    if not directory or _sampler is not None: return
    try:
        # Runner creates the directory. Do not create paths taken from source data.
        if not Path(directory).is_absolute() or not Path(directory).is_dir():
            raise ValueError('Trace directory must already exist and be absolute')
        _sampler = Sampler(directory)
        atexit.register(_sampler.close)
    except Exception as exc:
        print('FV local trace unavailable: ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr, flush=True)
