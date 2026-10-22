"""Meaningful offline summary checks against mixed and incomplete catalogues."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from geoviewer_summary import publish_summary


def payload(path):
    html = path.read_text(encoding='utf-8')
    return json.loads(re.search(r'const project=(.*?);\nconst ', html, re.S).group(1))


def flatten(categories):
    result = {}
    def walk(node):
        result[node['id']] = node
        for child in node.get('children', []):
            walk(child)
    for category in categories:
        walk(category)
    return result


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'
        (self.package / '01_project_metadata').mkdir(parents=True)
        self.exports = self.root / 'case_EXPORTS'; self.exports.mkdir()
        self.report = self.root / 'case_SUMMARY.html'

    def publish(self, **kw):
        return publish_summary(self.report, package=self.package, destination=self.exports, **kw)

    def test_mixed_tree_exact_relationships_and_downloads(self):
        context = {'project_name': '<script>alert(1)</script>', 'objects': [
            {'object_id': 'well', 'name': 'Alpha', 'category': 'WellTraceSubject'},
            {'object_id': 'log', 'name': 'GR', 'category': 'WellLogSubject', 'parent_id': 'well'},
            {'object_id': 'orphan', 'name': 'Alpha lookalike', 'category': 'WellLogSubject'},
            {'object_id': 'model', 'name': 'Reservoir A', 'category': 'PillarGrid2'},
            {'object_id': 'property', 'name': 'Porosity', 'category': 'FloatProperty', 'parent_id': 'model'},
            {'object_id': 'surface', 'name': 'Top A', 'category': 'SurfaceSubject'},
            {'object_id': 'mystery', 'name': 'Odd geometry', 'category': 'InterpretationMeshData'},
            {'object_id': 'config', 'name': 'UI palette', 'category': 'ContColorTableSubject'},
        ], 'seismic_objects': [{'object_id': 'seis', 'name': 'Volume A', 'category': 'seismic_volume', 'status': 'metadata_only'}]}
        (self.package/'01_project_metadata/project_context.json').write_text(json.dumps(context), encoding='utf-8')
        (self.exports/'logs').mkdir()
        (self.exports/'logs/GR.csv').write_text('md,raw_value\n1,5\n', encoding='utf-8')
        (self.exports/'logs/track.csv').write_text('well_name,md,x,y,z\nAlpha,1,1,2,3\n', encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'status': 'partial', 'files': [
            {'object_id': 'log', 'name': 'GR', 'category': 'logs', 'format': 'CSV', 'path': 'logs/GR.csv', 'status': 'exported'},
            {'object_id': 'log', 'name': 'GR', 'category': 'logs', 'format': 'JSON', 'path': 'logs/no.json', 'status': 'exported'},
            {'object_id': 'track', 'name': 'Track', 'category': 'trajectories', 'format': 'CSV', 'path': 'logs/track.csv', 'status': 'exported'},
            {'object_id': 'seis', 'name': 'Volume A', 'category': 'seismic', 'format': 'SEGY', 'path': '../escape.sgy', 'status': 'exported'}
        ]}), encoding='utf-8')
        result = self.publish(status='running')
        data = payload(self.report); nodes = flatten(data['categories'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual({x['id'] for x in nodes['well']['children']}, {'log', 'track'})
        self.assertNotIn('orphan', [x['id'] for x in nodes['well']['children']])
        self.assertEqual([x['id'] for x in nodes['model']['children']], ['property'])
        self.assertEqual(data['counts']['Seismic'], 1)
        self.assertEqual(len(nodes['log']['files']), 1)
        self.assertEqual(nodes['seis']['files'], [])
        self.assertNotIn('config', nodes)
        self.assertIn('mystery', nodes)
        self.assertNotIn('<script>alert(1)</script>', self.report.read_text(encoding='utf-8'))

    def test_no_index_and_relocation(self):
        (self.package/'01_project_metadata/project_context.json').write_text(json.dumps({'objects': [
            {'object_id': 'model', 'name': 'Model', 'category': 'PillarGrid2'}]}), encoding='utf-8')
        result = self.publish(status='running')
        self.assertEqual(result['counts']['Models'], 1)
        self.assertTrue(result['warnings'])
        self.report.unlink()
        (self.exports/'faults').mkdir()
        (self.exports/'faults/F.csv').write_text('x,y,z\n1,2,3\n', encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': [
            {'object_id': 'f', 'name': 'Fault', 'category': 'polygons', 'format': 'CSV',
             'path': 'faults/F.csv', 'status': 'exported'}]}), encoding='utf-8')
        self.publish()
        nodes = flatten(payload(self.report)['categories'])
        href = nodes['f']['files'][0]['href']
        self.assertFalse(href.startswith('/'))
        moved = self.root / 'moved'; moved.mkdir()
        os.replace(self.report, moved/self.report.name)
        os.replace(self.exports, moved/self.exports.name)
        self.assertTrue((moved / href).is_file())

    def test_large_csv_preview_is_bounded(self):
        (self.exports/'large.csv').write_text('md,raw_value\n' + '1,2\n'*100000, encoding='utf-8')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': [
            {'object_id': 'l', 'name': 'Large log', 'category': 'logs', 'format': 'CSV',
             'path': 'large.csv', 'status': 'exported'}]}), encoding='utf-8')
        self.publish()
        nodes = flatten(payload(self.report)['categories'])
        self.assertLessEqual(len(nodes['l']['preview']), 256)
        self.assertLess(self.report.stat().st_size, 150000)
        self.assertEqual(self.publish(status='failed')['report_path'], str(self.report))
        self.assertEqual(payload(self.report)['status'], 'failed')

    def test_null_flags_missing_file_and_primary_formats(self):
        (self.exports/'log_samples.csv').write_text(
            'md,raw_value,is_null,defined\n1,10,0,1\n2,-999.25,1,1\n3,30,0,1\n4,40,0,0\n5,1e309,0,1\n',
            encoding='utf-8')
        (self.exports/'log_audit.csv').write_text('sample,value\n1,1\n', encoding='utf-8')
        (self.exports/'log.las').write_text('~Version\n', encoding='utf-8')
        (self.exports/'grid.zmap').write_text('grid', encoding='utf-8')
        (self.exports/'grid.xyz').write_text('grid', encoding='utf-8')
        (self.exports/'grid_nodes.csv').write_text('x,y,raw_value,defined\n1,2,3,1\n4,5,-999,0\n', encoding='utf-8')
        (self.exports/'grid_cells.csv').write_text('x,y,raw_value\n0,0,0\n', encoding='utf-8')
        rows = [
            ('log', 'logs', 'LAS', 'log.las'), ('log', 'logs', 'CSV', 'log_audit.csv'),
            ('log', 'logs', 'CSV', 'log_samples.csv'), ('grid', 'grids', 'ZMAP', 'grid.zmap'),
            ('grid', 'grids', 'XYZ', 'grid.xyz'), ('grid', 'grids', 'CSV', 'grid_cells.csv'),
            ('grid', 'grids', 'CSV', 'grid_nodes.csv'),
            ('missing', 'seismic', 'SEGY', '../outside.sgy')]
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': [
            {'object_id': identity, 'name': identity, 'category': category, 'format': fmt,
             'path': path, 'status': 'exported'} for identity, category, fmt, path in rows]}), encoding='utf-8')
        self.publish()
        nodes = flatten(payload(self.report)['categories'])
        self.assertEqual([x['_valid'] for x in nodes['log']['preview']], [True, False, True, False, False])
        self.assertEqual(nodes['log']['preview'][1]['is_null'], '1')
        self.assertEqual([x['label'] for x in nodes['log']['files']], ['Download LAS', 'Download CSV'])
        self.assertTrue(nodes['log']['files'][1]['href'].endswith('log_samples.csv'))
        self.assertEqual([x['label'] for x in nodes['grid']['files']],
                         ['Download ZMAP', 'Download XYZ', 'Download CSV'])
        self.assertTrue(nodes['grid']['files'][2]['href'].endswith('grid_nodes.csv'))
        self.assertEqual(nodes['missing']['status'], 'unavailable')
        self.assertEqual(nodes['missing']['files'], [])
        html = self.report.read_text(encoding='utf-8')
        self.assertIn('<details class="panel">', html)
        self.assertIn('if(!p){started=false;continue}', html)

    def test_exact_seismic_transport_identity_is_one_dataset(self):
        (self.exports/'volume.segy').write_bytes(b'fixture')
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':[{'object_id':'transport','name':'Volume','category':'seismic','format':'SEGY','path':'volume.segy','status':'exported'}]}))
        context={'objects':[{'object_id':'native','name':'Volume','category':'SeismicVolume'}]}
        inventory={'objects':[{'id':'transport','name':'Volume','association':'exact_native_UUID_in_selected_store','native_objects':[{'object_id':'native'}]}]}
        self.publish(context=context,inventory=inventory)
        nodes=flatten(payload(self.report)['categories'])
        self.assertNotIn('transport',nodes)
        self.assertEqual(nodes['native']['files'][0]['label'],'Download SEGY')
        self.assertEqual(payload(self.report)['counts']['Seismic'],1)

    def test_preview_budget_reaches_later_logs_and_grids(self):
        (self.exports/'stations.csv').write_text('md,x,y,z\n'+('1,'+'2'*100+',3,4\n')*2000)
        (self.exports/'samples.csv').write_text('md,raw_value\n1,2\n2,3\n')
        (self.exports/'nodes.csv').write_text('x,y,z\n1,2,3\n2,3,4\n')
        rows=[{'object_id':f't{i}','category':'trajectories','format':'CSV','path':'stations.csv','status':'exported'} for i in range(24)]
        rows += [{'object_id':'late_log','category':'logs','format':'CSV','path':'samples.csv','status':'exported'},
                 {'object_id':'late_grid','category':'grids','format':'CSV','path':'nodes.csv','status':'exported'}]
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files':rows}))
        self.publish();nodes=flatten(payload(self.report)['categories'])
        self.assertTrue(nodes['late_log']['preview'])
        self.assertTrue(nodes['late_grid']['preview'])
        self.assertLessEqual(sum(len(n.get('preview',[])) for n in nodes.values()),20000)

    def test_unknown_native_payload_and_total_preview_budget(self):
        registry = self.package/'01_project_metadata/native_data_object_registry.csv'
        registry.write_text('object_id,object_type,decode_status\n'
                            'novel,BrandNewNativePayload,not_decoded\n'
                            'ui,LayoutServiceData,not_decoded\n', encoding='utf-8')
        (self.package/'01_project_metadata/project_context.json').write_text(json.dumps({'objects': [
            {'object_id': 'p', 'name': 'Generic polygon', 'category': 'PolygonsSubject'},
            {'object_id': 'f', 'name': 'Fault interpretation', 'category': 'FaultInterpretation'},
            {'object_id': 'cfg', 'name': 'Palette', 'category': 'ContColorTableSubject'}]}), encoding='utf-8')
        sample = 'md,raw_value\n' + '1,2\n'*256
        rows = []
        for number in range(100):
            path = f'log{number}.csv'
            (self.exports/path).write_text(sample, encoding='utf-8')
            rows.append({'object_id': f'l{number}', 'name': f'Log {number}', 'category': 'logs',
                         'format': 'CSV', 'path': path, 'status': 'exported'})
        (self.exports/'FILE_INDEX.json').write_text(json.dumps({'files': rows}), encoding='utf-8')
        result = self.publish()
        data = payload(self.report); nodes = flatten(data['categories'])
        self.assertIn('novel', nodes)
        self.assertNotIn('ui', nodes)
        self.assertNotIn('cfg', nodes)
        self.assertEqual(nodes['p']['kind'], 'Polygons / Faults')
        self.assertEqual(nodes['f']['kind'], 'Polygons / Faults')
        total = sum(len(node.get('preview', [])) for node in nodes.values())
        self.assertLessEqual(total, 20000)
        self.assertTrue(any('preview budget' in warning for warning in result['warnings']))


if __name__ == '__main__':
    unittest.main()
