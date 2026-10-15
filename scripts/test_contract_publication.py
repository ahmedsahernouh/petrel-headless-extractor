# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Publication matrix through the real finalization path, with synthetic inputs."""
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from geoviewer_contract import POINTER
from geoviewer_contract_publication import publish_preparation, verify_publication_binding
from validate_geoviewer_contract import validate_contract
import test_geoviewer_contract as fixtures


class ContractPublicationTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    add_row = fixtures.ContractTests.add_row
    table = fixtures.ContractTests.table

    def publish(self, **kwargs):
        return publish_preparation(self.package, self.exports, self.run, {'objects': []},
                                   file_index=self.index, **kwargs)

    def test_converted_empty_and_partial_indices_bind_exact_assessments(self):
        for mode in ('converted', 'empty', 'partial'):
            with self.subTest(mode=mode):
                if mode == 'empty':
                    self.index['files'] = []
                elif mode == 'partial':
                    self.index.update(status='partial', files=self.rows[:1],
                                      partial_files=[dict(self.rows[1], status='checkpoint_only_partial')])
                fixtures.write(self.exports/'FILE_INDEX.json', self.index)
                bundle = self.publish()
                verified = verify_publication_binding(bundle, self.run)
                self.assertEqual(verified['assessments'], 3)
                self.assertEqual(bundle['assessments']['source']['counts']['artifacts'],
                                 len(self.index['files'])+len(self.index['partial_files']))
                for name, row in bundle['assessments'].items():
                    report = json.loads(Path(row['path']).read_text())
                    self.assertEqual(report['generation'], bundle['generation'])
                    self.assertEqual(report['manifest_sha256'], bundle['manifest_sha256'])
                    self.assertEqual(report['profile'], name)

    def test_inventory_only_keeps_native_inventory_without_artifacts(self):
        (self.exports/'FILE_INDEX.json').unlink()
        before = {p.relative_to(self.package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.package.rglob('*') if p.is_file()}
        bundle = publish_preparation(self.package, self.exports, self.run, {'objects':[]}, conversion_enabled=False)
        report = validate_contract(self.exports/POINTER)
        self.assertEqual(report['counts']['entities'], 2)
        self.assertEqual(report['counts']['artifacts'], 0)
        self.assertEqual(report['counts']['source_ready'], 0)
        self.assertEqual(report['counts']['receiving_imports_executed'], 0)
        self.assertFalse(bundle['dataset_conversion_enabled'])
        self.assertTrue(all(a['state_axes']['original_run_publication']=='inventory_only' for a in report['assessments']))
        self.assertEqual(before, {p.relative_to(self.package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in self.package.rglob('*') if p.is_file()})

    def test_inventory_mode_refuses_replacing_existing_payload_index(self):
        before = (self.exports/'FILE_INDEX.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'cannot replace'):
            publish_preparation(self.package,self.exports,self.run,{},conversion_enabled=False)
        self.assertEqual((self.exports/'FILE_INDEX.json').read_bytes(), before)

    def test_missing_failed_conversion_index_not_promoted_to_empty_success(self):
        with self.assertRaisesRegex(ValueError, 'requires a committed index'):
            publish_preparation(self.package,self.exports,self.run,{},conversion_enabled=True)
        self.assertFalse((self.exports/POINTER).exists())

    def test_changed_and_stale_assessments_block_binding(self):
        old = self.publish()
        report = Path(old['assessments']['source']['path'])
        raw = report.read_bytes(); report.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError, 'Assessment binding failed'):
            verify_publication_binding(old,self.run)
        report.write_bytes(raw)
        new = self.publish()
        self.assertNotEqual(old['generation'],new['generation'])
        with self.assertRaisesRegex(ValueError,'Stale publication'):
            verify_publication_binding(old,self.run)
        changed = copy.deepcopy(new); changed['assessments']['source']['counts']['entities'] += 1
        with self.assertRaisesRegex(ValueError,'Assessment binding failed'):
            verify_publication_binding(changed,self.run)

    def test_binding_relocation_ignores_absolute_provenance_and_rejects_escape(self):
        bundle=self.publish()
        moved=Path(self.temp.name)/'relocated'; shutil.move(str(self.run),moved)
        self.assertEqual(verify_publication_binding(bundle,moved)['status'],'verified')
        changed=copy.deepcopy(bundle); changed['assessments']['source']['relative_path']='../outside.json'
        with self.assertRaises(ValueError): verify_publication_binding(changed,moved)

    def test_assessment_failure_does_not_return_successful_publication(self):
        with patch('validate_geoviewer_contract.assess_catalogue',side_effect=ValueError('Injected failed assessment')):
            with self.assertRaisesRegex(ValueError,'Injected failed assessment'): self.publish()
        # Source catalogue remains diagnosable, but there is no returned bound bundle.
        self.assertTrue((self.exports/POINTER).is_file())

    def test_report_only_launcher_publishes_catalogue_and_result_binding(self):
        import standalone_petrel_extract as launcher
        import geoviewer_metadata as metadata
        source=Path(self.temp.name)/'original'/'fixture.pet'; source.parent.mkdir()
        source.write_bytes(b'original fixture'); source.with_suffix('.ptd').mkdir()
        output=Path(self.temp.name)/'launcher-output'
        context={'project_name':'Unrecognized fixture','objects':[{'object_id':'unknown-object','name':'Unsupported object','category':'UnsupportedSubject'}],
                 'layout':'fixture','saved_version':{},'findings':[]}
        def dispatch(operation,args):
            if operation=='extract_portable_project':
                package=Path(args['output_dir'])/'package'; package.mkdir(parents=True)
                fixtures.write(package/'01_project_metadata/project_context.json',context)
                (package/'PROJECT_REPORT.html').write_text('<html><head></head><body><main>Inventory</main></body></html>')
                return {'summary':{'export_package':str(package)}}
            return {'report_path':'fixture-qc.json'}
        with patch.object(sys,'argv',['launcher','--project-file',str(source),'--output-root',str(output),'--report-only']), \
             patch.object(launcher,'preflight',return_value={'version':'test'}), \
             patch.object(metadata,'inspect_project',return_value=context), \
             patch.object(launcher.g,'dispatch',side_effect=dispatch), \
             patch.object(launcher.g,'verify_receipt',return_value={'status':'passed'}),redirect_stdout(io.StringIO()):
            code=launcher.main()
        self.assertEqual(code,0)
        result_path=next(output.glob('*_data/RUN_RESULT.json'))
        result=json.loads(result_path.read_text())
        self.assertFalse(result['dataset_conversion_enabled'])
        self.assertIsNone(result['file_index'])
        bundle=result['interoperability_contract']
        self.assertEqual(bundle['assessments']['source']['counts']['entities'],1)
        self.assertEqual(bundle['assessments']['source']['counts']['artifacts'],0)
        self.assertEqual(verify_publication_binding(bundle,result_path.parent)['status'],'verified')
        self.assertIn('Interoperability catalogue',Path(result['full_report']).read_text(encoding='utf-8'))
        self.assertIn('Search preparation evidence and blockers',Path(result['full_report']).read_text(encoding='utf-8'))
        self.assertEqual(bundle['human_report']['entities'],1)
        self.assertTrue(Path(bundle['human_report']['path']).is_file())
        self.assertEqual(source.read_bytes(),b'original fixture')


if __name__=='__main__': unittest.main()
