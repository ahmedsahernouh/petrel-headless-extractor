"""Synthetic forward tests for the drive/folder CRS audit runner."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fieldviewer_crs as exporter
import fieldviewer_crs_batch as batch


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.campaign = self.base / 'campaign'

    def pair(self, stem, *, model=False, folder=None):
        folder = folder or self.source
        folder.mkdir(parents=True, exist_ok=True)
        pet = folder / (stem + '.pet')
        pet.write_bytes(b'synthetic PET ' + stem.encode())
        store = folder / (stem + '.ptd')
        store.mkdir()
        if model:
            (store / 'Model.ptd').write_bytes(b'synthetic Model')
        (store / 'Data.ptd').write_bytes(b'untouched native payload')
        return pet

    def rows(self):
        with (self.campaign / 'RESULTS.csv').open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    def test_discovery_pairs_gaps_orphans_and_links(self):
        valid = self.pair('valid')
        missing = self.source / 'missing.pet'
        missing.write_bytes(b'synthetic PET')
        (self.source / 'orphan.ptd').mkdir()
        nested = self.pair('nested', folder=self.source / 'nested')
        linked = self.source / 'linked'
        try:
            os.symlink(nested.parent, linked, target_is_directory=True)
        except OSError:
            linked = None
        summary = batch.discover(self.source, self.campaign)
        plan = json.loads((self.campaign / 'DISCOVERY.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['projects'], 3)
        self.assertEqual({Path(e['project']): e['status'] for e in plan['entries']},
                         {valid: 'pending', missing: 'missing_pair', nested: 'pending'})
        issues = {e['status'] for e in plan['discovery_issues']}
        self.assertIn('orphan_store', issues)
        if linked is not None:
            self.assertIn('skipped_link_or_reparse', issues)
            self.assertFalse(any('linked' in e['project'] for e in plan['entries']))
        self.assertEqual(len(self.rows()), 3)

    def test_real_child_missing_model_resume_and_source_preservation(self):
        pet = self.pair('no_model')
        before = {p: p.read_bytes() for p in (pet, pet.with_suffix('.ptd') / 'Data.ptd')}
        batch.discover(self.source, self.campaign)
        result = batch.run(self.campaign)
        self.assertEqual(result['status_counts'], {'completed_with_gaps': 1})
        row = self.rows()[0]
        self.assertEqual(row['status'], 'completed_with_gaps')
        self.assertEqual(row['source_unchanged'], 'True')
        directory = self.campaign / 'projects' / batch.identity(pet)
        receipt = directory / 'CRS' / 'CRS_EXPORT.json'
        self.assertIn('Model.ptd missing', receipt.read_text(encoding='utf-8'))
        self.assertEqual(batch.main(['status', '--campaign', str(self.campaign)]), 10)
        with patch.object(batch.subprocess, 'run', side_effect=AssertionError('child repeated')):
            self.assertEqual(batch.run(self.campaign)['status_counts'], {'completed_with_gaps': 1})
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_resume_rejects_changed_receipt_source_and_code(self):
        pet = self.pair('bound')
        batch.discover(self.source, self.campaign)
        batch.run(self.campaign)
        directory = self.campaign / 'projects' / batch.identity(pet)
        receipt = directory / 'CRS' / 'CRS_EXPORT.json'
        original = receipt.read_bytes()
        receipt.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'receipt changed'):
            batch.run(self.campaign)
        receipt.write_bytes(original)
        pet.write_bytes(b'changed PET')
        with self.assertRaisesRegex(ValueError, 'Source metadata changed'):
            batch.run(self.campaign)
        pet.write_bytes(b'synthetic PET bound')
        with patch.object(batch, 'code_binding', return_value={}):
            with self.assertRaisesRegex(ValueError, 'tool code changed'):
                batch.run(self.campaign)
        self.assertFalse((self.campaign / 'RUNNING.lock').exists())

    def test_incomplete_folder_and_lock_block_execution(self):
        pet = self.pair('pending')
        batch.discover(self.source, self.campaign)
        directory = self.campaign / 'projects' / batch.identity(pet)
        directory.mkdir()
        with self.assertRaisesRegex(ValueError, 'Incomplete prior project'):
            batch.run(self.campaign)
        self.assertFalse((self.campaign / 'RUNNING.lock').exists())
        (self.campaign / 'RUNNING.lock').write_text('retained', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            batch.run(self.campaign)
        self.assertEqual((self.campaign / 'RUNNING.lock').read_text(encoding='utf-8'), 'retained')

    def test_csv_retains_multiple_declarations_and_every_project(self):
        pet = self.pair('multi', model=True)
        absent = self.source / 'absent.pet'
        absent.write_bytes(b'synthetic PET')
        metadata = dict(schema='petrel.model-crs-datum-declarations/1', status='parsed',
                        saved_version='2024.5', counts=dict(ProjectSubject_seen=2, stream_complete=True),
                        project_records=[
                            dict(projection=dict(declaration_state='stored_project_declaration',
                                                 status='parsed_stored_declaration',
                                                 late_bound=dict(name='First', authority='EPSG,1')),
                                 units_style=dict(is_used=False, reference_datum=0)),
                            dict(projection=dict(declaration_state='stored_project_declaration',
                                                 status='parsed_stored_declaration',
                                                 late_bound=dict(name='Second', authority='EPSG,2')),
                                 units_style=dict(is_used=True, reference_datum=2))],
                        datum_records=[dict(datum=0), dict(datum=25)],
                        well_reference_records=[], gaps=[])
        batch.discover(self.source, self.campaign)

        def controlled_child(args, *, stdout, stderr):
            self.assertEqual(Path(args[2]), Path(exporter.__file__))
            self.assertEqual(args[3:5], ['--project', str(pet)])
            with patch.object(exporter.parser, 'inspect_model', return_value=metadata):
                receipt = exporter.export(Path(args[4]), Path(args[6]))
            stdout.write(json.dumps(receipt))
            return type('Exit', (), {'returncode': 10})()

        with patch.object(batch.subprocess, 'run', side_effect=controlled_child) as child:
            result = batch.run(self.campaign)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(result['projects'], 2)
        self.assertEqual(result['status_counts'], {'completed_with_gaps': 1, 'missing_pair': 1})
        rows = {Path(r['project']): r for r in self.rows()}
        self.assertEqual(set(rows), {pet, absent})
        row = rows[pet]
        self.assertEqual(json.loads(row['crs_names']), ['First', 'Second'])
        self.assertEqual(json.loads(row['crs_authorities']), ['EPSG,1', 'EPSG,2'])
        self.assertEqual(json.loads(row['datum_definitions']), [{'datum': 0}, {'datum': 25}])
        self.assertEqual(json.loads(row['project_units_and_datum_tags']),
                         [{'is_used': False, 'reference_datum': 0},
                          {'is_used': True, 'reference_datum': 2}])


if __name__ == '__main__':
    unittest.main()
