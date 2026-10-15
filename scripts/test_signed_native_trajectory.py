# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Signed native MD recovery preserves data and requires a second typed reading."""
import struct
import unittest
from unittest.mock import patch
import export_petrel_native_spatial_zero_gui as d
from test_petrel_native_spatial_zero_gui import BASE_NAMES, dictionary
from test_petrel_native_recovery import element, vint


def payload(mds, version=None, declared_type=None, namespace=None, duplicate_records=False,
            child_versions=None):
    kind='ExplicitTrajectoryProviderData'
    names=BASE_NAMES+[kind,'records','item',*d.TRAJECTORY_FIELDS[kind]]
    records=bytearray(d.field_marker(names,'records')+b'\x06\x08\x88'+struct.pack('<b',len(mds)))
    for i,md in enumerate(mds):
        records+=d.field_marker(names,'item')
        for field in d.TRAJECTORY_FIELDS[kind]:
            value=md if field=='md' else float(i)
            records+=d.field_marker(names,field)+b'\x93'+struct.pack('<d',value)
        records+=b'\x01'
    records+=b'\x01'
    attrs={'Type':declared_type or kind,'Version':version or [1,0,1],'xmlns':namespace or BASE_NAMES[-1]}
    children=[bytes(records)]*(2 if duplicate_records else 1)
    if child_versions is not None:
        del attrs['Version']
        attrs['Id']=1
        children += [element('Version',v) for v in child_versions]
    doc=element('data',children=children,attrs=attrs)
    return dictionary(names)+vint(len(doc))+doc+b'\xa2'


class SignedTrajectoryTests(unittest.TestCase):
    def test_observed_child_versions_preserve_nonnegative_records(self):
        rows,evidence=d.decode_trajectory(payload([0.,100.],child_versions=[0,1]),'ExplicitTrajectoryProviderData')
        self.assertEqual([r['md'] for r in rows],[0.,100.])
        self.assertEqual(evidence['typed_envelope_profile'],'explicit_child_versions_0_1')

    def test_child_version_profile_is_exact_and_signed_scope_unchanged(self):
        for versions in ([1,0],[0],[0,1,1],[0,2],[False,True],[0.,1.]):
            with self.subTest(versions=versions), self.assertRaises(d.DecodeError):
                d.decode_trajectory(payload([0.,100.],child_versions=versions),'ExplicitTrajectoryProviderData')
        for mds in ([-1.,100.],[100.,0.],[0.,float('nan')]):
            with self.subTest(mds=mds), self.assertRaises(d.DecodeError):
                d.decode_trajectory(payload(mds,child_versions=[0,1]),'ExplicitTrajectoryProviderData')

    def test_child_version_profile_rejects_extra_or_conflicting_envelope_data(self):
        import copy
        raw=payload([0.,100.],child_versions=[0,1])
        original=list(d.native_binary.read_documents(raw))[0]
        changes=[lambda n:n.attrs.update(Version=[99]),lambda n:n.attrs.update(Id=True),
                 lambda n:n.attrs.update(Type='XYZTrajectoryProviderData'),
                 lambda n:n.attrs.update(xmlns='urn:unknown'),lambda n:n.content.append(1),
                 lambda n:n.children.append(copy.deepcopy(n.children[-1])),
                 lambda n:n.children[1].attrs.update(extra=1),
                 lambda n:n.children[1].children.append(copy.deepcopy(n.children[-1])),
                 lambda n:n.child('records').attrs.update(Size=3),
                 lambda n:n.child('records').children[0].child('md').content.__setitem__(0,2.)]
        for change in changes:
            node=copy.deepcopy(original);change(node)
            with patch.object(d.native_binary,'read_documents',return_value=iter([node])):
                with self.assertRaises(d.DecodeError):
                    d.decode_trajectory(raw,'ExplicitTrajectoryProviderData')

    def test_both_md_signs_require_declared_type_version_namespace(self):
        for mds in ([0., 100.], [-1., 100.]):
            for options in ({'version':[99]}, {'declared_type':'UnknownPayload'},
                            {'namespace':'urn:unknown'}, {'duplicate_records':True}):
                with self.subTest(mds=mds,options=options):
                    with self.assertRaises(d.DecodeError):
                        d.decode_trajectory(payload(mds,**options),'ExplicitTrajectoryProviderData')

    def test_trailing_document_and_truncation_rejected(self):
        good=payload([0.,100.])
        for bad in (good+good, good[:-2]):
            with self.assertRaises(d.DecodeError):
                d.decode_trajectory(bad,'ExplicitTrajectoryProviderData')

    def test_signed_monotonic_values_are_exact_and_reference_unknown(self):
        rows,evidence=d.decode_trajectory(payload([-80.,687.,1200.]),'ExplicitTrajectoryProviderData')
        self.assertEqual([r['md'] for r in rows],[-80.,687.,1200.])
        self.assertEqual(evidence['negative_md_records'],1)
        self.assertIn('independent_typed_readback_exact',evidence['md_validation'])
        self.assertIn('unresolved',evidence['md_reference_status'])

    def test_decreasing_md_remains_rejected(self):
        with self.assertRaisesRegex(d.DecodeError,'MD decreases'):
            d.decode_trajectory(payload([-80.,687.,600.]),'ExplicitTrajectoryProviderData')

    def test_unobserved_signed_profile_remains_rejected(self):
        with self.assertRaisesRegex(d.DecodeError,'not independently validated'):
            d.decode_trajectory(payload([-80.,687.],[99]),'ExplicitTrajectoryProviderData')

    def test_second_reader_disagreement_cannot_be_bypassed(self):
        raw=payload([-80.,687.]); document=list(d.native_binary.read_documents(raw))[0]
        document.child('records').children[0].child('md').content[0]=-81.
        with patch.object(d.native_binary,'read_documents',return_value=iter([document])):
            with self.assertRaisesRegex(d.DecodeError,'values disagree'):
                d.decode_trajectory(raw,'ExplicitTrajectoryProviderData')

    def test_nonnegative_legacy_path_is_unchanged(self):
        with patch.object(d,'signed_md_crosscheck',side_effect=AssertionError('unneeded')):
            rows,evidence=d.decode_trajectory(payload([0.,100.]),'ExplicitTrajectoryProviderData')
        self.assertEqual([r['md'] for r in rows],[0.,100.])
        self.assertNotIn('negative_md_records',evidence)


if __name__=='__main__':unittest.main()
