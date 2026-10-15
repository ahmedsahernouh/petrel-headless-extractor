# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Exact own-limit controls for one observed legacy SurfaceSubject profile."""
from contextlib import closing
from pathlib import Path
import csv
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import petrel_native_binary as b
import petrel_native_recovery as r
import test_petrel_native_recovery as fixtures


class LegacySurfaceLimitV10Tests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    fixture = fixtures.RecoveryTests.fixture

    def recover(self, **kwargs):
        tamper_identity = kwargs.pop('tamper_identity', False)
        base_root = self.root
        self._case_number = getattr(self, '_case_number', 0) + 1
        self.root = base_root/str(self._case_number)
        self.root.mkdir()
        options = dict(kind='RegValGrid2', surface=True, metric=False,
                       model_version=r.SURFACE_LIMIT_V10_2_20,
                       doc=fixtures.surface_doc(legacy=True),
                       limit_profile=True, force_negative=False)
        options.update(kwargs)
        try:
            package = self.fixture(**options)
        finally:
            self.root = base_root
        native = package/'08_native_project'
        metadata = r.Metadata(native/'project_file/test.pet', native/'ptd_store/Model.ptd')
        if tamper_identity:
            metadata.stubs[self.ids['surface']]['kind'] = 'SurfaceAttrSubject'
        with closing(sqlite3.connect(b.sqlite_readonly_uri(native/'ptd_store/Data.ptd')+'&immutable=1', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            blob = r.read_blob(db, 1)
        return r.recover_object(blob, self.ids['surface'], 'RegValGrid2', metadata, package/'native_data'), package

    def test_exact_profile_and_open_format_readback(self):
        record, package = self.recover()
        self.assertEqual(record['status'], 'missing_metadata')
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['numeric_round_trip'], 'exact')
        self.assertEqual(record['model_bounds_source'], 'own_limit')
        self.assertEqual(record['model_bounds_check'], 'passed')
        self.assertEqual(record['model_bounds_comparison'], 'exact_float64_equality_all_six_defined_node_xyz_extrema')
        self.assertIn('SurfaceSubject_10_2', record['model_bounds_profile'])
        self.assertIsNone(record['unit'])
        self.assertEqual(record['crs_status'], 'unresolved')
        root = package/'native_data'
        xyz = np.loadtxt(next(root.rglob('surface.xyz')), ndmin=2)
        np.testing.assert_array_equal(xyz, [[100,200,1],[120,200,2],[140,200,3],[100,230,4],[120,230,5]])
        with next(root.rglob('nodes.csv')).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(row['defined'] == '1' for row in rows), 5)
        zmap = next(root.rglob('surface.zmap')).read_text(encoding='utf-8')
        self.assertIn('2, 3,', zmap)
        for artifact in record['artifacts']:
            self.assertEqual(r.sha(root/artifact['path']), artifact['sha256'])

    def test_adjacent_model_version_rejected(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'bounds layout'):
            self.recover(model_version=[10, 2, 13, 1, 2, 20, 0, 0, 2])

    def test_missing_limit_rejected(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'own limit'):
            self.recover(omit_bounds=True)

    def test_existing_cached_limit_path_is_preserved(self):
        record, _ = self.recover(limit_profile=False)
        self.assertEqual(record['model_bounds_source'], 'own_cached_limit')
        self.assertEqual(record['model_bounds_check'], 'passed')
        self.assertNotIn('model_bounds_profile', record)
        self.assertNotIn('model_bounds_comparison', record)

    def test_wrong_payload_and_context_rejected(self):
        for doc in (fixtures.surface_doc(), fixtures.surface_doc(legacy=True, context=False)):
            with self.subTest(doc=doc[:40]), self.assertRaisesRegex(r.MissingMetadata, 'exact V0'):
                self.recover(doc=doc)

    def test_forced_negative_flag_requires_explicit_false_boolean(self):
        for value in (None, True, 0):
            with self.subTest(value=value), self.assertRaisesRegex(r.MissingMetadata, 'explicit false'):
                self.recover(force_negative=value)

    def test_rejection_identifies_true_flag_instead_of_implying_missing_identity(self):
        with self.assertRaises(r.MissingMetadata) as caught:
            self.recover(force_negative=True)
        self.assertIn('rejected: used_force_negative_depth=True (bool)', str(caught.exception))
        self.assertNotIn('own limit absent', str(caught.exception))
        self.assertNotIn('has_coordinate_context=', str(caught.exception))

    def test_bounds_arrays_sentinels_and_exact_mismatch_rejected(self):
        bad = [([100.,200.],[140.,230.,5.]),
               ([r.FLOAT_NULL]*3,[r.FLOAT_NULL]*3),
               ([100.,200.,1.],[140.,230.,r.FLOAT_NULL]),
               ([100.,200.,1.],[float('inf'),230.,5.]),
               ([np.nextafter(100.,101.),200.,1.],[140.,230.,5.]),
               ([100.,200.,1.],[140.,230.,np.nextafter(5.,6.)]),
               ([99.,199.,0.],[141.,231.,6.])]
        for lo, hi in bad:
            with self.subTest(lo=lo, hi=hi), self.assertRaises(b.NativeError):
                self.recover(limits=(lo,hi))

    def test_mask_and_identity_controls(self):
        with self.assertRaises(b.NativeError):
            self.recover(doc=fixtures.surface_doc(legacy=True, mask=b'\xff\xff'))
        with self.assertRaises(r.MissingMetadata):
            self.recover(tamper_identity=True)


if __name__ == '__main__':
    unittest.main()
