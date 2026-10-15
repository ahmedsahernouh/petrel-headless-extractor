# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Ancillary review failures must not alter saved selections or stop a queue.

All case execution and source checks are mocked; no extraction evidence is claimed.
"""
import copy
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_test_lab as lab
import geoviewer_campaign as c
import geoviewer_trace


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='GeoViewer_test_lab_review_')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.plan = self.root / 'manual_subset'
        (self.plan / 'LOGS').mkdir(parents=True)
        self.toolkit = self.root / 'toolkit'
        (self.toolkit / 'scripts').mkdir(parents=True)
        (self.toolkit / '00_manifest').mkdir()
        (self.toolkit / 'scripts/launch_standalone_petrel.ps1').write_text('# mocked only')
        c.write_json(self.toolkit / '00_manifest/toolkit_files.json', {'fixture': True})
        config = copy.deepcopy(lab.DEFAULTS)
        config.update(extractor_directory=str(self.toolkit), output_roots=[str(self.root / 'outputs')], reserve_free_gib=0)
        self.candidates = [dict(id='P00001', pet=str(self.root / 'never_read/Demo.pet'), total_bytes=1024,
                               file_count=3, eligibility='eligible', reason='', candidate_group='saved-group',
                               features=['recorded_version:unknown'])]
        self.state = dict(schema=lab.SCHEMA, source_catalogue='frozen catalogue reference',
                          source_catalogue_complete=False, config=config, selection_method='manual saved subset',
                          extractor_manifest_sha256=c.digest(self.toolkit / '00_manifest/toolkit_files.json'),
                          projects=[lab.job(self.candidates[0], 'full_extract'),
                                    lab.job(dict(self.candidates[0], id='P00002'), 'full_extract')])
        self.state['projects'][0].update(status='failed', reason='Original wrapper failure',
                                        attempts=[dict(number=1, exit_code=1, error='Original wrapper failure',
                                                       output=str(self.root / 'prior-output'))])
        c.write_json(self.plan / 'CAMPAIGN.json', self.state)

    def assert_review_warning(self, exception_type):
        original = copy.deepcopy(self.state)
        prior_csv = b'prior candidate CSV: preserve without verification\r\n'
        (self.plan / 'CANDIDATES.csv').write_bytes(prior_csv)
        with patch.object(c, 'snapshot', side_effect=AssertionError('No source reads')), \
             patch.object(c, 'discover', side_effect=AssertionError('No rediscovery')), \
             patch.object(lab, 'choose_wave', side_effect=AssertionError('No reselection')), \
             redirect_stdout(io.StringIO()) as printed:
            lab.review(self.plan, self.state)
        self.assertEqual(self.state, original)
        self.assertEqual(c.read_json(self.plan / 'CAMPAIGN.json'), original)
        self.assertEqual((self.plan / 'CANDIDATES.csv').read_bytes(), prior_csv)
        status = c.read_json(self.plan / 'REVIEW_STATUS.json')
        self.assertEqual(status['exception_type'], exception_type)
        self.assertEqual(status['status'], 'warning')
        self.assertNotIn('candidate_count', status)
        page = (self.plan / 'TEST_PLAN.html').read_text(encoding='utf-8')
        self.assertIn('candidate eligibility and full catalogue coverage cannot be reviewed', page)
        self.assertNotIn('href="CANDIDATES', page)
        self.assertNotIn('Candidate statuses:', page)
        self.assertIn('REVIEW WARNING', printed.getvalue())
        self.assertIn('P00002_X', page)
        self.assertIn('failed', (self.plan / 'INDEX.html').read_text(encoding='utf-8'))
        self.assertIn('P00002_X', (self.plan / 'PROJECTS.csv').read_text(encoding='utf-8-sig'))

    def test_manual_subset_missing_catalogue_retains_jobs_results_and_prior_csv(self):
        self.assert_review_warning('FileNotFoundError')
        self.assertFalse((self.plan / 'CANDIDATES.json').exists())

    def test_missing_catalogue_does_not_create_empty_csv(self):
        lab.review(self.plan, self.state)
        self.assertFalse((self.plan / 'CANDIDATES.csv').exists())

    def test_invalid_json_and_encoding(self):
        for payload, error in [(b'{broken', 'JSONDecodeError'), (b'\xff', 'UnicodeDecodeError')]:
            with self.subTest(error=error):
                (self.plan / 'CANDIDATES.json').write_bytes(payload)
                self.assert_review_warning(error)

    def test_invalid_schema(self):
        for payload in [{}, [None], [dict(self.candidates[0], eligibility={})],
                        [dict(self.candidates[0], features='not-a-list')],
                        [dict(self.candidates[0], total_bytes=-1)]]:
            with self.subTest(payload=payload):
                c.write_json(self.plan / 'CANDIDATES.json', payload)
                self.assert_review_warning('ValueError')

    def test_unreadable_ancillary_input_is_explicit(self):
        original_read = lab.read
        def read(path):
            if path.name == 'CANDIDATES.json':
                raise PermissionError('fixture: candidate input cannot be read')
            return original_read(path)
        with patch.object(lab, 'read', side_effect=read):
            self.assert_review_warning('PermissionError')

    def test_valid_catalogue_preserves_normal_review(self):
        original = copy.deepcopy(self.state)
        c.write_json(self.plan / 'CANDIDATES.json', self.candidates)
        lab.review(self.plan, self.state)
        status = c.read_json(self.plan / 'REVIEW_STATUS.json')
        self.assertEqual(status['status'], 'complete')
        self.assertEqual(status['candidate_count'], 1)
        page = (self.plan / 'TEST_PLAN.html').read_text(encoding='utf-8')
        self.assertIn('href="CANDIDATES.csv"', page)
        self.assertIn('Candidate statuses:', page)
        self.assertNotIn('Review warning:', page)
        self.assertEqual(c.read_json(self.plan / 'CAMPAIGN.json'), original)
        self.assertEqual(c.read_json(self.plan / 'CANDIDATES.json'), self.candidates)
        self.assertIn('eligible', (self.plan / 'CANDIDATES.csv').read_text(encoding='utf-8-sig'))

    def test_index_write_permission_failure_is_not_swallowed(self):
        with patch.object(c, 'indexes', side_effect=PermissionError('cannot write campaign')):
            with self.assertRaisesRegex(PermissionError, 'cannot write campaign'):
                lab.review(self.plan, self.state)

    def run_mocked(self, case_effects):
        """Run real run()/run_queue()/review(); mock only external execution and IO admission."""
        for project in self.state['projects']:
            project.update(status='queued', attempts=[])
        c.write_json(self.plan / 'CAMPAIGN.json', self.state)
        case_calls = []
        effects = iter(case_effects)
        def process(command, *args, **kwargs):
            if '--prepare' in command:
                c.write_json(Path(command[command.index('--session') + 1]), {'status': 'ready'})
                return 0
            if '--scenario' not in command:
                return 0  # Mock doctor; never launch it.
            case_calls.append(command)
            result = next(effects)
            if isinstance(result, Exception):
                raise result
            return result
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ))
            stack.enter_context(patch.object(geoviewer_trace, 'start'))
            stack.enter_context(patch.object(c, 'process', side_effect=process))
            stack.enter_context(patch.object(c, 'source_preflight', return_value={'status': 'unchanged'}))
            stack.enter_context(patch.object(c, 'choose_output', return_value=(self.root / 'outputs', [])))
            stack.enter_context(patch.object(c, 'collect_attempt'))
            stack.enter_context(patch.object(c, 'bundle_logs', return_value={'status': 'complete'}))
            stack.enter_context(patch.object(c, 'discover', side_effect=AssertionError('No discovery')))
            stack.enter_context(patch.object(lab, 'choose_wave', side_effect=AssertionError('No reselection')))
            result = lab.run(self.plan, 'full_extract')
        return result, case_calls

    def test_two_case_queue_continues_and_original_error_survives_both_reviews(self):
        with redirect_stdout(io.StringIO()) as printed:
            result, calls = self.run_mocked([RuntimeError('original wrapper failure fixture'), 1])
        self.assertEqual(result, 10)
        self.assertEqual(len(calls), 2)
        state = c.read_json(self.plan / 'CAMPAIGN.json')
        self.assertEqual([p['status'] for p in state['projects']], ['failed', 'failed'])
        attempt = state['projects'][0]['attempts'][0]
        self.assertEqual(attempt['error'], 'original wrapper failure fixture')
        self.assertEqual(attempt['exception_type'], 'RuntimeError')
        self.assertEqual([len(p['attempts']) for p in state['projects']], [1, 1])
        self.assertEqual(printed.getvalue().count('TEST PLAN REVIEW WARNING:'), 3)
        events = [json.loads(line) for line in (self.plan / 'LOGS/campaign.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'extraction_finished' for e in events), 2)
        self.assertIn('original wrapper failure fixture', next(e['error'] for e in events if e['event'] == 'attempt_failed'))

    def test_fatal_runtime_failure_is_not_masked_by_final_review(self):
        with self.assertRaisesRegex(RuntimeError, 'runtime/session validation failed'):
            self.run_mocked([75])
        state = c.read_json(self.plan / 'CAMPAIGN.json')
        self.assertEqual(state['projects'][0]['attempts'][0]['exit_code'], 75)
        self.assertEqual(state['projects'][1]['status'], 'queued')
        self.assertEqual(c.read_json(self.plan / 'REVIEW_STATUS.json')['status'], 'warning')


if __name__ == '__main__':
    unittest.main()
