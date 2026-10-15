# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Operation-scoped progress, with no extra reads or workflow ETA claims."""
import unittest
from unittest.mock import patch

import geoviewer_contract_publication as publication
from geoviewer_io import ContractProgress, contract_rows
from geoviewer_contract import POINTER
import test_geoviewer_contract as fixtures
import validate_geoviewer_contract as validator


class ContractProgressIntegrationTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    add_row = fixtures.ContractTests.add_row
    publish = fixtures.ContractTests.publish

    def test_publication_emits_scoped_actual_work(self):
        with patch('geoviewer_diagnostics.event') as events:
            bundle = publication.publish_preparation(self.package, self.exports, self.run,
                {}, file_index=self.index)
        frames = [c.kwargs for c in events.call_args_list if c.args == ('contract_operation',)]
        phases = {f['phase'] for f in frames}
        self.assertTrue({'catalogue_discovery', 'metadata_assembly', 'catalogue_table_write',
            'catalogue_table_read', 'reference_validation', 'profile_assessment',
            'human_report_rows'} <= phases, phases)
        self.assertNotIn('artifact_hash', phases)  # Default publication never bulk-hashes.
        operations = {}
        for frame in frames:
            self.assertEqual(frame['scope'], 'current_operation_only')
            self.assertFalse({'percent', 'eta', 'workflow_percent'} & set(frame))
            operations.setdefault(frame['operation_id'], []).append(frame)
        for rows in operations.values():
            self.assertEqual(rows[0]['state'], 'started')
            self.assertEqual(rows[-1]['state'], 'completed')
            counts = [r['completed'] for r in rows]
            self.assertEqual(counts, sorted(counts))
            if rows[-1]['total'] is not None:
                self.assertEqual(rows[-1]['completed'], rows[-1]['total'])
        self.assertEqual(publication.verify_publication_binding(bundle, self.run)['status'], 'verified')

    def test_hash_reports_consumed_bytes_and_keeps_independent_profile_checks(self):
        self.publish()
        with patch('geoviewer_diagnostics.event') as events:
            with validator.verified_profile_assessments(self.exports/POINTER,
                    hash_artifacts=True):
                pass
        frames = [c.kwargs for c in events.call_args_list if c.args == ('contract_operation',)
                  and c.kwargs['phase'] == 'artifact_hash' and c.kwargs['state'] == 'completed']
        self.assertEqual(len(frames), 3 * len(self.rows))
        self.assertTrue(all(f['unit'] == 'bytes' for f in frames))
        expected_sizes = sorted((self.exports/r['path']).stat().st_size for r in self.rows)
        self.assertEqual(sorted(f['completed'] for f in frames), sorted(expected_sizes * 3))
        self.assertEqual(len({f['operation_id'] for f in frames}), len(frames))

    def test_invalid_table_is_failed_not_completed(self):
        import json
        pointer = self.publish()
        manifest_path = self.exports/pointer['manifest']
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        table = manifest_path.parent/manifest['tables']['entities']['path']
        table.write_bytes(table.read_bytes().replace(b'Same', b'Tame', 1))
        with patch('geoviewer_diagnostics.event') as events:
            with self.assertRaisesRegex(ValueError, 'Table hash mismatch'):
                validator.validate_contract(self.exports/POINTER)
        frames = [c.kwargs for c in events.call_args_list if c.args == ('contract_operation',)
                  and c.kwargs['phase'] == 'catalogue_table_read' and c.kwargs['table'] == 'entities']
        self.assertTrue(frames, 'No internal catalogue-read progress was reported')
        self.assertEqual(frames[-1]['state'], 'failed')
        self.assertNotIn('completed', [f['state'] for f in frames])


class ProgressCounterTests(unittest.TestCase):
    def test_metadata_bound_failure_reports_actual_read_bytes(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'too-large.json'
            path.write_bytes(b'123456789')
            with patch('geoviewer_diagnostics.event') as events:
                with self.assertRaisesRegex(ValueError, 'byte bound'):
                    validator._metadata_digest(path, limit=8)
            terminal = events.call_args_list[-1].kwargs
            self.assertEqual((terminal['phase'], terminal['state'], terminal['completed']),
                             ('metadata_hash', 'failed', 9))

    def test_throttle_depends_on_actual_advances_not_timer_ticks(self):
        with patch('geoviewer_io.time.monotonic', return_value=0.0) as clock, \
             patch('geoviewer_diagnostics.event') as events:
            with ContractProgress('synthetic', total=20000) as progress:
                for _ in range(19999):
                    progress.advance()
                self.assertEqual(events.call_count, 1)
                clock.return_value = 2.1
                progress.advance()
                self.assertEqual(events.call_count, 2)
        self.assertEqual([c.kwargs['state'] for c in events.call_args_list],
                         ['started', 'progress', 'completed'])
        self.assertEqual(events.call_args_list[1].kwargs['completed'], 20000)

    def test_failure_preserves_original_exception_and_completed_count(self):
        problem = OSError('synthetic read error')
        with patch('geoviewer_diagnostics.event') as events:
            with self.assertRaises(OSError) as caught:
                with ContractProgress('synthetic_read', unit='bytes') as progress:
                    progress.advance(7)
                    raise problem
        self.assertIs(caught.exception, problem)
        terminal = events.call_args_list[-1].kwargs
        self.assertEqual((terminal['state'], terminal['completed']), ('failed', 7))
        self.assertIsNone(terminal['total'])

    def test_unknown_length_and_interrupted_iteration_do_not_report_completion(self):
        with patch('geoviewer_diagnostics.event') as events:
            rows = contract_rows(iter([1, 2, 3]), 'synthetic_rows')
            self.assertEqual(next(rows), 1)
            self.assertEqual(next(rows), 2)
            rows.close()
        terminal = events.call_args_list[-1].kwargs
        self.assertEqual(terminal['state'], 'interrupted')
        self.assertEqual(terminal['completed'], 1)
        self.assertIsNone(terminal['total'])


if __name__ == '__main__':
    unittest.main()
