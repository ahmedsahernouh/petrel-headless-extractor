# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Missing Office-lock policy must remain explicit through final package QC."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import petrel_geoscience_tools as g


class TransientManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.package=self.root/'package'
        (self.package/'00_manifest').mkdir(parents=True)
        (self.package/'01_project_metadata').mkdir()
        (self.package/'01_project_metadata/project_summary.json').write_text('{}')
        self.native=self.package/'08_native_project/Data.ptd'
        self.native.parent.mkdir(); self.native.write_bytes(b'native fixture')
        self.rows=[dict(source_object_path='native',source_object_type='petrel_native_store',
            export_format='binary',export_file='08_native_project/Data.ptd',validation_status='validated',
            sha256=hashlib.sha256(self.native.read_bytes()).hexdigest()),
            dict(source_object_path='office',source_object_type='companion_source_preserved',
            export_format='binary',export_file='09_source_companions/~$Book.xlsx',
            validation_status='skipped_transient_companion',sha256='')]
        (self.package/'09_source_companions').mkdir()

    def save(self):
        with (self.package/'00_manifest/export_manifest.csv').open('w',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(self.rows[0]));writer.writeheader();writer.writerows(self.rows)

    def test_explicit_absent_lock_is_omitted_not_hashed(self):
        self.save(); omissions=[]; paths={}
        rows=g.manifest_records(self.package,omissions=omissions,validated_paths=paths)
        self.assertEqual(len(rows),1);self.assertEqual(list(paths),['08_native_project/Data.ptd'])
        self.assertEqual(omissions[0]['integrity'],'not_checked; file_absent')

    def test_no_bypass_for_native_ordinary_or_unmarked_missing_files(self):
        for change in ({'source_object_type':'petrel_native_store'},
                       {'export_file':'09_source_companions/Book.xlsx'},
                       {'validation_status':'validated'}, {'sha256':'a'*64},
                       {'export_file':'../../~$escape.xlsx'}):
            with self.subTest(change=change):
                original=self.rows[1].copy(); self.rows[1].update(change);self.save()
                with self.assertRaises(g.InputError):g.manifest_records(self.package)
                self.rows[1]=original

    def test_reappeared_file_requires_revalidation(self):
        (self.package/self.rows[1]['export_file']).write_text('present')
        self.save()
        with self.assertRaisesRegex(g.InputError,'revalidate'):g.manifest_records(self.package)

    def test_missing_required_native_is_not_masked_by_omission(self):
        self.native.unlink();self.save()
        with self.assertRaises(g.InputError):g.manifest_records(self.package)

    def test_validator_through_final_qc_and_receipt_verification(self):
        self.rows[1]['validation_status']='';self.save()
        validator=Path(g.__file__).parent/'validate_export_package.ps1'
        run=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(validator),
            '-ExportPackage',str(self.package),'-UpdateManifest','-WriteChecksums','-FailOnMissingExports'],
            capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        receipt=g.qc_data_package(dict(export_package=str(self.package),output_dir=str(self.root/'qc'),
                                      petrel_version='unknown',dry_run=False))
        self.assertEqual(receipt['status'],'passed')
        self.assertEqual(receipt['summary']['manifest_omitted_transients'],1)
        result=json.loads((self.root/'qc/result.json').read_text())
        self.assertEqual(len(result['manifest_omissions']),1)
        audit=g.verify_receipt(receipt)
        self.assertEqual(audit['status'],'passed',audit)


if __name__=='__main__':unittest.main()
