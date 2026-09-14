# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Progress controls: real reads, silent child liveness, logs and tree cleanup."""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import petrel_progress as p


class ProgressTests(unittest.TestCase):
    def tearDown(self):
        p.set_reporter(None)

    def test_hashes_match_and_count_actual_bytes_without_source_changes(self):
        events = []
        class Recorder:
            def update(self, metric):
                events.append(metric.copy())
        with tempfile.TemporaryDirectory() as temp:
            files = [Path(temp)/str(n) for n in range(3)]
            data = [b'a' * (3*1048576+7), b'', b'xyz']
            for file, content in zip(files, data):
                file.write_bytes(content)
            p.set_reporter(Recorder())
            with p.hash_batch('Source pass', files):
                hashes = [p.hash_file(file) for file in files]
            self.assertEqual(hashes, [hashlib.sha256(x).hexdigest() for x in data])
            self.assertEqual(events[-1]['done'], sum(map(len, data)))
            self.assertEqual(events[-1]['files_done'], 3)
            self.assertTrue(events[-1]['complete'])
            self.assertTrue(any(0 < e['done'] < e['total'] for e in events))
            self.assertEqual([file.read_bytes() for file in files], data)

    def test_hash_failure_never_marks_task_complete(self):
        class Recorder:
            def update(self, metric):
                self.last = metric.copy()
        recorder = Recorder()
        p.set_reporter(recorder)
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/'data'; file.write_bytes(b'original')
            with self.assertRaises(OSError):
                with p.hash_batch('Source', [file]):
                    with patch.object(Path, 'open', side_effect=OSError('read failed')):
                        p.hash_file(file)
            self.assertFalse(recorder.last['complete'])
            self.assertEqual(recorder.last['done'], 0)

    def test_eta_is_local_and_terminal_completion_is_not_premature(self):
        output = io.StringIO()
        display = p.ConsoleProgress(stream=output)
        metric = dict(label='Source hash', total=1000, done=250, files_done=0,
                      files_total=1, file='cube.segy', started=time.monotonic()-4, complete=False)
        display.update(metric)
        self.assertIn('25.0%', display.render())
        self.assertIn('Hash ETA ~00:00:12', display.render())
        metric.update(done=1000)
        display.update(metric)
        self.assertNotIn('100.0%', display.render())
        display.phase(12, 'Final receipt')
        self.assertNotIn('ETA', display.render())
        display.close(success=False)
        self.assertIn('11/12 stages complete', output.getvalue())
        self.assertNotIn('12/12 stages complete', output.getvalue())
        display.close(success=True)
        self.assertIn('12/12 stages complete', output.getvalue())

    def test_long_elapsed_time_does_not_wrap_at_a_day(self):
        self.assertEqual(p.duration(90061), '25:01:01')

    def test_console_message_clears_progress_before_printing(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        output = Terminal()
        display = p.ConsoleProgress(stream=output)
        display._write('working', redraw=True)
        display.message('saved result', file=output)
        self.assertTrue(output.getvalue().endswith('\r       \rsaved result\n'))
        self.assertEqual(display.line_width, 0)

    def test_child_events_transport_and_unknown_lines_do_not_change_stage(self):
        output = io.StringIO()
        emitter = p.ChildEvents(output)
        emitter.update(dict(label='Hashing file', total=100*1024*1024, done=1024,
                            files_total=1, files_done=0, file='test.segy',
                            started=time.monotonic()-4, complete=False))
        display = p.ConsoleProgress(stream=io.StringIO())
        display.child_line('Stage 3/6: companion source inventory')
        display.child_line(output.getvalue().strip())
        self.assertEqual(display.number, 5)
        self.assertIn('Hash ETA', display.render())
        display.child_line('PETREL_PROGRESS:broken')
        display.child_line('Any other output')
        self.assertEqual(display.number, 5)

    def test_silent_child_has_heartbeat_and_live_complete_log(self):
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            display = p.ConsoleProgress(stream=output, interval=0.05).start()
            try:
                command = [sys.executable, '-B', '-c',
                           "import sys,time; print('Stage 1/6: copy',flush=True); "
                           "print('child stderr',file=sys.stderr,flush=True); time.sleep(1.1); "
                           "print('last line',flush=True)"]
                code = p.run_pipeline(command, temp, Path(temp)/'log.txt', 10)
                self.assertEqual(code, 0)
                log = (Path(temp)/'log.txt').read_text()
                self.assertIn('child stderr', log)
                self.assertIn('last line', log)
                self.assertGreaterEqual(output.getvalue().count('Stage 3/12 running'), 2)
                self.assertIn('Elapsed 00:00:01', output.getvalue())
            finally:
                display.close()

    def test_nonzero_child_is_preserved_without_console_progress_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp)/'log.txt'
            code = p.run_pipeline([sys.executable, '-B', '-c',
                                   "import sys; print('failure evidence'); sys.exit(7)"], temp, log, 10)
            self.assertEqual(code, 7)
            self.assertIn('failure evidence', log.read_text())
            self.assertIsNone(p._reporter)

    @unittest.skipUnless(os.name == 'nt', 'Windows owned-process-tree control')
    def test_timeout_stops_child_and_grandchild_and_retains_log(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp)/'grandchild.pid'
            child = Path(temp)/'child.py'
            child.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                             "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                             "Path(sys.argv[1]).write_text(str(p.pid))\n"
                             "print('before timeout',flush=True)\ntime.sleep(60)\n")
            log = Path(temp)/'log.txt'
            with self.assertRaises(subprocess.TimeoutExpired):
                p.run_pipeline([sys.executable, '-B', str(child), str(pidfile)], temp, log, 2)
            self.assertIn('before timeout', log.read_text())
            pid = int(pidfile.read_text())
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                code = ctypes.c_ulong()
                try:
                    self.assertTrue(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)))
                    self.assertNotEqual(code.value, 259, 'grandchild still active')
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main(verbosity=2)
