# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Report links must survive mapped/UNC spellings without inventing mappings."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_delivery as delivery


class ReportLinkTests(unittest.TestCase):
    def test_regular_relative_link_is_portable_and_escaped(self):
        with patch.object(Path, 'resolve', side_effect=AssertionError('unneeded IO')):
            self.assertEqual(delivery.href('output/log & summary.txt', 'output/report.html'),
                             'log%20%26%20summary.txt')

    @unittest.skipUnless(os.name == 'nt', 'Windows mapped-drive semantics')
    def test_verified_mapped_parent_becomes_relative_unc_link(self):
        mapped = Path('L:/FV/case')
        unc = Path('//server/share/FV/case')
        def resolve(path, strict=False):
            self.assertTrue(strict)
            self.assertIn(path, (mapped, unc))
            return unc
        with patch.object(Path, 'resolve', resolve):
            self.assertEqual(delivery.href(mapped/'pending summary.txt', unc/'REPORT.html'),
                             'pending%20summary.txt')

    @unittest.skipUnless(os.name == 'nt', 'Windows drive semantics')
    def test_different_roots_use_explicit_machine_specific_link_and_warning(self):
        with patch.object(Path, 'resolve', lambda path, strict=False: path), \
             patch.object(delivery, 'event') as event:
            self.assertEqual(delivery.href('M:/other/log.txt', 'L:/FV/report.html'),
                             'file:///M:/other/log.txt')
        self.assertEqual(event.call_args.args[0], 'report_link_absolute_fallback')

    @unittest.skipUnless(os.name == 'nt', 'Windows drive semantics')
    def test_unavailable_mapping_is_not_inferred_from_matching_suffix(self):
        with patch.object(Path, 'resolve', side_effect=OSError('offline')), \
             patch.object(delivery, 'event') as event:
            self.assertEqual(delivery.href('L:/FV/case/log.txt', '//server/share/FV/case/report.html'),
                             'file:///L:/FV/case/log.txt')
        self.assertTrue(event.called)

    def test_diagnostics_update_keeps_existing_report_content(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            report = root/'REPORT.html'
            report.write_text('<html><body><main><p>Retained figure</p></main></body></html>')
            delivery.diagnostics_section(report, 'completed_with_gaps', [],
                                         root/'summary.txt', root/'log.txt', root/'events.jsonl')
            html = report.read_text(encoding='utf-8')
            self.assertIn('Retained figure', html)
            for name in ('summary.txt', 'log.txt', 'events.jsonl'):
                self.assertIn('href="'+name+'"', html)

    def test_exports_unknown_units_remain_linked_and_counted_once_per_object(self):
        files=[dict(category='logs',object_id='well1-log1',name='Gamma Ray',
                    path='logs/samples.'+ext,format=ext.upper(),units='unknown',
                    numerical_qc='exact_readback') for ext in ('csv','json')]
        html=delivery.exports_section(dict(files=files), 'output/EXPORTS', 'output/report.html')
        self.assertIn('1 export groups · 2 files', html)
        self.assertIn('1 export groups have unresolved reported units', html)
        self.assertIn('Unresolved — exported values retained', html)
        self.assertIn('href="EXPORTS/logs/samples.csv"', html)
        self.assertIn('exact_readback', html)


if __name__ == '__main__':
    unittest.main()
