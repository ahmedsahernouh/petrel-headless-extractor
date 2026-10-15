"""Diagnostic display routing retains complete assessment evidence and integrity guards."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import geoviewer_preparation_report as report
from geoviewer_assessment_shards import write_sharded_assessment
from geoviewer_contract_publication import publish_preparation
import test_geoviewer_contract as fixtures
from validate_geoviewer_contract import verified_profile_assessments


class DiagnosticBudgetTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    add_row = fixtures.ContractTests.add_row

    def prepared(self):
        publish_preparation(self.package, self.exports, self.run, {},
                            file_index=self.index, conversion_enabled=True)
        pointer = json.loads((self.exports / 'INTEROPERABILITY_CURRENT.json').read_text())
        manifest_path = self.exports / pointer['manifest']
        folder = self.run / 'interoperability' / pointer['generation']
        return pointer, manifest_path, folder

    def publish(self, name, pointer, manifest_path, folder, assessments):
        return report.publish_human_report(folder / name, manifest_path, assessments,
                                           pointer['generation'], pointer['manifest_sha256'])

    def test_boundary_keeps_detail_and_above_uses_complete_summary(self):
        pointer, manifest_path, folder = self.prepared()
        with verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            count = max(len(value['diagnostics']) for value in assessments.values())
            self.assertGreater(count, 0)
            with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', count):
                detailed = self.publish('detail.html', pointer, manifest_path, folder, assessments)
            with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', count - 1):
                summary = self.publish('summary.html', pointer, manifest_path, folder, assessments)
        self.assertTrue(detailed['details'])
        self.assertEqual(summary['details'], [])
        html = Path(summary['path']).read_text()
        for profile in report.PROFILES:
            self.assertIn('validation_' + profile + '.json', html)
        self.assertIn('Complete contract tables', html)

    def test_beyond_assessment_allowance_fails_without_output(self):
        pointer, manifest_path, folder = self.prepared()
        with verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            count = max(len(value['diagnostics']) for value in assessments.values())
            with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', count - 1), \
                 patch.object(report, 'MAX_ASSESSMENT_ROWS', count - 1):
                with self.assertRaisesRegex(ValueError, 'diagnostic row bound'):
                    self.publish('over-limit.html', pointer, manifest_path, folder, assessments)
        self.assertFalse((folder / 'over-limit.html').exists())

    def test_unknown_diagnostic_entity_above_detail_limit_fails(self):
        pointer, manifest_path, folder = self.prepared()
        with verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            wrong = copy.deepcopy(assessments)
            row = dict(wrong['source']['diagnostics'][0])
            row['entity_key'] = 'unknown-entity'
            wrong['source']['diagnostics'].append(row)
            with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', 0):
                with self.assertRaisesRegex(ValueError, 'unknown entity'):
                    self.publish('unknown.html', pointer, manifest_path, folder, wrong)
        self.assertFalse((folder / 'unknown.html').exists())

    def test_broken_linked_member_hash_and_count_fail(self):
        pointer, manifest_path, folder = self.prepared()
        with verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            linked = folder / 'validation_source.json'
            linked.unlink()
            write_sharded_assessment(linked, assessments['source'])
            with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', 0):
                valid = self.publish('valid-shard.html', pointer, manifest_path, folder, assessments)
            self.assertEqual(valid['details'], [])
            envelope = json.loads(linked.read_text())
            member = next(part for field in envelope['list_order']
                          for part in envelope['lists'][field]['members'])
            member_path = folder / member['path']
            original = member_path.read_bytes()
            member_path.write_bytes(original + b' ')
            try:
                with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', 0):
                    with self.assertRaisesRegex(ValueError, 'mismatch|invalid|bound'):
                        self.publish('broken-hash.html', pointer, manifest_path, folder, assessments)
            finally:
                member_path.write_bytes(original)
            envelope['lists'][next(field for field in envelope['list_order']
                                   if member in envelope['lists'][field]['members'])]['members'][0]['rows'] += 1
            linked.write_text(json.dumps(envelope), encoding='utf-8')
            with patch.object(report, 'MAX_DETAIL_DIAGNOSTICS', 0):
                with self.assertRaisesRegex(ValueError, 'mismatch|invalid|bound'):
                    self.publish('broken-count.html', pointer, manifest_path, folder, assessments)
        self.assertFalse((folder / 'broken-hash.html').exists())
        self.assertFalse((folder / 'broken-count.html').exists())


if __name__ == '__main__':
    unittest.main()
