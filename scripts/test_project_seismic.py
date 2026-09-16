"""Seismic integration and delivery controls. https://saherlabs.dev/"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0,str(Path(__file__).resolve().parent))
import petrel_project_seismic as project_seismic
import petrel_file_convert as converter
import petrel_progress as progress
import test_petrel_binary_conversion as fixtures


class ProjectSeismicTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BinaryConversionTests();self.fixture.setUp()
        self.root=self.fixture.root
        self.project=self.fixture.inputs/'Project.pet';self.project.write_text('<project/>')
        self.store=self.project.with_suffix('.ptd');self.store.mkdir()

    def internal_zgy(self,**kwargs):
        original=self.fixture.fixture(**kwargs)
        target=self.store/original.name;shutil.move(str(original),target);return target

    def test_fast_conversion_does_not_hash_seismic(self):
        source=self.internal_zgy();digest=hashlib.sha256(source.read_bytes()).hexdigest()
        original=progress.hash_file
        def bounded(path):
            self.assertNotIn(Path(path).suffix.lower(),{'.zgy','.segy'})
            return original(path)
        with patch.object(progress,'hash_file',side_effect=bounded):
            result=converter.execute(source,self.fixture.outputs,'zgy-to-segy',{})
        self.assertTrue(result['summary']['all_decoded_samples_exact'])
        self.assertIsNone(result['source_unchanged'])
        self.assertTrue(result['source_stat_unchanged'])
        self.assertEqual(result['source_hashes_before'],{})
        self.assertIsNone(next(x for x in result['artifacts'] if x['path']=='volume.segy')['sha256'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)

    def test_full_hash_is_explicit_and_complete(self):
        source=self.internal_zgy()
        result=converter.execute(source,self.fixture.outputs,'zgy-to-segy',{'full_hash':True})
        self.assertEqual(result['source_hashes_before'][str(source)],hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertTrue(result['source_unchanged'])
        self.assertTrue(all(row['sha256'] for row in result['artifacts']))

    def test_fast_file_state_change_rejected(self):
        source=self.internal_zgy();original=converter.file_state;calls=[]
        def changed(path):
            state=original(path);calls.append(path)
            if len(calls)>1:state['mtime_ns']+=1
            return state
        with patch.object(converter,'file_state',side_effect=changed):
            with self.assertRaises(converter.InputError):converter.execute(source,self.fixture.outputs,'zgy-to-segy',{})
        self.assertFalse(list(self.fixture.outputs.rglob('volume.segy')))

    def test_changed_source_since_inventory_is_not_converted(self):
        source=self.internal_zgy();inventory=project_seismic.discover(self.project)
        inventory['objects'][0]['file_state']['mtime_ns']-=1
        project_seismic.convert_project(inventory,self.fixture.outputs,True)
        self.assertEqual(inventory['objects'][0]['status'],'conversion_failed')
        self.assertFalse(self.fixture.outputs.exists())

    def test_discovery_retains_unlinked_and_excludes_neighbor_store(self):
        inside=self.internal_zgy();outside=self.fixture.fixture(name='unlinked')
        neighbor=self.fixture.inputs/'Other.ptd';neighbor.mkdir();shutil.copyfile(inside,neighbor/'foreign.zgy')
        result=project_seismic.discover(self.project)
        rows={Path(row['source']).name:row for row in result['objects']}
        self.assertEqual(set(rows),{inside.name,outside.name})
        self.assertEqual(rows[inside.name]['association'],'selected_project_store')
        self.assertEqual(rows[outside.name]['association'],'unlinked_companion')
        self.assertEqual(rows[inside.name]['metadata']['size'],[3,4,16])
        self.assertIsNone(rows[inside.name]['sha256'])

    def test_explicit_xml_reference_and_missing_reference(self):
        source=self.fixture.fixture(name='outside')
        self.project.write_text('<project><file path="outside.zgy"/><file path="missing.zgy"/></project>')
        result=project_seismic.discover(self.project)
        rows={row['name']:row for row in result['objects']}
        self.assertEqual(rows[source.name]['association'],'explicit_project_reference')
        self.assertEqual(rows['missing.zgy']['status'],'missing')

    def test_report_only_has_inventory_without_conversion_or_hash(self):
        self.internal_zgy();inventory=project_seismic.discover(self.project)
        with patch.object(converter,'execute',side_effect=AssertionError('conversion disabled')),patch.object(progress,'hash_file',side_effect=AssertionError('hashing disabled')):
            project_seismic.convert_project(inventory,self.fixture.outputs,False)
        self.assertEqual(inventory['objects'][0]['status'],'not_selected')
        self.assertIn('metadata',inventory['objects'][0])
        self.assertFalse(self.fixture.outputs.exists())

    def test_native_bxml_file_reference_not_visual_name(self):
        import test_petrel_native_recovery as native
        source=self.fixture.fixture(name='external')
        doc=native.element('ProjectSerializer',children=[native.element('FileName',source.name),native.element('visualName','fake.zgy')])
        self.project.write_bytes(b'\xff\xff\x01\x00\x11\x00ProjectSerializer'+bytes(9)+native.envelope(native.frame(doc)))
        result=project_seismic.discover(self.project)
        rows={row['name']:row for row in result['objects']}
        self.assertEqual(set(rows),{'external.zgy'})
        self.assertEqual(rows['external.zgy']['association'],'explicit_project_reference')

    def test_project_conversion_reports_success_and_unsupported(self):
        self.internal_zgy();self.internal_zgy(name='depth',zunitdim=fixtures.UnitDimension.length,zunitname='m',zunitfactor=1.)
        inventory=project_seismic.discover(self.project)
        project_seismic.convert_project(inventory,self.fixture.outputs,True)
        self.assertEqual(inventory['counts'],{'converted':2})
        self.assertEqual(len(list(self.fixture.outputs.rglob('volume.segy'))),2)

    def test_report_is_full_html_with_relocated_links(self):
        data=self.root/'Output & Results';package=data/'Project_data'/'nested package';package.mkdir(parents=True)
        report=package/'PROJECT_REPORT.html';(package/'data.txt').write_text('data')
        report.write_text('<!DOCTYPE html><html><body><main><h1>Full report</h1><a href="#tree">Tree</a><a href="data.txt">Data</a><img src="data:image/png;base64,test"><script>const sample="<data>";</script></main></body></html>')
        self.internal_zgy();inventory=project_seismic.discover(self.project)
        target=data/'Project_REPORT.html'
        project_seismic.deliver_report(report,target,inventory,True)
        html=target.read_text(encoding='utf-8')
        self.assertIn('<h1>Full report</h1>',html)
        self.assertIn('href="#tree"',html)
        self.assertIn('href="Project_data/nested%20package/data.txt"',html)
        self.assertIn('Not calculated — full hashing disabled',html)
        self.assertIn('data:image/png;base64,test',html)
        self.assertIn('const sample="<data>";',html)
        self.assertNotIn('http-equiv="refresh"',html)

    def test_report_only_full_hash_can_be_selected(self):
        source=self.internal_zgy();inventory=project_seismic.discover(self.project)
        project_seismic.convert_project(inventory,self.fixture.outputs,False,True)
        self.assertEqual(inventory['objects'][0]['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertFalse(self.fixture.outputs.exists())

    def test_exact_zgy_report_only_has_figure_at_output_root(self):
        source=self.internal_zgy()
        self.assertTrue(project_seismic.single_file_run(source,self.fixture.outputs,{'report_only':True}))
        report=next(self.fixture.outputs.glob('*_REPORT.html'))
        self.assertIn('data:image/png;base64,',report.read_text(encoding='utf-8'))
        self.assertIn('Complete data inventory tree',report.read_text(encoding='utf-8'))
        self.assertFalse(list(self.fixture.outputs.rglob('volume.segy')))
        self.assertEqual(len(list(self.fixture.outputs.glob('*_data'))),1)

    def test_exact_zgy_conversion_updates_inventory_and_catalogue(self):
        source=self.internal_zgy()
        self.assertTrue(project_seismic.single_file_run(source,self.fixture.outputs,{}))
        report=next(self.fixture.outputs.glob('*_REPORT.html')).read_text(encoding='utf-8')
        self.assertRegex(report,r'<details class="object-node"[^>]+data-status="converted"')
        self.assertRegex(report,r'<tr data-object-id="[^"]+" data-status="converted"')
        self.assertIn('data-base-count="0">1</b>',report)
        self.assertIn('Open SEG-Y',report)


if __name__=='__main__':unittest.main()
