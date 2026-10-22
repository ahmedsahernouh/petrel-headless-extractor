# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Path-limited companion files retain source identity without blocking peers."""
import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_output_paths as paths
import portable_petrel_companion_extract as companion


TOPS = ('VERSION 2\nBEGIN HEADER\nWell\nSurface\nMD\nEND HEADER\n'
        '"A" "Top A" 1100\n')


class CompanionPathIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='companion_paths_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_root = self.root / 'source'
        self.source_root.mkdir()
        self.project = self.source_root / 'Fixture.pet'
        self.project.write_text('fixture')
        (self.source_root / 'Fixture.ptd').mkdir()
        self.package = self.root / 'package'
        (self.package / '00_manifest').mkdir(parents=True)
        (self.package / '00_manifest/export_manifest.csv').write_text('export_file,sha256\n')

    def source(self, name):
        source = self.source_root / name
        source.write_text(TOPS, encoding='utf-8')
        return source

    def workbook(self, name, sheets):
        source = self.source_root / name
        book = Workbook()
        book.remove(book.active)
        for title in sheets:
            book.create_sheet(title).append([title, 1])
        book.save(source)
        book.close()
        return source

    def run_companion(self):
        argv = ['companion', '--project-file', str(self.project),
                '--export-package', str(self.package), '--project-name', 'Fixture',
                '--mode', 'convert']
        with patch.object(sys, 'argv', argv), patch('builtins.print'):
            self.assertEqual(companion.main(), 0)
        with (self.package / '00_manifest/companion_source_inventory.csv').open(newline='', encoding='utf-8') as stream:
            rows = {row['source_relative_path']: row for row in csv.DictReader(stream)}
        report = json.loads((self.package / '07_workflows_reports/portable_extractor/companion_capability_report.json').read_text())
        with (self.package / '00_manifest/export_manifest.csv').open(newline='', encoding='utf-8') as stream:
            registered = {row['export_file'] for row in csv.DictReader(stream)}
        return rows, report, registered

    def test_long_preservation_path_is_skipped_while_short_top_is_converted(self):
        short = self.source('short.txt')
        long = self.source('long_' + 'x' * 95 + '.txt')
        source_hash = companion.sha256(long)
        short_family = companion.conversion_output_family(
            'petrel_well_tops_ascii', self.package / '02_wells/well_tops/short.txt')
        long_target = self.package / '09_source_companions' / long.name
        budget = max(paths.windows_length(path) for path in
                     [self.package / '09_source_companions/short.txt', *short_family]) + 1
        self.assertLess(budget, paths.windows_length(long_target))
        with patch.object(paths, 'FILE_LIMIT', budget):
            rows, report, registered = self.run_companion()
        self.assertEqual(rows[long.name]['status'], 'skipped_path_limit')
        self.assertEqual(rows[long.name]['sha256'], source_hash)
        self.assertEqual(rows[long.name]['size_bytes'], str(long.stat().st_size))
        self.assertEqual(rows[long.name]['preserved_file'], '')
        self.assertEqual(rows[long.name]['converted_files'], '')
        self.assertFalse(long_target.exists())
        self.assertEqual(rows[short.name]['status'], 'converted_and_preserved')
        self.assertEqual((self.package / rows[short.name]['preserved_file']).read_bytes(), short.read_bytes())
        self.assertEqual(len(rows[short.name]['converted_files'].split(';')), 2)
        self.assertTrue(report['has_gaps'])
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['counts']['skipped_path_limit'], 1)
        self.assertEqual(report['counts']['converted_source_files'], 1)
        self.assertNotIn('09_source_companions\\' + long.name, registered)
        self.assertIn(long.name, (self.package / '99_unexported_or_manual/portable_unsupported_inventory.csv').read_text())

    def test_derivative_path_limit_keeps_hash_verified_source_copy(self):
        source = self.source('tops.txt')
        preserved = self.package / '09_source_companions/tops.txt'
        family = companion.conversion_output_family(
            'petrel_well_tops_ascii', self.package / '02_wells/well_tops/tops.txt')
        budget = paths.windows_length(preserved)
        self.assertLess(budget, max(map(paths.windows_length, family)))
        with patch.object(paths, 'FILE_LIMIT', budget):
            rows, report, registered = self.run_companion()
        row = rows[source.name]
        self.assertEqual(row['status'], 'preserved_conversion_path_limited')
        self.assertEqual(row['conversion_gap'], 'derivative_output_path_limit')
        self.assertEqual(row['sha256'], companion.sha256(source))
        self.assertEqual((self.package / row['preserved_file']).read_bytes(), source.read_bytes())
        self.assertEqual(row['converted_files'], '')
        self.assertFalse(any(path.exists() for path in family))
        self.assertEqual(report['counts']['conversion_path_limited'], 1)
        self.assertTrue(report['has_gaps'])
        self.assertIn(row['preserved_file'], registered)
        self.assertFalse(any(str(path.relative_to(self.package)).replace('/', '\\') in registered for path in family))

    def test_real_short_sheet_converts_when_hypothetical_reserve_would_fail(self):
        source = self.workbook('book.xlsx', ['Data'])
        destination = self.package / '10_converted_ascii/workbooks/book'
        summary = destination / 'workbook.summary.json'
        csv_path = destination / 'Data.csv'
        hypothetical = destination / ('s' * paths.WORKBOOK_FILENAME_RESERVE)
        budget = max(paths.windows_length(summary), paths.windows_length(csv_path),
                     paths.windows_length(self.package / '09_source_companions/book.xlsx'))
        self.assertLess(budget, paths.windows_length(hypothetical))
        with patch.object(paths, 'FILE_LIMIT', budget):
            rows, report, registered = self.run_companion()
        row = rows[source.name]
        self.assertEqual(row['status'], 'converted_and_preserved')
        self.assertEqual(row['conversion_gap'], '')
        self.assertEqual((self.package / row['preserved_file']).read_bytes(), source.read_bytes())
        self.assertEqual({self.package / path for path in row['converted_files'].split(';')},
                         {csv_path, summary})
        self.assertTrue(csv_path.is_file())
        self.assertIn('Data,1', csv_path.read_text())
        self.assertIn(str(csv_path.relative_to(self.package)).replace('/', '\\'), registered)
        self.assertEqual(report['counts']['converted_source_files'], 1)
        self.assertEqual(report['counts']['conversion_path_limited'], 0)

    def test_native_store_files_are_pruned_before_stat_or_resolve(self):
        native_root = self.source_root / 'Fixture.ptd'
        native_files = set()
        for index in range(30):
            path = native_root / f'object_{index}.bin'
            path.write_bytes(b'native store bytes')
            native_files.add(path)
        short = self.source('short.txt')
        original_resolve = Path.resolve
        original_is_file = Path.is_file

        def checked_resolve(path, *args, **kwargs):
            self.assertNotIn(path, native_files, 'Excluded native file resolved over the network')
            return original_resolve(path, *args, **kwargs)

        def checked_is_file(path, *args, **kwargs):
            self.assertNotIn(path, native_files, 'Excluded native file statted over the network')
            return original_is_file(path, *args, **kwargs)

        with patch.object(Path, 'resolve', checked_resolve), patch.object(Path, 'is_file', checked_is_file):
            rows, report, _ = self.run_companion()
        self.assertEqual(set(rows), {short.name})
        self.assertEqual(rows[short.name]['status'], 'converted_and_preserved')
        self.assertEqual(report['counts']['converted_source_files'], 1)

    def test_companion_link_resolving_into_native_store_stays_excluded(self):
        native = self.source_root / 'Fixture.ptd/object.bin'
        native.write_bytes(b'native store bytes')
        alias = self.source('native_alias.txt')
        short = self.source('short.txt')
        original_resolve = Path.resolve

        def resolve_alias(path, *args, **kwargs):
            if path == alias:
                return native
            return original_resolve(path, *args, **kwargs)

        # A simulated resolved alias avoids requiring symlink creation privileges.
        with patch.object(Path, 'resolve', resolve_alias):
            rows, report, _ = self.run_companion()
        self.assertEqual(set(rows), {short.name})
        self.assertEqual(rows[short.name]['status'], 'converted_and_preserved')
        self.assertEqual(report['counts']['converted_source_files'], 1)

    def test_actual_long_sheet_cleans_partial_csvs_and_short_peer_converts(self):
        long = self.workbook('long.xlsx', ['Data', 'L' * 31])
        short = self.workbook('peer.xlsx', ['Data'])
        long_destination = self.package / '10_converted_ascii/workbooks/long'
        peer_destination = self.package / '10_converted_ascii/workbooks/peer'
        long_csv = long_destination / (('L' * 31) + '.csv')
        budget = max(paths.windows_length(path) for path in (
            long_destination / 'workbook.summary.json',
            peer_destination / 'workbook.summary.json',
            peer_destination / 'Data.csv',
            self.package / '09_source_companions/long.xlsx',
            self.package / '09_source_companions/peer.xlsx'))
        self.assertLess(budget, paths.windows_length(long_csv))
        with patch.object(paths, 'FILE_LIMIT', budget):
            rows, report, registered = self.run_companion()
        self.assertEqual(rows[long.name]['status'], 'preserved_conversion_path_limited')
        self.assertEqual(rows[long.name]['conversion_gap'], 'derivative_output_path_limit')
        self.assertEqual(rows[long.name]['converted_files'], '')
        self.assertEqual((self.package / rows[long.name]['preserved_file']).read_bytes(), long.read_bytes())
        self.assertFalse(long_destination.exists())
        self.assertEqual(rows[short.name]['status'], 'converted_and_preserved')
        self.assertTrue((peer_destination / 'Data.csv').is_file())
        self.assertIn(str((peer_destination / 'Data.csv').relative_to(self.package)).replace('/', '\\'), registered)
        self.assertFalse(any('workbooks\\long\\' in item for item in registered))
        self.assertEqual(report['counts']['conversion_path_limited'], 1)
        self.assertEqual(report['counts']['converted_source_files'], 1)
        self.assertTrue(report['has_gaps'])

    def test_runtime_path_error_removes_only_new_partial_copy(self):
        source = self.source('tops.txt')
        target = self.package / '09_source_companions/tops.txt'
        original_copy = shutil.copy2

        def fail_after_partial(src, dst):
            if Path(src) == source:
                Path(dst).write_bytes(b'partial')
                exc = OSError('filename too long')
                exc.winerror = 206
                raise exc
            return original_copy(src, dst)

        with patch.object(companion.shutil, 'copy2', side_effect=fail_after_partial):
            rows, report, registered = self.run_companion()
        self.assertEqual(rows[source.name]['status'], 'skipped_path_limit')
        self.assertFalse(target.exists())
        self.assertTrue(report['has_gaps'])
        self.assertFalse(any(name.startswith('09_source_companions') for name in registered))

    def test_runtime_path_error_and_cleanup_path_error_keep_short_peer(self):
        long = self.source('a_problem.txt')
        short = self.source('short.txt')
        target = self.package / '09_source_companions/a_problem.txt'
        original_copy = shutil.copy2
        original_unlink = Path.unlink

        def path_error():
            exc = OSError('filename too long')
            exc.winerror = 206
            return exc

        def fail_copy(src, dst):
            if Path(src) == long:
                raise path_error()
            return original_copy(src, dst)

        def fail_cleanup(path, *args, **kwargs):
            if path == target:
                raise path_error()
            return original_unlink(path, *args, **kwargs)

        with patch.object(companion.shutil, 'copy2', side_effect=fail_copy), \
             patch.object(Path, 'unlink', fail_cleanup):
            rows, report, registered = self.run_companion()
        self.assertEqual(rows[long.name]['status'], 'skipped_path_limit')
        self.assertIn('Cleanup unverified', rows[long.name]['error'])
        self.assertEqual(rows[long.name]['sha256'], companion.sha256(long))
        self.assertEqual(rows[long.name]['preserved_file'], '')
        self.assertEqual(rows[short.name]['status'], 'converted_and_preserved')
        self.assertEqual((self.package / rows[short.name]['preserved_file']).read_bytes(), short.read_bytes())
        self.assertNotIn('09_source_companions\\a_problem.txt', registered)
        self.assertEqual(report['counts']['skipped_path_limit'], 1)
        self.assertTrue(report['has_gaps'])

    def test_copy_hash_mismatch_and_source_change_remain_fatal(self):
        source = self.source('tops.txt')
        target = self.package / '09_source_companions/tops.txt'
        actual_hash = companion.sha256

        def wrong_target_hash(path):
            return '0' * 64 if Path(path) == target else actual_hash(path)

        with patch.object(companion, 'sha256', side_effect=wrong_target_hash):
            with self.assertRaisesRegex(RuntimeError, 'Copy hash mismatch'):
                self.run_companion()
        target.unlink()
        original_copy = shutil.copy2

        def change_source(src, dst):
            result = original_copy(src, dst)
            if Path(src) == source:
                source.write_text(TOPS + '# changed\n')
            return result

        with patch.object(companion.shutil, 'copy2', side_effect=change_source):
            with self.assertRaisesRegex(RuntimeError, 'Companion changed during processing'):
                self.run_companion()


if __name__ == '__main__':
    unittest.main()
