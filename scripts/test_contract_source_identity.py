# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Source revision fingerprints must not select among ambiguous input roles."""
import unittest

from geoviewer_contract import _source_identity


class SourceIdentityTests(unittest.TestCase):
    def receipt(self, hashes):
        return dict(source_hashes_before=hashes, source_hashes_after=dict(hashes),
                    source_unchanged=True)

    def test_duplicate_pet_roles_never_choose_first_even_when_hashes_match(self):
        for other in ('a' * 64, 'd' * 64):
            hashes = {'D:/A/first.pet': 'a' * 64, 'D:/A/other.pet': other,
                      'D:/A/Model.ptd': 'b' * 64, 'D:/A/Data.ptd': 'c' * 64}
            for ordered in (hashes, dict(reversed(list(hashes.items())))):
                with self.subTest(other=other, first=next(iter(ordered))):
                    with self.assertRaisesRegex(ValueError, 'Ambiguous source hash role'):
                        _source_identity(self.receipt(ordered), {})

    def test_case_variant_role_cannot_hide_duplicate(self):
        hashes = {'D:/A/first.pet': 'a' * 64, 'D:/A/Model.ptd': 'b' * 64,
                  'D:/A/MODEL.PTD': 'b' * 64, 'D:/A/Data.ptd': 'c' * 64}
        with self.assertRaisesRegex(ValueError, 'Ambiguous source hash role'):
            _source_identity(self.receipt(hashes), {})

    def test_windows_role_spelling_and_relocation_do_not_change_revision(self):
        hashes = {'D:/A/a.pet': 'a' * 64, 'D:/A/Model.ptd': 'b' * 64,
                  'D:/A/Data.ptd': 'c' * 64}
        relocated = {'E:\\Moved\\renamed.PET': 'A' * 64,
                     'E:\\Moved\\MODEL.PTD': 'B' * 64,
                     'E:\\Moved\\DATA.PTD': 'C' * 64}
        self.assertEqual(_source_identity(self.receipt(hashes), {}),
                         _source_identity(self.receipt(relocated), {}))

    def test_shared_native_project_subject_never_overrides_source_revision(self):
        # Same ProjectSubject tag observed in three separately retained projects.
        context = {'objects': [{'object_id': '1d9a2dd1-dd1d-4676-a92e-6057e64d33c2',
                                'category': 'ProjectSubject'}]}
        hashes = {'a.pet': 'a' * 64, 'Model.ptd': 'b' * 64, 'Data.ptd': 'c' * 64}
        changed = dict(hashes, **{'a.pet': 'd' * 64})
        first = _source_identity(self.receipt(hashes), context)
        second = _source_identity(self.receipt(changed), context)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], 'verified_pet_model_data_receipt_hashes')

    def test_incomplete_receipt_stays_explicitly_unresolved(self):
        result = _source_identity(self.receipt({'a.pet': 'a' * 64}), {})
        self.assertEqual(result[1], 'content_only_unresolved_source_revision')
        self.assertEqual(result[2], {})


if __name__ == '__main__':
    unittest.main()
