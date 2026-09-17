# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Release contracts: native identity, output navigation, diagnostics and path safety."""
import csv
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import geoviewer_delivery as delivery
import geoviewer_metadata as metadata
import petrel_native_binary as binary
from geoviewer_diagnostics import Diagnostics
from geoviewer_paths import project_files
from test_petrel_native_recovery import element,array,frame
import test_petrel_native_recovery as fixtures
import export_petrel_native_spatial_zero_gui as spatial


class ReleaseTests(unittest.TestCase):
    def metadata_fixture(self):
        fixture=fixtures.RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        package=fixture.fixture()
        native=package/'08_native_project'
        project=fixture.root/'test.pet'
        shutil.copy2(native/'project_file/test.pet',project)
        shutil.copytree(native/'ptd_store',project.with_suffix('.ptd'))
        return fixture,package,project

    def test_ambiguous_model_record_does_not_disable_other_decoders(self):
        fixture,package,project=self.metadata_fixture()
        model=project.with_suffix('.ptd')/'Model.ptd'
        docs=[body for names,body in binary.documents(binary.decompress(model.read_bytes()))]
        ambiguous=element('LocalPrincipalVectorPropertySubject',children=[
            element('unique_tag','11111111-1111-1111-1111-111111111111'),
            element('unique_tag','22222222-2222-2222-2222-222222222222')])
        docs[-1]=docs[-1][:-1]+element('version_string','2018.2')+b'\x01'
        seismic=element('SeismicSubject',children=[element('unique_tag','33333333-3333-3333-3333-333333333333'),
            element('name','Synthetic seismic'),element('file','cube.zgy')])
        model.write_bytes(fixtures.envelope(frame(ambiguous,*docs,seismic)))
        result=metadata.inspect_project(project)
        self.assertTrue(result['model_readable']);self.assertTrue(result['native_decoders_applicable'])
        self.assertFalse(result['model_metadata_complete']);self.assertEqual(result['model_record_error_count'],1)
        self.assertEqual(result['saved_version']['value'],'2018.2')
        self.assertEqual(result['seismic_objects'][0]['name'],'Synthetic seismic')
        self.assertTrue(result['seismic_inventory_complete'])
        issue=result['model_record_errors'][0]
        self.assertEqual(len(issue['candidate_object_ids']),2)
        self.assertFalse(set(issue['candidate_object_ids']) & {x['object_id'] for x in result['objects']})
        self.assertIn('unresolved metadata',metadata.compatibility(result)['reason'])
        with self.assertRaises(binary.NativeError):
            next(binary.read_documents(frame(ambiguous))).get('unique_tag')
        # Run the actual numeric reader against this model, not a mocked success.
        shutil.copy2(model,package/'08_native_project/ptd_store/Model.ptd')
        report=fixtures.r.run(package)
        self.assertEqual(report['objects'][0]['status'],'decoded')
        self.assertTrue(list((package/'native_data').rglob('curve.las')))
        rendered=delivery.metadata_section(result)
        self.assertIn('Metadata limitations',rendered);self.assertIn('expected one unique_tag',rendered)
        self.assertIn('2018.2',rendered)

    def test_malformed_model_container_still_blocks_native_gate(self):
        _,_,project=self.metadata_fixture()
        model=project.with_suffix('.ptd')/'Model.ptd'
        model.write_bytes(fixtures.envelope(binary.decompress(model.read_bytes())+b'\xff'))
        result=metadata.inspect_project(project)
        self.assertFalse(result['model_readable']);self.assertFalse(result['native_decoders_applicable'])
        self.assertIn('Unsupported BXML framing token',result['reason'])
        self.assertFalse(result['seismic_inventory_complete'])
        self.assertIn('total unknown (metadata incomplete)',delivery.metadata_section(result))

    def test_legacy_layout_reports_reason_without_inventing_support(self):
        _,_,project=self.metadata_fixture()
        store=project.with_suffix('.ptd')
        (store/'Model.ptd').unlink();(store/'Data.ptd').unlink();(store/'legacy.ptd').write_bytes(b'legacy')
        result=metadata.inspect_project(project)
        self.assertFalse(result['native_decoders_applicable'])
        self.assertIn('No Data.ptd',metadata.compatibility(result)['reason'])

    def test_project_progress_does_not_restart_during_seismic(self):
        import petrel_progress as progress
        stream=io.StringIO();display=progress.ConsoleProgress(stages=12,stream=stream).start()
        try:
            with progress.keep_stage(12):progress.phase(3,'Seismic writing')
            self.assertEqual(display.number,12)
            progress.phase(4,'Outside group');self.assertEqual(display.number,4)
        finally:display.close(False)

    def test_unc_sqlite_uses_path_not_authority_and_readonly(self):
        mocked=MagicMock();mocked.resolve.return_value.as_uri.return_value='file://server/share/a%20b/Data.ptd'
        with patch.object(binary,'Path',return_value=mocked):
            self.assertEqual(binary.sqlite_readonly_uri('ignored'),'file:////server/share/a%20b/Data.ptd?mode=ro')

    def test_native_date_keeps_unknown_timezone(self):
        value={'nbfx_type':0x96,'raw_hex':(638_000_000_000_000_001).to_bytes(8,'little').hex()}
        date=metadata.date_evidence(value)
        self.assertEqual(date['timezone'],'unknown');self.assertTrue(date['value'].endswith('0000001'))

    def test_shallow_index_keeps_receipt_valid_and_relocates(self):
        fixture=fixtures.RecoveryTests();fixture.setUp()
        try:
            package,receipt,record=fixture.run_fixture()
            destination=fixture.root/'run_EXPORTS'
            index=delivery.publish_exports(package,{'objects':[]},destination)
            self.assertTrue(index['files'])
            self.assertEqual({x['category'] for x in index['files']},{'logs'})
            for item in index['files']:
                p=destination/item['path'];self.assertTrue(p.is_file())
                self.assertIn('single_physical_copy',item['storage'])
                original=next(a for a in record['artifacts'] if a['sha256']==item['sha256'])
                self.assertTrue(os.path.samefile(p,package/'native_data'/original['path']))
            # A repeated publication must not overwrite an unrelated destination.
            self.assertEqual(len(delivery.publish_exports(package,{'objects':[]},destination)['files']),len(index['files']))
            report=fixture.root/'run_REPORT.html';report.write_text('<html><head></head><body><main>Inventory</main></body></html>')
            delivery.decorate(report,{},index,destination)
            text=report.read_text();self.assertIn('Open the extracted data',text);self.assertIn('samples.csv',text)
            self.assertNotIn('file://',text);self.assertIn('data:image/svg+xml;base64',text)
        finally:fixture.doCleanups()

    def test_failed_objects_and_previews_do_not_enter_export_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'data';package.mkdir()
            metadata.write_json(package/'07_workflows_reports/native_recovery/native_recovery_report.json',
                {'objects':[{'object_id':'x','status':'failed','artifacts':[{'path':'fake.csv'}]}]})
            result=delivery.publish_exports(package,{'objects':[{'status':'conversion_failed'}]},root/'EXPORTS')
            self.assertEqual(result['files'],[])

    def test_partial_report_escapes_untrusted_names(self):
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'report.html'
            delivery.partial_report(report,{'objects':[{'category':'Points','name':'<script>alert(1)</script>','object_id':'1'}]},'Failed','<bad>')
            content=report.read_text();self.assertNotIn('<script>',content);self.assertIn('&lt;bad&gt;',content);self.assertIn('<details>',content)

    def test_diagnostics_are_flushed_and_elapsed_override_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);log=Diagnostics(root/'log.txt',root/'events.jsonl')
            try:
                log.event('started',elapsed_seconds=1.5)
                entry=json.loads((root/'events.jsonl').read_text())
                self.assertEqual(entry['reported_elapsed_seconds'],1.5)
                self.assertGreaterEqual(entry['elapsed_seconds'],0)
            finally:log.close()

    def test_nested_toolkit_pruned_without_hiding_project_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);kit=root/'nested/versioned/GeoViewer';(kit/'scripts').mkdir(parents=True)
            for path in ('STANDALONE.txt','toolkit.json','scripts/repair_standalone_dependencies.ps1'):(kit/path).write_text('marker')
            data=root/'scripts';data.mkdir();(data/'user_data.txt').write_text('keep')
            self.assertEqual(list(project_files(root)),[data/'user_data.txt'])

    def test_points_identity_slots_are_validated_without_reordering(self):
        def payload(count):
            children=[element('user_data',attrs={'Size':0}),element('vertices',attrs={'Size':2},children=[array('double',[4.,2.,3.,1.,5.,6.],'<f8')]),
                element('has_attr',False),element('has_object_ids',True),element('object_ids',attrs={'Size':count},children=[element('item',attrs={'Version':3})]*count)]
            return frame(element('data',attrs={'Type':'Points3','Version':[1,2,0,1,1],'xmlns':'http://www.slb.com/Petrel/2011/03/Serialization'},children=children),split=True)
        values,evidence=spatial.decode_points3(payload(2))
        self.assertEqual(values,[(4.,2.,3.),(1.,5.,6.)]);self.assertIn('not_exported',evidence['object_ids_status'])
        with self.assertRaises(spatial.DecodeError):spatial.decode_points3(payload(1))


if __name__=='__main__':unittest.main()
