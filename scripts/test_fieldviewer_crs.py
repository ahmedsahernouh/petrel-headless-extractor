"""Focused file-boundary and receipt tests for the CRS-only adapter."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import fieldviewer_crs as crs


def metadata(state='stored_project_declaration', *, complete=True, status='parsed'):
    projection = (dict(status='parsed_stored_declaration', declaration_state=state,
                       raw='ESRI: <ILateBoundCoordinateReferenceSystem name="<bad>">'
                           '<AuthorityCode>EPSG,9</AuthorityCode>'
                           '</ILateBoundCoordinateReferenceSystem>',
                       late_bound=dict(name='<bad>', authority='EPSG,9'))
                  if state == 'stored_project_declaration' else
                  dict(status='unresolved_project_reference', declaration_state=state,
                       raw='00000000-0000-0000-0000-000000000000'))
    return dict(schema='petrel.model-crs-datum-declarations/1', status=status,
                saved_version='2024.5', project_records=[dict(projection=projection)],
                datum_records=[dict(datum=0)], well_reference_records=[], gaps=[],
                counts=dict(ProjectSubject_seen=1, stream_complete=complete))


class CrsExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.pet = self.source / 'sample.pet'
        self.pet.write_bytes(b'pet')
        self.store = self.source / 'sample.ptd'
        self.store.mkdir()
        (self.store / 'Model.ptd').write_bytes(b'model')
        (self.store / 'Data.ptd').write_bytes(b'do not touch')
        self.out = self.base / 'out'

    def test_typed_export_artifacts_and_no_data_access(self):
        actual_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path.name == 'Data.ptd':
                raise AssertionError('Data.ptd accessed')
            return actual_open(path, *args, **kwargs)
        with patch.object(crs.parser, 'inspect_model', return_value=metadata()), \
                patch.object(Path, 'open', guarded_open):
            receipt = crs.export(self.pet, self.out)
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(receipt['source_integrity_unchanged'])
        self.assertFalse(receipt['data_store_read'])
        self.assertFalse(receipt['physical_crs_verified'])
        self.assertEqual(json.loads((self.out / 'CRS_DATUM.json').read_text())['datum_records'][0]['datum'], 0)
        self.assertIn('&lt;bad&gt;', (self.out / 'CRS_REPORT.html').read_text())
        self.assertNotIn('<bad>', (self.out / 'CRS_REPORT.html').read_text())
        self.assertTrue((self.out / 'CRS_DECLARATION.xml').exists())
        manifest = json.loads((self.out / 'FILE_MANIFEST.json').read_text())
        paths = {Path(row['path']).name for row in manifest['artifacts']}
        self.assertIn('CRS_REPORT.html', paths)
        self.assertIn('CRS_DATUM.json', paths)
        self.assertNotIn('CRS_EXPORT.json', paths)
        self.assertNotIn('FILE_MANIFEST.json', paths)
        self.assertEqual([json.loads(line)['number'] for line in
                          (self.out / 'EVENTS.jsonl').read_text().splitlines()], list(range(1, 7)))
        self.assertTrue(all(json.loads(line)['event'] == 'stage' for line in
                            (self.out / 'EVENTS.jsonl').read_text().splitlines()))
        self.assertEqual(len(receipt['parser_code']), 4)
        self.assertTrue(all(row['sha256'] for row in receipt['parser_code']))

    def test_null_partial_and_ambiguous_are_gaps(self):
        for index, data in enumerate((metadata('serialized_null_reference'),
                                      metadata(complete=False),
                                      dict(metadata(), counts=dict(ProjectSubject_seen=2,
                                                                   stream_complete=True)))):
            with self.subTest(index=index), patch.object(crs.parser, 'inspect_model', return_value=data):
                receipt = crs.export(self.pet, self.base / f'out{index}')
            self.assertEqual(receipt['status'], 'completed_with_gaps')

    def test_missing_model_gap_and_invalid_pair_failure(self):
        (self.store / 'Model.ptd').unlink()
        receipt = crs.export(self.pet, self.out)
        self.assertEqual(receipt['status'], 'completed_with_gaps')
        self.assertTrue(receipt['source_integrity_unchanged'])
        self.assertIn('Model.ptd missing', receipt['gaps'][0]['reason'])
        (self.store / 'Data.ptd').unlink()
        self.store.rmdir()
        bad = crs.export(self.pet, self.base / 'bad')
        self.assertEqual(bad['status'], 'failed')
        self.assertEqual(bad['error']['type'], 'ValueError')
        self.assertTrue((self.base / 'bad' / 'CRS_DATUM.json').exists())
        self.assertTrue((self.base / 'bad' / 'FILE_MANIFEST.json').exists())
        self.assertIn('Matching same-stem', (self.base / 'bad' / 'CRS_REPORT.html').read_text())
        self.assertIn('Model.ptd missing', (self.out / 'CRS_REPORT.html').read_text())

    def test_source_change_is_failure(self):
        real_hash = crs._hash
        def changed(path):
            if Path(path) == self.pet:
                return '0' * 64
            return real_hash(path)
        with patch.object(crs.parser, 'inspect_model', return_value=metadata()), \
                patch.object(crs, '_hash', side_effect=changed):
            receipt = crs.export(self.pet, self.out)
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['source_integrity_unchanged'])
        self.assertIn('Source changed', receipt['error']['message'])

    def test_output_boundary_and_cli_exit_codes(self):
        with self.assertRaises(ValueError):
            crs.export(self.pet, self.source / 'nested')
        self.assertFalse((self.source / 'nested').exists())
        with patch.object(crs.parser, 'inspect_model', return_value=metadata()):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = crs.main(['--project', str(self.pet), '--output', str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'completed')
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = crs.main(['--project', str(self.pet), '--output', str(self.out)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'failed')
        (self.store / 'Model.ptd').unlink()
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = crs.main(['--project', str(self.pet), '--output', str(self.base / 'gap')])
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'completed_with_gaps')

    def test_utf16_xml_declaration_roundtrips(self):
        xml = ('<?xml version="1.0" encoding="utf-16"?>'
               '<ILateBoundCoordinateReferenceSystem name="Åin el Abd">'
               '<AuthorityCode>EPSG,20439</AuthorityCode>'
               '</ILateBoundCoordinateReferenceSystem>')
        result = metadata()
        result['project_records'][0]['projection']['raw'] = 'ESRI: ' + xml
        with patch.object(crs.parser, 'inspect_model', return_value=result):
            receipt = crs.export(self.pet, self.out)
        self.assertEqual(receipt['status'], 'completed')
        data = (self.out / 'CRS_DECLARATION.xml').read_bytes()
        self.assertTrue(data.startswith((b'\xff\xfe', b'\xfe\xff')))
        self.assertEqual(ET.parse(self.out / 'CRS_DECLARATION.xml').getroot().attrib['name'],
                         'Åin el Abd')
        self.assertEqual(json.loads((self.out / 'CRS_DATUM.json').read_text(encoding='utf-8'))
                         ['project_records'][0]['projection']['raw'], 'ESRI: ' + xml)

    def test_changed_code_or_foreign_module_is_failed_with_metadata(self):
        original = crs._bound_code
        baseline = original()
        changed = [dict(row) for row in baseline]
        changed[1]['sha256'] = '0' * 64
        with patch.object(crs.parser, 'inspect_model', return_value=metadata()), \
                patch.object(crs, '_bound_code', side_effect=[baseline, changed]):
            receipt = crs.export(self.pet, self.out)
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('Parser code', receipt['error']['message'])
        self.assertTrue((self.out / 'CRS_DATUM.json').exists())
        with patch.object(crs.parser, '__file__', str(self.base / 'foreign_parser.py')):
            foreign = crs.export(self.pet, self.base / 'foreign')
        self.assertEqual(foreign['status'], 'failed')
        self.assertIn('outside entrypoint', foreign['error']['message'])
        self.assertTrue((self.base / 'foreign' / 'CRS_DATUM.json').exists())

    def test_symlink_source_rejected_when_supported(self):
        link = self.source / 'link.pet'
        try:
            os.symlink(self.pet, link)
        except OSError as exc:
            self.skipTest('Windows symlink creation unavailable: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'link or reparse'):
            crs.export(link, self.out)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
