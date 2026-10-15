# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# https://saherlabs.dev/ | https://github.com/ahmedsahernouh/petrel-headless-extractor
"""Receipt path reuse preserves fresh artifact and input integrity checks."""
from collections import Counter
import csv
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import petrel_geoscience_tools as tools


class ReceiptIoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='FV Receipt ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source.txt'; self.source.write_text('original')
        self.out=self.root/'output'
        run=tools.Run('receipt_fixture',{'output_dir':str(self.out)},[self.source])
        (self.out/'samples.csv').write_text('md,value\n1,2\n')
        self.payload=run.finish({'fixture':True},{'rows':1})

    def rewrite_manifest(self, modify):
        manifest=self.out/'artifact_manifest.json'; receipt_path=self.out/'receipt.json'
        records=tools.read_json(manifest);modify(records);tools.write_json(manifest,records)
        receipt=tools.read_json(receipt_path);receipt['artifact_manifest_sha256']=tools.sha256(manifest)
        tools.write_json(receipt_path,receipt)

    def test_one_path_validation_per_artifact_and_all_fresh_hashes(self):
        hashes=Counter(); actual=tools.sha256
        def count(path):
            hashes[Path(path).name]+=1
            return actual(path)
        with patch.object(tools,'contained_file',wraps=tools.contained_file) as contained, \
             patch.object(tools,'sha256',side_effect=count):
            result=tools.verify_receipt(self.payload)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['artifacts_checked'],2)
        self.assertEqual(result['inputs_checked'],1)
        self.assertEqual(contained.call_count,2)
        self.assertTrue(all(c.kwargs['resolved_directory']==self.out.resolve() for c in contained.call_args_list))
        self.assertEqual(hashes,Counter({'artifact_manifest.json':1,'result.json':1,'samples.csv':1,'source.txt':1}))

    def test_tampered_artifact_is_rejected(self):
        (self.out/'samples.csv').write_text('md,value\n1,3\n')
        self.assertIn('Artifact integrity failed',tools.verify_receipt(self.payload)['reason'])

    def test_changed_input_is_rejected(self):
        self.source.write_text('changed')
        self.assertIn('Input has changed',tools.verify_receipt(self.payload)['reason'])

    def test_wrong_size_is_rejected_even_with_correct_hash(self):
        self.rewrite_manifest(lambda rows:rows[0].update(bytes=999))
        self.assertIn('Artifact integrity failed',tools.verify_receipt(self.payload)['reason'])

    def test_missing_artifact_is_rejected(self):
        (self.out/'samples.csv').unlink()
        self.assertIn('escapes package or is missing',tools.verify_receipt(self.payload)['reason'])

    def test_traversal_is_rejected_even_with_matching_manifest_hash(self):
        self.rewrite_manifest(lambda rows:rows.append({'path':'../source.txt','sha256':tools.sha256(self.source),'bytes':self.source.stat().st_size}))
        self.assertIn('escapes package or is missing',tools.verify_receipt(self.payload)['reason'])

    def test_payload_receipt_mismatch_is_rejected(self):
        payload=dict(self.payload,summary={'rows':999})
        self.assertIn('Payload differs from receipt',tools.verify_receipt(payload)['reason'])

    def test_artifact_manifest_tampering_is_rejected(self):
        with (self.out/'artifact_manifest.json').open('a') as f:f.write(' ')
        self.assertIn('Artifact manifest changed',tools.verify_receipt(self.payload)['reason'])

    def test_pipeline_package_paths_checked_once_and_hash_failure_retained(self):
        # Exercise the parent stage-9 boundary with a synthetic child package;
        # no native parser, PowerShell pipeline or Petrel is launched.
        for bad in (False,True):
            with self.subTest(wrong_hash=bad):
                source_dir=self.root/('input_'+str(bad));source_dir.mkdir()
                project=source_dir/'fixture.pet';project.write_text('synthetic')
                project.with_suffix('.ptd').mkdir()
                output=self.root/('pipeline_'+str(bad))
                def child(*args,**kwargs):
                    package=output/'package/fixture';package.mkdir(parents=True)
                    sample=package/'sample.csv';sample.write_text('x,y\n1,2\n')
                    (package/'00_manifest').mkdir()
                    with (package/'00_manifest/export_manifest.csv').open('w',newline='') as f:
                        w=csv.DictWriter(f,fieldnames=['export_file','sha256']);w.writeheader()
                        w.writerow({'export_file':'sample.csv','sha256':'0'*64 if bad else tools.sha256(sample)})
                    summary_path=package/'07_workflows_reports/portable_extractor/portable_extraction_run_summary.json'
                    summary_path.parent.mkdir(parents=True)
                    tools.write_json(summary_path,{'validation_status':'passed'})
                    return 0
                with patch.object(tools.progress,'run_pipeline',side_effect=child), \
                     patch.object(tools,'contained_file',wraps=tools.contained_file) as contained:
                    args={'project_file':str(project),'output_dir':str(output)}
                    if bad:
                        with self.assertRaisesRegex(RuntimeError,'Extracted package hash mismatch'):
                            tools.extract_portable_project(args)
                    else:
                        self.assertEqual(tools.extract_portable_project(args)['status'],'passed')
                    self.assertEqual(contained.call_count,1)

    @unittest.skipUnless(os.name=='nt','Windows junction test')
    def test_junction_outside_package_is_rejected(self):
        external=self.root/'external';external.mkdir();(external/'x.txt').write_text('external')
        link=self.out/'link'
        created=subprocess.run(['cmd','/d','/c','mklink','/J',str(link),str(external)],capture_output=True,text=True)
        if created.returncode:self.skipTest('Cannot create test junction')
        try:
            self.rewrite_manifest(lambda rows:rows.append({'path':'link/x.txt','sha256':tools.sha256(external/'x.txt'),'bytes':8}))
            self.assertIn('escapes package or is missing',tools.verify_receipt(self.payload)['reason'])
        finally:
            # Remove only the verified junction entry, never recurse into its target.
            self.assertTrue(link.is_junction());link.rmdir()


if __name__=='__main__':unittest.main()
