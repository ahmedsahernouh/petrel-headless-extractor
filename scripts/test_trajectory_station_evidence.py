# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Native station statistics must preserve values and unresolved physical roles."""
from copy import deepcopy
import unittest
from export_petrel_native_spatial_zero_gui import trajectory_station_evidence, DecodeError
import test_geoviewer_contract as fixtures
from geoviewer_contract import POINTER
from validate_geoviewer_contract import validate_contract


def rows(depths):
    return [dict(record_index=i,md=x,inclination=.2,azimuth=.4) for i,x in enumerate(depths)]


class StationEvidenceTests(unittest.TestCase):
    def test_signed_repeated_irregular_stations_preserved(self):
        r=rows([-2.,0.,0.,5.,10.]);before=deepcopy(r)
        q=trajectory_station_evidence(r)
        self.assertEqual(r,before)
        self.assertEqual((q['negative_md_stations'],q['adjacent_equal_md_pairs'],q['adjacent_pair_count']),(1,1,4))
        self.assertFalse(q['md_strictly_increasing']);self.assertTrue(q['md_nondecreasing'])
        self.assertFalse(q['md_spacing_exactly_uniform'])
        self.assertEqual((q['md_step_min'],q['md_step_max']),(0.,5.))
        self.assertTrue(q['gap_status'].startswith('not_inferred'))
        self.assertEqual(q['azimuth_reference'],'unresolved')

    def test_decrease_reported_even_below_decoder_tolerance(self):
        q=trajectory_station_evidence(rows([1.,1.-1e-12]))
        self.assertEqual(q['adjacent_decreasing_md_pairs'],1)
        self.assertFalse(q['md_nondecreasing'])

    def test_empty_and_single_are_not_monotonicity_evidence(self):
        for r in ([],rows([0.])):
            q=trajectory_station_evidence(r)
            self.assertIsNone(q['md_nondecreasing']);self.assertIsNone(q['md_spacing_exactly_uniform'])
            self.assertEqual(q['adjacent_pair_count'],0)

    def test_uniform_spacing_is_only_exact_numeric_description(self):
        q=trajectory_station_evidence(rows([0.,2.,4.]))
        self.assertTrue(q['md_spacing_exactly_uniform']);self.assertTrue(q['md_strictly_increasing'])
        self.assertEqual(q['md_reference'],'unresolved')

    def test_legacy_raw_angles_and_stored_md_not_path_distance(self):
        r=rows([0.,10.,20.])
        for v in r:v.update(x=1.,y=2.,z=3.,derived_cumulative_3d_distance=0.)
        q=trajectory_station_evidence(r,legacy=True)
        self.assertEqual(q['xyz_station_count'],3)
        self.assertEqual(q['md_max'],20.)
        self.assertEqual(q['native_angle_units'],'unresolved_raw_legacy_values')
        self.assertEqual(q['angle_derivation'],'none')

    def test_invalid_md_or_order_rejected_without_repair(self):
        for r in (rows([float('nan')]),rows([float('inf')]),rows([True]),rows([-1e308,1e308]),
                  [dict(record_index=1,md=0.)],[dict(record_index=True,md=0.)]):
            with self.subTest(r=r),self.assertRaises(DecodeError):trajectory_station_evidence(r)

    def test_contract_preserves_evidence_without_track_qualification(self):
        fx=fixtures.ContractTests();fx.setUp();self.addCleanup(fx.doCleanups)
        q=trajectory_station_evidence(rows([0.,2.,4.]))
        spatial=dict(objects=[dict(object_id='provider-a',blob_type='ExplicitTrajectoryProviderData',
            status='decoded',trajectory_station_evidence=q)])
        fixtures.write(fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',spatial)
        fx.add_row('provider-a','trajectory.csv',b'md,inclination,azimuth\n0,0.2,0.4\n','CSV')
        fx.rows[-1]['category']='trajectories';fx.publish()
        e=next(x for x in fx.table('metadata_evidence') if x['field']=='trajectory_station_evidence')
        self.assertEqual(e['raw_value'],q)
        assessment=validate_contract(fx.exports/POINTER,'opendtect')
        self.assertEqual(assessment['counts']['receiving_qualified'],0)

    def test_raw_settings_survive_catalogue_without_semantic_qualification(self):
        for code in (0, 1):
            with self.subTest(raw_azimuth_code=code):
                fx=fixtures.ContractTests();fx.setUp();self.addCleanup(fx.doCleanups)
                report=fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
                obj=dict(object_id='provider-a',blob_type='MdInclAzimTrajectoryProviderData',
                    status='decoded',angle_units_status='unresolved_native_settings',
                    trajectory_station_evidence=trajectory_station_evidence(rows([0.,2.,4.])))
                fixtures.write(report,dict(objects=[obj]))
                fx.add_row('provider-a','trajectory.csv',b'md,inclination_raw,azimuth_raw\n0,0.2,0.4\n','CSV')
                fx.rows[-1]['category']='trajectories';fx.publish()
                before={p:validate_contract(fx.exports/POINTER,p) for p in ('source','opendtect','paleoscan')}
                settings=dict(need_add_extra_points=True,azimuth_reference=code,interpolation_tolerance=.5)
                obj.update(native_settings=settings,
                    native_settings_semantics='unresolved; stored declarations only, no interpolation or azimuth conversion',
                    typed_envelope_profile='md_incl_settings_v1_attribute_version_1_0_1')
                fixtures.write(report,dict(objects=[obj]));fx.publish()
                evidence=fx.table('metadata_evidence')
                for field in ('native_settings','native_settings_semantics','typed_envelope_profile'):
                    item=next(x for x in evidence if x['field']==field)
                    self.assertEqual(item['raw_value'],obj[field])
                    self.assertTrue(item['locator'].endswith('/'+field))
                    self.assertIn('no semantic calibration',item['normalization_scope'])
                item=next(x for x in evidence if x['field']=='native_settings')
                self.assertIs(type(item['raw_value']['need_add_extra_points']),bool)
                self.assertIs(type(item['raw_value']['azimuth_reference']),int)
                self.assertEqual(item['evidence_status'],'recorded_source_value')
                for p in before:
                    after=validate_contract(fx.exports/POINTER,p)
                    self.assertEqual(after['counts'],before[p]['counts'])
                    self.assertEqual(after['counts']['receiving_qualified'],0)


if __name__=='__main__':unittest.main()
