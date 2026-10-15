# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Shared native artifact acceptance across QC and saved delivery views."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoviewer_delivery import exports_section, publish_exports
from geoviewer_incremental import IncrementalDelivery
import petrel_geoscience_tools as qc_tools
import test_geoviewer_incremental as fixtures


class SharedNativeArtifactDeliveryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.IncrementalDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def _owners(self):
        f = self.fixture
        first_commit, shared = f.native('shared-one', name='Shared One')
        good_commit, good = f.native('unrelated', name='Unrelated')
        first = json.loads(first_commit.read_text())['record']
        second = json.loads(json.dumps(first))
        second['object_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'test:shared-two'))
        second['name'] = 'Shared Two'
        unrelated = json.loads(good_commit.read_text())['record']
        receipt = f.package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        fixtures.write_json(receipt, {'objects': [first, second, unrelated]})
        manifest = f.package/'00_manifest/export_manifest.csv'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['export_file', 'sha256'])
            writer.writeheader()
            writer.writerow({'export_file': receipt.relative_to(f.package).as_posix(),
                             'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()})
        return first, second, unrelated, shared, good

    def _saved_ids(self):
        f = self.fixture
        with (f.destination/'FILE_INDEX.csv').open(encoding='utf-8-sig', newline='') as stream:
            csv_ids = {row['object_id'] for row in csv.DictReader(stream)}
        json_ids = {row['object_id'] for row in json.loads(
            (f.destination/'FILE_INDEX.json').read_text())['files']}
        return csv_ids, json_ids

    def test_same_size_corruption_withdraws_every_shared_owner_and_retains_unrelated(self):
        f = self.fixture
        first, second, unrelated, shared, good = self._owners()
        owners = {first['object_id'], second['object_id']}
        all_ids = owners | {unrelated['object_id']}
        good_bytes = good.read_bytes()
        original_shared = shared.read_bytes()

        initial = publish_exports(f.package, {}, f.destination)
        self.assertEqual({row['object_id'] for row in initial['files']}, all_ids)
        self.assertEqual(self._saved_ids(), (all_ids, all_ids))
        self.assertTrue(all(oid in exports_section(initial, f.destination, f.report)
                            for oid in all_ids))

        corrupted = original_shared.replace(b'0,1', b'0,9')
        self.assertNotEqual(corrupted, original_shared)
        self.assertEqual(len(corrupted), len(original_shared))
        shared.write_bytes(corrupted)
        qc = qc_tools.qc_data_package(dict(export_package=str(f.package),
                                           output_dir=str(f.run/'qc'), dry_run=False))
        self.assertEqual(qc['status'], 'failed')
        result = json.loads(Path(qc['report_path']).read_text())
        self.assertEqual({row['object_id'] for row in result['native_artifact_qc']['failures']
                          if row['code'] == 'sha256_mismatch'}, owners)
        self.assertEqual(qc_tools.verify_receipt(qc)['status'], 'failed')

        final = publish_exports(f.package, {}, f.destination, checkpoint_index=initial)
        self.assertEqual(final['status'], 'partial')
        self.assertEqual({row['object_id'] for row in final['files']}, {unrelated['object_id']})
        self.assertEqual(final['partial_files'], [])
        self.assertEqual(self._saved_ids(), ({unrelated['object_id']}, {unrelated['object_id']}))
        final_html = exports_section(final, f.destination, f.report)
        self.assertTrue(all('<small>'+oid+'</small>' not in final_html for oid in owners))
        self.assertIn('<small>'+unrelated['object_id']+'</small>', final_html)

        fresh = IncrementalDelivery(f.run, f.report, f.destination, {})
        checkpoint = fresh.refresh(package=f.package, stopped_pipeline=True)
        self.assertEqual({row['object_id'] for row in checkpoint['files']}, {unrelated['object_id']})
        self.assertEqual(self._saved_ids(), ({unrelated['object_id']}, {unrelated['object_id']}))
        saved_html = (f.destination/'INCREMENTAL_INDEX.html').read_text()
        self.assertTrue(all('<small>'+oid+'</small>' not in saved_html for oid in owners))
        self.assertIn('<small>'+unrelated['object_id']+'</small>', saved_html)
        self.assertEqual(shared.read_bytes(), corrupted)
        self.assertEqual(good.read_bytes(), good_bytes)
        self.assertTrue((f.package/'native_data').is_dir())

    def test_conflicting_shared_declaration_withholds_both_owners(self):
        f = self.fixture
        first, second, unrelated, shared, good = self._owners()
        receipt = f.package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        document = json.loads(receipt.read_text())
        document['objects'][1]['artifacts'][0]['sha256'] = '0' * 64
        fixtures.write_json(receipt, document)
        before = {str(path.relative_to(f.package)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (shared, good)}
        result = publish_exports(f.package, {}, f.destination)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual({row['object_id'] for row in result['files']}, {unrelated['object_id']})
        self.assertEqual(self._saved_ids(), ({unrelated['object_id']}, {unrelated['object_id']}))
        self.assertTrue(any('conflicting artifact declarations' in finding
                            for finding in result['findings']))
        self.assertEqual(before, {str(path.relative_to(f.package)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (shared, good)})

    def test_missing_and_conflicting_shared_sizes_withhold_both_owners(self):
        f = self.fixture
        for case in ('both_missing', 'one_missing', 'conflicting'):
            with self.subTest(case=case):
                first, second, unrelated, shared, good = self._owners()
                receipt = f.package/'07_workflows_reports/native_recovery/native_recovery_report.json'
                document = json.loads(receipt.read_text())
                if case == 'both_missing':
                    document['objects'][0]['artifacts'][0].pop('bytes')
                    document['objects'][1]['artifacts'][0].pop('bytes')
                elif case == 'one_missing':
                    document['objects'][1]['artifacts'][0].pop('bytes')
                else:
                    document['objects'][1]['artifacts'][0]['bytes'] += 1
                fixtures.write_json(receipt, document)
                shared_bytes, good_bytes = shared.read_bytes(), good.read_bytes()
                result = publish_exports(f.package, {}, f.destination)
                accepted = {unrelated['object_id']}
                self.assertEqual(result['status'], 'partial')
                self.assertEqual({row['object_id'] for row in result['files']}, accepted)
                self.assertEqual(self._saved_ids(), (accepted, accepted))
                html = exports_section(result, f.destination, f.report)
                self.assertTrue(all('<small>'+oid+'</small>' not in html
                                    for oid in (first['object_id'], second['object_id'])))
                self.assertIn('<small>'+unrelated['object_id']+'</small>', html)
                self.assertEqual(shared.read_bytes(), shared_bytes)
                self.assertEqual(good.read_bytes(), good_bytes)


if __name__ == '__main__':
    unittest.main()
