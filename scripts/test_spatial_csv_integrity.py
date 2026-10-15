# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Saved-byte integrity during the existing spatial split verification pass."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_delivery as delivery
from geoviewer_incremental import IncrementalDelivery


class SpatialCSVIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.run = self.root/'run'
        self.package = self.run/'extraction/package/Project'; self.package.mkdir(parents=True)
        self.destination = self.root/'EXPORTS'
        self.rows = [dict(object_id='poly', vertex_index='2', note='العربية, café "quoted"\r\nnext\nlast\rpart', x='20'),
                     dict(object_id='poly', vertex_index='0', note='東京', x='10')]
        receipt = dict(tool='export_petrel_native_spatial_zero_gui.py', completed_at_utc='2026-09-26T00:00:00Z',
                       polygon_vertex_rows=2, point_vertex_rows=2, trajectory_rows=2, native_well_head_rows=0,
                       objects=[])
        self.expected = {}
        for identity, relative, kind in [
                ('poly','05_spatial/polygons/native_polygons_vertices.csv','Polygons3'),
                ('point','05_spatial/points/native_points_vertices.csv','Points3'),
                ('traj','02_wells/trajectories/native_well_trajectory_records.csv','XYZTrajectoryProviderData')]:
            path = self.package/relative; path.parent.mkdir(parents=True, exist_ok=True)
            rows = [dict(row, object_id=identity) for row in self.rows]; self.expected[identity] = rows
            with path.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            receipt['objects'].append(dict(object_id=identity, status='decoded', blob_type=kind))
        path = self.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        path.parent.mkdir(parents=True); path.write_text(json.dumps(receipt), encoding='utf-8')
        self.addCleanup(patch.stopall)
        patch.object(delivery, 'event').start()

    def publish(self):
        return delivery.publish_exports(self.package, {}, self.destination)

    def verify_index(self, result):
        self.assertEqual(len(result['files']), 3)
        for row in result['files']:
            target = self.destination/row['path']
            self.assertEqual(row['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertIn('saved_split_CSV_bytes_sha256', row['integrity'])
            self.assertIn('verified_against_emitted_CSV_bytes', row['integrity'])
            self.assertEqual(row['units'], 'unknown'); self.assertEqual(row['record_count'], 2)
            with target.open(encoding='utf-8', newline='') as stream:
                self.assertEqual(list(csv.DictReader(stream)), self.expected[row['object_id']])
        saved = json.loads((self.destination/'FILE_INDEX.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['files'], result['files'])
        with (self.destination/'FILE_INDEX.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r['object_id'], r['sha256'], r['integrity']) for r in rows],
                         [(r['object_id'], r['sha256'], r['integrity']) for r in result['files']])

    def test_final_saved_bytes_unicode_multiline_order_and_one_pending_read(self):
        original = Path.open; reads = []
        def opened(path, mode='r', *args, **kwargs):
            if path.suffix == '.partial' and mode != 'x': reads.append((path, mode))
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', opened), patch.object(delivery, 'sha256', side_effect=AssertionError('extra hash reread')):
            result = self.publish()
        self.assertEqual(len(reads), 3); self.assertTrue(all(mode == 'rb' for _, mode in reads))
        self.verify_index(result)

    def test_incremental_and_final_share_digest_and_propagate_indexes(self):
        report = self.root/'REPORT.html'; report.write_text('<html><body><main></main></body></html>')
        publisher = IncrementalDelivery(self.run, report, self.destination, {})
        partial = publisher.refresh(stopped_pipeline=True); self.verify_index(partial)
        self.assertTrue(all('package_QC_not_established' in r['integrity'] for r in partial['files']))
        final = self.publish(); self.verify_index(final)
        self.assertEqual({r['path']: r['sha256'] for r in partial['files']},
                         {r['path']: r['sha256'] for r in final['files']})

    def test_saved_byte_mutation_is_rejected_and_retains_prior_and_partial(self):
        original = Path.open
        prior_index = self.publish()
        prior_bytes = {row['path']: (self.destination/row['path']).read_bytes() for row in prior_index['files']}
        sources = {path: path.read_bytes() for path in self.package.rglob('*.csv')}
        def opened(path, mode='r', *args, **kwargs):
            if path.suffix == '.partial' and mode == 'rb':
                with original(path, 'rb') as stream: raw = stream.read()
                with original(path, 'wb') as stream: stream.write(raw.replace(b',20\r\n', b',21\r\n'))
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', opened): result = self.publish()
        self.assertEqual(result['files'], [])
        self.assertTrue(result['partial'])
        integrity_findings = [s for s in result['findings'] if 'differ from emitted CSV bytes' in s]
        self.assertEqual(len(integrity_findings), 3)
        self.assertEqual({s.split(':', 1)[0] for s in integrity_findings}, {
            'Spatial delivery incomplete for poly', 'Spatial delivery incomplete for point',
            'Spatial delivery incomplete for traj'})
        # Final delivery also reports each decoded object whose primary export
        # was withheld. Preserve both the integrity cause and completeness gap.
        self.assertEqual(set(result['findings']) - set(integrity_findings), {
            'Decoded spatial primary output missing for polygons object poly',
            'Decoded spatial primary output missing for points object point',
            'Decoded spatial primary output missing for trajectories object traj'})
        self.assertEqual(len(result['findings']), 6)
        self.assertEqual(len(list(self.destination.rglob('*.partial'))), 3)
        self.assertEqual(prior_bytes, {path: (self.destination/path).read_bytes() for path in prior_bytes})
        self.assertEqual(sources, {path: path.read_bytes() for path in sources})

    def test_idempotent_target_is_still_hashed_and_conflict_preserves_both(self):
        first = self.publish()
        with patch.object(delivery, 'sha256', wraps=delivery.sha256) as hashed:
            self.assertEqual(first['files'], self.publish()['files'])
        self.assertEqual(hashed.call_count, 3)
        self.assertTrue(all(call.args[0].suffix == '.csv' for call in hashed.call_args_list))
        target = self.destination/first['files'][0]['path']; target.write_bytes(b'prior complete output')
        result = self.publish()
        self.assertNotIn('poly', [row['object_id'] for row in result['files']])
        self.assertEqual(target.read_bytes(), b'prior complete output')
        self.assertEqual(len(list(target.parent.glob('*.partial'))), 1)
        self.assertTrue(any('retained both' in finding for finding in result['findings']))

    def test_failed_count_decode_read_close_and_promotion_never_accept_affected_row(self):
        original_open = Path.open; original_rename = Path.rename
        for failure in ('count', 'decode', 'read', 'read_close', 'write_close', 'short_write', 'write_error', 'promotion'):
            with self.subTest(failure=failure):
                self.destination = self.root/failure
                prior = self.destination/'polygons/poly_poly.csv'; prior.parent.mkdir(parents=True)
                prior.write_bytes(b'prior complete output')
                class FaultyStream:
                    def __init__(self, stream): self.stream = stream
                    def __getattr__(self, name): return getattr(self.stream, name)
                    def __enter__(self): return self
                    def __exit__(self, *args): self.close()
                    def __iter__(self):
                        if failure == 'read': raise OSError(5, 'injected read')
                        return iter(self.stream)
                    def write(self, text):
                        if failure == 'short_write': return self.stream.write(text[:-1])
                        if failure == 'write_error': raise OSError(5, 'injected write')
                        return self.stream.write(text)
                    def close(self):
                        self.stream.close()
                        if failure in ('read_close', 'write_close'):
                            raise OSError(5, 'injected close')
                def opened(path, mode='r', *args, **kwargs):
                    affected = path.parent == prior.parent and path.suffix == '.partial'
                    if affected and mode == 'rb' and failure in ('count', 'decode'):
                        with original_open(path, 'wb') as stream:
                            stream.write(b'object_id\r\n' if failure == 'count' else b'object_id\r\n\xff\r\n')
                    stream = original_open(path, mode, *args, **kwargs)
                    if affected and ((mode == 'rb' and failure in ('read','read_close')) or
                                     (mode == 'x' and failure in ('write_close', 'short_write', 'write_error'))):
                        return FaultyStream(stream)
                    return stream
                def rename(path, target):
                    if path.parent == prior.parent: raise OSError(5, 'injected promotion')
                    return original_rename(path, target)
                # Use rename injection on a fresh target for promotion failure.
                if failure == 'promotion': prior.unlink()
                with patch.object(Path, 'open', opened), patch.object(Path, 'rename', rename):
                    result = self.publish()
                self.assertEqual({r['object_id'] for r in result['files']}, {'point','traj'})
                self.assertTrue(result['findings']); self.assertEqual(len(list(prior.parent.glob('*.partial'))), 1)
                if failure != 'promotion': self.assertEqual(prior.read_bytes(), b'prior complete output')
                else: self.assertFalse(prior.exists())


if __name__ == '__main__': unittest.main()
