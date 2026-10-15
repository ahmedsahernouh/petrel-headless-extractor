# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Focused, D-drive-only fixtures for per-run result access."""

import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_result_access as access


_EVIDENCE = Path(__file__).resolve().parents[1] / 'build' / 'result_access_20261008' / 'worker'


class ResultAccessTests(unittest.TestCase):
    def setUp(self):
        _EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='result access ', dir=_EVIDENCE)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / "Run α & 'quoted'"
        self.run.mkdir()
        self.report = self.run / "REPORT α & 'quoted'.html"
        self.report.write_text('<h1>Actual report</h1>', encoding='utf-8')

    def _links(self, page):
        source = Path(page).read_text(encoding='utf-8')
        return [unquote(x) for x in re.findall(r'href="([^"]+)"', source)]

    def test_report_and_actual_exports_use_relative_escaped_links(self):
        exports = self.run / "EXPORTS α & 'quoted'"
        exports.mkdir()
        (exports / 'sample.csv').write_text('x,y\n1,2\n', encoding='utf-8')
        result = access.publish_result_access(self.report, exports,
                                              label='<Well & log>',
                                              status='in_progress', desktop=False)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['desktop_shortcuts'], [])
        page = Path(result['entry_page'])
        self.assertRegex(page.name, r'^OPEN_RESULTS_[0-9a-f]{12}\.html$')
        html = page.read_text(encoding='utf-8')
        self.assertIn('&lt;Well &amp; log&gt;', html)
        self.assertNotIn('<Well & log>', html)
        self.assertEqual(len(self._links(page)), 2)
        self.assertTrue(all(not link.startswith(('file:', '/', '\\\\')) for link in self._links(page)))
        self.assertEqual((page.parent / self._links(page)[0]).resolve(), self.report)
        self.assertEqual((page.parent / self._links(page)[1]).resolve(), exports)
        self.assertIn('%20', html)
        self.assertIn('%26', html)

    def test_report_only_and_zero_failed_statuses(self):
        for status in ('in_progress', 'zero_exports', 'failed'):
            result = access.publish_result_access(self.report, label='A&B',
                                                  status=status, desktop=False)
            html = Path(result['entry_page']).read_text(encoding='utf-8')
            self.assertEqual(len(self._links(result['entry_page'])), 1)
            self.assertIn(f'Run status: {status}', html)
            self.assertNotIn('Open exported files', html)

    def test_idempotent_update_and_whole_folder_relocation(self):
        first = access.publish_result_access(self.report, label='test',
                                             status='in_progress', desktop=False)
        second = access.publish_result_access(self.report, label='test',
                                              status='failed', desktop=False)
        self.assertEqual(first['entry_page'], second['entry_page'])
        self.assertIn('Run status: failed', Path(second['entry_page']).read_text(encoding='utf-8'))
        destination = self.root / 'Moved run'
        shutil.move(self.run, destination)
        relocated = destination / Path(second['entry_page']).name
        self.assertEqual((relocated.parent / self._links(relocated)[0]).resolve(),
                         destination / self.report.name)

    def test_missing_and_outside_targets_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            access.publish_result_access(self.run / 'missing.html', desktop=False)
        with self.assertRaises(FileNotFoundError):
            access.publish_result_access(self.report, self.run / 'missing exports', desktop=False)
        foreign = self.root / 'Other' / 'EXPORTS'
        foreign.mkdir(parents=True)
        with self.assertRaises(ValueError):
            access.publish_result_access(self.report, foreign, desktop=False)
        self.assertEqual(list(self.run.glob('OPEN_RESULTS_*.html')), [])

    def test_entry_page_collision_and_symlink_do_not_get_overwritten(self):
        first = access.publish_result_access(self.report, desktop=False)
        page = Path(first['entry_page'])
        page.write_text('unrelated file', encoding='utf-8')
        collision = access.publish_result_access(self.report, status='failed', desktop=False)
        self.assertEqual(collision['entry_page'], '')
        self.assertIn('collision', ' '.join(collision['warnings']).lower())
        self.assertEqual(page.read_text(encoding='utf-8'), 'unrelated file')
        page.unlink()
        try:
            page.symlink_to(self.report)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Symlinks unavailable: {exc}')
        linked = access.publish_result_access(self.report, desktop=False)
        self.assertEqual(linked['entry_page'], '')
        self.assertEqual(self.report.read_text(encoding='utf-8'), '<h1>Actual report</h1>')

    def test_ordinary_file_appearing_during_first_publish_is_preserved(self):
        operation = 'rename' if os.name == 'nt' else 'link'
        original_publish = getattr(os, operation)
        raced_page = []

        def other_writer_first(source, destination):
            raced_page.append(Path(destination))
            Path(destination).write_text('other writer', encoding='utf-8')
            return original_publish(source, destination)

        with patch.object(access.os, operation, side_effect=other_writer_first):
            result = access.publish_result_access(self.report, desktop=False)
        self.assertEqual(result['entry_page'], '')
        self.assertEqual(len(raced_page), 1)
        self.assertEqual(raced_page[0].read_text(encoding='utf-8'), 'other writer')
        self.assertIn('collision', ' '.join(result['warnings']).lower())

    def test_owned_page_recheck_rejects_intervening_replacement(self):
        first = access.publish_result_access(self.report, desktop=False)
        page = Path(first['entry_page'])
        original_owned = access._owned_page
        checks = []

        def replace_before_second_check(path, marker):
            checks.append(True)
            if len(checks) == 2:
                path.write_text('other writer', encoding='utf-8')
            return original_owned(path, marker)

        with patch.object(access, '_owned_page', side_effect=replace_before_second_check):
            result = access.publish_result_access(self.report, status='failed', desktop=False)
        self.assertEqual(result['entry_page'], '')
        self.assertEqual(page.read_text(encoding='utf-8'), 'other writer')
        self.assertEqual(len(checks), 2)

    def test_desktop_unavailable_still_publishes_page(self):
        result = access.publish_result_access(self.report, desktop=self.root / 'absent')
        self.assertTrue(Path(result['entry_page']).is_file())
        if os.name == 'nt':
            self.assertIn('Desktop unavailable', ' '.join(result['warnings']))
        else:
            self.assertEqual(result['warnings'], [])

    @unittest.skipUnless(os.name == 'nt', 'Windows .lnk readback')
    def test_windows_shortcuts_are_verified_and_injection_free(self):
        desktop = self.root / 'Fake Desktop'
        desktop.mkdir()
        exports = self.run / 'EXPORTS'
        exports.mkdir()
        (exports / 'actual.csv').write_text('actual', encoding='utf-8')
        sentinel = self.root / 'injected.txt'
        label = "α ' ; Set-Content -Path '" + str(sentinel) + "' -Value bad; #"
        result = access.publish_result_access(self.report, exports, label=label,
                                              status='failed', desktop=desktop)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(len(result['desktop_shortcuts']), 2)
        self.assertFalse(sentinel.exists())
        for item in result['desktop_shortcuts']:
            self.assertTrue(Path(item).is_file())
            inspected = access._run_powershell({'action': 'inspect', 'link': str(item)})
            self.assertIn(inspected['target'], (str(self.report), str(exports)))
            self.assertEqual(inspected['arguments'], '')
            self.assertIn('FieldViewer result access v1', inspected['description'])
        again = access.publish_result_access(self.report, exports, label=label,
                                             status='zero_exports', desktop=desktop)
        self.assertEqual(sorted(again['desktop_shortcuts']), sorted(result['desktop_shortcuts']))
        self.assertEqual(again['warnings'], [])
        only_report = access.publish_result_access(self.report, label=label,
                                                   status='failed', desktop=desktop)
        self.assertEqual(len(only_report['desktop_shortcuts']), 1)
        self.assertTrue(any('Files' in Path(x).name for x in result['desktop_shortcuts']))
        self.assertTrue(all(Path(x).exists() for x in result['desktop_shortcuts']))

    @unittest.skipUnless(os.name == 'nt', 'Windows .lnk collision')
    def test_unrelated_desktop_shortcut_collision_is_preserved(self):
        desktop = self.root / 'Fake Desktop'
        desktop.mkdir()
        first = access.publish_result_access(self.report, label='Collision', desktop=desktop)
        link = Path(first['desktop_shortcuts'][0])
        link.write_bytes(b'unrelated shortcut bytes')
        result = access.publish_result_access(self.report, label='Collision',
                                              status='failed', desktop=desktop)
        self.assertEqual(link.read_bytes(), b'unrelated shortcut bytes')
        self.assertEqual(result['desktop_shortcuts'], [])
        self.assertIn('collision', ' '.join(result['warnings']).lower())

    @unittest.skipUnless(os.name == 'nt', 'Windows ASCII .lnk readback')
    def test_ascii_target_shortcut_and_target_existence_gate(self):
        ascii_run = self.root / 'ASCII Run'
        ascii_run.mkdir()
        report = ascii_run / 'REPORT.html'
        report.write_text('Actual report', encoding='utf-8')
        desktop = self.root / 'Fake Desktop'
        desktop.mkdir()
        result = access.publish_result_access(report, label='ASCII run', desktop=desktop)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(len(result['desktop_shortcuts']), 1)
        inspected = access._run_powershell({'action': 'inspect',
                                           'link': result['desktop_shortcuts'][0]})
        self.assertEqual(inspected['target'], str(report))
        self.assertEqual(inspected['arguments'], '')
        report.unlink()
        missing = access._run_powershell({'action': 'shortcuts', 'items': [{
            'link': str(desktop / 'Missing target.lnk'),
            'target': str(report), 'target_type': 'file',
            'marker': 'FieldViewer result access v1; missing; report'}]})
        self.assertEqual(missing['shortcuts'], [])
        self.assertTrue(missing['warnings'])

if __name__ == '__main__':
    unittest.main()
