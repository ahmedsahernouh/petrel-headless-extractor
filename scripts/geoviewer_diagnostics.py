# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Local process diagnostics; no uploads. Website: https://saherlabs.dev/

Writes line-oriented human and structured logs in a new caller-owned run.
Paths and error details can contain project information; users choose what to share.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid

PREFIX = 'GEOVIEWER_EVENT:'
SCHEMA = 'geoviewer.events/1'
_active = None
_child_sequence = 0


class Diagnostics:
    def __init__(self, text_path, events_path, *, run_id=None):
        global _active
        self.text_path, self.events_path = Path(text_path), Path(events_path)
        self.summary_path = self.text_path.with_name(self.text_path.name.replace('_LOG.txt', '_DIAGNOSTICS.txt'))
        if self.summary_path == self.text_path:
            self.summary_path = self.text_path.with_suffix('.summary.txt')
        self.started = time.monotonic(); self.lock = threading.RLock()
        self.run_id = run_id or uuid.uuid4().hex
        self.sequence = 0; self.problems = []; self.outcomes = {}; self.status = 'running'
        self.stage = None; self.fallback = None; self.sink_errors = []; self.closed = False
        self.log = self.events = None
        try:
            self.log = self.text_path.open('x', encoding='utf-8')
            self.events = self.events_path.open('x', encoding='utf-8')
        except OSError as exc:
            if not self._fallback(exc):raise
        self.segment = 0; self.segment_paths = [str(self.text_path)]
        _active = self
        self.previous_run_id=os.environ.get('GEOVIEWER_RUN_ID')
        os.environ['GEOVIEWER_RUN_ID'] = self.run_id

    def _fallback(self, exc):
        self.sink_errors.append(str(exc))
        if self.fallback is not None:
            return False
        try:
            self.fallback = Path(tempfile.mkdtemp(prefix='GeoViewer_diagnostics_'))
            for stream in (self.log, self.events):
                try:
                    if stream is not None:stream.close()
                except (OSError,ValueError): pass
            self.text_path=self.fallback/'LOG.txt';self.events_path=self.fallback/'EVENTS.jsonl';self.summary_path=self.fallback/'DIAGNOSTICS.txt'
            self.log = self.text_path.open('x', encoding='utf-8')
            self.events = self.events_path.open('x', encoding='utf-8')
            message = 'Diagnostics storage error: '+str(exc)+'; fallback: '+str(self.fallback)
            print(message, file=sys.stderr, flush=True)
            self.log.write(message+'\n'); self.log.flush()
            return True
        except OSError as fallback_error:
            self.sink_errors.append(str(fallback_error))
            try: print('Diagnostics could not be saved: '+str(fallback_error), file=sys.stderr, flush=True)
            except OSError: pass
            return False

    def _write(self, entry):
        raw = json.dumps(entry, ensure_ascii=True, default=str)+'\n'
        readable = entry['time_utc']+' '+entry['severity'].upper()+' '+entry['event']+' '+json.dumps(
            {k:v for k,v in entry.items() if k not in ('time_utc','severity','event')}, ensure_ascii=False, default=str)+'\n'
        for _ in range(2):
            try:
                self.events.write(raw); self.events.flush()
                self.log.write(readable); self.log.flush()
                if self.log.tell() > 50*1024*1024 and self.fallback is None:
                    self.log.close(); self.segment += 1
                    path = self.text_path.with_name(self.text_path.stem+f'.{self.segment:03}.txt')
                    self.log = path.open('x', encoding='utf-8'); self.segment_paths.append(str(path))
                return
            except (OSError, ValueError) as exc:
                if not self._fallback(exc): return

    def event(self, kind, **fields):
        with self.lock:
            if self.closed: return
            self.sequence += 1
            fields = dict(fields)
            if 'elapsed_seconds' in fields:
                fields['object_elapsed_seconds' if kind in ('native_object_outcome','object_committed','object_failed') else 'reported_elapsed_seconds'] = fields.pop('elapsed_seconds')
            for reserved in ('schema','run_id','sequence','event_id','time_utc','event'):
                if reserved in fields: fields['child_'+reserved] = fields.pop(reserved)
            severity = fields.pop('severity', 'error' if kind in ('failed','object_failed','io_failed') else 'info')
            entry = dict(fields, schema=SCHEMA, run_id=self.run_id, sequence=self.sequence,
                         event_id=f'{self.run_id}:{self.sequence}', time_utc=datetime.now(timezone.utc).isoformat(),
                         elapsed_seconds=round(time.monotonic()-self.started,3), event=kind,
                         severity=severity, pid=fields.get('pid',os.getpid()), parent_pid=fields.get('parent_pid',os.getppid()))
            self._write(entry)
            if kind in ('stage','stage_started'): self.stage = fields.get('label') or fields.get('stage')
            if kind in ('completed','run_finished'): self.status = fields.get('status','completed')
            if kind in ('failed','cancelled'): self.status = kind
            if kind in ('native_object_outcome','object_committed','object_failed') and fields.get('object_id'):
                self.outcomes[fields['object_id']] = fields.get('status', 'failed' if kind=='object_failed' else 'committed')
            if severity in ('error','warning'):
                self.problems.append(entry)
            if severity in ('error','warning') or kind in ('completed','run_finished','failed','cancelled','started'):
                self.snapshot()

    def snapshot(self):
        from collections import Counter
        lines = ['GeoViewer diagnostic summary', 'Run: '+self.run_id, 'Status: '+self.status,
                 'Current stage: '+str(self.stage), 'Object outcomes: '+json.dumps(dict(Counter(self.outcomes.values()))),
                 'Detailed log: '+str(self.text_path), 'Events: '+str(self.events_path),
                 'Log segments: '+json.dumps(self.segment_paths), 'Fallback: '+str(self.fallback),
                 'Logging failures: '+json.dumps(self.sink_errors), '']
        for entry in self.problems:
            lines.append(json.dumps({k:v for k,v in entry.items() if k not in ('traceback','record')}, ensure_ascii=False, default=str))
        destination = self.fallback/'DIAGNOSTICS.txt' if self.fallback else self.summary_path
        temporary = destination.with_name(destination.name+'.tmp')
        try:
            temporary.write_text('\n'.join(lines)+'\n', encoding='utf-8'); temporary.replace(destination)
        except OSError as exc:
            # Summary failure must never recursively log through summary replacement.
            if self._fallback(exc):
                try: (self.fallback/'DIAGNOSTICS.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
                except OSError: pass

    def close(self):
        global _active
        with self.lock:
            if self.closed: return
            self.snapshot()
            for stream in (self.log,self.events):
                try: stream.close()
                except OSError as exc: self.sink_errors.append(str(exc))
            self.closed = True
            if _active is self: _active = None
            if self.previous_run_id is None:os.environ.pop('GEOVIEWER_RUN_ID',None)
            else:os.environ['GEOVIEWER_RUN_ID']=self.previous_run_id


def event(kind, **fields):
    """Child workers stream events to their owner; the owner writes one ordered log."""
    global _child_sequence
    if _active is not None:
        _active.event(kind, **fields)
    elif os.environ.get('GEOVIEWER_RUN_ID') or os.environ.get('PETREL_PROGRESS_EVENTS') == '1':
        _child_sequence += 1
        data = dict(fields, event=kind, pid=os.getpid(), parent_pid=os.getppid(),
                    child_sequence=_child_sequence, child_time_utc=datetime.now(timezone.utc).isoformat())
        try: print(PREFIX+json.dumps(data,ensure_ascii=True,default=str),flush=True)
        except (OSError, UnicodeError): pass
