"""Local process diagnostics; no uploads. Website: https://saherlabs.dev/

Writes line-oriented human and structured logs in a new caller-owned run.
Paths and error details can contain project information; users choose what to share.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

_active=None


class Diagnostics:
    def __init__(self, text_path, events_path):
        global _active
        self.text_path=Path(text_path);self.events_path=Path(events_path)
        self.started=time.monotonic();self.lock=threading.RLock()
        self.log=self.text_path.open('x',encoding='utf-8')
        self.events=self.events_path.open('x',encoding='utf-8')
        _active=self

    def event(self, kind, **fields):
        with self.lock:
            if kind=='native_object_outcome' and 'elapsed_seconds' in fields:
                fields['object_elapsed_seconds']=fields.pop('elapsed_seconds')
            entry=dict(time_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=round(time.monotonic()-self.started,3),event=kind)
            entry.update(fields)
            self.events.write(json.dumps(entry,ensure_ascii=True,default=str)+'\n');self.events.flush()
            self.log.write(entry['time_utc']+' '+kind+' '+json.dumps(fields,ensure_ascii=False,default=str)+'\n');self.log.flush()

    def close(self):
        global _active
        with self.lock:self.log.close();self.events.close();_active=None


def event(kind, **fields):
    if _active is not None:_active.event(kind,**fields)
