"""Independent input-provenance regressions for the derived legacy assessment."""
from pathlib import Path
import hashlib
import unittest
from unittest.mock import patch
import geoviewer_legacy_contract as legacy

from geoviewer_legacy_contract import assess_legacy
from test_geoviewer_contract import ContractTests, write


class LegacyInputBindingsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ContractTests('test_multiple_representations_one_entity_and_roles')
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)

    def test_changed_head_input_changes_derived_fingerprint(self):
        f=self.fixture
        f.add_row('well_headers', 'well_headers/heads.csv', b'well_name\nOne\n', 'CSV')
        f.rows[-1]['category']='well_headers'
        write(f.exports/'FILE_INDEX.json', f.index)
        p=f.package/'02_wells/well_headers/native_well_heads.csv'
        p.parent.mkdir(parents=True)
        p.write_text('model_subject_offset,well_name\n100,One\n', encoding='utf-8')
        a=assess_legacy(f.package, f.exports)
        p.write_text('model_subject_offset,well_name\n100,Two\n', encoding='utf-8')
        b=assess_legacy(f.package, f.exports)
        self.assertNotEqual(a['normalized_catalogue']['entities'], b['normalized_catalogue']['entities'])
        self.assertNotEqual(a['derived_local_fingerprint'], b['derived_local_fingerprint'])

    def test_shared_assessment_dependencies_are_fingerprinted(self):
        f=self.fixture
        a=assess_legacy(f.package, f.exports)
        for module in ('geoviewer_receiving_profiles.py','geoviewer_contract_retention.py'):
            self.assertEqual(a['fingerprint_inputs']['adapter_script_sha256'].get(module),
                hashlib.sha256(Path(__file__).with_name(module).read_bytes()).hexdigest())

    def test_missing_receipt_becoming_oversize_is_not_still_missing(self):
        f=self.fixture
        missing=f.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
        original=legacy.assess_catalogue
        def changed(**kwargs):
            result=original(**kwargs)
            missing.parent.mkdir(parents=True,exist_ok=True)
            missing.write_bytes(b' '* (4*1024*1024+1))
            return result
        with patch.object(legacy,'MAX_RECEIPT_BYTES',4*1024*1024),patch.object(legacy,'assess_catalogue',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'receipt changed during legacy assessment'):
                assess_legacy(f.package,f.exports)

    def test_malformed_receipt_rows_are_not_silently_ignored(self):
        f=self.fixture
        p=f.package/'01_project_metadata/project_context.json'
        write(p,{'objects':['not an object record']})
        with self.assertRaisesRegex(ValueError,'Malformed project_context receipt object record'):
            assess_legacy(f.package,f.exports)
        write(p,f.context)
        write(f.package/'01_project_metadata/project_seismic_inventory.json',{'native_objects':[7]})
        with self.assertRaisesRegex(ValueError,'Malformed seismic inventory native object record'):
            assess_legacy(f.package,f.exports)


if __name__=='__main__':
    unittest.main()
