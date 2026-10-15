"""Ambiguous native subject references must not select a trajectory's well name."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import export_petrel_native_spatial_zero_gui as decoder


def heads(names):
    return [dict(well_name=name, trajectory_provider_object_id='provider',
                 model_subject_offset=index, model_well_name_offset=10+index,
                 provider_uuid_offset=20+index, x=1., y=2.)
            for index, name in enumerate(names)]


class ProviderIdentityTests(unittest.TestCase):
    def test_report_counts_only_decoded_providers_as_mapped(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder); source = package / 'Data.ptd'
            with sqlite3.connect(source) as db:
                db.executescript('CREATE TABLE data (id INTEGER); CREATE TABLE blob_parts (id INTEGER);')
            db.close()
            obj = SimpleNamespace(object_id='provider', data_pk=1, name='survey', version=1,
                                  blob_type='ExplicitTrajectoryProviderData', timestamp='')
            head_rows = heads(['A']) + [dict(heads(['B'])[0], trajectory_provider_object_id='unavailable')]
            args = argparse.Namespace(export_package=folder, data_file=str(source),
                                      well_tops_validation_csv=None, validation_tolerance=.02)
            with patch.object(decoder, 'live_native_objects', return_value=[obj]), \
                 patch.object(decoder, 'object_blob', return_value=b''), \
                 patch.object(decoder, 'decompress_lz4_block', return_value=(b'', {})), \
                 patch.object(decoder, 'parse_initial_dictionary', return_value=([], 0)), \
                 patch.object(decoder, 'strip_dictionary_extensions', return_value=(b'', [])), \
                 patch.object(decoder, 'decode_trajectory', return_value=([dict(record_index=0, x=1., y=2., z=3.)], {})), \
                 patch.object(decoder, 'load_model_well_heads', return_value=(head_rows, {'status':'decoded'})), \
                 patch.object(decoder, 'trajectory_name_candidates', return_value=({}, {})):
                self.assertEqual(decoder.run(args), 0)
            report = json.loads((package / '07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json').read_text())
            linkage = report['trajectory_name_linkage']
            self.assertEqual(linkage['provider_objects_requested'], 1)
            self.assertEqual(linkage['native_exact_provider_objects_mapped'], 1)
            self.assertEqual(linkage['native_exact_provider_links_total'], 2)
            self.assertEqual(linkage['native_exact_provider_links_without_decoded_rows'], ['unavailable'])

    def test_ambiguous_provider_never_attaches_station_z(self):
        for names in [('A', 'B'), ('A', 'A')]:
            for x in (1., 10.):
                rows = heads(names)
                rows[0]['z'] = 77.  # Existing native head value must survive.
                before = copy.deepcopy(rows)
                station = [dict(object_id='provider', record_index=0, x=x, y=2., z=123.)]
                enriched, mapping, _ = decoder.enrich_well_heads_with_trajectory(rows, station, .02)
                self.assertEqual(mapping, {})
                self.assertEqual(rows, before)
                self.assertEqual(enriched[0]['z'], 77.)
                self.assertNotIn('z', enriched[1])
                self.assertTrue(all('ambiguous' in r['xy_trajectory_crosscheck_status'] for r in enriched))

    def test_multiple_subjects_never_pick_a_last_name(self):
        for names in [('A', 'B'), ('B', 'A'), ('A', 'A')]:
            with self.subTest(names=names):
                rows = heads(names); before = copy.deepcopy(rows)
                enriched, mapping, receipt = decoder.enrich_well_heads_with_trajectory(rows, [], .02)
                self.assertEqual(mapping, {})
                self.assertEqual(rows, before)
                self.assertEqual([r['well_name'] for r in enriched], list(names))
                self.assertEqual(len(receipt['ambiguous_provider_subjects']['provider']), 2)
                self.assertTrue(all(r['trajectory_provider_link_status'] == 'ambiguous_multiple_well_subjects'
                                    for r in enriched))

    def test_single_subject_still_maps_exactly(self):
        _, mapping, receipt = decoder.enrich_well_heads_with_trajectory(heads(['A']), [], .02)
        self.assertEqual(mapping['provider']['well_name'], 'A')
        self.assertEqual(receipt['ambiguous_provider_subjects'], {})

    def test_pipeline_excludes_ambiguous_provider_from_proximity_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder); source = package/'Data.ptd'
            with sqlite3.connect(source) as db:
                db.executescript('CREATE TABLE data (id INTEGER); CREATE TABLE blob_parts (id INTEGER);')
            db.close()
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            obj = SimpleNamespace(object_id='provider', data_pk=1, name='survey', version=1,
                                  blob_type='ExplicitTrajectoryProviderData', timestamp='')
            args = argparse.Namespace(export_package=folder, data_file=str(source),
                                      well_tops_validation_csv=None, validation_tolerance=.02)
            with patch.object(decoder, 'live_native_objects', return_value=[obj]), \
                 patch.object(decoder, 'object_blob', return_value=b''), \
                 patch.object(decoder, 'decompress_lz4_block', return_value=(b'', {})), \
                 patch.object(decoder, 'parse_initial_dictionary', return_value=([], 0)), \
                 patch.object(decoder, 'strip_dictionary_extensions', return_value=(b'', [])), \
                 patch.object(decoder, 'decode_trajectory', return_value=([dict(record_index=0, x=1., y=2., z=3.)], {})), \
                 patch.object(decoder, 'load_model_well_heads', return_value=(heads(['A', 'B']), {'status':'decoded'})), \
                 patch.object(decoder, 'trajectory_name_candidates', return_value=({}, {})) as fallback:
                self.assertEqual(decoder.run(args), 0)
            self.assertEqual(fallback.call_args.args[1], [])
            # Discover the one trajectory table by its declared folder, not a guessed basename.
            tables = list((package/'02_wells/trajectories').glob('*.csv'))
            self.assertEqual(len(tables), 1)
            with tables[0].open(newline='', encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row['well_name'], '')
            self.assertEqual(row['well_name_mapping_status'], 'ambiguous_multiple_well_subjects')
            self.assertEqual(float(row['x']), 1.)
            receipt = json.loads((package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json').read_text())
            self.assertIn('provider', receipt['trajectory_name_linkage']['ambiguous_provider_subjects'])
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
