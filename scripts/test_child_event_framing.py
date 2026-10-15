# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for child event framing and transport provenance."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import geoviewer_diagnostics as diagnostics
import petrel_progress as progress


class ChildFramingTests(unittest.TestCase):
    def frame(self, **fields):
        return diagnostics.PREFIX + json.dumps(dict(event='test_event', **fields))

    def pipeline(self, line, *, stderr=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            owner = diagnostics.Diagnostics(root/'run_LOG.txt', root/'events.jsonl')
            try:
                code = ('import sys;sys.' + ('stderr' if stderr else 'stdout') + '.write(' + repr(line+'\n') + ')')
                self.assertEqual(progress.run_pipeline([sys.executable, '-B', '-c', code], root, root/'raw.log', 10), 0)
            finally:
                owner.close()
            return [json.loads(row) for row in owner.events_path.read_text().splitlines()], (root/'raw.log').read_text()

    def test_captured_legacy_joined_native_outcome_and_warning(self):
        # Regression case: adjacent events 395/396 were rejected as Extra data.
        first = {'object_id': 'ffc90953-4bcc-429d-bc8d-2b8d2982e3ed', 'object_name': 'M18-59',
                 'blob_type': 'WellTrace3', 'status': 'decoded', 'error': None, 'elapsed_seconds': 0.17,
                 'event': 'native_object_outcome', 'pid': 29136, 'parent_pid': 33024,
                 'child_sequence': 395, 'child_time_utc': '2026-09-27T11:59:31.736986+00:00'}
        second = {'line': 'WARNING: Native well-head metadata unavailable: DecodeError: Model BXML dictionary is missing well-head fields: well_head_; continuing with independent data',
                  'stream': 'stderr', 'pid': 29136, 'event': 'child_output', 'parent_pid': 33024,
                  'child_sequence': 396, 'child_time_utc': '2026-09-27T11:59:31.743634+00:00'}
        line = ''.join(diagnostics.PREFIX + json.dumps(value) for value in (first, second))
        rows, raw = self.pipeline(line)
        recovered = [row for row in rows if 'child_sequence' in row]
        self.assertEqual([row['event'] for row in recovered], ['native_object_outcome', 'child_output'])
        self.assertEqual([row['child_sequence'] for row in recovered], [395, 396])
        self.assertEqual(recovered[0]['object_elapsed_seconds'], 0.17)
        self.assertEqual(recovered[1]['child_reported_metadata']['stream'], 'stderr')
        self.assertTrue(all(row['stream'] == 'stdout' for row in recovered))
        self.assertEqual(len({row['pid'] for row in recovered}), 1)
        self.assertFalse(any(row['event'] == 'malformed_child_event' for row in rows))
        self.assertIn(line, raw)

    def test_prefix_inside_escaped_json_string_is_not_a_boundary(self):
        text = 'quote " and GEOVIEWER_EVENT:{bad} and PETREL_PROGRESS: inside text'
        frames = list(progress.child_frames(self.frame(line=text) + self.frame(number=2)))
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][1]['line'], text)

    def test_valid_frame_then_malformed_or_truncated_remainder_is_reported(self):
        for remainder in ('broken', diagnostics.PREFIX+'{"event":', diagnostics.PREFIX+'[]'):
            with self.subTest(remainder=remainder):
                line = self.frame(number=1) + remainder
                rows, raw = self.pipeline(line)
                self.assertEqual(sum(row['event'] == 'test_event' for row in rows), 1)
                faults = [row for row in rows if row['event'] == 'malformed_child_event']
                self.assertEqual(len(faults), 1)
                self.assertEqual(faults[0]['line'], line)
                self.assertEqual(faults[0]['stream'], 'stdout')
                self.assertIn(line, raw)

    def test_frame_count_is_bounded_and_remainder_reported(self):
        rows, _ = self.pipeline(self.frame()* (progress.MAX_CHILD_FRAMES + 1))
        self.assertEqual(sum(row['event'] == 'test_event' for row in rows), progress.MAX_CHILD_FRAMES)
        fault = next(row for row in rows if row['event'] == 'malformed_child_event')
        self.assertIn('count exceeds', fault['reason'])

    def test_line_size_is_bounded(self):
        with self.assertRaisesRegex(ValueError, 'line exceeds'):
            list(progress.child_frames(self.frame(line='x'*progress.MAX_CHILD_LINE)))

    def test_transport_and_owner_reserved_fields_cannot_be_spoofed(self):
        rows, _ = self.pipeline(self.frame(pid=-1, parent_pid=-2, stream='pretend',
            schema='pretend', run_id='pretend', sequence=-3, event_id='pretend', time_utc='pretend'), stderr=True)
        row = next(row for row in rows if row['event'] == 'test_event')
        self.assertGreater(row['pid'], 0)
        self.assertEqual(row['parent_pid'], os.getpid())
        self.assertEqual(row['stream'], 'stderr')
        self.assertEqual(row['schema'], diagnostics.SCHEMA)
        self.assertNotEqual(row['run_id'], 'pretend')
        self.assertGreater(row['sequence'], 0)
        self.assertEqual(row['child_reported_metadata']['pid'], -1)
        self.assertEqual(row['child_reported_metadata']['sequence'], -3)

    def test_concurrent_diagnostics_and_progress_use_one_complete_write(self):
        class SlowStream(io.StringIO):
            def __init__(self):
                super().__init__()
                self.writes = []
                self.busy = False
                self.collisions = 0
            def write(self, value):
                if self.busy: self.collisions += 1
                self.busy = True
                time.sleep(.0001)
                self.writes.append(value)
                result = super().write(value)
                self.busy = False
                return result
        stream = SlowStream()
        emitter = progress.ChildEvents(stream)
        def produce():
            for number in range(25):
                diagnostics.event('concurrent_event', number=number)
                emitter.update(dict(total=64*1024*1024, complete=True, started=time.monotonic()))
        with patch.object(diagnostics, '_active', None), patch.object(diagnostics, '_child_sequence', 0), \
             patch.object(sys, 'stdout', stream), patch.dict(os.environ, PETREL_PROGRESS_EVENTS='1'):
            workers = [threading.Thread(target=produce) for _ in range(8)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
        self.assertEqual(stream.collisions, 0)
        self.assertEqual(len(stream.writes), 400)
        self.assertTrue(all(value.endswith('\n') and value.count('\n') == 1 for value in stream.writes))
        decoded = [list(progress.child_frames(value))[0] for value in stream.writes]
        sequences = [data['child_sequence'] for prefix, data in decoded if prefix == diagnostics.PREFIX]
        self.assertEqual(sequences, list(range(1, 201)))
        self.assertEqual(sum(prefix == progress.EVENT_PREFIX for prefix, _ in decoded), 200)

    def test_mixed_progress_and_diagnostics_frames(self):
        metric = {'elapsed': 1, 'total': 100, 'done': 50, 'complete': False}
        line = self.frame() + progress.EVENT_PREFIX + json.dumps(metric) + self.frame()
        self.assertEqual([prefix for prefix, _ in progress.child_frames(line)],
                         [diagnostics.PREFIX, progress.EVENT_PREFIX, diagnostics.PREFIX])


if __name__ == '__main__':
    unittest.main(verbosity=2)
