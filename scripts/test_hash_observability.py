"""Small source-only fixtures for isolated seismic SHA-256 visibility."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_lanes as lanes
from geoviewer_diagnostics import Diagnostics
import petrel_progress as progress
import petrel_project_seismic as seismic


class HashObservabilityTests(unittest.TestCase):
    def test_child_hash_frames_and_digest_without_source_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.sgy'
            data = b'A' * (2 * 1048576 + 3)
            source.write_bytes(data)
            output = root/'output'
            diagnostics = Diagnostics(root/'run_LOG.txt', root/'run_EVENTS.jsonl')
            try:
                result = lanes.seismic_job(source, output, {'_hash_only': True}, timeout=30)
            finally:
                diagnostics.close()
            self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(source.read_bytes(), data)
            log = next(output.glob('_job_*/worker.log')).read_text(encoding='utf-8')
            frames = [json.loads(line.removeprefix(progress.EVENT_PREFIX)) for line in log.splitlines()
                      if line.startswith(progress.EVENT_PREFIX)]
            self.assertEqual([item['state'] for item in frames], ['started', 'completed'])
            self.assertEqual(frames[0]['source'], str(source))
            self.assertEqual(frames[0]['total'], len(data))
            self.assertEqual(frames[-1]['done'], len(data))
            self.assertEqual(frames[-1]['sha256'], result['sha256'])
            events = [json.loads(line) for line in (root/'run_EVENTS.jsonl').read_text().splitlines()]
            self.assertEqual([item['event'] for item in events if item['event'].startswith('hash_file_')],
                             ['hash_file_started', 'hash_file_completed'])

    def test_multi_chunk_progress_is_throttled_and_file_local(self):
        stream = io.StringIO()
        with patch.object(progress.time, 'monotonic', side_effect=[0, 0, 1, 6, 6, 7, 7, 8]):
            observer = progress.HashFileEvents('file.sgy', 12, stream=stream, interval=5)
            observer.emit('started')
            observer.advance(3)
            observer.advance(3)
            observer.advance(3)
            observer.advance(3)
            observer.emit('completed')
        frames = [json.loads(line.removeprefix(progress.EVENT_PREFIX)) for line in stream.getvalue().splitlines()]
        self.assertEqual([f['state'] for f in frames], ['started', 'progress', 'completed'])
        self.assertEqual(frames[1]['done'], 6)
        self.assertEqual(frames[1]['file_fraction'], .5)
        self.assertEqual(frames[1]['file_eta_seconds'], 6)
        self.assertEqual(frames[-1]['done'], 12)

    def test_real_multi_chunk_hash_reports_bytes_read(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'chunks.sgy'
            data = b'first' * 300000 + b'last' * 300000
            source.write_bytes(data)
            stream = io.StringIO()
            observer = progress.HashFileEvents(source, len(data), stream=stream, interval=0)
            observer.emit('started')
            digest = progress.hash_file(source, on_bytes=observer.advance)
            observer.emit('completed', sha256=digest)
            frames = [json.loads(line.removeprefix(progress.EVENT_PREFIX)) for line in stream.getvalue().splitlines()]
            partial = [f for f in frames if f['state'] == 'progress']
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertTrue(partial)
            self.assertTrue(all(0 < f['done'] < len(data) for f in partial))
            self.assertEqual(frames[-1]['done'], len(data))

    def _main_request(self, root, source):
        request = root/'request.json'
        response = root/'response.json'
        request.write_text(json.dumps({'source':str(source),'output':str(root/'out'),
                                       'options':{'_hash_only':True}}))
        return request, response

    def test_hash_failure_frame_and_failed_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source.sgy'; source.write_bytes(b'abc')
            request, response = self._main_request(root, source)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['lane','--seismic-request',str(request),'--response',str(response)]), \
                 patch.object(progress, 'hash_file', side_effect=OSError('read failed')), \
                 contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(OSError, 'read failed'):
                    lanes.main()
            self.assertEqual(json.loads(response.read_text())['status'], 'failed')
            self.assertIn('"state": "failed"', output.getvalue())
            self.assertNotIn('"state": "completed"', output.getvalue())

    def test_source_change_fails_before_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source.sgy'; source.write_bytes(b'abc')
            request, response = self._main_request(root, source)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['lane','--seismic-request',str(request),'--response',str(response)]), \
                 patch('petrel_seismic_integrity.file_state', side_effect=[{'size_bytes':3}, {'size_bytes':4}]), \
                 contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(ValueError, 'Source changed'):
                    lanes.main()
            self.assertEqual(json.loads(response.read_text())['status'], 'failed')
            self.assertIn('"state": "failed"', output.getvalue())
            self.assertNotIn('"state": "completed"', output.getvalue())

    def test_completed_frame_requires_all_expected_bytes(self):
        output = io.StringIO()
        observer = progress.HashFileEvents('short.sgy', 12, stream=output)
        observer.advance(9)
        with self.assertRaisesRegex(ValueError, 'byte count'):
            observer.emit('completed', sha256=hashlib.sha256(b'short').hexdigest())
        self.assertNotIn('"state": "completed"', output.getvalue())

    def test_empty_file_can_complete_at_zero_bytes(self):
        output = io.StringIO()
        observer = progress.HashFileEvents('empty.sgy', 0, stream=output)
        observer.emit('completed', sha256=hashlib.sha256(b'').hexdigest())
        frame = json.loads(output.getvalue().removeprefix(progress.EVENT_PREFIX))
        self.assertEqual((frame['state'], frame['done'], frame['total']), ('completed', 0, 0))

    def test_short_hash_lane_records_failure_without_completed_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source.sgy'; source.write_bytes(b'abc')
            request, response = self._main_request(root, source)
            output = io.StringIO()
            def short_hash(path, on_bytes):
                on_bytes(2)
                return hashlib.sha256(b'ab').hexdigest()
            with patch.object(sys, 'argv', ['lane', '--seismic-request', str(request), '--response', str(response)]), \
                 patch.object(progress, 'hash_file', side_effect=short_hash), \
                 contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(ValueError, 'byte count'):
                    lanes.main()
            self.assertEqual(json.loads(response.read_text())['status'], 'failed')
            self.assertIn('"state": "failed"', output.getvalue())
            self.assertNotIn('"state": "completed"', output.getvalue())
            self.assertEqual(source.read_bytes(), b'abc')

    def test_hash_diagnostics_count_read_bytes_separately_from_stat(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source.sgy'; source.write_bytes(b'abc')
            original_stat = Path.stat
            final_size = 64 * 1024 * 1024
            def divergent_stat(path, *args, **kwargs):
                if path == source:
                    return type('FinalState', (), {'st_size': final_size})()
                return original_stat(path, *args, **kwargs)
            # Fault injection: a final file size need not equal bytes consumed.
            with patch.object(Path, 'stat', divergent_stat), \
                 patch('geoviewer_diagnostics.event') as event:
                digest = progress.hash_file(source)
            self.assertEqual(digest, hashlib.sha256(b'abc').hexdigest())
            event.assert_called_once()
            self.assertEqual(event.call_args.args[0], 'hash_completed')
            self.assertEqual(event.call_args.kwargs['bytes'], 3)
            self.assertEqual(event.call_args.kwargs['observed_size_bytes'], final_size)

    def test_hash_disabled_and_workload_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            rows = [dict(id='a',source=str(Path(temp)/'a.sgy'),format='.sgy',association='selected_project_store',
                         status='inventoried',file_state={'size_bytes':7}),
                    dict(id='b',source=str(Path(temp)/'b.sgy'),format='.sgy',association='unlinked_companion',
                         status='inventoried',file_state={'size_bytes':11}),
                    dict(id='c',source='remote.sgy',format='.sgy',association='native_model_external_reference',
                         status='missing')]
            inventory={'objects':rows}
            with patch.object(progress,'hash_file') as hash_file, patch.object(lanes,'seismic_job') as child:
                seismic.convert_project(inventory,Path(temp)/'out',False,full_hash=False)
            hash_file.assert_not_called();child.assert_not_called()
            self.assertEqual(inventory['workload']['proven_linked'],
                             {'available_count':1,'available_bytes':7,'missing_count':1})
            self.assertEqual(inventory['workload']['unlinked'],
                             {'available_count':1,'available_bytes':11,'missing_count':0})


if __name__ == '__main__':
    unittest.main()
