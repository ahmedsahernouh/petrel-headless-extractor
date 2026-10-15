# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Companion publication acceptance and rejection boundaries."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_delivery as delivery
from geoviewer_delivery import publish_exports, exports_section


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


class CompanionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.package=self.root/'package';self.package.mkdir()
        self.destination=self.root/'EXPORTS'
        self.sources=[];self.manifest=[]

    def add(self, name, kind, extension, derivative):
        original='input/'+name+extension
        preserved='09_source_companions/'+original
        output='10_converted_ascii/'+derivative
        source=self.package/preserved;source.parent.mkdir(parents=True,exist_ok=True)
        source.write_text('source '+name,encoding='utf-8')
        target=self.package/output;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('value\n1\n',encoding='utf-8')
        self.sources.append(dict(source_relative_path=original,category=kind,extension=extension,
            size_bytes=source.stat().st_size,sha256=digest(source),status='converted_and_preserved',
            preserved_file=preserved,converted_files=output))
        self.manifest.append(dict(source_object_type='companion_converted_ascii',export_file=output,
            export_status='exported_zero_gui_read_only',validation_status='validated',sha256=digest(target),
            notes='Converted from '+original+'; source preserved separately.'))
        return target

    def save(self):
        table(self.package/'00_manifest/companion_source_inventory.csv',self.sources)
        table(self.package/'00_manifest/export_manifest.csv',self.manifest)

    def publish(self,checkpoint=None):
        self.save()
        return publish_exports(self.package,{'objects':[]},self.destination,checkpoint)

    def test_las_workbook_tops_lineage_csv_json_report_and_repeat(self):
        self.add('well','las_well_data','.las','las/well.csv')
        self.add('book','excel_workbook','.xlsx','workbooks/book/sheet.csv')
        tops=self.add('tops','petrel_well_tops_ascii','.txt','tops/tops.summary.json')
        actual=self.package/'02_wells/well_tops/tops.summary.json'
        actual.parent.mkdir(parents=True,exist_ok=True);tops.rename(actual)
        self.sources[-1]['converted_files']='02_wells/well_tops/tops.summary.json'
        self.manifest[-1]['export_file']='02_wells/well_tops/tops.summary.json'
        first=self.publish();second=self.publish()
        self.assertEqual(first['files'],second['files'])
        self.assertEqual(len(first['files']),3)
        self.assertEqual({r['category'] for r in first['files']},
            {'companion_logs','companion_workbooks','companion_well_tops'})
        self.assertTrue(all(r['source_lane']=='companion' and r['source_reference'].startswith('input/')
                            and r['numerical_qc']=='companion_format_conversion_and_file_integrity_only'
                            for r in first['files']))
        self.assertTrue(all((self.destination/r['path']).is_file() for r in first['files']))
        with (self.destination/'FILE_INDEX.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),3)
        self.assertEqual([r['source_reference'] for r in rows],[r['source_reference'] for r in first['files']])
        self.assertEqual([r['sha256'] for r in rows],[r['sha256'] for r in first['files']])
        self.assertEqual(json.loads((self.destination/'FILE_INDEX.json').read_text())['files'],first['files'])
        html=exports_section(first,self.destination,self.root/'report.html')
        self.assertIn('3 companion derivatives',html)
        self.assertIn('Companion source: input/well.las',html)

    def test_gis_geometry_and_crs_formats_do_not_expand_other_companion_lanes(self):
        self.add('geometry','gis_shapefile_geometry','.shp','gis/geometry.geojson')
        self.add('crs','gis_shapefile_geometry','.shp','gis/crs.prj')
        self.add('wrong_lane','las_well_data','.las','las/wrong_lane.geojson')
        result=self.publish()
        self.assertEqual(len(result['files']),2)
        self.assertEqual({r['category'] for r in result['files']},{'companion_gis'})
        self.assertEqual({Path(r['path']).suffix for r in result['files']},{'.geojson','.prj'})
        self.assertTrue(any('wrong_lane.geojson' in f and 'not a supported' in f for f in result['findings']))

    def test_rejections_withdraw_stale_checkpoint_while_valid_sibling_survives(self):
        changed=self.add('changed','las_well_data','.las','las/changed.csv')
        missing=self.add('missing','excel_workbook','.xlsx','workbooks/missing.csv')
        good=self.add('good','petrel_well_tops_ascii','.txt','tops/good.csv')
        first=self.publish()
        changed.write_text('value\n2\n',encoding='utf-8') # same size, different hash
        missing.unlink()
        second=self.publish(first)
        self.assertEqual(len(second['files']),1)
        self.assertEqual(second['files'][0]['source_reference'],'input/good.txt')
        self.assertTrue(any('SHA-256 mismatch' in f for f in second['findings']))
        self.assertTrue(any('missing' in f for f in second['findings']))
        self.assertEqual(digest(good),second['files'][0]['sha256'])

    def test_unregistered_ambiguous_traversal_and_linked_ancestor(self):
        self.add('good','las_well_data','.las','las/good.csv')
        self.add('unregistered','las_well_data','.las','las/unregistered.csv')
        self.manifest.pop()
        self.add('ambiguous','excel_workbook','.xlsx','workbooks/ambiguous.csv')
        self.sources.append(dict(self.sources[-1]))
        self.add('traversal','petrel_well_tops_ascii','.txt','tops/traversal.csv')
        self.sources[-1]['converted_files']='../escape.csv'
        self.manifest[-1]['export_file']='../escape.csv'
        self.add('linked','las_well_data','.las','linked/linked.csv')
        target=self.package/'10_converted_ascii/linked'
        original=Path.is_symlink
        with patch.object(Path,'is_symlink',lambda path: path==target or original(path)):
            result=self.publish()
        self.assertEqual([r['source_reference'] for r in result['files']],['input/good.las'])
        self.assertEqual(sum('Companion derivative rejected' in f for f in result['findings']),4)

    def test_preserved_source_same_size_change_withdraws_both_derivatives(self):
        self.add('well','las_well_data','.las','las/well.csv')
        second=self.package/'10_converted_ascii/las/well.summary.json'
        second.write_text('{"curve_count":1}',encoding='utf-8')
        self.sources[0]['converted_files']+=';10_converted_ascii/las/well.summary.json'
        additional=dict(self.manifest[0],export_file='10_converted_ascii/las/well.summary.json',sha256=digest(second))
        self.manifest.append(additional)
        first=self.publish();self.assertEqual(len(first['files']),2)
        preserved=self.package/self.sources[0]['preserved_file']
        original=preserved.read_text(encoding='utf-8')
        # Keep the exact byte count while changing content.
        preserved.write_text(original[:-1]+'X',encoding='utf-8')
        self.assertEqual(preserved.stat().st_size,int(self.sources[0]['size_bytes']))
        second_run=self.publish(first)
        self.assertEqual(second_run['files'],[])
        self.assertEqual(sum('preserved source SHA-256 mismatch' in f for f in second_run['findings']),2)

    def test_duplicate_manifest_registration_rejects_only_ambiguous_derivative(self):
        self.add('ambiguous','las_well_data','.las','las/ambiguous.csv')
        self.add('good','excel_workbook','.xlsx','workbooks/good.csv')
        self.manifest.append(dict(self.manifest[0]))
        result=self.publish()
        self.assertEqual([r['source_reference'] for r in result['files']],['input/good.xlsx'])
        self.assertTrue(any('ambiguous companion manifest registration' in f for f in result['findings']))

    def test_multiple_derivatives_hash_preserved_source_once_per_call(self):
        first=self.add('well','las_well_data','.las','las/well.csv')
        second=self.package/'10_converted_ascii/las/well.summary.json'
        second.write_text('{"curve_count":1}',encoding='utf-8')
        self.sources[0]['converted_files']+=';10_converted_ascii/las/well.summary.json'
        self.manifest.append(dict(self.manifest[0],export_file='10_converted_ascii/las/well.summary.json',
                                  sha256=digest(second)))
        preserved=self.package/self.sources[0]['preserved_file']
        seen=[];original=delivery.sha256
        def count(path):
            seen.append(Path(path));return original(path)
        with patch.object(delivery,'sha256',side_effect=count):
            result=self.publish()
        self.assertEqual(len(result['files']),2)
        self.assertEqual(seen.count(preserved),1)
        self.assertEqual(seen.count(first),1)
        self.assertEqual(seen.count(second),1)
        self.assertEqual(len(seen),3)

    def test_historical_well_top_registration_requires_inventory_owner_and_summary_signature(self):
        wrong=self.add('wrong','las_well_data','.las','wrong/wrong.summary.json')
        top=self.add('tops','petrel_well_tops_ascii','.txt','tops/tops.summary.json')
        for index,path in ((0,wrong),(1,top)):
            actual=self.package/('02_wells/well_tops/'+path.name)
            actual.parent.mkdir(parents=True,exist_ok=True)
            path.rename(actual)
            relative=actual.relative_to(self.package).as_posix()
            self.sources[index]['converted_files']=relative
            self.manifest[index].update(source_object_type='well_top',export_file=relative,
                source_object_path='File export/'+relative,export_format='CSV',export_status='exported',
                sha256=digest(actual),notes='Auto-registered from export package folder scan; no matching inventory row was found.')
        top_actual=self.package/self.sources[1]['converted_files']
        top_actual.write_text(json.dumps({'converter':'portable_petrel_well_tops_ascii_parser',
            'native_binary_decode':False,'petrel_export_confirmed':True,'records':2,
            'source_header_columns':['Well','Surface'],
            'validation_boundary':'Rows parsed from an open Petrel Well Tops ASCII companion.'}),encoding='utf-8')
        self.manifest[1]['sha256']=digest(top_actual)
        first=self.publish()
        self.assertEqual(len(first['files']),1)
        self.assertEqual(first['files'][0]['registration_scope'],'well_top_package_scan_inventory_bound')
        self.assertEqual(first['files'][0]['format'],'JSON')
        self.assertIn('labels JSON sidecar CSV',first['files'][0]['registration_format_warning'])
        self.assertTrue(any('02_wells/well_tops/wrong.summary.json' in f for f in first['findings']))
        top_actual.write_text('{"arbitrary":true}',encoding='utf-8')
        self.manifest[1]['sha256']=digest(top_actual)
        second=self.publish(first)
        self.assertEqual(second['files'],[])
        self.assertTrue(any('lacks converter signature' in f for f in second['findings']))

    def test_ambiguous_well_top_owner_rejected(self):
        top=self.add('tops','petrel_well_tops_ascii','.txt','tops/tops.csv')
        actual=self.package/'02_wells/well_tops/tops.csv';actual.parent.mkdir(parents=True,exist_ok=True)
        top.rename(actual)
        relative=actual.relative_to(self.package).as_posix()
        self.sources[0]['converted_files']=relative
        self.manifest[0].update(source_object_type='well_top',export_file=relative,
            source_object_path='File export/'+relative,export_format='CSV',export_status='exported',
            notes='Auto-registered from export package folder scan; no matching inventory row was found.')
        self.sources.append(dict(self.sources[0]))
        result=self.publish()
        self.assertEqual(result['files'],[])
        self.assertTrue(any('ambiguous source binding' in f for f in result['findings']))


if __name__=='__main__':unittest.main()
