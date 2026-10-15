"""Report aggregate boundary and integrity controls for the current release source."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_preparation_report as report
import validate_geoviewer_contract as validator
from geoviewer_contract_publication import publish_preparation
from test_geoviewer_contract import ContractTests


class ReportBudgetRepairTests(unittest.TestCase):
    setUp = ContractTests.setUp
    add_row = ContractTests.add_row

    def prepared(self):
        publish_preparation(self.package, self.exports, self.run, {},
                            file_index=self.index, conversion_enabled=True)
        pointer = json.loads((self.exports / 'INTEROPERABILITY_CURRENT.json').read_text())
        manifest_path = self.exports / pointer['manifest']
        manifest = json.loads(manifest_path.read_text())
        folder = self.run / 'interoperability' / pointer['generation']
        sizes = [((manifest_path.parent / manifest['tables'][name]['path']).stat().st_size)
                 for name in ('entities', 'artifacts', 'relationships', 'metadata_evidence', 'gaps')]
        return pointer, manifest_path, folder, sum(sizes)

    def publish(self, path, pointer, manifest_path, assessments):
        return report.publish_human_report(path, manifest_path, assessments,
                                           pointer['generation'], pointer['manifest_sha256'])

    def test_exact_aggregate_boundary_uses_detail_and_one_less_uses_verified_summary(self):
        pointer, manifest_path, folder, total = self.prepared()
        with validator.verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            with patch.object(report, 'MAX_TABLE_BYTES', total):
                detail = self.publish(folder / 'EXACT.html', pointer, manifest_path, assessments)
            self.assertGreater(len(detail['details']), 0)
            with patch.object(report, 'MAX_TABLE_BYTES', total - 1):
                summary = self.publish(folder / 'OVER.html', pointer, manifest_path, assessments)
        html = Path(summary['path']).read_text()
        self.assertEqual(summary['details'], [])
        self.assertIn('bounded summary', html)
        self.assertIn('metadata_evidence.jsonl', html)
        self.assertIn('Complete source assessment JSON', html)
        self.assertEqual(hashlib.sha256(Path(summary['path']).read_bytes()).hexdigest(), summary['sha256'])

    def test_corrupted_table_fails_summary_without_publishing(self):
        pointer, manifest_path, folder, total = self.prepared()
        target = folder / 'CORRUPT.html'
        with validator.verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            table = manifest_path.parent / 'metadata_evidence.jsonl'
            with table.open('r+b') as stream:
                first = stream.read(1)
                stream.seek(0)
                stream.write(b' ' if first != b' ' else b'X')
            try:
                with patch.object(report, 'MAX_TABLE_BYTES', total - 1):
                    with self.assertRaises((ValueError, json.JSONDecodeError)):
                        self.publish(target, pointer, manifest_path, assessments)
            finally:
                with table.open('r+b') as stream:
                    stream.write(first)
        self.assertFalse(target.exists())

    def test_wrong_generation_and_reader_row_bounds_still_fail(self):
        pointer, manifest_path, folder, total = self.prepared()
        with validator.verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            with self.assertRaisesRegex(ValueError, 'generation mismatch'):
                report.publish_human_report(folder / 'WRONG.html', manifest_path, assessments,
                                            'wrong-generation', pointer['manifest_sha256'])
            with patch.object(report, 'MAX_TABLE_BYTES', total - 1), \
                 patch.object(validator, 'MAX_TABLE_ROWS', 1):
                with self.assertRaisesRegex(ValueError, 'row bound'):
                    self.publish(folder / 'ROWS.html', pointer, manifest_path, assessments)
            with patch.object(report, 'MAX_TABLE_BYTES', total - 1), \
                 patch.object(validator, 'MAX_ROW_BYTES', 1):
                with self.assertRaisesRegex(ValueError, 'byte/row bounds'):
                    self.publish(folder / 'ROW_BYTES.html', pointer, manifest_path, assessments)
        for name in ('WRONG.html', 'ROWS.html', 'ROW_BYTES.html'):
            self.assertFalse((folder / name).exists())

    def test_summary_rejects_invalid_ownership_and_cross_entity_reference(self):
        pointer, manifest_path, folder, total = self.prepared()
        manifest = json.loads(manifest_path.read_text())
        tables = report._tables(manifest_path, manifest)
        identities = {name: report._index(tables[name], key)
                      for name, key in (('entities', 'entity_key'), ('artifacts', 'artifact_key'))}
        with validator.verified_profile_assessments(self.exports / 'INTEROPERABILITY_CURRENT.json') as assessments:
            evaluated = {name: report._index(value['assessments'], 'entity_key')
                         for name, value in assessments.items()}
            bad = copy.deepcopy(identities)
            next(iter(bad['artifacts'].values()))['owner_keys'] = ['absent-entity']
            with self.assertRaisesRegex(ValueError, 'invalid owners'):
                report._summary_reference_checks(manifest_path, manifest, bad, evaluated, assessments)

            relation = manifest_path.parent / manifest['tables']['relationships']['path']
            original = relation.read_bytes()
            rows = [json.loads(line) for line in original.splitlines()]
            self.assertTrue(rows)
            rows[0]['target_key'] = 'absent-entity'
            altered = b''.join((json.dumps(row, sort_keys=True, separators=(',', ':'),
                                          ensure_ascii=False) + '\n').encode() for row in rows)
            changed_manifest = copy.deepcopy(manifest)
            changed_manifest['tables']['relationships']['sha256'] = hashlib.sha256(altered).hexdigest()
            try:
                relation.write_bytes(altered)
                with self.assertRaisesRegex(ValueError, 'unknown entity reference'):
                    report._summary_reference_checks(manifest_path, changed_manifest,
                                                     identities, evaluated, assessments)
            finally:
                relation.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
