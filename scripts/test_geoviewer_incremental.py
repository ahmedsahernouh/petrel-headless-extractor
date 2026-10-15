# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for live accepted-export indexing."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

# The bundled runtime's ._pth can otherwise resolve an installed older module.
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import geoviewer_incremental as incremental
import geoviewer_diagnostics as diagnostics


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class IncrementalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root/'run'; self.package = self.run/'extraction/package/Project'
        self.package.mkdir(parents=True)
        self.report = self.run/'REPORT.html'
        self.report.write_text('<!doctype html><html><head><title>Base</title></head><body><main><section id="diagram">KEEP DIAGRAM</section></main></body></html>', encoding='utf-8')
        self.destination = self.root/'EXPORTS'
        self.delivery = incremental.IncrementalDelivery(self.run, self.report, self.destination, {}, interval=.05)

    def native(self, identity='log-1', name='Log One', content='i,value\n0,1\n',
               status='decoded', exact='exact', unit=None, relative=None, well=None):
        try: identity = str(uuid.UUID(identity))
        except ValueError: identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'test:'+identity))
        directory = self.package/'native_data'/identity; directory.mkdir(parents=True, exist_ok=True)
        relative = relative or identity+'/samples.csv'
        artifact = self.package/'native_data'/relative
        artifact.parent.mkdir(parents=True, exist_ok=True); artifact.write_text(content, encoding='utf-8')
        record = dict(object_id=identity, name=name, blob_type='FloatWellLog', status=status,
                      numeric_round_trip=exact, unit=unit,
                      artifacts=[dict(path=relative, bytes=artifact.stat().st_size,
                                      sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())])
        if well: record['well'] = well
        commit = directory/'_commit.json'
        write_json(commit, dict(schema='geoviewer.object/1', tool_version='1.0.0', record=record))
        return commit, artifact

    def seismic(self, status='converted', result_status='passed', artifact_name='volume.segy'):
        folder = self.run/'seismic/cube_1'; folder.mkdir(parents=True, exist_ok=True)
        artifact = folder/artifact_name; artifact.write_bytes(b'SEGY')
        source = self.root/'cube.zgy'; source.write_bytes(b'ZGY')
        receipt = dict(status=result_status, operation='zgy-to-segy', source=str(source),
                       receipt_path=str(folder/'RUN_RESULT.json'),
                       summary=dict(all_decoded_samples_exact=True, headers_verified=True,
                                    profile=dict(source_vertical_unit='ms')),
                       artifacts=[dict(path=artifact.name, size_bytes=artifact.stat().st_size,
                                       sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())])
        write_json(folder/'RUN_RESULT.json', receipt)
        return dict(objects=[dict(id='cube-1', name='Cube One', source=str(source), status=status,
                                  receipt_path=str(folder/'RUN_RESULT.json'), result=receipt)])

    def test_partial_commit_is_excluded_and_valid_unknown_units_is_included(self):
        pending = self.package/'native_data/log-x.partial'; pending.mkdir(parents=True)
        write_json(pending/'_commit.json', {'schema':'geoviewer.object/1','tool_version':'1.0.0','record':{}})
        self.native(unit=None)
        result = self.delivery.refresh(package=self.package)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['files'][0]['units'], 'unknown')
        self.assertNotIn('log-x', json.dumps(result))
        self.assertTrue((self.destination/'FILE_INDEX.csv').is_file())

    def test_final_html_index_accepts_spatial_row_without_label(self):
        row = dict(object_id='polygon-1', name='Polygon One', category='polygons',
                   path='polygons/Polygon_One.csv', units='unknown',
                   numerical_qc='row-preserving split')
        html = self.delivery._html(dict(partial=False, files=[row]))
        self.assertIn('href="polygons/Polygon_One.csv"', html)
        self.assertIn('>Polygon_One.csv</a>', html)
        self.assertIn('row-preserving split', html)

    def test_malformed_changed_traversal_and_symlink_sources_are_rejected(self):
        commit, artifact = self.native(identity='bad-schema')
        saved = json.loads(commit.read_text()); saved['schema'] = 'wrong'; write_json(commit, saved)
        self.native(identity='traverse', relative='../outside.csv')
        link_commit, link_artifact = self.native(identity='linked')
        outside = self.root/'outside.csv'; outside.write_text('x')
        try:
            link_artifact.unlink(); link_artifact.symlink_to(outside)
        except OSError:
            original_is_symlink = Path.is_symlink
            def reports_link(path):
                return path == link_artifact or original_is_symlink(path)
            with patch.object(Path, 'is_symlink', reports_link):
                result = self.delivery.refresh(package=self.package)
        else:
            result = self.delivery.refresh(package=self.package)
        self.assertEqual(result['files'], [])
        self.assertGreaterEqual(len(result['findings']), 2)

    def test_duplicate_refresh_is_idempotent_and_commit_change_retains_prior(self):
        commit, _ = self.native()
        first = self.delivery.refresh(package=self.package)
        before = (self.destination/'FILE_INDEX.json').read_bytes()
        second = self.delivery.refresh(package=self.package)
        self.assertEqual(first['files'], second['files'])
        self.assertEqual(before, (self.destination/'FILE_INDEX.json').read_bytes())
        changed = json.loads(commit.read_text()); changed['record']['numeric_round_trip'] = 'approximate'; write_json(commit, changed)
        third = self.delivery.refresh(package=self.package)
        self.assertEqual(len(third['files']), 1)
        self.assertTrue(any('frozen accepted receipt retained' in x for x in self.delivery.findings))

    def test_committed_object_survives_interrupted_next_object(self):
        self.native(identity='first')
        self.assertEqual(len(self.delivery.refresh(package=self.package)['files']), 1)
        partial = self.package/'native_data/second.partial'; partial.mkdir(parents=True)
        (partial/'samples.csv').write_text('unfinished')
        self.assertEqual(len(self.delivery.refresh(package=self.package)['files']), 1)

    def test_final_publication_retains_commits_without_aggregate_and_skips_missing(self):
        from geoviewer_delivery import publish_exports
        self.native(identity='retained')
        self.native(identity='missing')
        checkpoint=self.delivery.refresh(package=self.package)
        lost=next(row for row in checkpoint['files'] if row['name']=='Log One')
        (self.destination/lost['path']).unlink()
        result=publish_exports(self.package,{'objects':[]},self.destination,checkpoint_index=checkpoint)
        self.assertEqual(result['files'],[])
        self.assertEqual(len(result['partial_files']),1)
        self.assertEqual(result['partial_files'][0]['status'],'checkpoint_only_partial')
        self.assertTrue(result['findings'])
        html=self.delivery._html(result)
        self.assertIn('Partial checkpoint',html)
        self.assertNotIn('remain pending',html)

    def test_hardlink_failure_falls_back_to_original_validated_path(self):
        _, source = self.native()
        with patch.object(incremental.os, 'link', side_effect=OSError(95, 'unsupported')):
            result = self.delivery.refresh(package=self.package)
        row = result['files'][0]
        self.assertEqual(row['storage'], 'original_path_filesystem_cannot_hardlink')
        self.assertTrue((self.destination/row['path']).samefile(source))
        self.assertTrue(any('unsupported' in x for x in result['findings']))

    def test_well_prefix_matches_final_delivery_name(self):
        _, source = self.native(identity='well-log', name='Gamma Ray', well='Well A')
        row = self.delivery.refresh(package=self.package)['files'][0]
        expected = 'Well_A_Gamma_Ray_'+row['object_id'][:36]+'_'+source.stem+source.suffix
        self.assertEqual(Path(row['path']).name, expected)

    def test_malformed_second_artifact_prevents_any_receipt_publication(self):
        commit, first = self.native(identity='two-files')
        receipt = json.loads(commit.read_text())
        second = first.with_name('second.csv'); second.write_text('x')
        receipt['record']['artifacts'].append(dict(path=second.relative_to(self.package/'native_data').as_posix(),
                                                   bytes=99, sha256='0'*64))
        write_json(commit, receipt)
        result = self.delivery.refresh(package=self.package)
        self.assertEqual(result['files'], [])
        self.assertFalse((self.destination/'logs').exists())

    def test_report_checkpoint_is_single_and_preserves_existing_content(self):
        self.native()
        self.delivery.refresh(package=self.package); self.delivery.refresh(package=self.package)
        text = self.report.read_text(encoding='utf-8')
        self.assertEqual(text.count(incremental.START), 1)
        self.assertEqual(text.count('id="incremental-delivery-checkpoint"'), 1)
        self.assertIn('KEEP DIAGRAM', text)
        self.assertIn('INCREMENTAL_INDEX.html', text)

    def test_only_completed_numerically_checked_seismic_is_indexed(self):
        good = self.seismic()
        result = self.delivery.refresh(package=self.package, inventory=good)
        self.assertEqual([x['category'] for x in result['files']], ['seismic'])
        self.assertEqual(result['files'][0]['numerical_qc'], 'every_amplitude_and_trace_geometry')
        interrupted = self.seismic(status='converting')
        interrupted['objects'][0]['result']['receipt_path'] = str(self.run/'seismic/cube_1/RUN_RESULT.json')
        again = incremental.IncrementalDelivery(self.run, self.report, self.root/'OTHER', {})
        self.assertEqual(again.refresh(package=self.package, inventory=interrupted)['files'], [])

    def test_bad_seismic_size_and_receipt_escape_are_rejected(self):
        inventory = self.seismic()
        receipt = Path(inventory['objects'][0]['result']['receipt_path'])
        data = json.loads(receipt.read_text()); data['artifacts'][0]['size_bytes'] += 1; write_json(receipt, data)
        self.assertEqual(self.delivery.refresh(package=self.package, inventory=inventory)['files'], [])
        outside = self.root/'outside_result.json'; write_json(outside, data)
        inventory['objects'][0]['result']['receipt_path'] = str(outside)
        result = self.delivery.refresh(package=self.package, inventory=inventory)
        self.assertEqual(result['files'], [])
        self.assertTrue(any('escaped' in x for x in result['findings']))

    def test_seismic_rows_withdraw_on_next_observable_failure(self):
        for mode in ('missing_artifact', 'wrong_size', 'missing_receipt',
                     'malformed_receipt', 'failed_receipt', 'failed_inventory'):
            with self.subTest(mode=mode):
                inventory = self.seismic()
                delivery = incremental.IncrementalDelivery(self.run, self.report,
                    self.root/('EXPORTS_'+mode), {})
                initial = delivery.refresh(package=self.package, inventory=inventory)
                self.assertEqual(len(initial['files']), 1)
                receipt = Path(inventory['objects'][0]['receipt_path'])
                artifact = receipt.parent/'volume.segy'
                if mode == 'missing_artifact': artifact.unlink()
                elif mode == 'wrong_size': artifact.write_bytes(b'BAD')
                elif mode == 'missing_receipt': receipt.unlink()
                elif mode == 'malformed_receipt': receipt.write_text('{', encoding='utf-8')
                elif mode == 'failed_receipt':
                    saved = json.loads(receipt.read_text()); saved['status'] = 'failed'; write_json(receipt, saved)
                else: inventory['objects'][0]['status'] = 'conversion_failed'
                current = delivery.refresh(package=self.package, inventory=inventory)
                self.assertEqual(current['files'], [])
                self.assertEqual(len(current['invalid_seismic']), 1)
                self.assertEqual(json.loads((delivery.destination/'FILE_INDEX.json').read_text())['files'], [])

    def test_seismic_none_inventory_preserves_valid_row_but_checks_saved_binding(self):
        inventory = self.seismic()
        self.assertEqual(len(self.delivery.refresh(package=self.package, inventory=inventory)['files']), 1)
        self.assertEqual(len(self.delivery.refresh(package=self.package, inventory=None)['files']), 1)
        Path(inventory['objects'][0]['receipt_path']).parent.joinpath('volume.segy').unlink()
        self.assertEqual(self.delivery.refresh(package=self.package, inventory=None)['files'], [])

    def test_known_bad_seismic_bytes_do_not_reenter_from_size_only(self):
        inventory = self.seismic()
        self.assertEqual(len(self.delivery.refresh(package=self.package, inventory=inventory)['files']), 1)
        artifact = Path(inventory['objects'][0]['receipt_path']).parent/'volume.segy'
        artifact.write_bytes(b'BAD')
        self.assertEqual(self.delivery.refresh(package=self.package, inventory=inventory)['files'], [])
        artifact.write_bytes(b'BADD')
        self.assertEqual(self.delivery.refresh(package=self.package, inventory=inventory)['files'], [])
        artifact.write_bytes(b'SEGY')
        self.assertEqual(len(self.delivery.refresh(package=self.package, inventory=inventory)['files']), 1)

    def test_changed_seismic_receipt_does_not_duplicate_accepted_identity(self):
        inventory = self.seismic()
        self.assertEqual(len(self.delivery.refresh(package=self.package, inventory=inventory)['files']), 1)
        receipt = Path(inventory['objects'][0]['receipt_path'])
        changed = json.loads(receipt.read_text()); changed['note'] = 'changed'
        write_json(receipt, changed)
        current = self.delivery.refresh(package=self.package, inventory=inventory)
        self.assertEqual(current['files'], [])
        self.assertEqual(len(current['invalid_seismic']), 1)

    def test_seismic_delivery_link_is_repaired_and_conflict_is_not_claimed(self):
        inventory = self.seismic()
        first = self.delivery.refresh(package=self.package, inventory=inventory)['files'][0]
        source = Path(inventory['objects'][0]['receipt_path']).parent/'volume.segy'
        target = self.destination/first['path']
        target.unlink()
        repaired = self.delivery.refresh(package=self.package, inventory=inventory)['files'][0]
        self.assertTrue((self.destination/repaired['path']).samefile(source))
        target.unlink(); target.write_bytes(b'other')
        fallback = self.delivery.refresh(package=self.package, inventory=inventory)['files'][0]
        self.assertEqual(target.read_bytes(), b'other')
        self.assertTrue((self.destination/fallback['path']).samefile(source))
        self.assertNotEqual(fallback['path'], first['path'])

    def test_report_only_does_not_publish_files(self):
        self.native()
        delivery = incremental.IncrementalDelivery(self.run, self.report, self.destination, {}, enabled=False)
        result = delivery.refresh(package=self.package, inventory=self.seismic())
        self.assertEqual(result['files'], [])
        self.assertFalse(self.destination.exists())
        self.assertIn('0 accepted files', self.report.read_text())

    def test_snapshot_failure_keeps_last_good_json(self):
        self.native(identity='one')
        self.delivery.refresh(package=self.package)
        path = self.destination/'FILE_INDEX.json'; before = path.read_bytes()
        self.native(identity='two')
        with patch.object(incremental, 'atomic_json', side_effect=OSError(13, 'injected')):
            result = self.delivery.refresh(package=self.package)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(result['files']), 1)
        self.assertTrue(self.delivery.worker_errors)

    def test_ambiguous_package_is_rejected_without_erasing_prior_index(self):
        self.native(); self.delivery.refresh()
        before = (self.destination/'FILE_INDEX.json').read_bytes()
        (self.run/'extraction/package/Second').mkdir()
        result = self.delivery.refresh()
        self.assertEqual(len(result['files']), 1)
        self.assertEqual((self.destination/'FILE_INDEX.json').read_bytes(), before)
        self.assertTrue(any('Ambiguous' in x for x in self.delivery.worker_errors))

    def test_background_stop_is_bounded_and_prevents_later_mutation(self):
        self.native(identity='one')
        self.delivery.start()
        deadline = time.monotonic()+2
        while not (self.destination/'FILE_INDEX.json').is_file() and time.monotonic() < deadline:
            time.sleep(.02)
        stopped = self.delivery.stop()
        self.assertEqual(len(stopped['files']), 1)
        self.assertFalse(self.delivery._thread.is_alive())
        before = (self.destination/'FILE_INDEX.json').read_bytes()
        self.native(identity='two')
        time.sleep(.1)
        self.assertEqual((self.destination/'FILE_INDEX.json').read_bytes(), before)

    def test_changed_valid_receipt_does_not_duplicate_and_mutation_withdraws_prior(self):
        commit, artifact = self.native(identity='frozen')
        first = self.delivery.refresh(package=self.package)
        self.assertEqual(len(first['files']), 1)
        changed = json.loads(commit.read_text()); changed['record']['name'] = 'Changed Name'; write_json(commit, changed)
        second = self.delivery.refresh(package=self.package)
        self.assertEqual(len(second['files']), 1)
        self.assertEqual(second['files'][0]['name'], 'Log One')
        artifact.write_text('i,value\n0,9\n', encoding='utf-8')  # same byte length
        changed['record']['name'] = 'Changed Again'; write_json(commit, changed)
        third = self.delivery.refresh(package=self.package)
        self.assertEqual(third['files'], [])
        self.assertTrue(any('withdrawn' in x for x in self.delivery.findings))

    def test_live_native_evidence_loss_withdraws_only_affected_rows_and_recovers(self):
        self.native(identity='unrelated', name='Unrelated')
        for mode in ('missing_artifact', 'size_mismatch', 'missing_commit',
                     'malformed_commit', 'missing_directory'):
            with self.subTest(mode=mode):
                commit, artifact = self.native(identity='affected', name='Affected')
                original_receipt = commit.read_bytes()
                original_artifact = artifact.read_bytes()
                initial = self.delivery.refresh(package=self.package)
                self.assertEqual({row['name'] for row in initial['files']},
                                 {'Affected', 'Unrelated'})
                if mode == 'missing_artifact':
                    artifact.unlink()
                elif mode == 'size_mismatch':
                    artifact.write_bytes(original_artifact+b'extra')
                elif mode == 'missing_commit':
                    commit.unlink()
                elif mode == 'malformed_commit':
                    commit.write_text('{malformed', encoding='utf-8')
                else:
                    shutil.rmtree(commit.parent)
                withdrawn = self.delivery.refresh(package=self.package)
                self.assertEqual([row['name'] for row in withdrawn['files']], ['Unrelated'])
                self.assertEqual([row['name'] for row in json.loads(
                    (self.destination/'FILE_INDEX.json').read_text())['files']], ['Unrelated'])
                commit.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(original_artifact)
                commit.write_bytes(original_receipt)
                recovered = self.delivery.refresh(package=self.package)
                self.assertEqual({row['name'] for row in recovered['files']},
                                 {'Affected', 'Unrelated'})

    def test_observed_loss_quarantines_same_size_corruption_until_hash_restored(self):
        commit, artifact = self.native(identity='quarantined')
        self.assertEqual(len(self.delivery.refresh(package=self.package)['files']), 1)
        original = artifact.read_bytes()
        artifact.unlink()
        self.assertEqual(self.delivery.refresh(package=self.package)['files'], [])
        artifact.write_bytes(original.replace(b'0,1', b'0,9'))
        self.assertEqual(artifact.stat().st_size, len(original))
        for _ in range(2):
            self.assertEqual(self.delivery.refresh(package=self.package)['files'], [])
        artifact.write_bytes(original)
        self.assertEqual(len(self.delivery.refresh(package=self.package)['files']), 1)
        self.assertTrue(commit.is_file())

    def test_changed_receipt_corruption_stays_withdrawn_until_frozen_hash_restored(self):
        commit, artifact = self.native(identity='changed-quarantine')
        self.assertEqual(len(self.delivery.refresh(package=self.package)['files']), 1)
        original = artifact.read_bytes()
        artifact.write_bytes(original.replace(b'0,1', b'0,9'))
        changed = json.loads(commit.read_text())
        changed['record']['name'] = 'New Receipt Name'
        write_json(commit, changed)
        for _ in range(3):
            self.assertEqual(self.delivery.refresh(package=self.package)['files'], [])
        artifact.write_bytes(original)
        restored = self.delivery.refresh(package=self.package)
        self.assertEqual([row['name'] for row in restored['files']], ['Log One'])

    def test_changed_receipt_rebinds_frozen_delivery_paths(self):
        for mode in ('deleted', 'foreign'):
            with self.subTest(mode=mode):
                commit, artifact = self.native(identity=mode)
                initial = next(row for row in self.delivery.refresh(package=self.package)['files']
                               if row['object_id'] == commit.parent.name)
                target = self.destination/initial['path']
                target.unlink()
                if mode == 'foreign':
                    target.write_bytes(b'foreign data')
                changed = json.loads(commit.read_text())
                changed['record']['name'] = 'New Receipt Name'
                write_json(commit, changed)
                row = next(row for row in self.delivery.refresh(package=self.package)['files']
                           if row['object_id'] == commit.parent.name)
                self.assertEqual(row['name'], 'Log One')
                self.assertTrue(os.path.samefile(artifact, self.destination/row['path']))
                if mode == 'foreign':
                    self.assertEqual(target.read_bytes(), b'foreign data')
                    self.assertEqual(row['storage'], 'original_path_filesystem_cannot_hardlink')
                else:
                    self.assertEqual(row['storage'], 'single_physical_copy_hardlink')

    def test_deleted_delivery_link_is_recreated_from_valid_native_source(self):
        _, artifact = self.native(identity='deleted-link')
        first = self.delivery.refresh(package=self.package)['files'][0]
        target = self.destination/first['path']
        target.unlink()
        refreshed = self.delivery.refresh(package=self.package)['files'][0]
        self.assertEqual(refreshed['storage'], 'single_physical_copy_hardlink')
        self.assertTrue(os.path.samefile(artifact, self.destination/refreshed['path']))

    def test_replaced_delivery_link_does_not_publish_foreign_bytes(self):
        _, artifact = self.native(identity='foreign-link')
        first = self.delivery.refresh(package=self.package)['files'][0]
        target = self.destination/first['path']
        target.unlink()
        target.write_bytes(b'foreign data')
        refreshed = self.delivery.refresh(package=self.package)['files'][0]
        self.assertEqual(target.read_bytes(), b'foreign data')
        self.assertEqual(refreshed['storage'], 'original_path_filesystem_cannot_hardlink')
        self.assertTrue(os.path.samefile(artifact, self.destination/refreshed['path']))

    def test_raw_package_link_alias_is_rejected_before_resolve(self):
        self.native()
        alias = self.run/'extraction/package/Alias'; alias.mkdir()
        original = Path.is_symlink
        def linked(path):
            return path == alias or original(path)
        with patch.object(Path, 'is_symlink', linked):
            result = self.delivery.refresh(package=alias)
        self.assertEqual(result['files'], [])
        self.assertTrue(any('linked ancestry' in x for x in self.delivery.worker_errors))

    def test_stop_raises_if_snapshot_writer_remains_blocked(self):
        self.native(); entered = threading.Event(); release = threading.Event()
        self.delivery.stop_timeout = .2
        original = self.delivery._write_snapshot
        def blocked(index):
            entered.set(); release.wait(10); return original(index)
        with patch.object(self.delivery, '_write_snapshot', blocked):
            self.delivery.start(); self.assertTrue(entered.wait(2))
            with self.assertRaises(TimeoutError):
                self.delivery.stop()
            self.assertTrue(self.delivery._thread.is_alive())
            self.assertTrue(any('Worker stack:' in item for item in self.delivery.worker_errors))
            release.set(); self.delivery._thread.join(2)
        self.assertFalse(self.delivery._thread.is_alive())

    def test_slow_network_candidate_drains_before_final_publication(self):
        self.native(identity='first')
        original_index = self.delivery.refresh(package=self.package)
        before = (self.destination/'FILE_INDEX.json').read_bytes()
        slow_commit, _ = self.native(identity='second')
        entered = threading.Event(); release = threading.Event()
        original_is_file = Path.is_file

        def delayed_is_file(path):
            if path == slow_commit and threading.current_thread() == self.delivery._thread:
                entered.set()
                self.assertTrue(release.wait(10))
            return original_is_file(path)

        with patch.object(Path, 'is_file', delayed_is_file):
            self.delivery.start()
            self.assertTrue(entered.wait(2))
            timer = threading.Timer(5.2, release.set)
            timer.start()
            try:
                self.assertEqual(self.delivery.stop()['files'], original_index['files'])
            finally:
                release.set()
                timer.join()
        self.assertFalse(self.delivery._thread.is_alive())
        self.assertEqual((self.destination/'FILE_INDEX.json').read_bytes(), before)
        # The foreground final publication can now safely consume retained receipts.
        final = incremental.IncrementalDelivery(self.run, self.report, self.destination, {})
        self.assertEqual(len(final.refresh(package=self.package)['files']), 2)

    def test_findings_use_shared_diagnostics_schema(self):
        log = self.root/'shared_LOG.txt'; events = self.root/'shared_EVENTS.jsonl'
        owner = diagnostics.Diagnostics(log, events)
        try:
            commit, _ = self.native(identity='diagnostic')
            value = json.loads(commit.read_text()); value['schema'] = 'bad'; write_json(commit, value)
            delivery = incremental.IncrementalDelivery(self.run, self.report, self.destination, {},
                                                       log_path=log, event_path=events)
            delivery.refresh(package=self.package)
        finally:
            owner.close()
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        finding = next(row for row in rows if row['event'] == 'incremental_delivery_finding')
        self.assertEqual(finding['schema'], diagnostics.SCHEMA)
        self.assertIn('event_id', finding)


if __name__ == '__main__':
    unittest.main()
