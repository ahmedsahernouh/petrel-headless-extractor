# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Testing-runner regressions using synthetic metadata only."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_test_lab as lab
import geoviewer_test_worker as worker
import geoviewer_campaign as c


class TestLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='GeoViewer_test_lab_unit_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalogue = self.root / 'crawler'
        (self.catalogue / 'inventories').mkdir(parents=True)
        (self.catalogue / 'LOGS').mkdir()
        self.toolkit = self.root / 'GeoViewer'
        (self.toolkit / '00_manifest').mkdir(parents=True)
        c.write_json(self.toolkit / '00_manifest/toolkit_files.json', {'version': '1.0.0'})
        self.config = copy.deepcopy(lab.DEFAULTS)
        self.config.update(profile_cases=3, output_roots=[str(self.root / 'outputs')], reserve_free_gib=0,
                           exclude_path_patterns=['\\temporary\\'])
        self.state = dict(schema=c.SCHEMA, projects=[], config={}, discovery_complete=False,
                          extractor_manifest_sha256=c.digest(self.toolkit / '00_manifest/toolkit_files.json'), roots=[])
        self.events = []

    def add(self, number, total=100, directory='real', datafiles=None):
        pid = 'P' + str(number).zfill(5)
        path = self.root / directory / pid / 'Demo.pet'
        rows = [{'relative': 'PROJECT.pet', 'size': total - 40, 'mtime_ns': 1},
                {'relative': 'STORE/Data.ptd', 'size': 20, 'mtime_ns': 1},
                {'relative': 'STORE/Model.ptd', 'size': 20, 'mtime_ns': 1}]
        rows += datafiles or []
        c.write_json(self.catalogue / 'inventories' / (pid + '.json'), rows)
        self.state['projects'].append(dict(id=pid, pet=str(path), total_bytes=total, file_count=len(rows), status='queued', attempts=[]))
        return pid

    def prepare(self):
        c.write_json(self.catalogue / 'CAMPAIGN.json', self.state)
        (self.catalogue / 'LOGS/campaign.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in self.events))
        destination = self.root / 'lab'
        with patch.object(c, 'snapshot', side_effect=AssertionError('No source inventory scan permitted')), \
             patch.object(c, 'connected_roots', side_effect=AssertionError('No drive discovery permitted')):
            state = lab.prepare(self.catalogue, destination, self.config, self.toolkit)
        return destination, state

    def test_prepare_uses_only_saved_metadata_and_keeps_incomplete_catalogue_honest(self):
        self.add(1)
        destination, state = self.prepare()
        self.assertFalse(state['source_catalogue_complete'])
        self.assertEqual(state['schema'], lab.SCHEMA)
        self.assertEqual(state['projects'][0]['lab_scenario'], 'metadata_profile')
        self.assertFalse((self.root / 'outputs').exists())
        self.assertTrue((destination / 'TEST_PLAN.html').is_file())
        self.assertFalse(c.read_json(self.catalogue / 'CAMPAIGN.json')['discovery_complete'])

    def test_filters_fixture_paths_and_preserves_exclusion_reason(self):
        self.add(1, directory='temporary'); self.add(2, total=102)
        destination, state = self.prepare()
        rows = c.read_json(destination / 'CANDIDATES.json')
        self.assertEqual(rows[0]['eligibility'], 'excluded_by_path_rule')
        self.assertEqual(len(state['projects']), 1)
        self.assertEqual(state['projects'][0]['source_project_id'], 'P00002')

    def test_confirmed_duplicates_reused_but_size_groups_not_claimed_identical(self):
        self.add(1); self.add(2); self.add(3)
        self.events.append(dict(event='duplicate_verified', project='P00002', duplicate_of='P00001'))
        destination, state = self.prepare()
        rows = c.read_json(destination / 'CANDIDATES.json')
        self.assertEqual([r['eligibility'] for r in rows], ['eligible', 'verified_duplicate', 'eligible'])
        self.assertEqual(len(state['projects']), 1)
        self.assertEqual(rows[0]['candidate_group'], rows[2]['candidate_group'])

    def test_invalid_pair_retained_without_attempt(self):
        self.add(1); self.state['projects'][0].update(status='invalid_pair', reason='Missing store')
        destination, state = self.prepare()
        self.assertEqual(state['projects'], [])
        self.assertEqual(c.read_json(destination / 'CANDIDATES.json')[0]['reason'], 'Missing store')

    def test_native_size_budgets_and_diversity_are_deterministic(self):
        candidates = [dict(id=str(i), eligibility='eligible', candidate_group=str(i), total_bytes=size, features=features)
                      for i, size, features in [(1, 10, ['drive:L']), (2, 10, ['drive:L']), (3, 30, ['drive:M']), (4, 200, ['drive:E'])]]
        selected = lab.choose_wave(candidates, 3, budget=50, maximum=100)
        self.assertEqual([r['id'] for r in selected], ['1', '3', '2'])
        self.assertEqual(selected, lab.choose_wave(list(reversed(candidates)), 3, budget=50, maximum=100))

    def test_filename_year_is_hint_only(self):
        p = dict(pet=str(self.root / 'Petrel 2024/Demo.pet'), total_bytes=120)
        values = lab.features(p, [], [], self.config)
        self.assertIn('name_hint:recent_year_unverified', values)
        self.assertFalse(any(v.startswith('recorded_version:') for v in values))

    def test_full_extraction_requires_profiles_and_uses_recorded_types(self):
        self.add(1)
        destination, state = self.prepare()
        with self.assertRaisesRegex(ValueError, 'metadata profiles'):
            lab.refresh_extraction_wave(destination, state)
        out = self.root / 'profile_output'
        (out / 'profile_data').mkdir(parents=True)
        c.write_json(out / 'profile_data/PROFILE_RESULT.json', dict(context=dict(saved_version={'value': '2024.3'},
                     layout='model_sqlite_bxml', payload_type_counts={'FloatWellLog': 40, 'RegValGrid2': 9})))
        state['projects'][0]['attempts'] = [dict(output=str(out), number=1)]
        state['projects'][0]['status'] = 'completed'
        lab.refresh_extraction_wave(destination, state)
        p = state['projects'][-1]
        self.assertEqual(p['lab_scenario'], 'full_extract')
        self.assertIn('recorded_version:2024.3', p['selection_features'])
        self.assertIn('payload_type:FloatWellLog', p['selection_features'])
        self.assertEqual(len(state['projects']), 2)
        lab.refresh_extraction_wave(destination, state)
        self.assertEqual(len(state['projects']), 2)

    def test_changed_build_rejects_preparation(self):
        self.add(1)
        self.state['extractor_manifest_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'different extractor build'): self.prepare()

    def test_config_rejects_unbounded_zero_limits(self):
        with self.assertRaises(ValueError): lab.validate_config(dict(self.config, profile_timeout_seconds=0))
        with self.assertRaises(ValueError): lab.validate_config(dict(self.config, extraction_cases=0))

    def test_run_to_completion_is_explicit_and_ordinary_limits_remain_finite(self):
        self.assertEqual(lab.elapsed_limits(self.config,'metadata_profile'),(self.config['session_hours']*3600,
                         self.config['profile_timeout_seconds']/3600))
        self.assertEqual(lab.elapsed_limits(self.config,'full_extract'),(self.config['session_hours']*3600,
                         self.config['extraction_timeout_hours']))
        self.assertEqual(lab.elapsed_limits(self.config,'full_extract',True),(None,None))
        configured=dict(self.config,run_to_completion=True)
        self.assertEqual(lab.elapsed_limits(configured,'full_extract'),(None,None))
        with self.assertRaisesRegex(ValueError,'true or false'):
            lab.validate_config(dict(self.config,run_to_completion='yes'))

    def test_profile_source_check_detects_change_without_recursing(self):
        source = self.root / 'Demo.pet'; source.write_text('abc')
        project = dict(pet=str(source), lab_scenario='metadata_profile')
        rows = [dict(relative='PROJECT.pet', size=3, mtime_ns=source.stat().st_mtime_ns)]
        with patch.object(c, 'snapshot', side_effect=AssertionError('Profile should not recurse')):
            self.assertEqual(lab.metadata_source_check(project, rows, None), (rows, []))
            source.write_text('longer')
            current, errors = lab.metadata_source_check(project, rows, None)
            self.assertFalse(errors)
            self.assertEqual(current[0]['size'], 6)
            self.assertNotEqual(current, rows)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.toolkit = Path('D:/fixture/GeoViewer')
        self.receipt = dict(schema='geoviewer.test-session/1', status='ready', host=worker.platform.node(),
                            toolkit=str(self.toolkit.resolve()), owner_pid=123, owner_birth='birth',
                            python=str(Path(sys.executable).resolve()), guarded_files={'fixture': [1, 2, 3]}, manifest_sha256='sha')

    def test_cached_session_checks_small_identity_without_calling_preflight(self):
        with patch.object(worker, 'process_birth', return_value='birth'), \
             patch.object(worker, 'guarded_files', return_value={'fixture': [1, 2, 3]}), \
             patch.object(worker, 'digest', return_value='sha'):
            worker.validate_session(self.receipt, self.toolkit)

    def test_code_or_pid_reuse_invalidates_session(self):
        with patch.object(worker, 'process_birth', return_value='reused'):
            with self.assertRaisesRegex(RuntimeError, 'owner changed'): worker.validate_session(self.receipt, self.toolkit)
        with patch.object(worker, 'process_birth', return_value='birth'), patch.object(worker, 'guarded_files', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'changed'): worker.validate_session(self.receipt, self.toolkit)

    def test_session_records_and_enforces_elapsed_time_policy(self):
        receipt=dict(self.receipt,run_to_completion=True)
        with patch.object(worker, 'process_birth', return_value='birth'), \
             patch.object(worker, 'guarded_files', return_value={'fixture': [1, 2, 3]}), \
             patch.object(worker, 'digest', return_value='sha'):
            worker.validate_session(receipt,self.toolkit,True)
            with self.assertRaisesRegex(RuntimeError,'policy changed'):
                worker.validate_session(receipt,self.toolkit,False)

    def test_worker_propagates_explicit_completion_flag_only_when_selected(self):
        finite=worker.standalone_argv(self.toolkit,Path('D:/source.pet'),Path('D:/out'))
        unlimited=worker.standalone_argv(self.toolkit,Path('D:/source.pet'),Path('D:/out'),run_to_completion=True)
        self.assertNotIn('--run-to-completion',finite)
        self.assertIn('--bounded',finite)
        self.assertEqual(unlimited[-1],'--run-to-completion')

    @unittest.skipUnless(os.name == 'nt', 'Windows live process identity')
    def test_current_process_birth_is_stable(self):
        self.assertEqual(worker.process_birth(os.getpid()), worker.process_birth(os.getpid()))


if __name__ == '__main__': unittest.main()
