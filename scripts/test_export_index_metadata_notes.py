# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic provenance-warning coverage for accepted artifact indexes."""
import csv
from html import escape
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_geoviewer_incremental as fixtures
write_json = fixtures.write_json
from geoviewer_delivery import publish_exports, exports_section


class ExportMetadataNotesTests(unittest.TestCase):
    setUp = fixtures.IncrementalDeliveryTests.setUp
    native = fixtures.IncrementalDeliveryTests.native

    def fixture(self, blob_type='RegValGrid2'):
        commit, artifact = self.native(status='missing_metadata')
        saved = json.loads(commit.read_text())
        record = saved['record']
        record['blob_type'] = blob_type
        record['reason'] = ('stored native QA values; computation validity and scalar units unresolved; '
                            'is_recalculation_needed=true; up_to_date=false <script>alert(1)</script>')
        record['qa_state'] = dict(is_recalculation_needed=True, up_to_date=False)
        for suffix in ('json', 'xyz', 'zmap'):
            other = artifact.with_name('metadata.'+suffix)
            other.write_text('synthetic fixture', encoding='utf-8')
            import hashlib
            record['artifacts'].append(dict(path=other.relative_to(self.package/'native_data').as_posix(),
                bytes=other.stat().st_size, sha256=hashlib.sha256(other.read_bytes()).hexdigest()))
        write_json(commit, saved)
        return record

    def assert_rows(self, rows, record):
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row['recovery_status'], 'missing_metadata')
            self.assertEqual(row['metadata_warning'], record['reason'])
            self.assertEqual(row['status'], 'exported')
            self.assertEqual(row['numerical_qc'], 'exact_readback')
            self.assertEqual(row['units'], 'unknown')

    def assert_saved(self, record):
        self.assert_rows(json.loads((self.destination/'FILE_INDEX.json').read_text())['files'], record)
        with (self.destination/'FILE_INDEX.csv').open(encoding='utf-8-sig', newline='') as handle:
            self.assert_rows(list(csv.DictReader(handle)), record)

    def assert_html(self, html, record):
        self.assertIn('missing_metadata', html)
        self.assertIn(escape(record['reason']), html)
        self.assertNotIn('<script>', html)

    def test_native_warning_survives_incremental_final_and_checkpoint_indexes(self):
        record = self.fixture()
        checkpoint = self.delivery.refresh(package=self.package)
        self.assert_rows(checkpoint['files'], record)
        self.assert_saved(record)
        self.assert_html((self.destination/'INCREMENTAL_INDEX.html').read_text(encoding='utf-8'), record)
        self.delivery.refresh(package=self.package)
        self.assert_saved(record)
        # Missing aggregate receipt: retain fresh rows only with a partial scope.
        merged = publish_exports(self.package, {}, self.destination, checkpoint_index=checkpoint)
        self.assertEqual(merged['files'], [])
        self.assertEqual(len(merged['partial_files']), 4)
        self.assertTrue(all(row['status']=='checkpoint_only_partial' and
                            row['metadata_warning']==record['reason'] for row in merged['partial_files']))
        self.assertEqual(json.loads((self.destination/'FILE_INDEX.json').read_text())['files'], [])
        self.assert_html(exports_section(merged, self.destination, self.report), record)
        # Completed aggregate receipt exercises the independent final native loop.
        write_json(self.package/'07_workflows_reports/native_recovery/native_recovery_report.json',
                   dict(objects=[record]))
        final = publish_exports(self.package, {}, self.destination, checkpoint_index=checkpoint)
        self.assert_rows(final['files'], record)
        self.assert_saved(record)
        self.assert_html(exports_section(final, self.destination, self.report), record)
        self.assert_html(self.delivery._html(final), record)

    def test_generic_log_reason_and_older_checkpoint_compatibility(self):
        record = self.fixture('FloatWellLog')
        checkpoint = self.delivery.refresh(package=self.package)
        self.assert_rows(checkpoint['files'], record)
        for row in checkpoint['files']:
            row.pop('recovery_status')
            row.pop('metadata_warning')
        self.assertIn('Log One', self.delivery._html(checkpoint))
        final = publish_exports(self.package, {}, self.destination, checkpoint_index=checkpoint)
        self.assertEqual(final['files'], [])
        for row in final['partial_files']:
            self.assertEqual(row['metadata_warning'], '')
            self.assertEqual(row['recovery_status'], '')
        self.assertIn('Log One', exports_section(final, self.destination, self.report))


if __name__ == '__main__':
    unittest.main()
