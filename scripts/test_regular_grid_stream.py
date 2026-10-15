# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Streaming-grid numerical equivalence, mask completeness and resource guards."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import petrel_native_binary as b
import petrel_native_recovery as r
import petrel_surface_export as export
import petrel_regular_grid_stream as stream
import test_petrel_native_recovery as fixture


class RegularGridStreamTests(unittest.TestCase):
    def node(self, **kw):
        return b.object_document(fixture.envelope(fixture.frame(fixture.surface_doc(**kw))))

    def streamed(self, node):
        with patch.object(r, 'MAX_SURFACE_NODES', 3):
            return r.decode_surface(node, 'RegValGrid2')

    def test_same_source_coordinates_values_masks_and_bounds(self):
        for angle in (0., .125, -.75):
            for packed in (False, True):
                node = self.node(rotation=angle, packed=packed)
                full = r.decode_surface(node, 'RegValGrid2'); chunked = self.streamed(node)
                for old, new in zip(full[:7], chunked[:7]):
                    np.testing.assert_array_equal(old, new[:] if isinstance(new, stream.RegularColumn) else new)
                low, high = stream.bounds(*chunked[2:6])
                np.testing.assert_array_equal(low, [v[full[5]].min() for v in full[2:5]])
                np.testing.assert_array_equal(high, [v[full[5]].max() for v in full[2:5]])

    def test_column_allocation_guard_rejects_full_and_boolean_selections(self):
        col = stream.RegularColumn(stream.CHUNK+1, 3, 'x')
        for selection in (slice(None), np.ones(3, dtype=bool), np.array([-1]), np.array([len(col)])):
            with self.subTest(selection=repr(selection)), self.assertRaises((ValueError, IndexError)):
                col[selection]
        self.assertEqual(len(col[:10]), 10)

    def test_explicit_unknown_scalar_and_unbounded_profiles_stay_rejected(self):
        unknown = self.node(); unknown.attrs['Version'] = [99]
        explicit = self.node(kind='ValGrid2')
        scalar = self.node()
        scalar.child('grid').children = [b.Node('node', content=[float(i)]) for i in range(6)]
        binary64 = self.node()
        binary64.child('grid').children[0].values = np.arange(6, dtype=np.float64)
        for node, kind in ((unknown, 'RegValGrid2'), (explicit, 'ValGrid2'), (scalar, 'RegValGrid2'), (binary64, 'RegValGrid2')):
            with patch.object(r, 'MAX_SURFACE_NODES', 3), self.assertRaisesRegex(b.NativeError, 'memory bound'):
                r.decode_surface(node, kind)
        with patch.object(stream, 'MAX_NODES', 5), self.assertRaisesRegex(b.NativeError, 'memory bound'):
            self.streamed(self.node())
        with patch.object(stream, 'MAX_AXIS', 2), self.assertRaisesRegex(b.NativeError, 'memory bound'):
            self.streamed(self.node())
        self.assertEqual(b.MAX_DOCUMENT, 128*1024*1024)
        self.assertEqual(r.MAX_SURFACE_NODES, 10_000_000)

    def test_v0_profile_matches_dense_values_geometry_and_masks(self):
        for rotation in (0., .125, -.75):
            for packed in (False, True):
                node=self.node(legacy=True,packed=packed,rotation=rotation)
                dense=r.decode_surface(node,'RegValGrid2');bounded=self.streamed(node)
                self.assertEqual(bounded[-1]['coordinate_storage_profile'],'packed_regular_v0_chunked/1')
                for old,new in zip(dense[:7],bounded[:7]):
                    np.testing.assert_array_equal(old,new[:] if isinstance(new,stream.RegularColumn) else new)
                low,high=stream.bounds(*bounded[2:6])
                np.testing.assert_array_equal(low,[v[dense[5]].min() for v in dense[2:5]])
                np.testing.assert_array_equal(high,[v[dense[5]].max() for v in dense[2:5]])

    def test_v0_production_model_gate_and_complete_export(self):
        case=fixture.RecoveryTests();case.setUp()
        try:
            for flag in (False,True):
                case.root=case.root/str(flag);case.root.mkdir()
                package=case.fixture(kind='RegValGrid2',surface=True,doc=fixture.surface_doc(legacy=True,packed=True),
                    model_version=r.SURFACE_LIMIT_V10_2_20,limit_profile=True,force_negative=flag)
                with patch.object(r,'MAX_SURFACE_NODES',3):report=r.run(package)
                item=report['objects'][0]
                if flag:
                    self.assertFalse(item.get('dataset_exported',False))
                    self.assertIn('used_force_negative_depth=True',item['reason'])
                else:
                    self.assertTrue(item['dataset_exported'])
                    self.assertEqual(item['numeric_round_trip'],'exact')
                    self.assertEqual(item['coordinate_storage_profile'],'packed_regular_v0_chunked/1')
                    self.assertEqual(item['model_bounds_comparison'],'exact_float64_equality_all_six_defined_node_xyz_extrema')
                    xyz=np.loadtxt(next(package.rglob('surface.xyz')),ndmin=2)
                    np.testing.assert_array_equal(xyz,[[100,200,1],[120,200,2],[140,200,3],[100,230,4],[120,230,5]])
        finally:case.doCleanups()

    def test_geometry_connections_and_native_nonfinite_guards(self):
        for node in (self.node(connections=True), self.node(mask=b'')):
            with self.assertRaises(b.NativeError): self.streamed(node)
        for index, value in ((0, np.nan), (5, np.inf), (5, -np.inf)):
            node = self.node(); values = node.child('grid').children[0].values.copy(); values[index] = value
            node.child('grid').children[0].values = values
            with self.assertRaisesRegex(b.NativeError, 'Non-finite'): self.streamed(node)
        node = self.node(); node.child('original_max').children[0].values = np.array([140., 231.])
        with self.assertRaisesRegex(b.NativeError, 'extents'): self.streamed(node)

    def test_complete_mask_runs_across_chunks(self):
        for mask in (np.ones(17, dtype=bool), np.zeros(17, dtype=bool),
                     np.arange(17)%2 == 0, np.array([1]*4+[0]*8+[1]*5, dtype=bool)):
            with patch.object(stream, 'CHUNK', 4):
                rows = np.vstack(list(stream.mask_run_blocks(mask)))
            rebuilt = np.zeros(len(mask), dtype=bool); end = 0
            for start, length, defined in rows:
                self.assertEqual(start, end); self.assertGreater(length, 0)
                end = start+length; rebuilt[start:end] = bool(defined)
            self.assertEqual(end, len(mask)); np.testing.assert_array_equal(rebuilt, mask)
            self.assertEqual(len(rows), 1+np.count_nonzero(mask[1:] != mask[:-1]))

    def test_written_tables_zmap_preview_preserve_original_sampling(self):
        node = self.node()
        values = node.child('grid').children[0].values.copy(); values[4] = r.FLOAT_NULL; values[5] = np.nan
        node.child('grid').children[0].values = values
        decoded = self.streamed(node); i,j,x,y,z,valid,cells,info = decoded
        info.update(name='test', blob_type='RegValGrid2')
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)
            export.write_surface(output, info, i,j,x,y,z,valid,cells,
                                 native_node_defs=r.bitmask(node,'node_defs',len(z)))
            self.assertEqual(info['zmap_status'], 'written_verified')
            self.assertEqual(info['full_grid_stats']['valid_count'], 4)
            table = np.loadtxt(output/'nodes.csv', delimiter=',', skiprows=1)
            np.testing.assert_array_equal(table[:,0], [0,1,2,3])
            np.testing.assert_array_equal(table[:,3:6], [[100,200,1],[120,200,2],[140,200,3],[100,230,4]])
            self.assertTrue((output/'usable_node_definitions.csv').is_file())
            with np.load(output/'grid_preview.npz') as preview:
                self.assertTrue(np.isnan(preview['value'][1,2]))
                self.assertFalse(preview['cell_valid'].any())
            # ZMAP null text must agree exactly with its declared double header,
            # even when native z remains packed float32 in memory.
            lines=(output/'surface.zmap').read_text().splitlines()
            self.assertIn(f'{info["zmap_null"]:.17e}', '\n'.join(lines))

    def test_chunked_preview_matches_dense_with_native_cell_holes(self):
        node = self.node(dims=(6,5),mask=b'\xff\xdf\xff\x3f')
        full=r.decode_surface(node,'RegValGrid2'); chunked=self.streamed(node)
        full[6][7]=False; chunked[6][7]=False
        with tempfile.TemporaryDirectory() as td:
            dense=Path(td)/'dense.npz'; sparse=Path(td)/'sparse.npz'
            before=export.preview(dense,*full[2:7],full[-1])
            after=export.preview(sparse,*chunked[2:7],chunked[-1])
            with np.load(dense) as left,np.load(sparse) as right:
                for name in left.files:np.testing.assert_array_equal(left[name],right[name])
            self.assertEqual(before,after)

    def test_end_to_end_metadata_bounds_still_required(self):
        case=fixture.RecoveryTests();case.setUp()
        try:
            package=case.fixture(kind='RegValGrid2',surface=True)
            with patch.object(r,'MAX_SURFACE_NODES',3):report=r.run(package)
            item=report['objects'][0]
            self.assertEqual(item['numeric_round_trip'],'exact')
            self.assertEqual(item['model_bounds_check'],'passed')
            self.assertEqual(item['coordinate_storage_profile'],'packed_regular_chunked/1')
            case.root=case.root/'bad_bounds'
            package=case.fixture(kind='RegValGrid2',surface=True,limits=([100,200,1],[141,230,5]))
            with patch.object(r,'MAX_SURFACE_NODES',3):report=r.run(package)
            self.assertIn('disagree',report['objects'][0]['reason'])
            self.assertFalse(report['objects'][0].get('dataset_exported',False))
        finally:case.doCleanups()


if __name__=='__main__':unittest.main()
