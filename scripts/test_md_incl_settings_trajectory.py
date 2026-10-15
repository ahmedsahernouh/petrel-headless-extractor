# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Exact retained MD/inclination/azimuth settings profile, with raw angle output."""
import copy
import struct
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_petrel_native_spatial_zero_gui as d
from test_petrel_native_spatial_zero_gui import BASE_NAMES, dictionary
from test_petrel_native_recovery import element, vint

KIND = 'MdInclAzimTrajectoryProviderData'


def payload(mds=(0., 100.), versions=(0, 1), settings_version=1, reference=1, tolerance=.5):
    names = BASE_NAMES + [KIND, 'records', 'item', *d.TRAJECTORY_FIELDS[KIND]]
    records = bytearray(d.field_marker(names, 'records') + b'\x06\x08\x88' + struct.pack('<b', len(mds)))
    for md in mds:
        records += d.field_marker(names, 'item')
        for field, value in zip(d.TRAJECTORY_FIELDS[KIND], (md, .25, 1.5)):
            records += d.field_marker(names, field) + b'\x93' + struct.pack('<d', value)
        records += b'\x01'
    records += b'\x01'
    settings = element('settings', attrs={'Version': settings_version}, children=[
        element('need_add_extra_points', True), element('azimuth_reference', reference),
        element('interpolation_tolerance', tolerance)])
    body = element('data', attrs={'xmlns': BASE_NAMES[-1], 'Id': 1, 'Type': KIND},
                   children=[settings, bytes(records), *[element('Version', v) for v in versions]])
    return dictionary(names) + vint(len(body)) + body + b'\xa2'


class MdInclSettingsTests(unittest.TestCase):
    def test_exact_profile_preserves_stations_and_raw_settings_without_resampling(self):
        rows, evidence = d.decode_trajectory(payload(), KIND)
        self.assertEqual([r['md'] for r in rows], [0., 100.])
        self.assertEqual([r['inclination_raw'] for r in rows], [.25, .25])
        self.assertEqual([r['azimuth_raw'] for r in rows], [1.5, 1.5])
        self.assertNotIn('inclination', rows[0])
        self.assertNotIn('azimuth', rows[0])
        self.assertEqual(evidence['typed_envelope_profile'], 'md_incl_settings_v1_child_versions_0_1')
        self.assertEqual(evidence['native_settings']['azimuth_reference'], 1)
        self.assertTrue(evidence['native_settings']['need_add_extra_points'])
        self.assertIn('unresolved', evidence['angle_units_status'])
        self.assertIn('unresolved', evidence['native_settings_semantics'])
        self.assertEqual(evidence['trajectory_station_evidence']['angle_derivation'], 'none')

    def test_signed_decreasing_and_nonfinite_values_remain_rejected(self):
        for mds in ((-1., 10.), (10., 0.), (0., float('nan')), (0., float('inf'))):
            with self.subTest(mds=mds), self.assertRaises(d.DecodeError):
                d.decode_trajectory(payload(mds), KIND)

    def test_unknown_version_and_azimuth_codes_fail_closed(self):
        for options in ({'versions': (1, 0)}, {'versions': (0, 2)}, {'versions': (False, True)},
                        {'settings_version': 2}, {'settings_version': True}, {'reference': 2},
                        {'reference': True}, {'tolerance': -1.}, {'tolerance': float('inf')}):
            with self.subTest(options=options), self.assertRaises(d.DecodeError):
                d.decode_trajectory(payload(**options), KIND)

    def test_settings_and_envelope_ambiguity_rejected(self):
        raw = payload(); original = next(d.native_binary.read_documents(raw))
        mutations = [lambda n: n.attrs.update(Version=[1, 0, 1]),
                     lambda n: n.attrs.update(Id=True),
                     lambda n: n.children.append(copy.deepcopy(n.children[-1])),
                     lambda n: n.child('settings').children.append(copy.deepcopy(n.child('settings').children[0])),
                     lambda n: n.child('settings').attrs.update(extra=1),
                     lambda n: n.child('settings').child('need_add_extra_points').content.__setitem__(0, 1),
                     lambda n: n.child('records').attrs.update(extra=1),
                     lambda n: n.child('records').children[0].attrs.update(extra=1),
                     lambda n: n.child('records').children[0].child('md').attrs.update(extra=1),
                     lambda n: n.child('records').children[0].child('md').content.__setitem__(0, False)]
        for change in mutations:
            node = copy.deepcopy(original); change(node)
            with self.subTest(change=change), patch.object(d.native_binary, 'read_documents', return_value=iter([node])):
                with self.assertRaises(d.DecodeError): d.decode_trajectory(raw, KIND)

    def test_second_reader_disagreement_is_rejected(self):
        raw = payload(); node = next(d.native_binary.read_documents(raw))
        node.child('records').children[0].child('azimuth').content[0] = 1.6
        with patch.object(d.native_binary, 'read_documents', return_value=iter([node])):
            with self.assertRaisesRegex(d.DecodeError, 'values disagree'):
                d.decode_trajectory(raw, KIND)

    def test_truncation_trailing_document_and_declared_type_rejected(self):
        raw = payload()
        for value, kind in ((raw[:-2], KIND), (raw + raw, KIND), (raw, 'ExplicitTrajectoryProviderData')):
            with self.subTest(kind=kind), self.assertRaises(d.DecodeError):
                d.decode_trajectory(value, kind)


if __name__ == '__main__':
    unittest.main()
