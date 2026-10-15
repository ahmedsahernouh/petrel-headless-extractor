"""Exact user-declared relocation maps never change native inputs or guess linkage."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import petrel_project_seismic as s


class ReferenceMapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'example.pet';self.project.write_bytes(b'original-project')
        self.replacement=self.root/'relocated.sgy';self.replacement.write_bytes(b'example-segy')
        self.original='S:\\old\\example.segy'
        self.inventory={'project_file':str(self.project),'objects':[
            dict(id='native',source=self.original,reference=self.original,format='.segy',association='native_model_external_reference',status='missing'),
            dict(id='nearby',source=str(self.replacement),reference='',format='.sgy',association='unlinked_companion',status='inventoried',file_state={'size_bytes':12})]}
        self.mapping={'schema':'fieldviewer.seismic-reference-map/1','project_file':str(self.project),
                      'references':[{'original_reference':self.original,'replacement':str(self.replacement)}]}
    def apply(self):
        path=self.root/'map.json';path.write_text(json.dumps(self.mapping))
        return s.apply_reference_map(self.inventory,path)
    def test_explicit_map_preserves_identity_and_sources(self):
        before=self.project.read_bytes();data=self.replacement.read_bytes();result=self.apply()
        self.assertEqual(len(result['objects']),1);row=result['objects'][0]
        self.assertEqual(row['id'],'native');self.assertEqual(row['reference'],self.original)
        self.assertEqual(row['association'],'user_declared_reference_mapping')
        self.assertIn('not established',row['mapping']['identity_status'])
        self.assertEqual(result['workload']['user_declared']['available_count'],1)
        self.assertEqual(result['workload']['proven_linked']['available_count'],0)
        self.assertEqual(before,self.project.read_bytes());self.assertEqual(data,self.replacement.read_bytes())
        self.assertEqual(row['mapping']['map_sha256'],hashlib.sha256((self.root/'map.json').read_bytes()).hexdigest())
    def test_invalid_maps_are_atomic(self):
        for field,value in (('project_file',str(self.root/'other.pet')),('schema','wrong')):
            with self.subTest(field=field):
                baseline=copy.deepcopy(self.inventory);self.mapping[field]=value
                with self.assertRaises(ValueError):self.apply()
                self.assertEqual(self.inventory,baseline)
                self.mapping[field]=str(self.project) if field=='project_file' else 'fieldviewer.seismic-reference-map/1'
    def test_no_fuzzy_reference_matching(self):
        self.mapping['references'][0]['original_reference']='example.segy'
        with self.assertRaisesRegex(ValueError,'exact native'):self.apply()
    def test_missing_or_different_format_target_rejected(self):
        self.mapping['references'][0]['replacement']=str(self.root/'absent.sgy')
        with self.assertRaises(FileNotFoundError):self.apply()
        target=self.root/'other.zgy';target.write_bytes(b'zgy')
        self.mapping['references'][0]['replacement']=str(target)
        with self.assertRaisesRegex(ValueError,'format family'):self.apply()
    def test_duplicate_reference_rejected(self):
        self.mapping['references'].append(dict(self.mapping['references'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate original'):self.apply()
    def test_relative_target_rejected(self):
        self.mapping['references'][0]['replacement']='relocated.sgy'
        with self.assertRaisesRegex(ValueError,'absolute'):self.apply()
    def test_duplicate_json_keys_rejected(self):
        path=self.root/'map.json';path.write_text('{"schema":"a","schema":"b"}')
        with self.assertRaisesRegex(ValueError,'Duplicate reference-map key'):s.apply_reference_map(self.inventory,path)

    def test_mapped_zgy_converts_and_report_retains_declaration(self):
        import test_petrel_binary_conversion as fixtures
        import shutil
        fixture=fixtures.BinaryConversionTests();fixture.setUp()
        self.addCleanup(shutil.rmtree,fixture.root)
        target=fixture.fixture(name='relocated')
        original='S:\\old\\cube.zgy'
        self.inventory['objects']=[dict(id='stable-native',name='Cube',source=original,reference=original,
            format='.zgy',association='native_model_external_reference',status='missing')]
        self.mapping['references']=[dict(original_reference=original,replacement=str(target))]
        before=hashlib.sha256(target.read_bytes()).hexdigest()
        inventory=self.apply()
        s.convert_project(inventory,fixture.outputs,True,full_hash=False)
        row=inventory['objects'][0]
        self.assertEqual(row['status'],'converted')
        self.assertEqual(row['id'],'stable-native')
        self.assertTrue(row['result']['summary']['all_decoded_samples_exact'])
        self.assertEqual(before,hashlib.sha256(target.read_bytes()).hexdigest())
        base=fixture.outputs/'base.html';base.write_text('<html><body><main>Report</main></body></html>')
        report=fixture.outputs/'report.html';s.deliver_report(base,report,inventory,True)
        html=report.read_text(encoding='utf-8')
        self.assertIn('original/replacement equivalence unverified',html)
        self.assertIn('user-declared replacements',html)
        self.assertIn(row['mapping']['map_sha256'],html)
        self.assertIn('Open SEG-Y',html)


if __name__=='__main__':unittest.main()
