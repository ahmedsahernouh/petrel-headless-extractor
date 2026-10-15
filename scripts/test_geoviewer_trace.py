# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Check local stall evidence without opening native project data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_trace as t


class TraceTests(unittest.TestCase):
    def test_waiting_operation_and_paths_recorded_without_payload_or_secrets(self):
        stop = threading.Event()
        ready = threading.Event()
        def fixture_wait():
            path = Path('D:/fixture/Data.ptd')
            context = dict(operation='test_wait', object_id='test-object')
            password = 'NEVER_RECORD_PASSWORD'
            payload = b'NEVER_RECORD_PAYLOAD'
            ready.set()
            stop.wait(5)
        thread = threading.Thread(target=fixture_wait, name='fixture-blocked')
        thread.start(); ready.wait(1)
        try:
            rows = t.snapshot()
            frames = next(r['frames'] for r in rows if r['thread'] == 'fixture-blocked')
            matched = next(r for r in frames if r['function'] == 'fixture_wait')
            self.assertEqual(matched['context']['object_id'], 'test-object')
            self.assertEqual(matched['context']['path'], str(Path('D:/fixture/Data.ptd')))
            self.assertNotIn('NEVER_RECORD', json.dumps(rows))
        finally:
            stop.set(); thread.join()

    def test_local_samples_survive_child_forced_termination(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'child.py'
            script.write_text('import sys,time\nsys.path.insert(0,' + repr(str(Path(t.__file__).parent)) +
                              ')\nimport geoviewer_trace as t\n'
                              'original_run=t.Sampler.run\n'
                              'def delayed_run(self):\n'
                              ' time.sleep(.25)\n'
                              ' original_run(self)\n'
                              't.Sampler.run=delayed_run\n'
                              # A long startup record must not be mistaken for a sample.
                              'sys.argv[0]="synthetic-child-"+"x"*400\n'
                              's=t.Sampler(' + repr(folder) + ',30)\ntime.sleep(30)\n')
            child = subprocess.Popen([sys.executable, '-B', str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                files = []
                sampled = False
                while time.monotonic() < deadline:
                    files = list(Path(folder).glob('python_*.jsonl'))
                    if files:
                        # The file can be observed between write and flush, or
                        # while a JSON record is being appended. Only complete
                        # sample records establish readiness for termination.
                        text = files[0].read_text(encoding='utf-8')
                        complete = text.rsplit('\n', 1)[0] if '\n' in text else ''
                        rows = [json.loads(r) for r in complete.splitlines()]
                        sampled = any(r['event'] == 'stack_sample' for r in rows)
                        if sampled: break
                    if child.poll() is not None: break
                    time.sleep(.02)
                self.assertTrue(files)
                self.assertTrue(sampled, 'Child did not persist a complete stack_sample before deadline')
                persisted_sample = next(r for r in rows if r['event'] == 'stack_sample')
                self.assertTrue(any(thread['frames'] for thread in persisted_sample['threads']))
                self.assertIsNone(child.poll(), 'Synthetic child exited before forced termination')
                child.kill(); child.wait(timeout=5)
                rows = [json.loads(r) for r in files[0].read_text(encoding='utf-8').splitlines()]
                self.assertEqual(rows[0]['event'], 'process_started')
                self.assertTrue(any(r['event'] == 'stack_sample' for r in rows))
                self.assertIn(persisted_sample, rows)
                self.assertFalse(any(r['event'] == 'process_finished' for r in rows))
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
                child.stderr.close()

    def test_disabled_without_explicit_runner_path(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(t, 'Sampler') as constructor:
            t.start()
            constructor.assert_not_called()


if __name__ == '__main__': unittest.main()
