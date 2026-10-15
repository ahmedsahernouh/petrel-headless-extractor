# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Exact own-cache controls for the observed modern SurfaceSubject profile."""
from pathlib import Path
import sqlite3
import unittest
from contextlib import closing

import numpy as np
import petrel_native_binary as b
import petrel_native_recovery as r
import test_petrel_native_recovery as fixtures


class ModernSurfaceOwnContextTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    fixture = fixtures.RecoveryTests.fixture

    def recover(self, **kwargs):
        options = dict(kind='RegValGrid2', surface=True, metric=False,
                       model_version=r.SURFACE_OWN_CONTEXT_V11,
                       doc=fixtures.surface_doc(context=False))
        options.update(kwargs)
        package = self.fixture(**options)
        native = package/'08_native_project'
        metadata = r.Metadata(native/'project_file/test.pet', native/'ptd_store/Model.ptd')
        with closing(sqlite3.connect(b.sqlite_readonly_uri(native/'ptd_store/Data.ptd')+'&immutable=1', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            blob = r.read_blob(db, 1)
        return r.recover_object(blob, self.ids['surface'], 'RegValGrid2', metadata, package/'native_data')

    def test_exact_own_cache_exports_with_unresolved_metadata(self):
        record = self.recover()
        self.assertEqual(record['status'], 'missing_metadata')
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['numeric_round_trip'], 'exact')
        self.assertEqual(record['model_bounds_source'], 'own_cached_limit')
        self.assertEqual(record['model_bounds_check'], 'passed')
        self.assertEqual(record['model_bounds_comparison'], 'exact_float64_equality')
        self.assertFalse(record['coordinate_context_declared'])
        self.assertEqual(record['crs_status'], 'unresolved')
        self.assertIsNone(record['unit'])
        for artifact in record['artifacts']:
            self.assertEqual(r.sha(self.root/'package/native_data'/artifact['path']), artifact['sha256'])

    def test_adjacent_profile_rejected(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'coordinate context'):
            self.recover(model_version=[12, 3, 13, 1, 2, 20, 0, 0, 1])

    def test_missing_own_cache_rejected(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'own exact cached bounds'):
            self.recover(omit_bounds=True)

    def test_limit_cannot_substitute_for_own_cache(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'own exact cached bounds'):
            self.recover(limit_profile=True)

    def test_float_null_cache_rejected(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'explicitly undefined'):
            self.recover(limits=([r.FLOAT_NULL]*3, [r.FLOAT_NULL]*3))

    def test_partial_float_null_cache_rejected(self):
        with self.assertRaisesRegex(r.QCError, 'sentinel'):
            self.recover(limits=([100., 200., 1.], [140., 230., r.FLOAT_NULL]))

    def test_one_ulp_xy_mismatch_rejected(self):
        with self.assertRaisesRegex(r.QCError, 'disagree'):
            self.recover(limits=([np.nextafter(100., 101.), 200., 1.], [140., 230., 5.]))

    def test_z_mismatch_rejected(self):
        with self.assertRaisesRegex(r.QCError, 'disagree'):
            self.recover(limits=([100., 200., 1.], [140., 230., 5.0001]))

    def test_nonfinite_cache_rejected(self):
        with self.assertRaisesRegex(r.QCError, 'Invalid native'):
            self.recover(limits=([100., 200., 1.], [float('inf'), 230., 5.]))

    def test_malformed_cache_rejected(self):
        with self.assertRaisesRegex(b.NativeError, 'array shape'):
            self.recover(limits=([100., 200.], [140., 230., 5.]))

    def test_malformed_geometry_rejected(self):
        doc = fixtures.surface_doc(context=False).replace(
            fixtures.array('double', [140., 230.], '<f8'),
            fixtures.array('double', [141., 230.], '<f8'))
        with self.assertRaisesRegex(b.NativeError, 'extents disagree'):
            self.recover(doc=doc)

    def test_nonzero_axis_flip_rejected(self):
        doc = fixtures.surface_doc(context=False).replace(
            fixtures.element('axis_flip_state', 0), fixtures.element('axis_flip_state', 1))
        with self.assertRaisesRegex(r.MissingMetadata, 'unflipped'):
            self.recover(doc=doc)

    def test_nonzero_dip_rejected(self):
        doc = fixtures.surface_doc(context=False).replace(
            fixtures.element('dip', children=[fixtures.element('radians', 0)]),
            fixtures.element('dip', children=[fixtures.element('radians', 0.01)]))
        with self.assertRaisesRegex(r.MissingMetadata, 'untilted'):
            self.recover(doc=doc)

    def test_rotation_with_wrong_bounds_rejected(self):
        with self.assertRaisesRegex(r.QCError, 'disagree'):
            self.recover(doc=fixtures.surface_doc(context=False, rotation=0.01))

    def test_empty_grid_has_no_geometry_validation_or_exports(self):
        record = self.recover(doc=fixtures.surface_doc(context=False, mask=b'\x00'))
        self.assertEqual(record['status'], 'empty_supported_object')
        self.assertEqual(record['artifacts'], [])
        self.assertNotIn('model_bounds_check', record)
        self.assertNotIn('surface_geometry_check', record)

    def test_qa_class_is_not_relabelled_or_accepted(self):
        with self.assertRaisesRegex(r.MissingMetadata, 'QA model profile'):
            self.recover(surface_subject='QualityAssuranceSubject')


if __name__ == '__main__':
    unittest.main()
