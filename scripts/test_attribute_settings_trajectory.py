# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Attribute-Version settings trajectory acceptance and rejection boundaries."""
import copy
import struct
import unittest
from unittest.mock import patch
import export_petrel_native_spatial_zero_gui as d
from test_petrel_native_spatial_zero_gui import BASE_NAMES, dictionary
from test_petrel_native_recovery import element, vint
from test_md_incl_settings_trajectory import payload as child_payload

KIND='MdInclAzimTrajectoryProviderData'

def payload(mds=(0.,100.), reference=0, version=(1,0,1), settings_version=1, tolerance=.5):
    names=BASE_NAMES+[KIND,'records','item',*d.TRAJECTORY_FIELDS[KIND]]
    records=bytearray(d.field_marker(names,'records')+b'\x06\x08\x88'+struct.pack('<b',len(mds)))
    for md in mds:
        records+=d.field_marker(names,'item')
        for field,value in zip(d.TRAJECTORY_FIELDS[KIND],(md,.25,1.5)):
            records+=d.field_marker(names,field)+b'\x93'+struct.pack('<d',value)
        records+=b'\x01'
    records+=b'\x01'
    settings=element('settings',attrs={'Version':settings_version},children=[element('need_add_extra_points',True),element('azimuth_reference',reference),element('interpolation_tolerance',tolerance)])
    body=element('data',attrs={'xmlns':BASE_NAMES[-1],'Id':1,'Type':KIND,'Version':list(version)},children=[settings,bytes(records)])
    return dictionary(names)+vint(len(body))+body+b'\xa2'

class AttributeSettingsTests(unittest.TestCase):
    def test_two_observed_codes_preserve_raw_values_without_physical_claims(self):
        for code in (0,1):
            with self.subTest(code=code):
                rows,evidence=d.decode_trajectory(payload(reference=code),KIND)
                self.assertEqual([r['md'] for r in rows],[0.,100.])
                self.assertEqual([r['inclination_raw'] for r in rows],[.25,.25])
                self.assertEqual([r['azimuth_raw'] for r in rows],[1.5,1.5])
                self.assertNotIn('inclination',rows[0]);self.assertNotIn('azimuth',rows[0])
                self.assertEqual(evidence['native_settings']['azimuth_reference'],code)
                self.assertEqual(evidence['typed_envelope_profile'],'md_incl_settings_v1_attribute_version_1_0_1')
                self.assertIn('unresolved',evidence['angle_units_status'])
                self.assertEqual(evidence['trajectory_station_evidence']['angle_derivation'],'none')

    def test_unknown_versions_codes_and_invalid_settings_are_rejected(self):
        for args in ({'version':(1,0,2)},{'version':(True,False,True)},{'settings_version':2},
                     {'settings_version':True},{'reference':2},{'reference':-1},{'reference':True},
                     {'tolerance':-1.},{'tolerance':float('nan')},{'tolerance':float('inf')}):
            with self.subTest(args=args),self.assertRaises(d.DecodeError):d.decode_trajectory(payload(**args),KIND)

    def test_unknown_child_profile_code_is_not_enabled_by_attribute_profile(self):
        with self.assertRaises(d.DecodeError):d.decode_trajectory(child_payload(reference=0),KIND)

    def test_negative_decreasing_nonfinite_and_large_md_are_rejected(self):
        for mds in ((-1.,10.),(10.,0.),(0.,float('nan')),(0.,float('inf')),(0.,1e31)):
            with self.subTest(mds=mds),self.assertRaises(d.DecodeError):d.decode_trajectory(payload(mds),KIND)

    def test_ambiguous_shape_types_and_reader_disagreement_are_rejected(self):
        raw=payload();original=next(d.native_binary.read_documents(raw))
        changes=[lambda n:n.attrs.update(Id=True),lambda n:n.attrs.update(extra=1),
                 lambda n:n.children.append(copy.deepcopy(n.child('settings'))),
                 lambda n:n.children.reverse(),lambda n:n.content.append('extra'),
                 lambda n:n.child('settings').children.append(copy.deepcopy(n.child('settings').children[0])),
                 lambda n:n.child('settings').attrs.update(extra=1),
                 lambda n:n.child('settings').child('need_add_extra_points').content.__setitem__(0,1),
                 lambda n:n.child('records').attrs.update(Size=3),lambda n:n.child('records').attrs.update(extra=1),
                 lambda n:n.child('records').children[0].attrs.update(extra=1),
                 lambda n:n.child('records').children[0].child('md').attrs.update(extra=1),
                 lambda n:n.child('records').children[0].child('md').content.__setitem__(0,False),
                 lambda n:n.child('records').children[0].child('azimuth').content.__setitem__(0,1.6)]
        for change in changes:
            node=copy.deepcopy(original);change(node)
            with self.subTest(change=change),patch.object(d.native_binary,'read_documents',return_value=iter([node])):
                with self.assertRaises(d.DecodeError):d.decode_trajectory(raw,KIND)

    def test_truncated_multiple_documents_and_wrong_declared_type_rejected(self):
        raw=payload()
        for data,kind in ((raw[:-2],KIND),(raw+raw,KIND),(raw,'ExplicitTrajectoryProviderData')):
            with self.subTest(kind=kind),self.assertRaises(d.DecodeError):d.decode_trajectory(data,kind)

if __name__=='__main__':unittest.main()
