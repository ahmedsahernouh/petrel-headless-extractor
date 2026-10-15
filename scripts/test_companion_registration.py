# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Converter ownership survives the actual PowerShell registration/QC boundary."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import portable_petrel_companion_extract as companion
from geoviewer_delivery import publish_exports, exports_section

SCRIPTS=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_rows(path,rows,fields=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
def read_rows(path):
    with path.open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))

@unittest.skipUnless(os.name=='nt' and shutil.which('powershell.exe'),'Windows registrar integration')
class CompanionRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.package=self.root/'package';self.exports=self.root/'EXPORTS'
        self.manifest=self.package/'00_manifest/export_manifest.csv'
        self.source=self.root/'independent_input/tops.txt';self.source.parent.mkdir()
        self.source.write_text('# Petrel well tops\nVERSION 2\nBEGIN HEADER\nWell\nSurface\nX\nY\nZ\nMD\nEND HEADER\n"A" "Top A" 500000 2900000 -1000 1100\n',encoding='utf-8')
        self.source_hash=sha(self.source)
        preserved=self.package/'09_source_companions/input/tops.txt';preserved.parent.mkdir(parents=True)
        shutil.copyfile(self.source,preserved)
        self.generated,self.summary=companion.convert_petrel_well_tops_ascii(self.source,self.package/'02_wells/well_tops/input/tops.txt')
        self.headers=['export_id','project_name','petrel_version','export_date_utc','source_object_path',
            'source_object_type','export_format','export_file','export_status','validation_status','sha256','notes']
        self.rows=[companion.manifest_row(self.headers,self.package,p,'fixture','unknown','companion_converted_ascii',
            'Converted from input/tops.txt; source preserved separately.') for p in self.generated]
        write_rows(self.manifest,self.rows)
        self.inventory=self.package/'00_manifest/companion_source_inventory.csv'
        write_rows(self.inventory,[dict(source_relative_path='input/tops.txt',category='petrel_well_tops_ascii',
            extension='.txt',size_bytes=preserved.stat().st_size,sha256=self.source_hash,
            status='converted_and_preserved',preserved_file=companion.rel(self.package,preserved),
            converted_files=';'.join(companion.rel(self.package,p) for p in self.generated))])
        metadata=self.package/'01_project_metadata';metadata.mkdir()
        (metadata/'project_summary.json').write_text('{}')

    def run_ps(self,name,*args):
        path=Path(os.environ.get('FV_TEST_REGISTRAR',SCRIPTS/name)) if name=='register_petrel_file_exports.ps1' else SCRIPTS/name
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(path),
            '-ExportPackage',str(self.package),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout

    def register(self):return self.run_ps('register_petrel_file_exports.ps1','-ProjectName','fixture','-PetrelVersion','unknown','-RegisterUnknown')
    def validate(self):return self.run_ps('validate_export_package.ps1','-UpdateManifest','-WriteChecksums','-FailOnMissingExports')

    def test_converter_identity_format_and_lineage_survive_registration_qc_and_delivery(self):
        before=read_rows(self.manifest);self.register()
        self.assertEqual(read_rows(self.manifest),before)
        self.validate();validated=read_rows(self.manifest)
        self.assertTrue(all(r['validation_status']=='validated' for r in validated))
        self.assertEqual({r['export_format'] for r in validated},{'CSV','JSON'})
        first=publish_exports(self.package,{'objects':[]},self.exports)
        self.assertEqual(len(first['files']),2,first['findings'])
        self.assertTrue(all(r['source_lane']=='companion' and r['category']=='companion_well_tops' for r in first['files']))
        self.assertTrue(all(r['registration_scope']=='companion_converted_ascii' and not r['registration_format_warning'] for r in first['files']))
        self.assertEqual({r['source_reference'] for r in first['files']},{'input/tops.txt'})
        for r in first['files']:self.assertEqual(sha(self.exports/r['path']),r['sha256'])
        self.register();self.assertEqual(read_rows(self.manifest),validated)
        second=publish_exports(self.package,{'objects':[]},self.exports)
        self.assertEqual(first['files'],second['files'])
        html=exports_section(second,self.exports,self.root/'report.html')
        self.assertIn('2 companion derivatives',html)
        self.assertEqual(sha(self.source),self.source_hash)
        self.assertEqual(self.summary['records'],1);self.assertFalse(self.summary['native_binary_decode'])

    def test_generic_top_scan_records_json_format_without_inventing_companion_lineage(self):
        # A file without an owning converter row still belongs to the generic scan.
        write_rows(self.manifest,[],self.headers);self.register()
        rows=read_rows(self.manifest)
        summary=next(r for r in rows if r['export_file'].endswith('.summary.json'))
        self.assertEqual(summary['export_format'],'JSON')
        self.assertEqual(summary['source_object_type'],'well_top')
        self.assertTrue(summary['source_object_path'].startswith('File export/'))
        self.assertEqual(summary['validation_status'],'unchecked')

    def test_invalid_converter_registration_is_preserved_then_rejected(self):
        self.rows[0]['export_status']='failed'
        write_rows(self.manifest,self.rows);self.register()
        self.assertEqual(read_rows(self.manifest),self.rows)
        self.validate()
        result=publish_exports(self.package,{'objects':[]},self.exports)
        self.assertEqual(len(result['files']),1)
        self.assertTrue(any('not validated and exported' in f for f in result['findings']))
        self.assertEqual(result['files'][0]['format'],'JSON')

    def test_separator_case_aliases_do_not_reclassify_producer_rows(self):
        for row in self.rows:row['export_file']=row['export_file'].replace('\\','/').upper()
        write_rows(self.manifest,self.rows);self.register()
        self.assertEqual(read_rows(self.manifest),self.rows)

    def test_missing_inventory_cannot_be_rescanned_into_valid_companion(self):
        self.inventory.unlink();self.register();self.validate()
        result=publish_exports(self.package,{'objects':[]},self.exports)
        self.assertFalse(result['files']);self.assertTrue(result['partial'])
        self.assertTrue(any('Companion source inventory missing' in f for f in result['findings']))

if __name__=='__main__':unittest.main()
