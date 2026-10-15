# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic batch acceptance; never scans actual drives or extracts real data."""
from contextlib import redirect_stdout
import csv
import html
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

# Embedded Python intentionally omits the current directory from its import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_campaign as c


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='GeoViewer_campaign_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / 'campaign'; self.campaign.mkdir()
        (self.campaign / 'inventories').mkdir()
        self.events = c.Events(self.campaign)
        self.sources = self.root / 'sources'; self.sources.mkdir()
        self.toolkit = self.root / 'GeoViewer'; (self.toolkit / 'scripts').mkdir(parents=True)
        (self.toolkit / 'scripts' / 'launch_standalone_petrel.ps1').write_text('# fixture')
        (self.toolkit / '00_manifest').mkdir()
        c.write_json(self.toolkit / '00_manifest' / 'toolkit_files.json', {'version': '1.0.0'})
        self.config = dict(scan_connected_drives=False, extra_roots=[str(self.sources)],
                           output_roots=[str(self.root / 'out')], reserve_free_gib=0, space_multiplier=1,
                           extractor_directory=str(self.toolkit), full_hash=True)
        self.state = dict(schema=c.SCHEMA, projects=[], config=self.config, discovery_complete=False)
        self.output = io.StringIO()
        self.quiet = redirect_stdout(self.output); self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def project(self, parent, name='Demo', data=b'1234', pet=b'Petrel2018'):
        directory = self.sources / parent; directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + '.pet'); path.write_bytes(pet)
        store = path.with_suffix('.ptd'); store.mkdir()
        (store / 'Model.ptd').write_bytes(data)
        (store / 'Data.ptd').write_bytes(b'payload')
        return path

    def scan(self):
        c.discover(self.campaign, self.state, self.events)
        return self.state['projects']

    def fake_process(self, command, logfile, **kwargs):
        logfile.write_text('captured output', encoding='utf-8')
        if '-ProjectFile' not in command:
            return 0
        output = Path(command[command.index('-OutputRoot') + 1])
        source = command[command.index('-ProjectFile') + 1]
        status, code = ('failed', 1) if 'Fail' in source else ('completed_with_gaps', 10)
        run = output / 'trial_data'; run.mkdir()
        report = output / 'trial_REPORT.html'; report.write_text('fixture report')
        c.write_json(run / 'RUN_RESULT.json', dict(status=status, full_report=str(report),
                     project_context={'saved_version': {'value': '2024.5'}}, category_outcomes=[{'status': 'partial'}]))
        (output / 'trial_LOG.txt').write_text('debug')
        (run / 'SECRET.pet').write_text('MUST NOT BE COLLECTED')
        with zipfile.ZipFile(output / 'trial_SUPPORT.zip', 'w') as z:
            z.writestr('errors.txt', 'fixture problem')
        self.assertEqual(kwargs['env']['TEMP'], str(output / '_scratch'))
        self.assertEqual(command[-1], '-FullHash')
        self.assertIn('-NoPause', command)
        return code

    def test_sizes_exact_pair_not_neighboring_projects(self):
        pet = self.project('same', 'A')
        self.project('same', 'B', data=b'X' * 100)
        rows, errors = c.snapshot(pet, self.events)
        self.assertFalse(errors)
        self.assertEqual(sum(r['size'] for r in rows), len(b'Petrel2018') + 4 + len(b'payload'))
        self.assertEqual(len(rows), 3)

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive file handle regression')
    def test_archive_succeeds_with_supervisor_lock_held(self):
        import ctypes
        from ctypes import wintypes as w
        self.project('lock_case'); self.scan()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        lock = self.campaign / 'LOGS/supervisor.lock'
        handle = kernel.CreateFileW(str(lock), 0xC0000000, 0, None, 4, 0x80, None)
        self.assertNotEqual(handle, w.HANDLE(-1).value)
        try:
            result = c.bundle_logs(self.campaign)
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(any(r['path'].endswith('supervisor.lock') for r in result['excluded']))
        with zipfile.ZipFile(self.campaign / 'CAMPAIGN_LOGS.zip') as z:
            self.assertIsNone(z.testzip())
            self.assertNotIn('LOGS/supervisor.lock', z.namelist())

    def test_archive_unreadable_log_is_explicit_partial(self):
        self.project('case'); self.scan()
        denied = self.campaign / 'LOGS/denied.log'; denied.write_text('original')
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path == denied: raise PermissionError('test denied')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            result = c.bundle_logs(self.campaign)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['unreadable'][0]['path'], 'LOGS/denied.log')
        self.assertEqual(denied.read_text(), 'original')
        with zipfile.ZipFile(self.campaign / 'CAMPAIGN_LOGS.zip') as z:
            self.assertIsNone(z.testzip())
            self.assertNotIn('LOGS/denied.log', z.namelist())

    def test_archive_publication_failure_preserves_previous_zip(self):
        self.project('case'); self.scan(); c.bundle_logs(self.campaign)
        archive = self.campaign / 'CAMPAIGN_LOGS.zip'
        before = archive.read_bytes()
        original = Path.replace
        def denied(path, target):
            if Path(target) == archive: raise PermissionError('publication denied')
            return original(path, target)
        with patch.object(Path, 'replace', denied): result = c.bundle_logs(self.campaign)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['previous_archive_retained'])
        self.assertEqual(before, archive.read_bytes())
        self.assertEqual(c.read_json(self.campaign / 'ARCHIVE_STATUS.json')['status'], 'failed')

    def test_exact_case_selector_does_not_run_queued_neighbors(self):
        import geoviewer_test_lab as lab
        self.project('a', 'First'); self.project('b', 'Second', pet=b'different')
        rows = self.scan()
        for p in rows: p['lab_scenario'] = 'full_extract'
        chosen = rows[1]['id']
        adapter = dict(verified_session=True, select=lambda p: lab.selected_case(p, 'full_extract', [chosen]),
                       command=lambda p, out: ['-ProjectFile', p['pet'], '-OutputRoot', str(out), '-NoPause', '-FullHash'],
                       session_seconds=30, timeout_hours=lambda p: 1, admission=lambda p: p, after_case=lambda *args: None)
        with patch.object(c, 'process', side_effect=self.fake_process):
            c.run_queue(self.campaign, self.state, self.events, execution=adapter)
        self.assertEqual(rows[0]['status'], 'queued')
        self.assertEqual(rows[0]['attempts'], [])
        self.assertEqual(len(rows[1]['attempts']), 1)

    def test_exact_copies_deduplicated_and_versions_retained(self):
        self.project('a', 'Original')
        self.project('b', 'Renamed')
        self.project('c', 'Demo2010', pet=b'Petrel2010')
        rows = self.scan()
        self.assertEqual(sum(p['status'] == 'duplicate' for p in rows), 1)
        self.assertEqual(sum(p['status'] == 'queued' for p in rows), 2)
        duplicate = next(p for p in rows if p['status'] == 'duplicate')
        self.assertIn(duplicate['duplicate_of'], [p['id'] for p in rows if p['status'] == 'queued'])

    def test_same_size_different_payload_not_duplicate(self):
        self.project('a', data=b'abcd')
        self.project('b', data=b'efgh')
        self.assertTrue(all(p['status'] == 'queued' for p in self.scan()))

    def test_companion_difference_retains_both(self):
        a = self.project('a'); b = self.project('b')
        (a.parent / 'external.zgy').write_bytes(b'abcd')
        (b.parent / 'external.zgy').write_bytes(b'efgh')
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(p['status'] == 'queued' for p in rows))

    def test_identical_companions_allow_duplicate(self):
        for parent in ('a', 'b'):
            pet = self.project(parent)
            (pet.parent / 'external.zgy').write_bytes(b'same')
        self.assertEqual(sum(p['status'] == 'duplicate' for p in self.scan()), 1)

    def test_uncertain_duplicates_are_retained(self):
        self.project('a'); self.project('b')
        with patch.object(c, 'companion_fingerprint', side_effect=PermissionError('fixture denial')):
            rows = self.scan()
        self.assertTrue(all(p['status'] == 'queued' for p in rows))
        self.assertTrue(all(p.get('deduplication_warning') for p in rows))

    def test_missing_store_listed_but_not_queued(self):
        (self.sources / 'lonely.PET').write_text('fixture')
        rows = self.scan()
        self.assertEqual(rows[0]['status'], 'invalid_pair')

    def test_skip_generated_outputs_and_ptd_embedded_projects(self):
        pet = self.project('real')
        (pet.with_suffix('.ptd') / 'nested.pet').write_text('fixture')
        copied = self.project('old_output')
        (copied.parent / 'RUN_RESULT.json').write_text('{}')
        rows = self.scan()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['pet'], str(pet))

    def test_root_alias_and_overlap_collapsed(self):
        rows = [dict(path=str(self.root / 'alias'), canonical=str(self.sources)),
                dict(path=str(self.sources), canonical=str(self.sources)),
                dict(path=str(self.sources / 'sub'), canonical=str(self.sources / 'sub'))]
        selected, aliases = c.collapse_roots(rows)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(aliases), 2)

    def test_scan_permission_error_is_recorded(self):
        with patch.object(c.os, 'scandir', side_effect=PermissionError('fixture denied')):
            self.scan()
        self.assertEqual(self.state['roots'][0]['scan_errors'], 1)
        self.assertIn('fixture denied', self.events.path.read_text())

    def test_failed_project_does_not_block_next_and_logs_collected(self):
        self.project('a', 'Fail', pet=b'failure')
        self.project('b', 'Okay', pet=b'okay')
        self.scan()
        with patch.object(c, 'process', side_effect=self.fake_process):
            c.run_queue(self.campaign, self.state, self.events)
        self.assertEqual({p['status'] for p in self.state['projects']}, {'failed', 'completed_with_gaps'})
        self.assertTrue(all(p['saved_version'] == '2024.5' for p in self.state['projects']))
        self.assertEqual(len(list((self.campaign / 'LOGS').rglob('*_SUPPORT.zip'))), 2)
        c.bundle_logs(self.campaign)
        with zipfile.ZipFile(self.campaign / 'CAMPAIGN_LOGS.zip') as z:
            self.assertIsNone(z.testzip())
            self.assertFalse(any(n.endswith('.pet') for n in z.namelist()))
            self.assertIn('UNREDACTED', z.read('README.txt').decode())

    def test_resume_skips_attempted_and_explicit_retry_creates_new_folder(self):
        self.project('a', 'Fail')
        self.scan()
        with patch.object(c, 'process', side_effect=self.fake_process):
            c.run_queue(self.campaign, self.state, self.events)
            c.run_queue(self.campaign, self.state, self.events)
            self.assertEqual(len(self.state['projects'][0]['attempts']), 1)
            c.run_queue(self.campaign, self.state, self.events, retry_failed=True)
        attempts = self.state['projects'][0]['attempts']
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0]['output'], attempts[1]['output'])

    def test_changed_source_is_not_extracted(self):
        pet = self.project('a'); self.scan()
        (pet.with_suffix('.ptd') / 'Data.ptd').write_bytes(b'changed')
        with patch.object(c, 'process', return_value=0) as process:
            c.run_queue(self.campaign, self.state, self.events)
        self.assertEqual(self.state['projects'][0]['status'], 'source_changed')
        self.assertEqual(process.call_count, 1)  # Doctor only.
        receipt = c.read_json(self.state['projects'][0]['latest_preflight'])
        self.assertTrue(receipt['inventory_changed'])
        self.assertEqual(receipt['diff']['changed'][0]['relative'], 'STORE/Data.ptd')

    def test_preflight_access_failure_does_not_claim_source_change(self):
        pet = self.project('a'); project = self.scan()[0]
        baseline = (self.campaign / 'inventories' / (project['id'] + '.json')).read_bytes()
        original = os.scandir
        def denied(path):
            if Path(path) == pet.with_suffix('.ptd'):
                raise PermissionError(13, 'fixture denied', str(path))
            return original(path)
        with patch.object(c.os, 'scandir', side_effect=denied), patch.object(c, 'process', return_value=0) as process:
            c.run_queue(self.campaign, self.state, self.events)
        self.assertEqual(project['status'], 'source_unavailable')
        self.assertEqual(process.call_count, 1)  # Doctor only; never extraction.
        receipt = c.read_json(project['latest_preflight'])
        self.assertIsNone(receipt['inventory_changed'])
        self.assertFalse(receipt['inventory_complete'])
        self.assertEqual(receipt['diff']['removed'], [])
        self.assertEqual(len(receipt['diff']['unobserved']), 2)
        self.assertEqual(receipt['access_errors'][0]['error_type'], 'PermissionError')
        self.assertEqual(receipt['access_errors'][0]['errno'], 13)
        self.assertIn('fixture denied', receipt['access_errors'][0]['error'])
        self.assertEqual((self.campaign / 'inventories' / (project['id'] + '.json')).read_bytes(), baseline)

    def test_preflight_diff_retains_additions_removals_and_prior_receipts(self):
        pet = self.project('a'); project = self.scan()[0]
        c.source_preflight(self.campaign, project, self.events)
        first = Path(project['latest_preflight']); first_bytes = first.read_bytes()
        (pet.with_suffix('.ptd') / 'Data.ptd').unlink()
        (pet.with_suffix('.ptd') / 'Added.ptd').write_bytes(b'new')
        receipt = c.source_preflight(self.campaign, project, self.events)
        self.assertEqual(receipt['status'], 'source_changed')
        self.assertEqual([r['relative'] for r in receipt['diff']['removed']], ['STORE/Data.ptd'])
        self.assertEqual([r['relative'] for r in receipt['diff']['added']], ['STORE/Added.ptd'])
        self.assertNotEqual(first, Path(project['latest_preflight']))
        self.assertEqual(first.read_bytes(), first_bytes)

    def test_preflight_order_only_change_is_not_source_change(self):
        self.project('a'); project = self.scan()[0]
        receipt = c.source_preflight(self.campaign, project, self.events,
                                    lambda p, rows, e: (list(reversed(rows)), []))
        self.assertEqual(receipt['status'], 'unchanged')

    def test_preflight_missing_source_records_exception(self):
        pet = self.project('a'); project = self.scan()[0]; pet.unlink()
        receipt = c.source_preflight(self.campaign, project, self.events)
        self.assertEqual(receipt['status'], 'source_unavailable')
        self.assertEqual(receipt['access_errors'][0]['error_type'], 'FileNotFoundError')
        self.assertIsNone(receipt['after'])

    def test_skipped_preflight_is_directly_accessible_without_an_extraction_attempt(self):
        self.project('a'); project = self.scan()[0]
        receipt = c.source_preflight(self.campaign, project, self.events,
            lambda p, rows, e: (rows[:1], [c.access_error('fixture', 'scan_directory', PermissionError('denied <store>'))]))
        project.update(status=receipt['status'], reason='Source unavailable: denied <store> & read incomplete')
        evidence = Path(project['latest_preflight']); evidence_bytes = evidence.read_bytes()
        self.assertFalse(project.get('attempts'))
        c.indexes(self.campaign, self.state)
        with (self.campaign / 'PROJECTS.csv').open(encoding='utf-8-sig', newline='') as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['latest_preflight'], str(evidence))
        self.assertEqual(row['status'], 'source_unavailable')
        text = (self.campaign / 'INDEX.html').read_text(encoding='utf-8')
        self.assertIn('href="' + html.escape(evidence.as_uri(), quote=True) + '">Source preflight evidence</a>', text)
        self.assertIn(html.escape(project['reason']), text)
        self.assertNotIn('denied <store>', text)
        self.assertEqual(evidence.read_bytes(), evidence_bytes)
        c.bundle_logs(self.campaign)
        with zipfile.ZipFile(self.campaign / 'CAMPAIGN_LOGS.zip') as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read(evidence.relative_to(self.campaign).as_posix()), evidence_bytes)

    def test_historical_case_without_preflight_has_no_invented_evidence_link(self):
        self.project('a'); project = self.scan()[0]
        project.update(status='source_changed', reason='Historical cause not recorded')
        c.indexes(self.campaign, self.state)
        text = (self.campaign / 'INDEX.html').read_text(encoding='utf-8')
        self.assertIn('Historical cause not recorded', text)
        self.assertNotIn('Source preflight evidence</a>', text)
        with (self.campaign / 'PROJECTS.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(next(csv.DictReader(stream))['latest_preflight'], '')

    def test_low_space_defers_without_launching_project(self):
        self.project('a'); self.scan()
        with patch.object(c, 'process', return_value=0) as process, patch.object(c.shutil, 'disk_usage') as disk:
            disk.return_value.free = 0
            c.run_queue(self.campaign, self.state, self.events)
        self.assertEqual(self.state['projects'][0]['status'], 'deferred_space')
        self.assertEqual(process.call_count, 1)

    def test_output_inside_source_rejected(self):
        pet = self.project('a'); project = self.scan()[0]
        self.config['output_roots'] = [str(pet.parent / 'wrong')]
        output, checks = c.choose_output(self.config, project, 'trial')
        self.assertIsNone(output)
        self.assertIn('inside source', checks[0]['error'])

    def test_stop_after_current_prevents_next_launch(self):
        self.project('a'); self.scan()
        (self.campaign / 'STOP_AFTER_CURRENT.txt').touch()
        with patch.object(c, 'process', return_value=0) as process:
            c.run_queue(self.campaign, self.state, self.events)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(self.state['projects'][0]['status'], 'queued')

    def test_surviving_child_blocks_explicit_retry(self):
        self.project('a'); self.scan()
        self.state['projects'][0].update(status='interrupted', attempts=[{'child_pid': os.getpid()}])
        with patch.object(c, 'process', return_value=0) as process:
            with self.assertRaisesRegex(RuntimeError, 'overlapping retry'):
                c.run_queue(self.campaign, self.state, self.events, retry_failed=True)
        self.assertEqual(process.call_count, 1)  # Doctor only, no second extraction.

    def test_max_projects_leaves_queue(self):
        self.project('a', pet=b'a'); self.project('b', pet=b'b'); self.scan()
        with patch.object(c, 'process', side_effect=self.fake_process):
            c.run_queue(self.campaign, self.state, self.events, max_projects=1)
        self.assertEqual(sum(p['status'] == 'queued' for p in self.state['projects']), 1)

    def test_success_requires_receipt(self):
        self.assertEqual(c.outcome(0, None), 'failed')
        self.assertEqual(c.outcome(0, 'failed'), 'failed')
        self.assertEqual(c.outcome(1, 'completed'), 'failed')
        self.assertEqual(c.outcome(0, 'completed'), 'completed')
        self.assertEqual(c.outcome(10, 'completed_with_gaps'), 'completed_with_gaps')

    def test_real_child_logs_unicode_spaces_and_nonzero_exit(self):
        child = self.root / 'child with spaces.py'
        child.write_text("import sys\nprint('fixture child: ' + sys.argv[1])\nraise SystemExit(7)\n")
        logfile = self.campaign / 'LOGS' / 'child.txt'
        result = c.process([sys.executable, str(child), 'A & B'], logfile)
        self.assertEqual(result, 7)
        self.assertIn('A & B', logfile.read_text())

    def test_config_validation(self):
        c.validate_config(self.config)
        with self.assertRaises(ValueError):
            c.validate_config(dict(self.config, space_multiplier=-1))
        with self.assertRaises(ValueError):
            c.validate_config(dict(self.config, output_roots=['relative']))

    def test_process_liveness_and_start_callback(self):
        self.assertTrue(c.process_alive(os.getpid()))
        pids = []
        result = c.process([sys.executable, '-c', 'print("started")'], self.campaign / 'LOGS' / 'pid.txt', on_started=pids.append)
        self.assertEqual(result, 0)
        self.assertEqual(len(pids), 1)
        self.assertFalse(c.process_alive(pids[0]))

    def test_windows_mapped_alias_paths(self):
        if os.name != 'nt':
            self.skipTest('Windows path normalization')
        roots = [dict(path='L:\\', canonical='\\\\host\\share'),
                 dict(path='T:\\', canonical='\\\\host\\share')]
        self.assertEqual(c.canonical('L:\\a\\b.pet', roots), c.canonical('T:\\a\\b.pet', roots))
        self.assertEqual(c.canonical('L:\\a\\b.pet', roots), c.canonical('\\\\host\\share\\a\\b.pet', roots))

    def test_bootstrap_fallback_bundle_collected(self):
        pet = self.project('a'); project = self.scan()[0]
        output = self.root / 'failed_output'; output.mkdir()
        fallback = self.root / 'STARTUP_SUPPORT.zip'
        with zipfile.ZipFile(fallback, 'w') as z:
            z.writestr('startup.txt', 'failure')
        console = self.campaign / 'LOGS' / 'bootstrap.txt'
        console.write_text('SUPPORT ZIP (startup only, unredacted; review before sharing): ' + str(fallback))
        attempt = dict(number=1, output=str(output), console=str(console))
        c.collect_attempt(self.campaign, project, attempt, self.events)
        self.assertEqual(len(attempt['collected']), 1)

    def test_missing_full_bundle_recovered_with_verified_collector(self):
        self.project('a'); project = self.scan()[0]
        output = self.root / 'fallback_output'; session = output / '_scratch' / 'GeoViewer_support_fixture'
        session.mkdir(parents=True)
        c.write_json(session / 'run.json', {'run': 'fixture'})
        builder = self.toolkit / 'scripts' / 'geoviewer_support.py'
        builder.write_text('# fixture collector')
        c.write_json(self.toolkit / '00_manifest' / 'toolkit_files.json',
                     {'files': [{'path': 'scripts/geoviewer_support.py', 'sha256': c.digest(builder)}]})
        console = self.campaign / 'LOGS' / 'collector.txt'; console.write_text('fixture')
        attempt = dict(number=1, output=str(output), console=str(console), extractor_directory=str(self.toolkit))
        def recover(command, logfile, **kwargs):
            self.assertEqual(command[-1], str(session))
            logfile.write_text('fixture collection')
            with zipfile.ZipFile(output / 'fixture_SUPPORT.zip', 'w') as z:
                z.writestr('errors.txt', 'full logs')
            return 0
        with patch.object(c, 'process', side_effect=recover) as process:
            c.collect_attempt(self.campaign, project, attempt, self.events)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(len(attempt['collected']), 1)

    def test_tampered_collector_not_executed(self):
        self.project('a'); project = self.scan()[0]
        output = self.root / 'fallback_output'; session = output / '_scratch' / 'GeoViewer_support_fixture'
        session.mkdir(parents=True)
        c.write_json(session / 'run.json', {'run': 'fixture'})
        builder = self.toolkit / 'scripts' / 'geoviewer_support.py'; builder.write_text('changed')
        c.write_json(self.toolkit / '00_manifest' / 'toolkit_files.json',
                     {'files': [{'path': 'scripts/geoviewer_support.py', 'sha256': '0' * 64}]})
        console = self.campaign / 'LOGS' / 'collector.txt'; console.write_text('fixture')
        attempt = dict(number=1, output=str(output), console=str(console), extractor_directory=str(self.toolkit))
        with patch.object(c, 'process') as process:
            c.collect_attempt(self.campaign, project, attempt, self.events)
        process.assert_not_called()
        self.assertIn('integrity', attempt['support_recovery_warning'])

    def test_incomplete_native_tree_not_queued(self):
        self.project('a')
        original = c.snapshot
        def incomplete(pet, events):
            rows, _ = original(pet, events)
            return rows, ['unreadable directory']
        with patch.object(c, 'snapshot', side_effect=incomplete):
            rows = self.scan()
        self.assertEqual(rows[0]['status'], 'inventory_incomplete')

    def test_discovery_does_not_change_source_bytes(self):
        pet = self.project('a')
        paths = [pet, *pet.with_suffix('.ptd').iterdir()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        self.scan()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})

    def main_args(self):
        path = self.root / 'config.json'
        c.write_json(path, dict(self.config, campaign_root=str(self.root / 'catalogues')))
        return ['--config', str(path), '--extractor-directory', str(self.toolkit)]

    def test_default_crawler_saves_list_without_any_extraction(self):
        self.project('a')
        args = self.main_args()
        with patch.object(c, 'run_queue', side_effect=AssertionError('Crawler must never extract')):
            self.assertEqual(c.main(args), 0)
        pointer = c.read_json(self.root / 'LATEST_CRAWL.json')
        state = c.read_json(Path(pointer['campaign']) / 'CAMPAIGN.json')
        self.assertTrue(state['discovery_complete'])
        self.assertEqual(state['projects'][0]['status'], 'queued')
        self.assertEqual(state['projects'][0]['attempts'], [])
        self.assertFalse((self.root / 'out').exists())

    def test_runner_uses_latest_list_without_rediscovery(self):
        self.project('a')
        args = self.main_args()
        c.main(args)
        with patch.object(c, 'discover', side_effect=AssertionError('Runner must never discover')), \
             patch.object(c, 'connected_roots', side_effect=AssertionError('Runner must never enumerate drives')), \
             patch.object(c, 'deduplicate', side_effect=AssertionError('Runner must not repeat deduplication')), \
             patch.object(c, 'process', side_effect=self.fake_process):
            self.assertEqual(c.main(args + ['--mode', 'run']), 10)
        pointer = c.read_json(self.root / 'LATEST_CRAWL.json')
        state = c.read_json(Path(pointer['campaign']) / 'CAMPAIGN.json')
        self.assertEqual(state['projects'][0]['status'], 'completed_with_gaps')

    def test_runner_accepts_explicit_json_or_csv_view(self):
        self.project('a')
        args = self.main_args(); c.main(args)
        folder = Path(c.read_json(self.root / 'LATEST_CRAWL.json')['campaign'])
        for name in ('CAMPAIGN.json', 'PROJECTS.csv'):
            self.assertEqual(c.select_saved_campaign(self.root / 'config.json', folder / name), folder)
        with patch.object(c, 'discover', side_effect=AssertionError('Must read the selected list')), \
             patch.object(c, 'process', side_effect=self.fake_process):
            self.assertEqual(c.main(args + ['--mode', 'run', '--list', str(folder / 'PROJECTS.csv')]), 10)

    def test_runner_with_no_list_stops_without_scanning(self):
        with patch.object(c, 'discover', side_effect=AssertionError('No implicit discovery')):
            with self.assertRaisesRegex(ValueError, 'No completed crawler list'):
                c.main(self.main_args() + ['--mode', 'run'])

    def test_incomplete_crawl_cannot_publish_or_run(self):
        with self.assertRaisesRegex(ValueError, 'incomplete crawl'):
            c.publish_crawl_pointer(self.root / 'config.json', self.campaign, self.state)
        self.assertFalse((self.root / 'LATEST_CRAWL.json').exists())
        c.write_json(self.campaign / 'CAMPAIGN.json', self.state)
        with self.assertRaisesRegex(ValueError, 'Interrupted discovery'):
            c.main(self.main_args() + ['--mode', 'run', '--list', str(self.campaign)])

    def test_crawler_rejects_resume_instead_of_silently_skipping_scan(self):
        with self.assertRaisesRegex(ValueError, 'Crawler mode creates a new list'):
            c.main(self.main_args() + ['--resume', str(self.campaign)])

    def test_case_analyzer_failure_does_not_stop_queue(self):
        self.project('a'); self.project('b', pet=b'another case')
        self.scan()
        with patch('geoviewer_case_diagnostics.analyze_case', side_effect=ValueError('fixture analysis error')), \
             patch.object(c, 'process', side_effect=self.fake_process):
            c.run_queue(self.campaign, self.state, self.events)
        self.assertTrue(all(p['status'] == 'completed_with_gaps' for p in self.state['projects']))
        self.assertTrue(all('fixture analysis error' in p['attempts'][0]['case_diagnostics_error'] for p in self.state['projects']))

    def test_metrics_start_failure_does_not_fail_extraction(self):
        self.project('a'); self.scan()
        def callback_process(command, logfile, **kwargs):
            if kwargs.get('on_started'):
                kwargs['on_started'](os.getpid())
            return self.fake_process(command, logfile, **kwargs)
        with patch('geoviewer_process_metrics.ProcessSampler.start', side_effect=OSError('fixture metrics denied')), \
             patch.object(c, 'process', side_effect=callback_process):
            c.run_queue(self.campaign, self.state, self.events)
        project = self.state['projects'][0]
        self.assertEqual(project['status'], 'completed_with_gaps')
        self.assertIn('fixture metrics denied', project['attempts'][0]['metrics_error'])

    def test_campaign_analysis_failure_retains_original_zip_evidence(self):
        self.project('a'); self.scan()
        c.indexes(self.campaign, self.state)
        with patch('geoviewer_case_diagnostics.campaign_analysis', side_effect=ValueError('fixture comparison error')):
            c.bundle_logs(self.campaign)
        with zipfile.ZipFile(self.campaign / 'CAMPAIGN_LOGS.zip') as archive:
            self.assertIn('INDEX.html', archive.namelist())
            self.assertIn('inventories/P00001.json', archive.namelist())
            self.assertIn('fixture comparison error', archive.read('LOGS/campaign.jsonl').decode())

    def adapter_spec(self, **overrides):
        def command(project, output):
            return ['fixture-worker', '-ProjectFile', project['pet'], '-OutputRoot', str(output), '-NoPause', '-FullHash']
        result=dict(verified_session=True, select=lambda p: True, session_seconds=60, command=command,
                    timeout_hours=lambda p: 1, admission=lambda p: p, after_case=lambda *args: None,
                    stop_exit_codes=[75])
        result.update(overrides)
        return result

    def test_verified_testing_adapter_skips_repeated_doctor_and_filters_cases(self):
        self.project('a'); self.project('b', pet=b'another')
        self.scan()
        adapter = self.adapter_spec()
        adapter['select'] = lambda p: p['id'] == 'P00001'
        with patch.object(c, 'process', side_effect=self.fake_process) as process:
            c.run_queue(self.campaign, self.state, self.events, execution=adapter)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(self.state['projects'][1]['status'], 'queued')

    def test_unlimited_adapter_passes_none_to_parent_wait_without_session_expiry(self):
        self.project('a'); self.scan()
        adapter=self.adapter_spec(session_seconds=None,timeout_hours=lambda p: None)
        seen=[]
        def capture(command,logfile,**kwargs):
            seen.append(kwargs['timeout_hours'])
            return self.fake_process(command,logfile,**kwargs)
        with patch.object(c,'process',side_effect=capture):
            c.run_queue(self.campaign,self.state,self.events,execution=adapter)
        self.assertEqual(seen,[None])

    def test_runtime_invalidation_stops_remaining_test_jobs(self):
        self.project('a'); self.project('b', pet=b'another')
        self.scan()
        def changed_runtime(command, logfile, **kwargs):
            self.fake_process(command, logfile, **kwargs)
            return 75
        with patch.object(c, 'process', side_effect=changed_runtime):
            with self.assertRaisesRegex(RuntimeError, 'runtime/session validation failed'):
                c.run_queue(self.campaign, self.state, self.events, execution=self.adapter_spec())
        self.assertEqual(self.state['projects'][0]['status'], 'failed')
        self.assertEqual(self.state['projects'][1]['status'], 'queued')

    def test_unverified_testing_adapter_cannot_bypass_preflight(self):
        self.project('a'); self.scan()
        adapter = self.adapter_spec(); adapter['verified_session'] = False
        with self.assertRaisesRegex(ValueError, 'verified session'):
            c.run_queue(self.campaign, self.state, self.events, execution=adapter)


if __name__ == '__main__':
    unittest.main()
