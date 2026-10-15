# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic support ZIP coverage for preserved spatial failure diagnostics."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import geoviewer_support as support


class SpatialFailureSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spatial_support_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root/'Trial_data'
        self.reports = self.run/'extraction/package/Trial/07_workflows_reports/native_spatial_zero_gui'
        self.reports.mkdir(parents=True)
        self.canonical = self.reports/'native_spatial_decode_report.json'
        self.canonical.write_text(json.dumps({'status': 'completed', 'outputs': ['retained.csv']}))
        self.failure = self.reports/('native_spatial_decode_failure_'+'a'*32+'.json')
        self.failure.write_text(json.dumps({'status': 'failed', 'operation': 'run_native_spatial',
            'preserved_report_path': str(self.canonical),
            'error': {'exception_type': 'OperationalError', 'message': 'no such column: time_stamp',
                      'traceback': 'Traceback: sqlite3.OperationalError: no such column: time_stamp'},
            'api_key': 'PRIVATE_SECRET', 'native_payload': 'RAW_NATIVE_PAYLOAD'}))

    def bundle(self):
        with redirect_stdout(io.StringIO()):
            path = support.build_bundle({'run': str(self.run), 'status': 'failed'},
                                        destination=self.root/'support.zip')
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            files = {name: archive.read(name).decode() for name in archive.namelist()}
        return files, json.loads(files['CONTENTS.json'])

    def test_zip_preserves_canonical_and_maps_scrubbed_causal_failure(self):
        originals = {p: p.read_bytes() for p in (self.canonical, self.failure)}
        (self.reports/'payload.ptd').write_text('UNRELATED_NATIVE_STORE')
        files, index = self.bundle()
        canonical = next(row for row in index['files'] if row['role'] == self.canonical.name)
        failure = next(row for row in index['files'] if row['role'] == 'native_spatial_decode_failure')
        self.assertEqual(failure['source_name'], self.failure.name)
        self.assertNotEqual(canonical['member'], failure['member'])
        self.assertEqual(json.loads(files[canonical['member']])['status'], 'completed')
        result = json.loads(files[failure['member']])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['message'], 'no such column: time_stamp')
        self.assertIn('Traceback', result['error']['traceback'])
        self.assertEqual(json.loads(files['ENVIRONMENT.json'])['status'], 'failed')
        for value in ('PRIVATE_SECRET', 'RAW_NATIVE_PAYLOAD', 'UNRELATED_NATIVE_STORE'):
            self.assertNotIn(value, '\n'.join(files.values()))
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_discovery_rejects_lookalikes_wrong_roots_and_link_escapes(self):
        name = self.failure.name
        for folder in (self.run, self.run/'source', self.run/'extraction/package/Trial/native_data',
                       self.run/'extraction/package/Trial/07_workflows_reports/other'):
            folder.mkdir(parents=True, exist_ok=True)
            (folder/name).write_text('WRONG_ROOT')
        for name in ('native_spatial_decode_failure_notes.json',
                     'native_spatial_decode_failure_'+'b'*31+'.json',
                     'native_spatial_decode_failure_'+'c'*32+'.json.tmp'):
            (self.reports/name).write_text('LOOKALIKE')
        linked = self.reports/('native_spatial_decode_failure_'+'d'*32+'.json')
        linked.write_text('SYMLINK_ESCAPE')
        escaped = self.reports/('native_spatial_decode_failure_'+'e'*32+'.json')
        escaped.write_text('RESOLVED_ESCAPE')
        original_link, original_resolve = Path.is_symlink, Path.resolve
        def resolve(path, *args, **kwargs):
            return self.root/'outside.json' if path == escaped else original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'is_symlink', lambda p: p == linked or original_link(p)), \
             patch.object(Path, 'resolve', resolve):
            files, _ = self.bundle()
        text = '\n'.join(files.values())
        for marker in ('WRONG_ROOT', 'LOOKALIKE', 'SYMLINK_ESCAPE', 'RESOLVED_ESCAPE'):
            self.assertNotIn(marker, text)

    def test_locked_failure_is_reported_as_unavailable_without_losing_canonical(self):
        original_read = Path.read_text
        def read(path, *args, **kwargs):
            if path == self.failure:
                raise PermissionError(13, 'synthetic sharing violation')
            return original_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            _, index = self.bundle()
        self.assertTrue(any(row['role'] == self.canonical.name for row in index['files']))
        missing = next(row for row in index['unavailable'] if row['role'] == 'native_spatial_decode_failure')
        self.assertEqual(missing['source_name'], self.failure.name)
        self.assertIn('sharing violation', missing['error'])


if __name__ == '__main__':
    unittest.main()
