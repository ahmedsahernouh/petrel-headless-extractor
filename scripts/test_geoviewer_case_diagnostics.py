# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Synthetic evidence and live Windows counter checks; no real project reads."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoviewer_case_diagnostics as d
import geoviewer_process_metrics as m


class DiagnosticsTests(unittest.TestCase):
    def test_forwarded_origin_keeps_stage_and_retry_scopes_separate(self):
        base=dict(run_id='A',pid=700)
        first=dict(base,child_reported_metadata=dict(pid=111))
        second=dict(base,child_reported_metadata=dict(pid=222))
        self.assertNotEqual(d.event_scope(first,'events'),d.event_scope(second,'events'))
        wrapped=dict(base,child_reported_metadata=dict(pid=700,child_reported_metadata=dict(pid=111)))
        self.assertEqual(d.event_scope(first,'events'),d.event_scope(wrapped,'events'))
        self.events([
            dict(first,event='stage',label='Origin A',elapsed_seconds=0),
            dict(second,event='stage',label='Origin B',elapsed_seconds=1),
            dict(first,event='stage',label='Origin A next',elapsed_seconds=5,previous_stage_seconds=5),
            dict(second,event='completed',elapsed_seconds=8),
            dict(first,event='completed',elapsed_seconds=9)])
        case=self.analyze()
        self.assertEqual(sorted((r['stage'],r['seconds']) for r in case['performance']['stages']),
                         [('Origin A',5),('Origin A next',4),('Origin B',7)])
        self.assertEqual(case['performance']['stages_without_terminal_event'],[])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='GeoViewer_diagnostics_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / 'campaign'
        self.output = self.root / 'output'
        self.output.mkdir()
        self.package = self.output / 'trial_data/extraction/package/Trial'
        self.destination = self.campaign / 'LOGS/P00001/attempt_001'
        self.project = dict(id='P00001', pet=str(self.root / 'Demo2010_filename_only.pet'), total_bytes=120, file_count=2,
                            status='completed_with_gaps', attempts=[])
        self.attempt = dict(number=1, output=str(self.output), exit_code=10, receipt_status='completed_with_gaps', elapsed_seconds=9)
        self.project['attempts'].append(self.attempt)
        self.state = dict(projects=[self.project], config={'full_hash': True}, extractor_manifest_sha256='fixture-build-hash',
                          root_aliases=[dict(path='L:\\', canonical='\\\\server\\share')])
        self.write(self.campaign / 'CAMPAIGN.json', self.state)
        self.write(self.campaign / 'inventories/P00001.json', [{'relative': 'Demo.ptd/Model.ptd', 'size': 100}, {'relative': 'Demo.pet', 'size': 20}])
        self.context = dict(saved_version={'value': '2024.5', 'source': 'native metadata'}, layout='bxml-lz4',
                            object_inventory_complete=True, objects=[{'id': 'A', 'category': 'logs'}, {'id': 'B', 'category': 'grids'}, {'id': 'C', 'category': 'polygons'}])
        self.write(self.output / 'trial_data/RUN_RESULT.json', dict(status='completed_with_gaps', project_context=self.context))
        self.write(self.package / '01_project_metadata/project_context.json', self.context)

    def test_explicit_stage_receipts_keep_retry_clocks_and_missing_end_separate(self):
        base = dict(category='logs_surfaces', started_at_utc='2026-09-26T00:00:00Z',
                    ended_at_utc='2026-09-26T00:00:02Z', elapsed_seconds=2,
                    status='completed', pipeline_run_id='run-a', stage_id='one')
        self.write(self.package / '01_project_metadata/stage_budgets.json',
                   {'attempts': [base, dict(base, pipeline_run_id='run-b', elapsed_seconds=3),
                                 dict(base, stage_id='two', status='running', ended_at_utc=None)]})
        # Same ID in a journal is not counted a second time; IDs from other runs are distinct.
        self.write(self.package.parent / ('pipeline_timings_' + 'a'*32 + '.json'), [base])
        perf = self.analyze()['performance']
        self.assertEqual(sorted(r['seconds'] for r in perf['stages']), [2, 3])
        self.assertEqual(len(perf['stages_without_terminal_event']), 1)
        self.assertIsNone(perf['stages_without_terminal_event'][0]['seconds'])
        gaps = self.analyze()['evidence_gaps']
        self.assertTrue(any('event-derived stage/hash' in g['reason'] for g in gaps))
        self.assertFalse(any('pipeline timing journal missing' in g['reason'] for g in gaps))

    def test_invalid_timing_journal_stays_an_evidence_gap(self):
        self.write(self.package.parent / ('pipeline_timings_' + 'a'*32 + '.json'), {'stages': []})
        self.write(self.package.parent / 'pipeline_timings_arbitrary.json', [{'elapsed_seconds': 99}])
        case = self.analyze()
        self.assertEqual(case['performance']['stages'], [])
        self.assertTrue(any('Expected pipeline timing journal array' in g['reason'] for g in case['evidence_gaps']))

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def events(self, values):
        path = self.output / 'trial_EVENTS.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in values), encoding='utf-8')
        return path

    def analyze(self):
        result = d.analyze_case(self.campaign, self.project, self.attempt, self.destination)
        self.assertTrue(Path(result['path']).is_file())
        return json.loads(Path(result['path']).read_text())

    def objects(self, rows):
        self.write(self.package / '07_workflows_reports/native_recovery/native_recovery_report.json', dict(objects=rows))

    def occurrences(self):
        return [json.loads(row) for row in (self.destination / 'ERROR_OCCURRENCES.jsonl').read_text().splitlines()]

    def test_version_comes_from_metadata_and_inventory_separate_from_decoders(self):
        self.objects([dict(object_id='A', blob_type='WellLog', status='decoded', object_version=0, registry_version=7)])
        case = self.analyze()
        self.assertEqual(case['source']['saved_version'], '2024.5')
        self.assertEqual(case['recorded_inventory_objects'], 3)
        self.assertEqual(sum(r['count'] for r in case['decoder_record_counts']), 1)
        self.assertIsNone(case['whole_project_recovery_percentage'])
        self.assertEqual(case['decoder_profile_counts'][0]['decoder_profile'], 'unknown')
        self.assertEqual(case['decoder_profile_counts'][0]['serialization_version'], '0')
        self.assertEqual(case['settings']['extractor_manifest_sha256'], 'fixture-build-hash')
        self.assertEqual(case['settings']['drive_aliases'], self.state['root_aliases'])

    def test_receipts_override_duplicate_and_stale_object_events(self):
        self.objects([dict(object_id='A', blob_type='WellLog', status='decoded', object_version=5, elapsed_seconds=12)])
        self.events([dict(event=event, object_id='A', blob_type='WellLog', status='failed') for event in
                     ('object_failed', 'object_committed', 'native_object_outcome', 'native_object_outcome')])
        case = self.analyze()
        self.assertEqual(case['decoder_record_counts'], [dict(lane='native', object_type='WellLog', status='decoded', count=1)])
        self.assertEqual(case['performance']['slowest_objects'][0]['seconds'], 12)

    def test_failed_layout_dimensions_do_not_imply_accepted_profile(self):
        self.objects([dict(object_id='N', blob_type='RegValGrid2', status='unsupported_layout',
                           object_version=[9, 0], registry_version=4,
                           attempted_decoder_profile='native_grid', error={'operation': 'validate_object_profile'})])
        self.write(self.package / '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
                   dict(objects=[dict(object_id='S', blob_type='Points3', version=7, status='failed_closed',
                                      envelope={'envelope': 'LZ4_v1_raw_block'}, attempted_decoder_profile='decode_points3',
                                      failure_checkpoint='decode_points3', error='node bounds mismatch')]))
        self.write(self.output / 'trial_data/seismic_results.json', dict(objects=[
            dict(id='Z', status='conversion_failed', result={'operation': 'zgy-to-segy', 'version': '1.0.0'}),
            dict(id='M', status='missing', format='.zgy')]))
        case = self.analyze()
        rows = [json.loads(line) for line in (self.destination / 'OBJECT_OUTCOMES.jsonl').read_text().splitlines()]
        detail = {r['object_id']: r['decoder_evidence'] for r in rows}
        self.assertEqual(detail['N']['serialization_version'], [9, 0])
        self.assertEqual(detail['N']['failure_checkpoint'], 'validate_object_profile')
        self.assertEqual(detail['S']['registry_version'], 7)
        self.assertIsNone(detail['S']['serialization_version'])
        self.assertEqual(detail['S']['envelope'], 'LZ4_v1_raw_block')
        self.assertEqual(detail['Z']['decoder_family'], 'zgy-to-segy')
        self.assertIsNone(detail['Z']['registry_version'])
        self.assertIsNone(detail['M']['decoder_family'])
        self.assertTrue(all(r['decoder_profile'] is None for r in detail.values()))
        self.assertEqual(sum(r['count'] for r in case['decoder_profile_counts']), 4)

    def test_sparse_stage_pairs_use_same_clock_and_leave_unknown_tail(self):
        self.events([dict(event='stage', run_id='A', pid=7, label='Copy', elapsed_seconds=10),
                     dict(event='stage', run_id='B', pid=7, label='Other run', elapsed_seconds=40),
                     dict(event='stage', run_id='A', pid=7, label='Decode', elapsed_seconds=25),
                     dict(event='completed', run_id='B', pid=7, elapsed_seconds=35)])
        case = self.analyze()
        stages = case['performance']['stages']
        self.assertEqual([(r['stage'], r['seconds']) for r in stages], [('Copy', 15)])
        self.assertEqual(stages[0]['timing_basis'], 'event-clock interval to next stage')
        self.assertTrue(any(r['label'] == 'Decode' for r in case['performance']['stages_without_terminal_event']))

    def test_stage_pair_does_not_cross_event_files(self):
        self.events([dict(event='stage', pid=7, label='Copy', elapsed_seconds=0)])
        (self.output / 'z_EVENTS.jsonl').write_text(json.dumps(dict(event='completed', pid=7, elapsed_seconds=100)) + '\n')
        self.assertEqual(self.analyze()['performance']['stages'], [])

    def fallback(self, values):
        path = self.output / '_scratch/GeoViewer_diagnostics_fixture/EVENTS.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in values))
        self.write(self.output / 'trial_data/RUN_RESULT.json', dict(status='completed_with_gaps', structured_events=str(path)))
        return path

    def test_fallback_split_stage_deduplicates_and_reconciles_terminal(self):
        start = dict(event='stage', run_id='A', pid=7, sequence=1, event_id='A:1', label='Copy', elapsed_seconds=5)
        hashed = dict(event='hash_completed', run_id='A', pid=7, sequence=2, event_id='A:2', path='fixture', bytes=10, seconds=1)
        self.events([start, hashed])
        self.fallback([hashed, dict(event='run_finished', run_id='A', pid=7, sequence=3, event_id='A:3', elapsed_seconds=15)])
        case = self.analyze()
        self.assertEqual([(s['stage'], s['seconds']) for s in case['performance']['stages']], [('Copy', 10)])
        self.assertEqual(case['performance']['stages_without_terminal_event'], [])
        self.assertEqual(case['performance']['completed_hash_calls'], 1)
        self.assertEqual(case['event_counts']['hash_completed'], 1)

    def test_event_ids_keep_runs_distinct_and_conflicts_visible(self):
        first = dict(event='hash_completed', run_id='A', pid=7, sequence=1, event_id='one', path='fixture', bytes=10, seconds=1)
        self.events([first])
        self.fallback([dict(first, seconds=2), dict(first, run_id='B', sequence=2)])
        case = self.analyze()
        self.assertEqual(case['performance']['completed_hash_calls'], 2)
        self.assertTrue(any('Conflicting duplicate event_id' in g['reason'] for g in case['evidence_gaps']))

    def test_segments_merge_by_sequence_and_unreferenced_scratch_is_ignored(self):
        self.events([dict(event='completed', run_id='A', pid=7, sequence=2, elapsed_seconds=15)])
        self.fallback([dict(event='stage', run_id='A', pid=7, sequence=1, label='Copy', elapsed_seconds=5)])
        rogue = self.output / '_scratch/GeoViewer_diagnostics_rogue/EVENTS.jsonl'
        rogue.parent.mkdir(parents=True)
        rogue.write_text('{"event":"unexpected"}\n')
        case = self.analyze()
        self.assertEqual([s['seconds'] for s in case['performance']['stages']], [10])
        self.assertNotIn('unexpected', case['event_counts'])

    def test_fallback_retry_recovery_and_other_runs_are_separate(self):
        base = dict(run_id='A', pid=7, operation='write', source='s', destination='d')
        err = dict(base, event='io_error', attempt=1, severity='warning', event_id='A:1', sequence=1)
        self.events([err, dict(event='stage', run_id='A', pid=7, sequence=2, event_id='A:2', elapsed_seconds=1, label='Copy')])
        self.fallback([err, dict(base, event='io_recovered', attempt=2, sequence=3, event_id='A:3'),
                       dict(event='completed', run_id='B', pid=7, sequence=4, elapsed_seconds=20),
                       dict(event='completed', run_id='A', pid=8, sequence=5, elapsed_seconds=20)])
        case = self.analyze()
        self.assertEqual(case['error_occurrence_counts'], {'recovered': 1})
        self.assertEqual(case['performance']['stages'], [])
        self.assertEqual(len(case['performance']['stages_without_terminal_event']), 1)

    def test_fallback_references_are_exact_bounded_and_missing_explicit(self):
        outside = self.root / 'EVENTS.jsonl'
        outside.write_text('{"event":"unexpected"}\n')
        for ref in (str(outside), str(self.output / 'native_data/EVENTS.jsonl'),
                    str(self.output / '_scratch/GeoViewer_diagnostics_missing/EVENTS.jsonl')):
            self.write(self.output / 'trial_data/RUN_RESULT.json', {'structured_events': ref})
            case = self.analyze()
            self.assertEqual(case['event_counts'], {})
            self.assertTrue(any('structured_events reference' in g['reason'] for g in case['evidence_gaps']))

    def test_fallback_invalid_timing_and_malformed_lines_remain_unknown(self):
        self.events([dict(event='stage', run_id='A', pid=7, label='Copy', elapsed_seconds=50)])
        path = self.fallback([dict(event='completed', run_id='A', pid=7, elapsed_seconds=5)])
        with path.open('a') as stream:
            stream.write('invalid json\n')
        case = self.analyze()
        self.assertEqual(case['performance']['stages'], [])
        self.assertEqual(len(case['performance']['stages_without_terminal_event']), 1)
        self.assertTrue(any('Terminal stage interval timing invalid' in g['reason'] for g in case['evidence_gaps']))
        self.assertTrue(any(g.get('line') == 2 for g in case['evidence_gaps']))

    def test_event_only_outcome_preserved_once(self):
        self.events([dict(event='native_object_outcome', object_id='Q', blob_type='Grid', status='unsupported', reason='profile unknown')] * 2)
        case = self.analyze()
        self.assertEqual(sum(r['count'] for r in case['decoder_record_counts']), 1)
        self.assertEqual(case['decoder_record_counts'][0]['lane'], 'event_only')

    def test_parent_timeout_is_reported_without_claiming_conversion(self):
        self.attempt.update(exit_code=1, error='Configured per-project time limit exceeded',
                            exception_type='TimeoutError', timeout_seconds=7200)
        self.events([dict(event='stage', pid=7, label='Source integrity hashes', elapsed_seconds=0)])
        case = self.analyze()
        failures = [json.loads(r) for r in (self.destination / 'ERROR_OCCURRENCES.jsonl').read_text().splitlines()]
        timeout = next(r for r in failures if r['owner'] == 'runner')
        self.assertEqual(timeout['normalized']['exception_type'], 'TimeoutError')
        self.assertIn('time limit exceeded', timeout['normalized']['reason'])
        self.assertEqual(timeout['record']['active_stages'][0]['label'], 'Source integrity hashes')
        self.assertFalse(case['conversion_tested'])
        self.assertTrue(case['conversion_requested'])

    def test_stage_array_and_informational_reason(self):
        self.write(self.package / '01_project_metadata/pipeline_stages.json',
                   [dict(category='report', status='partial', reason='denied')])
        self.write(self.package / '01_project_metadata/native_compatibility.json',
                   dict(status='ready', reason='Native database is readable'))
        case = self.analyze()
        failures = [json.loads(r) for r in (self.destination / 'ERROR_OCCURRENCES.jsonl').read_text().splitlines()]
        self.assertTrue(any(r['owner'] == 'stage_receipt' for r in failures))
        self.assertFalse(any('readable' in r['normalized']['reason'] for r in failures))
        self.assertFalse(any('pipeline_stages' in r.get('file', '') for r in case['evidence_gaps']))

    def test_string_exception_is_not_replaced_by_status(self):
        signature, identity = d.issue_signature(dict(status='failed_closed', error='DecodeError: decreasing MD'))
        self.assertEqual(identity['reason'], 'DecodeError: decreasing MD')

    def test_missing_version_and_events_never_guessed_from_filename(self):
        self.write(self.package / '01_project_metadata/project_context.json', {})
        self.write(self.output / 'trial_data/RUN_RESULT.json', {'status': 'failed'})
        case = self.analyze()
        self.assertEqual(case['source']['saved_version'], 'unknown')
        self.assertEqual(case['performance']['process_metrics']['status'], 'not_recorded')
        self.assertTrue(any('missing' in r['reason'].lower() for r in case['evidence_gaps']))

    def test_malformed_context_retains_receipt_context_and_continues_bad_events(self):
        (self.package / '01_project_metadata/project_context.json').write_text('{bad')
        path = self.events([{'event': 'native_preflight', 'saved_version': {'value': '2010'}, 'layout': 'legacy'}])
        with path.open('a') as stream:
            stream.write('{partial\n[1,2]\n')
        case = self.analyze()
        self.assertEqual(case['source']['saved_version'], '2024.5')
        self.assertGreaterEqual(len(case['evidence_gaps']), 3)

    def test_final_seismic_results_replace_earlier_discovery_state(self):
        self.write(self.output / 'trial_data/seismic_results.json', dict(objects=[dict(object_id='Z', status='converted')]))
        self.write(self.package / '01_project_metadata/project_seismic_inventory.json', dict(objects=[dict(object_id='Z', status='unavailable')]))
        case = self.analyze()
        self.assertEqual(case['decoder_record_counts'][0]['status'], 'converted')

    def test_payloads_credentials_and_unrelated_native_files_are_not_collected(self):
        self.objects([dict(object_id='A', status='decoded', samples=[987654321], password='TOPSECRET', blob_data='PAYLOAD_MARKER')])
        payload = self.package / '04_native_data/SECRET.ptd'
        payload.parent.mkdir(parents=True)
        payload.write_text('NATIVE_PAYLOAD_NEVER_READ')
        original = Path.open
        def guard(path, *args, **kwargs):
            if path == payload:
                raise AssertionError('Native payload was opened')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guard):
            self.analyze()
        text = ''.join(p.read_text() for p in self.destination.iterdir())
        for forbidden in ('987654321', 'TOPSECRET', 'PAYLOAD_MARKER', 'NATIVE_PAYLOAD_NEVER_READ'):
            self.assertNotIn(forbidden, text)
        self.assertIn('[omitted: data payload]', text)

    def test_hash_passes_and_previous_stage_duration(self):
        self.events([dict(event='stage', pid=7, label='Hashing', elapsed_seconds=0),
                     dict(event='hash_completed', path='Data.ptd', bytes=100, seconds=2),
                     dict(event='hash_completed', path='Data.ptd', bytes=100, seconds=3),
                     dict(event='stage', pid=7, label='Decode', elapsed_seconds=5, previous_stage_seconds=5),
                     dict(event='completed', pid=7, elapsed_seconds=9)])
        perf = self.analyze()['performance']
        self.assertEqual(perf['observed_hash_bytes'], 200)
        self.assertEqual(perf['repeated_hash_bytes'], 100)
        self.assertEqual([(r['stage'], r['seconds']) for r in perf['stages']], [('Hashing', 5), ('Decode', 4)])

    def test_error_normalization_groups_instances_but_preserves_profiles(self):
        a = dict(blob_type='Points', object_version=0, reason='Ambiguous at point 168 in "D:\\One\\abc.ptd"')
        b = dict(blob_type='Points', object_version=0, reason='Ambiguous at point 399 in "E:\\Two\\xyz.ptd"')
        self.assertEqual(d.issue_signature(a)[0], d.issue_signature(b)[0])
        self.assertIsNone(d.issue_signature(a)[1]['profile'])
        self.assertEqual(d.issue_signature(a)[1]['serialization_version'], 0)
        self.assertNotEqual(d.issue_signature(a)[0], d.issue_signature(dict(b, object_version=1))[0])

    def test_exact_contiguous_retry_sequence_is_annotated_recovered(self):
        identity = dict(operation='finalize_directory', source='pending-A', destination='final-A', pid=17)
        self.events([
            dict(event='io_error', severity='warning', attempt=1, message='sharing violation', **identity),
            dict(event='retry_scheduled', severity='warning', attempt=1, delay_seconds=1, message='sharing violation', **identity),
            dict(event='retry_scheduled', severity='warning', attempt=2, delay_seconds=2, message='sharing violation', **identity),
            dict(event='io_recovered', attempt=3, duration_seconds=3, **identity),
        ])
        case = self.analyze()
        occurrences = [row for row in self.occurrences() if row['owner'] == 'event']
        self.assertEqual(len(occurrences), 3)
        self.assertTrue(all(row['resolution'] == 'recovered' for row in occurrences))
        self.assertTrue(all(row['recovery_evidence'].endswith(':4') for row in occurrences))
        self.assertTrue(all(row['recovery_record']['attempt'] == 3 for row in occurrences))
        self.assertEqual(case['error_occurrence_counts']['recovered'], 3)

        d.campaign_analysis(self.campaign, self.state)
        summary = json.loads((self.campaign / 'LOGS/ANALYSIS/CAMPAIGN_ANALYSIS.json').read_text())
        self.assertEqual(summary['error_occurrence_counts'], {'recovered': 3})
        self.assertTrue(all(group['unresolved_occurrences'] == 0 for group in summary['grouped_issues']))
        self.assertFalse(summary['investigation_candidates'])

    def test_recovery_does_not_cross_identity_or_later_operation_boundary(self):
        base = dict(operation='finalize_directory', source='pending-A', destination='final-A', pid=17)
        self.events([
            dict(event='retry_scheduled', severity='warning', attempt=1, message='first transient', **base),
            dict(event='io_recovered', attempt=2, **base),
            dict(event='io_failed', severity='error', attempt=1, message='later final failure', **base),
            dict(event='retry_scheduled', severity='warning', attempt=1, message='other pid',
                 operation=base['operation'], source=base['source'], destination=base['destination'], pid=18),
            dict(event='retry_scheduled', severity='warning', attempt=1, message='other destination',
                 operation=base['operation'], source=base['source'], destination='final-B', pid=17),
            dict(event='io_recovered', attempt=2, **base),
        ])
        self.analyze()
        occurrences = [row for row in self.occurrences() if row['owner'] == 'event']
        self.assertEqual([row['resolution'] for row in occurrences],
                         ['recovered', 'unresolved', 'unresolved', 'unresolved'])
        self.assertNotIn('recovery_evidence', occurrences[1])

    def test_ambiguous_retry_identity_and_noncontiguous_attempt_stay_unresolved(self):
        self.events([
            dict(event='retry_scheduled', severity='warning', attempt=1, operation='replace_summary',
                 source='a', destination='b', message='missing pid'),
            dict(event='io_recovered', attempt=2, operation='replace_summary', source='a', destination='b'),
            dict(event='retry_scheduled', severity='warning', attempt='1', operation='replace_summary',
                 source='c', destination='d', pid=9, message='malformed attempt'),
            dict(event='retry_scheduled', severity='warning', attempt=2, operation='replace_summary',
                 source='e', destination='f', pid=10, message='sequence starts late'),
            dict(event='io_recovered', attempt=3, operation='replace_summary', source='e', destination='f', pid=10),
            dict(event='retry_scheduled', severity='warning', attempt=1, operation=['bad'],
                 source='g', destination='h', pid=11, message='malformed operation'),
            dict(event='retry_scheduled', severity='warning', attempt=1, operation='replace_summary',
                 source='i', destination='j', pid=12, message='possible first operation'),
            dict(event='retry_scheduled', severity='warning', attempt=1, operation='replace_summary',
                 source='i', destination='j', pid=12, message='possible later operation'),
            dict(event='io_recovered', attempt=2, operation='replace_summary', source='i', destination='j', pid=12),
        ])
        self.analyze()
        self.assertTrue(all(row['resolution'] == 'unresolved' for row in self.occurrences() if row['owner'] == 'event'))

    def test_retry_recovery_does_not_cross_event_files(self):
        identity = dict(operation='finalize_directory', source='pending-A', destination='final-A', pid=17)
        (self.output / 'a_EVENTS.jsonl').write_text(json.dumps(dict(
            event='retry_scheduled', severity='warning', attempt=1, message='transient', **identity)) + '\n')
        (self.output / 'b_EVENTS.jsonl').write_text(json.dumps(dict(
            event='io_recovered', attempt=2, **identity)) + '\n')
        self.analyze()
        event_rows = [row for row in self.occurrences() if row['owner'] == 'event']
        self.assertEqual(len(event_rows), 1)
        self.assertEqual(event_rows[0]['resolution'], 'unresolved')

    def test_huge_attempt_is_malformed_and_stays_unresolved(self):
        identity = dict(operation='replace_summary', source='a', destination='b', pid=8)
        self.events([
            dict(event='retry_scheduled', severity='warning', attempt=10**9, message='invalid attempt', **identity),
            dict(event='io_recovered', attempt=10**9 + 1, **identity),
        ])
        self.analyze()
        event_rows = [row for row in self.occurrences() if row['owner'] == 'event']
        self.assertEqual(len(event_rows), 1)
        self.assertEqual(event_rows[0]['resolution'], 'unresolved')

    def test_io_failed_ends_retry_sequence_before_later_recovery(self):
        identity = dict(operation='report_update', source='report.tmp', destination='report.json', pid=21)
        self.events([
            dict(event='retry_scheduled', severity='warning', attempt=1, message='transient', **identity),
            dict(event='io_failed', severity='error', attempt=2, message='terminal failure', **identity),
            dict(event='io_recovered', attempt=2, **identity),
        ])
        self.analyze()
        event_rows = [row for row in self.occurrences() if row['owner'] == 'event']
        self.assertEqual([row['resolution'] for row in event_rows], ['unresolved', 'unresolved'])

    def test_campaign_treats_legacy_occurrence_without_resolution_as_unresolved(self):
        self.events([dict(event='io_failed', severity='error', attempt=1, operation='report_update',
                          source='report.tmp', destination='report.json', pid=21, message='access denied')])
        self.analyze()
        occurrence = self.occurrences()[0]
        occurrence.pop('resolution')
        (self.destination / 'ERROR_OCCURRENCES.jsonl').write_text(json.dumps(occurrence) + '\n', encoding='utf-8')
        d.campaign_analysis(self.campaign, self.state)
        summary = json.loads((self.campaign / 'LOGS/ANALYSIS/CAMPAIGN_ANALYSIS.json').read_text())
        self.assertEqual(summary['error_occurrence_counts'], {'unresolved': 1})
        self.assertEqual(summary['grouped_issues'][0]['unresolved_occurrences'], 1)
        self.assertEqual(summary['grouped_issues'][0]['recovered_occurrences'], 0)
        self.assertEqual(len(summary['investigation_candidates']), 1)

    def test_latest_attempt_only_in_comparison(self):
        self.objects([dict(object_id='A', blob_type='WellLog', status='failed', reason='unsupported profile')])
        self.analyze()
        self.attempt = dict(self.attempt, number=2)
        self.project['attempts'].append(self.attempt)
        self.destination = self.campaign / 'LOGS/P00001/attempt_002'
        self.objects([dict(object_id='A', blob_type='WellLog', status='decoded')])
        self.analyze()
        d.campaign_analysis(self.campaign, self.state)
        summary = json.loads((self.campaign / 'LOGS/ANALYSIS/CAMPAIGN_ANALYSIS.json').read_text())
        self.assertEqual(summary['attempts_total'], 2)
        self.assertEqual(summary['latest_attempts_analyzed'], 1)
        self.assertEqual(summary['version_type_matrix'][0]['objects'], 1)
        self.assertEqual(summary['version_type_matrix'][0]['status'], 'decoded')
        self.assertFalse(summary['grouped_issues'])
        self.assertTrue((self.campaign / 'LOGS/P00001/attempt_001/ERROR_OCCURRENCES.jsonl').is_file())

    def test_metrics_summary_counts_process_identity_once_and_keeps_reused_pid_distinct(self):
        path = self.root / 'metrics.jsonl'
        rows = [dict(processes=[dict(pid=7, created_ticks=100, cpu_seconds=2, read_bytes=50, private_bytes=100)], output_free_bytes=1000),
                dict(processes=[dict(pid=7, created_ticks=100, cpu_seconds=3, read_bytes=70, private_bytes=90)], output_free_bytes=900),
                dict(processes=[dict(pid=7, created_ticks=200, cpu_seconds=1, read_bytes=10, private_bytes=200)], output_free_bytes=800)]
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        result = d.metrics_summary(path, [])
        self.assertEqual(d.clean(result)['sample_count'], 3)
        self.assertEqual(result['observed_processes'], 2)
        self.assertEqual(result['sampled_process_totals']['cpu_seconds'], 4)
        self.assertEqual(result['sampled_process_totals']['read_bytes'], 80)
        self.assertEqual(result['sampled_peak_tree_private_bytes'], 200)
        self.assertEqual(result['output_free_change_bytes'], -200)

    def test_diagnostic_json_size_limit_is_explicit(self):
        with patch.object(d, 'JSON_LIMIT', 2):
            case = self.analyze()
        self.assertTrue(any('limit' in r['reason'] for r in case['evidence_gaps']))
        self.assertEqual(case['source']['saved_version'], 'unknown')

    def test_export_index_summarizes_formats_qc_and_logical_bytes(self):
        self.write(self.output / 'trial_EXPORTS/FILE_INDEX.json', dict(files=[
            dict(format='ZMAP', category='Grids', bytes=12, storage='hardlink', numerical_qc='passed'),
            dict(format='SEG-Y', category='Seismic', bytes=34, storage='copy', numerical_qc='passed')]))
        case = self.analyze()
        self.assertEqual(case['artifacts']['logical_bytes'], 46)
        self.assertEqual(case['artifacts']['formats'], {'ZMAP': 1, 'SEG-Y': 1})
        self.assertEqual(case['artifacts']['numerical_qc'], {'passed': 2})

    def test_csv_escapes_formula_prefix(self):
        path = self.root / 'safe.csv'
        d.csv_file(path, [dict(name='=1+1')], ['name'])
        self.assertIn("'=1+1", path.read_text())

    @unittest.skipUnless(os.name == 'nt', 'Windows counters')
    def test_live_windows_metrics_are_available(self):
        row = m.windows_tree_sample(os.getpid())
        own = next(p for p in row['processes'] if p['pid'] == os.getpid())
        self.assertGreater(own['private_bytes'], 0)
        self.assertGreater(own['created_ticks'], 0)
        self.assertGreaterEqual(own['cpu_seconds'], 0)
        self.assertIn('read_bytes', own)

    def test_sampler_failure_is_recorded_without_raising(self):
        sampler = m.ProcessSampler(self.root / 'metrics.jsonl', self.root, interval=1)
        with patch.object(m, 'windows_tree_sample', side_effect=OSError('fixture counter failure')):
            sampler.start(os.getpid())
            deadline = time.monotonic() + 3
            while not sampler.errors and time.monotonic() < deadline:
                time.sleep(.01)
            result = sampler.close()
        self.assertTrue(any('fixture counter failure' in e for e in result['errors']))
        self.assertFalse(result['sampling_still_stopping'])


if __name__ == '__main__':
    unittest.main()
