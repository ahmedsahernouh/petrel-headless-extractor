# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic-only tests of failures before the spatial decoder writes its receipt."""
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_petrel_native_spatial_zero_gui as spatial
import geoviewer_stage as stage


class SpatialEarlyFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / 'export'
        self.package.mkdir()
        self.source = self.root / 'Data.ptd'
        with contextlib.closing(sqlite3.connect(self.source)) as connection:
            connection.execute('CREATE TABLE data (data_pk INTEGER, droid TEXT, name TEXT, version INTEGER, blob_type TEXT)')
            connection.execute('CREATE TABLE blob_parts (data_fk INTEGER, part INTEGER, blob_data BLOB)')
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.report = self.package / '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        self.argv = ['--export-package', str(self.package), '--data-file', str(self.source)]

    def tearDown(self):
        self.temp.cleanup()

    def child(self, argv=None):
        env = dict(os.environ, PETREL_PROGRESS_EVENTS='1', PYTHONDONTWRITEBYTECODE='1')
        script = Path(spatial.__file__).resolve()
        # The bundled interpreter has an isolated _pth that points at frozen
        # scripts. Bind the current source directory before running this CLI.
        bootstrap = ('import importlib.util,pathlib,runpy,sys; '
                     'entry=pathlib.Path(sys.argv[1]).resolve(); '
                     'sys.argv=sys.argv[1:]; '
                     'sys.path.insert(0,str(entry.parent)); '
                     'assert pathlib.Path(importlib.util.find_spec("petrel_native_geometry_attributes").origin).resolve().parent == entry.parent; '
                     'runpy.run_path(str(entry),run_name="__main__")')
        return subprocess.run([sys.executable, '-B', '-c', bootstrap, str(script), *(argv or self.argv)],
                              capture_output=True, text=True, env=env, timeout=30)

    def test_child_schema_failure_receipt_and_stage_cause(self):
        result = self.child()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Traceback (most recent call last)', result.stderr)
        self.assertIn('no such column: time_stamp', result.stderr)
        report = json.loads(self.report.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertIsNone(report['source_unchanged'])
        self.assertEqual(report['source_integrity_status'], 'unchecked')
        self.assertEqual(report['error']['exception_type'], 'OperationalError')
        self.assertEqual(report['error']['source_path'], str(self.source.resolve()))
        for key in ('errno', 'winerror', 'traceback', 'operation', 'category'):
            self.assertIn(key, report['error'])
        events = [json.loads(line.split(':', 1)[1]) for line in result.stdout.splitlines()
                  if line.startswith('GEOVIEWER_EVENT:')]
        self.assertEqual(events[-1]['event'], 'spatial_failed')
        self.assertEqual(events[-1]['traceback'], report['error']['traceback'])
        self.assertEqual(events[-1]['report_path'], str(self.report.resolve()))
        self.assertEqual(stage.record(self.package, 'spatial', result.returncode, self.report), 0)
        row = json.loads((self.package / '01_project_metadata/pipeline_stages.json').read_text())[-1]
        self.assertEqual(row['reason'], 'no such column: time_stamp')
        self.assertEqual(row['error']['exception_type'], 'OperationalError')
        self.assertFalse(row['fatal'])
        self.assertEqual(report['outputs'], [])
        self.assertFalse((self.package / '05_spatial').exists())
        self.assertFalse((self.package / '02_wells').exists())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)

    def test_existing_completed_and_partial_receipts_are_preserved(self):
        self.report.parent.mkdir(parents=True)
        for status in ('completed', 'partial'):
            with self.subTest(status=status):
                original = json.dumps({'status': status, 'outputs': ['retained.csv']}).encode()
                self.report.write_bytes(original)
                result = self.child()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.report.read_bytes(), original)
                events = [json.loads(line.split(':', 1)[1]) for line in result.stdout.splitlines()
                          if line.startswith('GEOVIEWER_EVENT:')]
                failure_path = Path(events[-1]['report_path'])
                self.assertNotEqual(failure_path, self.report)
                self.assertIn(str(failure_path), result.stderr)
                receipt = json.loads(failure_path.read_text())
                self.assertEqual(receipt['preserved_report_path'], str(self.report.resolve()))
                self.assertEqual(receipt['error']['message'], 'no such column: time_stamp')

    def test_nonexistent_output_is_not_created(self):
        absent = self.root / 'absent'
        result = self.child(['--export-package', str(absent), '--data-file', str(self.source)])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(absent.exists())
        self.assertIn('Export package directory was not found', result.stderr)
        self.assertIn('Secondary failure saving spatial diagnostics', result.stderr)

    def test_secondary_write_and_event_errors_keep_original_and_exit_code(self):
        stderr = io.StringIO()
        with mock.patch.object(sys, 'argv', ['spatial', *self.argv]), \
             mock.patch('geoviewer_io.atomic_json', side_effect=OSError(errno.ENOSPC, 'synthetic disk full')), \
             mock.patch('geoviewer_diagnostics.event', side_effect=OSError('synthetic event failure')), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(spatial.main(), 2)
        output = stderr.getvalue()
        self.assertIn('sqlite3.OperationalError: no such column: time_stamp', output)
        self.assertIn('Traceback (most recent call last)', output)
        self.assertIn('synthetic disk full', output)
        self.assertIn('synthetic event failure', output)
        self.assertFalse(self.report.exists())

    def test_keyboard_interrupt_keeps_existing_behavior(self):
        with mock.patch.object(sys, 'argv', ['spatial', *self.argv]), \
             mock.patch.object(spatial, 'run', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                spatial.main()
        self.assertFalse(self.report.exists())


if __name__ == '__main__':
    unittest.main()
