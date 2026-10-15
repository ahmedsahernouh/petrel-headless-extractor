# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Small companion-stage fixtures for referenced seismic inventory rows."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portable_petrel_companion_extract as companion


HEADERS = ['export_id', 'project_name', 'petrel_version', 'export_date_utc',
           'source_object_path', 'source_object_type', 'export_format',
           'export_file', 'export_status', 'validation_status', 'sha256', 'notes']


class CompanionReferenceRowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'fixture.pet').write_bytes(b'project')
        (self.source / 'fixture.ptd').mkdir()
        self.package = self.root / 'package'
        manifest = self.package / '00_manifest/export_manifest.csv'
        manifest.parent.mkdir(parents=True)
        with manifest.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()

    def add(self, name, contents=b'data'):
        path = self.source / name
        path.write_bytes(contents)
        return path

    def run_stage(self, mode='convert', max_file_bytes=None, reference_seismic=True):
        args = ['companion', '--project-file', str(self.source/'fixture.pet'),
                '--export-package', str(self.package), '--project-name', 'fixture',
                '--mode', mode]
        if reference_seismic:
            args.append('--reference-seismic')
        if max_file_bytes is not None:
            args += ['--max-file-bytes', str(max_file_bytes)]
        with patch.object(sys, 'argv', args):
            self.assertEqual(companion.main(), 0)
        with (self.package/'00_manifest/companion_source_inventory.csv').open(newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        report = json.loads((self.package/'07_workflows_reports/portable_extractor/companion_capability_report.json').read_text(encoding='utf-8'))
        with (self.package/'00_manifest/export_manifest.csv').open(newline='', encoding='utf-8') as handle:
            manifest = list(csv.DictReader(handle))
        return fields, {row['source_relative_path']: row for row in rows}, report, manifest

    def assert_finalized(self, rows, report, manifest):
        self.assertEqual(report['counts']['inventoried'], len(rows))
        self.assertEqual(report['counts']['converted_sources_with_gaps'],
                         sum(bool(row['conversion_gap']) for row in rows.values()))
        registered = {row['export_file'].replace('\\', '/') for row in manifest}
        self.assertIn('00_manifest/companion_source_inventory.csv', registered)
        self.assertIn('07_workflows_reports/portable_extractor/companion_capability_report.json', registered)

    def test_all_reference_only_finalizes_without_copy_or_hash(self):
        self.add('a.sgy')
        self.add('b.zgy')
        with patch.object(companion, 'sha256', wraps=companion.sha256) as hashed:
            fields, rows, report, manifest = self.run_stage()
        self.assertFalse(any(Path(call.args[0]).parent == self.source for call in hashed.call_args_list))
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual({row['conversion_gap'] for row in rows.values()}, {''})
        self.assertEqual({row['sha256'] for row in rows.values()}, {''})
        self.assertEqual({row['status'] for row in rows.values()}, {'referenced_seismic_not_copied_or_hashed'})
        self.assertFalse((self.package/'09_source_companions').exists())
        self.assert_finalized(rows, report, manifest)

    def test_mixed_orders_keep_one_schema_and_honest_gaps(self):
        for seismic, other in [('a.sgy', 'z.txt'), ('z.sgy', 'a.txt')]:
            with self.subTest(seismic=seismic):
                # Separate fixtures so one ordering cannot mask the other.
                with tempfile.TemporaryDirectory() as temp:
                    previous = (self.root, self.source, self.package)
                    try:
                        self.root = Path(temp)
                        self.source = self.root/'source'; self.source.mkdir()
                        (self.source/'fixture.pet').write_bytes(b'project')
                        (self.source/'fixture.ptd').mkdir()
                        self.package = self.root/'package'
                        manifest = self.package/'00_manifest/export_manifest.csv'
                        manifest.parent.mkdir(parents=True)
                        with manifest.open('w', newline='', encoding='utf-8') as handle:
                            csv.DictWriter(handle, fieldnames=HEADERS).writeheader()
                        self.add(seismic)
                        source = self.add(other, b'plain text\n')
                        fields, rows, report, manifest_rows = self.run_stage()
                        self.assertEqual(set(fields), set(next(iter(rows.values()))))
                        self.assertEqual(rows[seismic]['conversion_gap'], '')
                        self.assertEqual(rows[other]['status'], 'preserved')
                        self.assertEqual(rows[other]['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                        self.assertEqual((self.package/'09_source_companions'/other).read_bytes(), source.read_bytes())
                        self.assertFalse((self.package/'09_source_companions'/seismic).exists())
                        self.assert_finalized(rows, report, manifest_rows)
                    finally:
                        self.root, self.source, self.package = previous

    def test_unsupported_and_size_limit_remain_visible(self):
        self.add('a.sgy')
        self.add('b.unknown', b'\0unsupported')
        self.add('c.txt', b'too large')
        _, rows, report, manifest = self.run_stage(max_file_bytes=8)
        self.assertEqual(rows['b.unknown']['category'], 'unsupported_or_unknown')
        self.assertEqual(rows['c.txt']['status'], 'skipped_size_limit')
        self.assertEqual(rows['c.txt']['sha256'], hashlib.sha256(b'too large').hexdigest())
        self.assertEqual(report['counts']['unsupported_or_manual'], 3)
        self.assert_finalized(rows, report, manifest)

    def test_without_reference_flag_seismic_is_fresh_hashed_and_preserved(self):
        source = self.add('a.sgy', b'seismic bytes')
        _, rows, report, manifest = self.run_stage(mode='copy', reference_seismic=False)
        self.assertEqual(rows['a.sgy']['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(rows['a.sgy']['status'], 'preserved')
        self.assertEqual((self.package/'09_source_companions/a.sgy').read_bytes(), source.read_bytes())
        self.assertEqual(report['counts']['preserved'], 1)
        self.assert_finalized(rows, report, manifest)

    def test_no_companions_still_has_complete_csv_schema(self):
        fields, rows, report, manifest = self.run_stage()
        self.assertEqual(fields, list(companion.INVENTORY_FIELDS))
        self.assertEqual(rows, {})
        self.assert_finalized(rows, report, manifest)

    def test_real_conversion_gap_and_failure_remain_distinct(self):
        self.add('a.sgy')
        self.add('b.shp', b'geometry')
        self.add('c.shp', b'bad geometry')

        def convert(source, destination):
            if source.name == 'c.shp':
                raise ValueError('invalid geometry')
            output = destination.with_suffix('.csv')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text('x,y\n1,2\n', encoding='utf-8')
            return [output], {'attributes_complete': False,
                              'attribute_status': 'missing_dbf_attributes'}

        with patch.object(companion, 'convert_shapefile', side_effect=convert), \
             patch.dict(sys.modules, {'shapefile': object()}):
            _, rows, report, manifest = self.run_stage()
        self.assertEqual(rows['b.shp']['status'], 'converted_and_preserved')
        self.assertEqual(rows['b.shp']['conversion_gap'], 'missing_dbf_attributes')
        self.assertTrue(rows['b.shp']['converted_files'])
        self.assertEqual(rows['c.shp']['status'], 'preserved_conversion_failed')
        self.assertIn('invalid geometry', rows['c.shp']['error'])
        self.assertEqual(report['counts']['converted_sources_with_gaps'], 1)
        self.assertEqual(report['counts']['conversion_failures'], 1)
        self.assertEqual(report['counts']['unsupported_or_manual'], 3)
        self.assert_finalized(rows, report, manifest)


if __name__ == '__main__':
    unittest.main()
