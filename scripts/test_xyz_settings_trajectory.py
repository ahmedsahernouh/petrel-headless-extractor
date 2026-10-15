# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Stored XYZ stations have no measured-depth or angle arrays to infer."""
import copy
import math
import struct
import unittest
from unittest.mock import patch

import export_petrel_native_spatial_zero_gui as d
from test_petrel_native_spatial_zero_gui import BASE_NAMES, dictionary
from test_petrel_native_recovery import element, vint
import test_geoviewer_contract as fixtures
from geoviewer_contract import POINTER
from validate_geoviewer_contract import validate_contract

KIND = 'XYZTrajectoryProviderData'


def payload(points=((10., 20., 30.), (13., 24., 30.)), *, settings=True,
            version=(1, 0, 1), settings_version=1, algorithm=1, first_md=0,
            calculated=True):
    names = BASE_NAMES + [KIND, 'records', 'item', 'x', 'y', 'z']
    records = bytearray(d.field_marker(names, 'records') + b'\x06\x08\x88' + struct.pack('<b', len(points)))
    for point in points:
        records += d.field_marker(names, 'item')
        for field, value in zip(('x', 'y', 'z'), point):
            records += d.field_marker(names, field) + b'\x93' + struct.pack('<d', value)
        records += b'\x01'
    records += b'\x01'
    children = []
    if settings:
        children.append(element('settings', attrs={'Version': settings_version}, children=[
            element('calculation_algorithm', algorithm), element('first_md', first_md),
            element('is_first_md_calculated', calculated)]))
    children.append(bytes(records))
    body = element('data', attrs={'xmlns': BASE_NAMES[-1], 'Id': 1, 'Type': KIND,
                                 'Version': list(version)}, children=children)
    return dictionary(names) + vint(len(body)) + body + b'\xa2'


class XyzSettingsTests(unittest.TestCase):
    def test_stored_xyz_and_distance_are_not_md_or_survey_angles(self):
        rows, evidence = d.decode_trajectory(payload(), KIND)
        self.assertEqual([(x['x'], x['y'], x['z']) for x in rows], [(10., 20., 30.), (13., 24., 30.)])
        self.assertEqual([x['derived_cumulative_3d_distance'] for x in rows], [0., 5.])
        self.assertTrue(all('md' not in x and 'inclination' not in x and 'azimuth' not in x for x in rows))
        self.assertEqual(evidence['native_settings'], dict(calculation_algorithm=1, first_md=0, is_first_md_calculated=True))
        self.assertEqual(evidence['typed_envelope_profile'], 'xyz_settings_v1_attribute_version_1_0_1')
        self.assertIn('not stored', evidence['md_reference_status'])
        q = evidence['trajectory_station_evidence']
        self.assertEqual(q['md_source'], 'not_stored_in_xyz_provider')
        for key in ('md_min', 'md_max', 'md_strictly_increasing', 'md_nondecreasing', 'md_spacing_exactly_uniform', 'negative_md_stations', 'adjacent_decreasing_md_pairs'):
            self.assertIsNone(q[key])
        self.assertEqual((q['station_count'], q['xyz_station_count']), (2, 2))
        self.assertEqual(q['angle_derivation'], 'none')
        self.assertEqual(q['native_angle_units'], 'not_stored_in_xyz_provider')

    def test_preexisting_settings_free_xyz_profile_also_has_no_stored_md(self):
        rows, evidence = d.decode_trajectory(payload(settings=False), KIND)
        self.assertNotIn('native_settings', evidence)
        self.assertEqual(evidence['trajectory_station_evidence']['md_source'], 'not_stored_in_xyz_provider')
        self.assertNotIn('md', rows[0])

    def test_empty_single_and_repeated_xyz_do_not_establish_md(self):
        for points in ((), ((0., 0., 0.),), ((0., 0., 0.), (0., 0., 0.))):
            with self.subTest(points=points):
                rows, evidence = d.decode_trajectory(payload(points), KIND)
                self.assertEqual(len(rows), len(points))
                self.assertIsNone(evidence['trajectory_station_evidence']['md_nondecreasing'])

    def test_unobserved_settings_and_versions_stay_rejected(self):
        for options in ({'version': (1, 0, 2)}, {'version': (True, False, True)},
                        {'settings_version': 2}, {'settings_version': True},
                        {'algorithm': 0}, {'algorithm': True}, {'algorithm': 1.},
                        {'first_md': 1}, {'first_md': True}, {'first_md': float('nan')},
                        {'first_md': float('inf')}, {'calculated': False}, {'calculated': 1}):
            with self.subTest(options=options), self.assertRaises(d.DecodeError):
                d.decode_trajectory(payload(**options), KIND)

    def test_invalid_coordinates_rejected(self):
        for bad in (float('nan'), float('inf'), -float('inf'), 1e30, d.NATIVE_FLOAT_MAX_SENTINEL):
            for axis in range(3):
                point = [1., 2., 3.]; point[axis] = bad
                with self.subTest(bad=bad, axis=axis), self.assertRaises(d.DecodeError):
                    d.decode_trajectory(payload((point,)), KIND)

    def test_extra_duplicate_reordered_or_conflicting_fields_rejected(self):
        raw = payload(); original = next(d.native_binary.read_documents(raw))
        changes = [lambda n: n.attrs.update(Id=True), lambda n: n.attrs.update(extra=1),
                   lambda n: n.attrs.update(xmlns='urn:unknown'), lambda n: n.content.append('extra'),
                   lambda n: n.children.reverse(), lambda n: n.children.append(copy.deepcopy(n.child('settings'))),
                   lambda n: n.child('settings').children.reverse(),
                   lambda n: n.child('settings').attrs.update(extra=1),
                   lambda n: n.child('settings').children.append(copy.deepcopy(n.child('settings').children[0])),
                   lambda n: n.child('records').attrs.update(Size=3),
                   lambda n: n.child('records').attrs.update(extra=1),
                   lambda n: n.child('records').children[0].attrs.update(extra=1),
                   lambda n: n.child('records').children[0].child('x').attrs.update(extra=1),
                   lambda n: n.child('records').children[0].child('x').content.__setitem__(0, True),
                   lambda n: n.child('records').children[0].child('x').content.__setitem__(0, 99.)]
        for change in changes:
            node = copy.deepcopy(original); change(node)
            with self.subTest(change=change), patch.object(d.native_binary, 'read_documents', return_value=iter([node])):
                with self.assertRaises(d.DecodeError): d.decode_trajectory(raw, KIND)

    def test_truncated_multiple_documents_and_wrong_declared_type_rejected(self):
        raw = payload()
        for data, kind in ((raw[:-2], KIND), (raw+raw, KIND), (raw, 'ExplicitTrajectoryProviderData')):
            with self.subTest(kind=kind), self.assertRaises(d.DecodeError): d.decode_trajectory(data, kind)

    def test_xyz_metadata_mode_is_explicit_and_rejects_mixed_station_fields(self):
        row = dict(record_index=0, x=1., y=2., z=3.)
        with self.assertRaises(d.DecodeError): d.trajectory_station_evidence([row])
        for extras in ({'md': 1.}, {'inclination_raw': 1.}, {'record_index': True}, {'z': math.inf}):
            with self.subTest(extras=extras), self.assertRaises(d.DecodeError):
                d.trajectory_station_evidence([{**row, **extras}], xyz_only=True)

    def test_catalogue_keeps_raw_settings_and_absent_md_unqualified(self):
        fx = fixtures.ContractTests(); fx.setUp(); self.addCleanup(fx.doCleanups)
        rows, evidence = d.decode_trajectory(payload(), KIND)
        obj = dict(object_id='provider-xyz', blob_type=KIND, status='decoded', **evidence)
        fixtures.write(fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json', dict(objects=[obj]))
        fx.add_row('provider-xyz', 'trajectory.csv', b'x,y,z,md\n10,20,30,\n13,24,30,\n', 'CSV')
        fx.rows[-1]['category'] = 'trajectories'; fx.publish()
        values = {x['field']: x['raw_value'] for x in fx.table('metadata_evidence')}
        self.assertEqual(values['native_settings'], evidence['native_settings'])
        self.assertEqual(values['trajectory_station_evidence']['md_source'], 'not_stored_in_xyz_provider')
        for profile in ('source', 'opendtect', 'paleoscan'):
            self.assertEqual(validate_contract(fx.exports/POINTER, profile)['counts']['receiving_qualified'], 0)


if __name__ == '__main__': unittest.main()
