"""Synthetic, reduced-threshold checks for the standalone assessment writer."""
import os
import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import geoviewer_assessment_shards as shards
import geoviewer_contract_publication as publication
from geoviewer_contract import POINTER, _read
from geoviewer_contract_retention import build_retention_view
import test_geoviewer_receiving_profiles as profile_fixtures
import validate_geoviewer_contract as validator
from validate_geoviewer_contract import main, validate_contract


class StandaloneAssessmentTests(unittest.TestCase):
    setUp = profile_fixtures.ProfileIntegrationTests.setUp
    add_row = profile_fixtures.ProfileIntegrationTests.add_row
    publish = profile_fixtures.ProfileIntegrationTests.publish
    table = profile_fixtures.ProfileIntegrationTests.table
    republish = profile_fixtures.ProfileIntegrationTests.republish
    ready_log = profile_fixtures.ProfileIntegrationTests.ready_log
    decisions = profile_fixtures.ProfileIntegrationTests.decisions

    def inputs(self):
        self.ready_log()
        decision = self.decisions()
        artifacts = self.table('artifacts')
        retention = Path(self.temp.name) / 'retention.json'
        profile_fixtures.write(retention, build_retention_view(
            generation=self.pointer['generation'], manifest_sha256=self.pointer['manifest_sha256'],
            artifacts=artifacts,
            dispositions=[dict(artifact_key=a['artifact_key'], status='retained',
                               reason='Synthetic current copy', evidence_reference='fixture') for a in artifacts]))
        expected = validate_contract(self.exports / POINTER, 'opendtect',
                                     decisions=decision, retention_view=retention)
        return decision, retention, expected

    def output(self, name='assessment.json'):
        return Path(self.temp.name) / 'reports' / name

    def test_flat_real_validator_decision_retention_readback(self):
        decision, retention, expected = self.inputs()
        path = self.output()
        self.assertEqual(main([str(self.exports / POINTER), '--profile', 'opendtect',
                               '--decisions', str(decision), '--retention-view', str(retention),
                               '--output', str(path)]), 0)
        report, _ = _read(path)
        self.assertEqual(report, expected)
        self.assertEqual(report['counts']['qualified_for_planned_mapping'], 1)
        self.assertIn('current_retention', report['artifact_availability'][0])

    def test_reduced_flat_cap_shards_complete_real_report(self):
        _, _, expected = self.inputs()
        path = self.output()
        # Synthetic 1 KiB flat threshold, not a 256 MiB production reproduction.
        with patch.object(publication, 'MAX_TABLE_BYTES', 1024):
            result = publication.write_bounded_assessment(path, expected)
        envelope, actual_hash = _read(path)
        self.assertEqual(result['format'], shards.ENCODING)
        self.assertEqual(actual_hash, result['sha256'])
        self.assertEqual(envelope['base'], {k: v for k, v in expected.items() if k not in shards.LIST_FIELDS})
        verified = shards.verify_sharded_assessment(path, envelope,
            entities={a['entity_key'] for a in expected['assessments']},
            artifacts={a['artifact_key'] for a in expected['artifact_availability']},
            expected_report=expected)
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(verified['rows'], envelope['aggregate']['rows'])
        self.assertEqual(verified['members'], len(result['members']))
        changed = copy.deepcopy(expected)
        changed['diagnostics'].append({'code': 'synthetic mismatch'})
        with self.assertRaisesRegex(ValueError, 'readback list length'):
            shards.verify_sharded_assessment(path, envelope,
                entities={a['entity_key'] for a in expected['assessments']},
                artifacts={a['artifact_key'] for a in expected['artifact_availability']},
                expected_report=changed)
        changed = copy.deepcopy(expected)
        changed['counts']['source_ready'] += 1
        with self.assertRaisesRegex(ValueError, 'readback base'):
            shards.verify_sharded_assessment(path, envelope,
                entities={a['entity_key'] for a in expected['assessments']},
                artifacts={a['artifact_key'] for a in expected['artifact_availability']},
                expected_report=changed)

    def test_readback_mismatch_never_exposes_final_or_members(self):
        _, _, expected = self.inputs()
        path = self.output()
        with patch.object(publication, 'MAX_TABLE_BYTES', 1024), \
             patch.object(publication, 'verify_sharded_assessment', side_effect=ValueError('mismatch')):
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                publication.write_bounded_assessment(path, expected)
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob('assessment.*.jsonl')), [])
        self.assertEqual(list(path.parent.glob('*.assessment-stage')), [])

    def test_interrupted_promotion_cleans_only_own_member_links(self):
        _, _, expected = self.inputs()
        path = self.output()
        promote_name = 'rename' if os.name == 'nt' else 'link'
        original_promote = getattr(os, promote_name)
        def interrupted(source, target, *args, **kwargs):
            if Path(target) == path:
                raise OSError('interrupted final publication')
            return original_promote(source, target, *args, **kwargs)
        with patch.object(publication, 'MAX_TABLE_BYTES', 1024), \
             patch.object(publication.os, promote_name, side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                publication.write_bounded_assessment(path, expected)
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob('assessment.*.jsonl')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive rename path')
    def test_windows_promotion_works_without_hardlinks(self):
        _, _, expected = self.inputs()
        path = self.output()
        path.parent.mkdir(parents=True, exist_ok=True)
        member = path.with_name(path.stem + '.assessments.0001.jsonl')
        member.write_bytes(b'existing member')
        with patch.object(publication, 'MAX_TABLE_BYTES', 1024), \
             patch.object(publication.os, 'link', side_effect=OSError('hardlinks unavailable')):
            with self.assertRaises(FileExistsError):
                publication.write_bounded_assessment(path, expected)
            self.assertEqual(member.read_bytes(), b'existing member')
            self.assertFalse(path.exists())
            member.unlink()
            result = publication.write_bounded_assessment(path, expected)
            with self.assertRaises(FileExistsError):
                publication.write_bounded_assessment(path, expected)
        self.assertEqual(result['format'], shards.ENCODING)
        envelope, _ = _read(path)
        self.assertEqual(shards.verify_sharded_assessment(path, envelope,
            entities={a['entity_key'] for a in expected['assessments']},
            artifacts={a['artifact_key'] for a in expected['artifact_availability']},
            expected_report=expected)['status'], 'verified')

    def test_stage_cleanup_failure_does_not_mask_committed_report(self):
        _, _, expected = self.inputs()
        path = self.output()
        with patch.object(publication.shutil, 'rmtree', side_effect=OSError('cleanup failed')):
            result = publication.write_bounded_assessment(path, expected)
        self.assertTrue(path.exists())
        self.assertIn('cleanup failed', result['stage_cleanup_warning'])
        self.assertEqual(_read(path)[0], expected)

    def test_existing_report_member_and_run_output_guards(self):
        _, _, expected = self.inputs()
        path = self.output()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            publication.write_bounded_assessment(path, expected)
        self.assertEqual(path.read_bytes(), b'existing')
        path.unlink()
        member = path.with_name(path.stem + '.assessments.0001.jsonl')
        member.write_bytes(b'existing member')
        with patch.object(publication, 'MAX_TABLE_BYTES', 1024), self.assertRaises(FileExistsError):
            publication.write_bounded_assessment(path, expected)
        self.assertEqual(member.read_bytes(), b'existing member')
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob('assessment.artifact_availability.*.jsonl')), [])
        self.assertEqual(main([str(self.exports / POINTER), '--output',
                               str(self.run / 'inside.json')]), 2)

    def test_reduced_production_bounds_fail_closed(self):
        _, _, expected = self.inputs()
        for field, limit, message in (
                ('MAX_TOTAL_BYTES', 1, 'aggregate byte bound'),
                ('MAX_MEMBERS', 0, 'member count bound'),
                ('MAX_TOTAL_ROWS', 0, 'aggregate row bound'),
                ('MAX_ROW_BYTES', 1, 'physical row byte bound')):
            path = self.output(field + '.json')
            with self.subTest(field=field), patch.object(publication, 'MAX_TABLE_BYTES', 1024), \
                 patch.object(shards, field, limit), self.assertRaisesRegex(ValueError, message):
                publication.write_bounded_assessment(path, expected)
            self.assertFalse(path.exists())
        oversized = dict(expected)
        oversized['diagnostics'] = [{'text': 'x' * 500}]
        path = self.output('logical.json')
        with patch.object(publication, 'MAX_TABLE_BYTES', 1024), \
             patch.object(shards, 'MAX_LOGICAL_BYTES', 100), self.assertRaisesRegex(ValueError, 'logical item byte bound'):
            publication.write_bounded_assessment(path, oversized)
        self.assertFalse(path.exists())

    def test_actual_cli_subprocess_fresh_output(self):
        decision, retention, expected = self.inputs()
        path = self.output('subprocess.json')
        command = [sys.executable, '-B', str(Path(validator.__file__)),
                   str(self.exports / POINTER), '--profile', 'opendtect', '--decisions', str(decision),
                   '--retention-view', str(retention), '--output', str(path)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_read(path)[0], expected)


if __name__ == '__main__':
    unittest.main()
