# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Native grid controls, independent ZMAP readback, topology and report checks.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
from pathlib import Path
import math
import io, json, unittest, sys
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent))
from contextlib import redirect_stdout
import numpy as np
from zmapio import ZMAPGrid
import test_petrel_native_recovery as fixtures
import petrel_native_binary as binary
import petrel_native_recovery as r
import petrel_surface_export as export
import report_petrel_project_audit as audit
import petrel_visual_report as visual


class GridTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    fixture=fixtures.RecoveryTests.fixture
    run_fixture=fixtures.RecoveryTests.run_fixture

    def grid(self,**options):
        options.setdefault('doc',fixtures.surface_doc(packed=True,legacy=True))
        return self.run_fixture(kind='RegValGrid2',surface=True,**options)

    def test_native_grid_xyz_zmap_and_index_order(self):
        package,_,record=self.grid(metric=False)
        self.assertEqual(record['status'],'missing_metadata')
        self.assertIsNone(record['unit']);self.assertIsNone(record['horizontal_unit'])
        self.assertEqual(record['numeric_round_trip'],'exact')
        self.assertTrue(record['dataset_exported'])
        zmap=ZMAPGrid(str(next(package.rglob('surface.zmap'))))
        np.testing.assert_array_equal(zmap.z_values,[[4,1],[5,2],[np.nan,3]])
        np.testing.assert_array_equal(zmap.x_values,[[100,120,140],[100,120,140]])
        np.testing.assert_array_equal(zmap.y_values,[[230,230,230],[200,200,200]])
        csv=np.loadtxt(next(package.rglob('nodes.csv')),delimiter=',',skiprows=1)
        np.testing.assert_array_equal(csv[:,0],np.arange(5))
        np.testing.assert_array_equal(csv[:,1:3],[[0,0],[1,0],[2,0],[0,1],[1,1]])
        masks=np.loadtxt(next(package.rglob('node_definitions.csv')),delimiter=',',skiprows=1,ndmin=2)
        np.testing.assert_array_equal(np.repeat(masks[:,2],masks[:,1].astype(int)),[1,1,1,1,1,0])
        count=audit.gather_native_inventory(package)
        self.assertEqual(count['native_surface_objects_written'],1)
        self.assertEqual(count['native_surface_units_unresolved'],1)
        self.assertEqual(count['decoded_object_type_counts'],{})

    def test_explicit_mesh_has_xyz_but_no_invented_zmap(self):
        package,_,record=self.run_fixture(kind='ValGrid2',surface=True,metric=False,doc=fixtures.surface_doc('ValGrid2',packed=True))
        self.assertTrue(record['dataset_exported']);self.assertFalse(list(package.rglob('*.zmap')))
        self.assertEqual(record['defined_nodes'],5)

    def test_unicode_zmap_label_and_multiple_lines_per_column(self):
        path=self.root/'unicode.zmap';z=np.arange(15,dtype=float);valid=np.ones(15,bool);valid[[3,13]]=False
        export.write_zmap(path,z,valid,dict(node_size=[3,5],name='Grid \u0633\u0637\u062d',origin=[-20.,30.],maximum=[0.,110.]))
        grid=ZMAPGrid(str(path));expected=np.where(valid,z,np.nan).reshape(5,3)[::-1].T
        np.testing.assert_array_equal(grid.z_values,expected)
        np.testing.assert_array_equal(grid.x_values[0],[-20,-10,0])
        np.testing.assert_array_equal(grid.y_values[:,0],[110,90,70,50,30])

    def test_context_false_requires_exact_independent_bounds(self):
        _,_,record=self.grid(doc=fixtures.surface_doc(packed=True,legacy=True,context=False))
        self.assertEqual(record['model_bounds_check'],'passed')
        self.assertTrue(record['dataset_exported'])

    def test_enclosing_limit_permitted_for_context_declared(self):
        _,_,record=self.grid(limit_profile=True,limits=([90,190,0],[150,240,9]))
        self.assertEqual(record['model_bounds_check'],'within_enclosing_native_limit')

    def test_undefined_native_cache_is_explicit(self):
        _,_,record=self.grid(limit_profile=True,limits=([r.FLOAT_NULL]*3,[r.FLOAT_NULL]*3))
        self.assertEqual(record['model_bounds_check'],'unavailable_native_cache_undefined')
        self.assertTrue(record['dataset_exported'])

    def test_context_false_enclosing_limit_stays_blocked(self):
        package,_,record=self.grid(limit_profile=True,limits=([90,190,0],[150,240,9]),doc=fixtures.surface_doc(packed=True,legacy=True,context=False))
        self.assertEqual(record['status'],'missing_metadata');self.assertFalse(list(package.rglob('*.xyz')))

    def test_attribute_without_own_coordinate_context_stays_blocked(self):
        package,_,record=self.grid(surface_subject='SurfaceAttrSubject',doc=fixtures.surface_doc(packed=True,legacy=True,context=False))
        self.assertEqual(record['status'],'missing_metadata');self.assertFalse(list(package.rglob('*.xyz')))

    def test_outside_model_limit_rejected(self):
        package,_,record=self.grid(limit_profile=True,limits=([100,200,2],[140,230,5]))
        self.assertEqual(record['status'],'conversion_failed');self.assertFalse(list(package.rglob('*.xyz')))

    def test_old_cached_limit_stays_exact(self):
        _,_,record=self.grid(limits=([90,190,0],[150,240,9]))
        self.assertEqual(record['status'],'conversion_failed')

    def test_insufficient_space_is_per_object_failure_without_ascii(self):
        with patch.object(export.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            package,report,record=self.grid()
        self.assertEqual(report['status'],'partial');self.assertTrue(report['source_unchanged'])
        self.assertEqual(record['status'],'conversion_failed');self.assertFalse(record['artifacts'])
        self.assertFalse(list(package.rglob('*.xyz'))+list(package.rglob('nodes.csv')))

    def test_packed_padding_and_lsb_order(self):
        doc=fixtures.surface_doc(dims=(4,2),mask=b'\x81',packed=True,legacy=True)
        node=binary.object_document(fixtures.envelope(fixtures.frame(doc)))
        np.testing.assert_array_equal(r.decode_surface(node,'RegValGrid2')[5],[1,0,0,0,0,0,0,1])
        for padding in (b'\x00', b'\x01', b'\xff'):
            node.child('node_defs').child('ignore').content=[padding]
            np.testing.assert_array_equal(r.decode_surface(node,'RegValGrid2')[5],[1,0,0,0,0,0,0,1])
        for padding in (b'', b'\x00\xff', 0, 'x'):
            node.child('node_defs').child('ignore').content=[padding]
            with self.assertRaises(r.NativeError):r.decode_surface(node,'RegValGrid2')
        node.child('node_defs').children.pop()
        with self.assertRaises(r.NativeError):r.decode_surface(node,'RegValGrid2')
        node=binary.object_document(fixtures.envelope(fixtures.frame(fixtures.surface_doc(packed=True,legacy=True))))
        node.child('node_defs').children.append(binary.Node('ignore',content=[b'\xff']))
        with self.assertRaises(r.NativeError):r.decode_surface(node,'RegValGrid2')

    def test_older_limit_profile_requires_exact_bounds(self):
        _,_,record=self.grid(limit_profile=True,model_version=r.SURFACE_LIMIT_V8,
                             doc=fixtures.surface_doc(packed=True,legacy=True,context=False))
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['model_bounds_source'],'own_limit')
        self.assertEqual(record['model_bounds_check'],'passed')

    def test_v9_19_limit_profile_requires_exact_bounds_and_keeps_units_unknown(self):
        _,_,record=self.grid(limit_profile=True,model_version=r.SURFACE_LIMIT_V9_19,
                             metric=False,doc=fixtures.surface_doc(packed=True,legacy=True,context=False))
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['model_bounds_check'],'passed')
        self.assertEqual(record['numeric_round_trip'],'exact')
        self.assertIsNone(record['unit'])

    def test_v9_19_limit_profile_rejects_enclosure_and_reports_axes(self):
        package,_,record=self.grid(limit_profile=True,model_version=r.SURFACE_LIMIT_V9_19,
                                  limits=([90,190,1],[150,240,5]))
        self.assertEqual(record['status'],'conversion_failed')
        self.assertEqual(record['error']['bounds_exact_by_axis'],[False,False,True])
        self.assertEqual(record['failure_checkpoint'],'validate_surface_model_bounds')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_adjacent_v9_limit_profile_is_still_unsupported(self):
        package,_,record=self.grid(limit_profile=True,model_version=[9,2,13,1,2,20,0,0,1])
        self.assertEqual(record['status'],'missing_metadata')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_rotated_grid_xyz_uses_pivot_and_never_false_zmap(self):
        package,_,record=self.grid(doc=fixtures.surface_doc(rotation=math.pi/2),
                                  limits=([70,200,1],[100,240,5]))
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['model_bounds_check'],'passed')
        self.assertFalse(record['axis_aligned'])
        self.assertEqual(record['zmap_status'],'not_applicable')
        self.assertFalse(list(package.rglob('*.zmap')))
        np.testing.assert_allclose(np.loadtxt(next(package.rglob('surface.xyz'))),
                                   [[100,200,1],[100,220,2],[100,240,3],[70,200,4],[70,220,5]],rtol=0,atol=1e-12)
        with np.load(next(package.rglob('grid_preview.npz'))) as preview:
            np.testing.assert_allclose(preview['x'][0],[100,100,100],rtol=0,atol=1e-12)

    def test_negative_rotation_and_false_legacy_context(self):
        package,_,record=self.grid(doc=fixtures.surface_doc(rotation=-math.pi/2,legacy=True,context=False),
                                  limit_profile=True,model_version=r.SURFACE_LIMIT_V8,
                                  limits=([100,160,1],[130,200,5]))
        self.assertTrue(record['dataset_exported'])
        np.testing.assert_allclose(np.loadtxt(next(package.rglob('surface.xyz'))),
                                   [[100,200,1],[100,180,2],[100,160,3],[130,200,4],[130,180,5]],rtol=0,atol=1e-12)

    def test_rotated_grid_enclosing_bounds_are_insufficient(self):
        package,_,record=self.grid(doc=fixtures.surface_doc(rotation=math.pi/2),limit_profile=True,
                                  limits=([0,0,0],[1000,1000,99]))
        self.assertEqual(record['status'],'missing_metadata')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_rotated_grid_undefined_cache_is_insufficient(self):
        package,_,record=self.grid(doc=fixtures.surface_doc(rotation=math.pi/2),limit_profile=True,
                                  limits=([r.FLOAT_NULL]*3,[r.FLOAT_NULL]*3))
        self.assertEqual(record['status'],'missing_metadata')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_regular_attribute_own_bounds_recover_without_parent_guess(self):
        package,_,record=self.grid(doc=fixtures.surface_doc(context=False),surface_subject='SurfaceAttrSubject',
                                  model_version=r.SURFACE_ATTRIBUTE_XYZ_V6)
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['model_bounds_check'],'passed')
        self.assertIn('own_regular_geometry',record['attribute_geometry_check'])
        self.assertTrue(list(package.rglob('*.zmap')))

    def test_regular_attribute_mismatched_bounds_block_export(self):
        package,_,record=self.grid(doc=fixtures.surface_doc(context=False),surface_subject='SurfaceAttrSubject',
                                  model_version=r.SURFACE_ATTRIBUTE_XYZ_V6,limits=([100,200,1],[140,230,4]))
        self.assertEqual(record['status'],'conversion_failed')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_rotation_does_not_enable_dip_flip_or_invalid_angles(self):
        node=binary.object_document(fixtures.envelope(fixtures.frame(fixtures.surface_doc())))
        radians=node.child('original_rotation').child('radians')
        for angle in (float('nan'),float('inf'),7,True,'0.2'):
            radians.content=[angle]
            with self.assertRaises(r.NativeError):r.decode_surface(node,'RegValGrid2')
        radians.content=[0.2]
        node.child('dip').child('radians').content=[0.1]
        with self.assertRaises(r.MissingMetadata):r.decode_surface(node,'RegValGrid2')
        node.child('dip').child('radians').content=[0]
        node.child('axis_flip_state').content=[1]
        with self.assertRaises(r.MissingMetadata):r.decode_surface(node,'RegValGrid2')

    def test_direct_zmap_writer_rejects_rotated_geometry_before_write(self):
        path=self.root/'invalid.zmap'
        with self.assertRaisesRegex(ValueError,'Rotated'):
            export.write_zmap(path,np.ones(4),np.ones(4,bool),dict(rotation_radians=.1))
        self.assertFalse(path.exists())

    def test_older_limit_profile_rejects_enclosing_bounds(self):
        package,_,record=self.grid(limit_profile=True,model_version=r.SURFACE_LIMIT_V8,
                                  limits=([90,190,0],[150,240,9]))
        self.assertEqual(record['status'],'conversion_failed')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_older_limit_profile_rejects_unknown_version(self):
        package,_,record=self.grid(limit_profile=True,model_version=[8,2,12,1,0,1,99,0,0,1])
        self.assertEqual(record['status'],'missing_metadata')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_explicit_attribute_uses_own_exact_bounds(self):
        package,_,record=self.run_fixture(kind='ValGrid2',surface=True,surface_subject='SurfaceAttrSubject',
                                          model_version=r.SURFACE_ATTRIBUTE_XYZ_V6,metric=False)
        self.assertTrue(record['dataset_exported'])
        self.assertEqual(record['numeric_round_trip'],'exact')
        self.assertEqual(record['status'],'missing_metadata')
        self.assertEqual(record['model_bounds_check'],'passed')
        self.assertIn('no inherited coordinates',record['attribute_geometry_check'])
        self.assertFalse(list(package.rglob('*.zmap')))
        np.testing.assert_array_equal(np.loadtxt(next(package.rglob('surface.xyz'))),
                                      [[100,200,1],[120,200,2],[140,200,3],[100,230,4],[120,230,5]])

    def test_explicit_attribute_rejects_unknown_version(self):
        package,_,record=self.run_fixture(kind='ValGrid2',surface=True,surface_subject='SurfaceAttrSubject')
        self.assertEqual(record['status'],'missing_metadata')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_explicit_attribute_rejects_missing_bounds(self):
        package,_,record=self.run_fixture(kind='ValGrid2',surface=True,surface_subject='SurfaceAttrSubject',
                                          model_version=r.SURFACE_ATTRIBUTE_XYZ_V6,omit_bounds=True)
        self.assertEqual(record['status'],'missing_metadata')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_explicit_attribute_rejects_enclosing_bounds(self):
        package,_,record=self.run_fixture(kind='ValGrid2',surface=True,surface_subject='SurfaceAttrSubject',
                                          model_version=r.SURFACE_ATTRIBUTE_XYZ_V6,limits=([99,200,1],[140,230,5]))
        self.assertEqual(record['status'],'conversion_failed')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_explicit_attribute_rejects_wrong_value_bounds(self):
        package,_,record=self.run_fixture(kind='ValGrid2',surface=True,surface_subject='SurfaceAttrSubject',
                                          model_version=r.SURFACE_ATTRIBUTE_XYZ_V6,limits=([100,200,1],[140,230,4.99]))
        self.assertEqual(record['status'],'conversion_failed')
        self.assertFalse(list(package.rglob('*.xyz')))

    def test_unknown_field_and_huge_grid_rejected(self):
        doc=fixtures.surface_doc(packed=True,legacy=True)
        node=binary.object_document(fixtures.envelope(fixtures.frame(doc)))
        node.children.append(node.child('user_data'))
        with self.assertRaises(r.NativeError):r.decode_surface(node,'RegValGrid2')
        node.children.pop()
        node.child('node_size').child('int').values=np.array([4000,4000],dtype='<i4')
        # V0's bounded packed profile now admits this size, but never a mask
        # or payload whose declared lattice differs from its actual records.
        with self.assertRaises(r.NativeError):r.decode_surface(node,'RegValGrid2')
        node.child('node_size').child('int').values=np.array([4000,9000],dtype='<i4')
        with self.assertRaisesRegex(r.NativeError,'memory bound'):r.decode_surface(node,'RegValGrid2')

    def test_native_and_usable_mask_preserved_separately(self):
        path=self.root/'masks';path.mkdir()
        x=np.array([0,1,0,1]);y=np.array([0,0,1,1]);z=np.array([1,2,3,r.FLOAT_NULL])
        info=dict(name='Grid',node_size=[2,2],blob_type='ValGrid2')
        export.write_surface(path,info,np.array([0,1,0,1]),np.array([0,0,1,1]),x,y,z,np.array([1,1,1,0],bool),np.ones(1,bool),True,np.ones(4,bool))
        self.assertTrue((path/'usable_node_definitions.csv').is_file())
        raw=np.loadtxt(path/'node_definitions.csv',delimiter=',',skiprows=1,ndmin=2)
        np.testing.assert_array_equal(raw,[[0,4,1]])

    def test_decimated_preview_never_bridges_hole(self):
        nx,ny=600,4;x=np.tile(np.arange(nx),ny);y=np.repeat(np.arange(ny),nx);z=x+y
        valid=np.ones(nx*ny,bool);valid.reshape(ny,nx)[:,299]=False
        cells=np.ones((ny-1)*(nx-1),bool)
        path=self.root/'preview.npz'
        details=export.preview(path,x,y,z,valid,cells,dict(node_size=[nx,ny]))
        with np.load(path) as p:
            bridges=(p['i'][:-1]<=299)&(p['i'][1:]>=299)
            self.assertFalse(p['cell_valid'][:,bridges].any())
            self.assertEqual(len(p['i']),256)
        self.assertEqual(details['full_grid_stats']['valid_count'],nx*ny-ny)

    def test_report_only_writes_preview_without_ascii(self):
        package=self.fixture(kind='RegValGrid2',surface=True,doc=fixtures.surface_doc(packed=True,legacy=True))
        with redirect_stdout(io.StringIO()):report=r.run(package,grids_only=True,preview_only=True)
        self.assertFalse(report['objects'][0]['dataset_exported'])
        self.assertTrue(list(package.rglob('grid_preview.npz')))
        self.assertFalse(list(package.rglob('*.xyz'))+list(package.rglob('*.zmap'))+list(package.rglob('nodes.csv')))
        self.assertEqual(audit.gather_native_inventory(package)['native_surface_objects_written'],0)

    def test_grid_figure_uses_preview_and_checks_hash(self):
        package,_,record=self.grid(metric=False)
        target=package/'visuals';target.mkdir()
        figures=visual.Figures(package)
        visible={'links':visual.read_artifacts(package,record)}
        visual.plot_native(package,record,visible,figures)
        self.assertEqual(visible['preview_status'],'plotted')
        self.assertIn('unresolved',figures.items[0]['caption'])
        path=next(package.rglob('grid_preview.npz'));path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(ValueError):visual.plot_native(package,record,visible,figures)


if __name__=='__main__':unittest.main()
