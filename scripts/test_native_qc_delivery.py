# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""A genuine post-decode mutation must not survive as an accepted checkpoint."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_incremental as incremental
import petrel_geoscience_tools as tools
import test_geoviewer_incremental as fixtures


class NativeQcDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.IncrementalDeliveryTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture

    def receipt(self, commits):
        package=self.f.package
        report=package/'07_workflows_reports/native_recovery/native_recovery_report.json'
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({'objects':[json.loads(c.read_text())['record'] for c in commits]}))
        (package/'00_manifest').mkdir()
        with (package/'00_manifest/export_manifest.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['export_file','sha256']);writer.writeheader()
            writer.writerow(dict(export_file=report.relative_to(package).as_posix(),sha256=hashlib.sha256(report.read_bytes()).hexdigest()))

    def reject(self, commits, changed):
        self.receipt(commits)
        initial=self.f.delivery.refresh(); self.assertEqual(len(initial['files']),len(commits))
        for path in changed:
            path.write_bytes(path.read_bytes().replace(b'0,1',b'0,9'))
        qc=tools.qc_data_package(dict(export_package=str(self.f.package),output_dir=str(self.f.run/'qc'),dry_run=False))
        self.assertEqual(qc['status'],'failed')
        self.assertEqual(tools.verify_receipt(qc)['status'],'failed')
        fresh=incremental.IncrementalDelivery(self.f.run,self.f.report,self.f.destination,{})
        return fresh.refresh(stopped_pipeline=True)

    def test_bad_native_object_withdrawn_good_object_retained_and_no_data_deleted(self):
        bad,bad_file=self.f.native('bad')
        good,good_file=self.f.native('good')
        good_bytes=good_file.read_bytes()
        index=self.reject([bad,good],[bad_file])
        good_id=json.loads(good.read_text())['record']['object_id']
        self.assertEqual([r['object_id'] for r in index['files']],[good_id])
        self.assertEqual(good_file.read_bytes(),good_bytes)
        self.assertIn(b'0,9',bad_file.read_bytes())
        self.assertTrue(bad.is_file())
        self.assertEqual(index['status'],'stopped_partial')
        self.assertIn('withdrawn',' '.join(index['worker_errors']))
        html=(self.f.destination/'INCREMENTAL_INDEX.html').read_text()
        self.assertIn('indexed native files withdrawn',html)
        self.assertNotIn(json.loads(bad.read_text())['record']['object_id'],html)

    def test_all_rejected_replaces_old_nonempty_index_with_empty_index(self):
        bad,file=self.f.native('bad')
        index=self.reject([bad],[file])
        self.assertEqual(index['files'],[])
        saved=json.loads((self.f.destination/'FILE_INDEX.json').read_text())
        self.assertEqual(saved['files'],[])
        with (self.f.destination/'FILE_INDEX.csv').open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(list(csv.DictReader(stream)),[])
        self.assertTrue(file.is_file())

    def test_native_las_format_failure_withdraws_only_its_owner(self):
        from test_native_las_qc import LAS
        import uuid
        bad_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'test:bad-las'))
        good_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'test:good-las'))
        bad,bad_file=self.f.native('bad-las', content='broken LAS', relative=bad_id+'/curve.las')
        good,good_file=self.f.native('good-las', content=LAS, relative=good_id+'/curve.las')
        self.receipt([bad,good])
        self.assertEqual(len(self.f.delivery.refresh()['files']),2)
        qc=tools.qc_data_package(dict(export_package=str(self.f.package),output_dir=str(self.f.run/'qc')))
        self.assertEqual(qc['status'],'failed')
        self.assertEqual(qc['summary']['las_parse_failures'],1)
        fresh=incremental.IncrementalDelivery(self.f.run,self.f.report,self.f.destination,{})
        index=fresh.refresh(stopped_pipeline=True)
        good_id=json.loads(good.read_text())['record']['object_id']
        self.assertEqual([r['object_id'] for r in index['files']],[good_id])
        self.assertTrue(bad_file.is_file())
        self.assertEqual(good_file.read_text(),LAS)

    def test_global_receipt_failure_withdraws_native_but_preserves_other_categories(self):
        self.f.native('first'); inventory=self.f.seismic()
        fixtures.write_json(self.f.run/'qc/result.json',dict(package=str(self.f.package),native_artifact_qc=dict(
            status='failed',failures=[dict(scope='global',object_id=None,code='invalid_receipt')])) )
        index=self.f.delivery.refresh(inventory=inventory,stopped_pipeline=True)
        self.assertEqual([r['category'] for r in index['files']],['seismic'])

    def test_other_package_qc_does_not_reject_current_objects(self):
        commit,_=self.f.native('first')
        other=self.f.root/'other';other.mkdir()
        identity=json.loads(commit.read_text())['record']['object_id']
        fixtures.write_json(self.f.run/'qc/result.json',dict(package=str(other),native_artifact_qc=dict(
            status='failed',failures=[dict(scope='object',object_id=identity)])))
        index=self.f.delivery.refresh(stopped_pipeline=True)
        self.assertEqual(len(index['files']),1)
        self.assertIn('different package',' '.join(index['worker_errors']))

    def test_failed_qc_without_details_cannot_leave_stale_accepted_index(self):
        self.f.native('first')
        self.assertEqual(len(self.f.delivery.refresh()['files']),1)
        fixtures.write_json(self.f.run/'qc/result.json',dict(package=str(self.f.package),
            native_artifact_qc=dict(status='failed',failures=[])))
        fresh=incremental.IncrementalDelivery(self.f.run,self.f.report,self.f.destination,{})
        index=fresh.refresh(stopped_pipeline=True)
        self.assertEqual(index['files'],[])
        self.assertEqual(json.loads((self.f.destination/'FILE_INDEX.json').read_text())['files'],[])
        self.assertIn('no usable failure details',' '.join(index['worker_errors']))


if __name__=='__main__': unittest.main()
