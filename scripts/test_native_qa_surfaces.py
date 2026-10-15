# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded stored QA grids: identity, geometry, state and unresolved semantics.
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
"""
from contextlib import closing
import json
import sqlite3
import unittest
import uuid
from unittest.mock import patch

import numpy as np
import petrel_native_binary as b
import petrel_native_recovery as r
import test_petrel_native_recovery as f


class QASurfaceTests(unittest.TestCase):
    setUp = f.RecoveryTests.setUp
    fixture = f.RecoveryTests.fixture

    def prepare(self, subject='QualityAssuranceAttrSubject', flags=None, **options):
        # Independent fixture directory for every subcase; never reuse an export.
        self.root = self.root / uuid.uuid4().hex[:6]
        kwargs = dict(kind='RegValGrid2', surface=True, surface_subject=subject,
                      model_version=r.QA_MODEL_PROFILES[subject])
        kwargs.update(options)
        package = self.fixture(**kwargs)
        native = package/'08_native_project'
        project = native/'project_file/test.pet'; model = native/'ptd_store/Model.ptd'
        metadata = r.Metadata(project, model)
        own = metadata.model[self.ids['surface']]
        for key, value in (flags or {}).items():
            own.children.append(next(b.read_documents(f.frame(f.element(key, value)))))
        with closing(sqlite3.connect(b.sqlite_readonly_uri(native/'ptd_store/Data.ptd')+'&immutable=1', uri=True)) as db:
            blob = r.read_blob(db, 1)
        self.output = package/'native_data'
        return metadata, blob, kwargs['kind'], project, model

    def recover(self, **kwargs):
        metadata, blob, kind, _, _ = self.prepare(**kwargs)
        return r.recover_object(blob, self.ids['surface'], kind, metadata, self.output)

    def test_both_classes_arbitrary_stored_values_and_typed_flags(self):
        for subject in r.QA_MODEL_PROFILES:
            with self.subTest(subject=subject):
                record = self.recover(subject=subject, flags={'is_recalculation_needed':True, 'up_to_date':False})
                self.assertEqual(record['subject_type'], subject)
                self.assertEqual(record['status'], 'missing_metadata')
                self.assertTrue(record['dataset_exported'])
                self.assertEqual(record['numeric_round_trip'], 'exact')
                self.assertEqual(record['model_bounds_comparison'], 'exact_float64_equality')
                self.assertEqual(record['qa_state']['is_recalculation_needed'], {'state':'present', 'value':True})
                self.assertIn('up_to_date=false', record['reason'])
                self.assertIn('stored native QA values; computation validity and scalar units unresolved', record['reason'])
                self.assertIsNone(record['unit']); self.assertIsNone(record['depth_unit'])
                self.assertIsNone(record['measurement']); self.assertEqual(record['horizontal_unit'], 'm')
                self.assertEqual(record['crs_status'], 'unresolved')
                self.assertEqual(record['qa']['xyz_third_column'], 'stored native scalar; not asserted depth or elevation')
                xyz = np.loadtxt(next(self.output.rglob('surface.xyz')))
                np.testing.assert_array_equal(xyz[:,2], [1,2,3,4,5])
                for artifact in record['artifacts']:
                    self.assertEqual(r.sha(self.output/artifact['path']), artifact['sha256'])
                saved = json.loads(next(self.output.rglob('metadata.json')).read_text())
                self.assertEqual(saved['qa_state'], record['qa_state'])
                import petrel_visual_report as visual
                rendered = visual.render_section({'visual_report':{'objects':[record], 'summary_path':'metadata.json'}},
                                                 lambda audit,path:path)
                self.assertIn('stored native QA values; computation validity and scalar units unresolved', rendered)
                self.assertIn('is_recalculation_needed=true; up_to_date=false', rendered)
                self.assertIn('unit not resolved', rendered)

    def test_zero_values_remain_defined(self):
        doc = f.surface_doc().replace(f.array('node', range(1,7), '<f4'), f.array('node', [0]*6, '<f4'))
        record = self.recover(doc=doc, limits=([100.,200.,0.],[140.,230.,0.]))
        self.assertEqual(record['defined_nodes'], 5)
        self.assertTrue(np.all(np.loadtxt(next(self.output.rglob('surface.xyz')))[:,2] == 0))

    def test_absent_flags_explicit_and_no_template_resolution(self):
        record = self.recover()
        for value in record['qa_state'].values(): self.assertEqual(value, {'state':'absent','value':None})
        self.assertIn('is_recalculation_needed=absent', record['reason'])
        self.assertEqual(record['declared_template_unit'], 'm')
        self.assertEqual(record['declared_template_measurement'], 'Length')
        self.assertEqual(record['unit_status'], 'unresolved_QA_scalar_semantics')

    def test_bad_flag_types_rejected(self):
        for key in ('is_recalculation_needed','up_to_date'):
            for value in ('true', 1, 0.0):
                with self.subTest(key=key,value=value), self.assertRaisesRegex(r.MissingMetadata,'boolean'):
                    self.recover(flags={key:value})

    def test_template_conflict_or_absence_never_resolves_units(self):
        for missing in (True,False):
            metadata, blob, kind, _, _ = self.prepare(flags={'is_recalculation_needed':False,'up_to_date':True})
            template = metadata.model[self.ids['template']]
            if missing: del metadata.model[self.ids['template']]
            else:
                template.children = [c for c in template.children if c.name not in ('initial_unit','standard_measurement')]
                for key,value in [('initial_unit','gAPI'),('standard_measurement','API_Gamma_Ray')]:
                    template.children.append(next(b.read_documents(f.frame(f.element(key,value)))))
            record = r.recover_object(blob,self.ids['surface'],kind,metadata,self.output)
            self.assertEqual(record['status'],'missing_metadata'); self.assertIsNone(record['unit'])
            self.assertEqual(record['declared_template_unit'], None if missing else 'gAPI')
            self.assertEqual(record['qa']['computation_validity'],'unresolved')

    def test_adjacent_model_profiles_rejected(self):
        for subject, profile in r.QA_MODEL_PROFILES.items():
            with self.subTest(subject=subject), self.assertRaisesRegex(r.MissingMetadata,'QA model profile'):
                self.recover(subject=subject, model_version=[profile[0]+1]+profile[1:])

    def test_adjacent_native_profile_and_context_false_rejected(self):
        for doc in (f.surface_doc(legacy=True), f.surface_doc(context=False)):
            with self.assertRaisesRegex(r.MissingMetadata,'coordinate context true'):
                self.recover(doc=doc)
        doc=f.surface_doc().replace(f.text(r.PROFILES['RegValGrid2']),f.text([2]+r.PROFILES['RegValGrid2'][1:]))
        with self.assertRaisesRegex(b.NativeError,'type/version'): self.recover(doc=doc)

    def test_valgrid_and_logs_never_accept_qa(self):
        for kind in ('ValGrid2','FloatWellLog','IntWellLog'):
            with self.subTest(kind=kind), self.assertRaisesRegex(r.MissingMetadata,'UUID and class linkage'):
                self.recover(kind=kind, doc=f.surface_doc(kind) if kind=='ValGrid2' else f.log_doc(kind))

    def test_missing_cache_or_limit_substitute_rejected(self):
        for opts in ({'omit_bounds':True},{'limit_profile':True}):
            with self.assertRaisesRegex(r.MissingMetadata,'own exact cached bounds'): self.recover(**opts)

    def test_bad_cache_rejected(self):
        for limits in (([100.,200.],[140.,230.,5.]), ([100.,200.,1.],[float('inf'),230.,5.]),
                       ([r.FLOAT_NULL]*3,[r.FLOAT_NULL]*3), ([100.,200.,1.],[140.,230.,r.FLOAT_NULL])):
            with self.subTest(limits=limits), self.assertRaises(b.NativeError): self.recover(limits=limits)

    def test_each_one_ulp_bound_rejected(self):
        for index in range(6):
            limits=np.array([[100.,200.,1.],[140.,230.,5.]])
            limits.flat[index]=np.nextafter(limits.flat[index],float('inf'))
            with self.subTest(index=index), self.assertRaisesRegex(r.QCError,'disagree'):
                self.recover(limits=limits.tolist())

    def test_target_cache_never_substitutes_own(self):
        for opts in ({'omit_bounds':True},{'limits':([100.,200.,1.],[140.,230.,5.001])}):
            metadata,blob,kind,_,_=self.prepare(**opts)
            target=next(b.read_documents(f.frame(f.element('SurfaceSubject',children=[
                f.element('cached_limit',children=[f.element('min',children=[f.array('double',[100.,200.,1.],'<f8')]),
                                                  f.element('max',children=[f.array('double',[140.,230.,5.],'<f8')])])]))))
            metadata.model['target']=target
            metadata.model[self.ids['surface']].children.append(next(b.read_documents(f.frame(f.element('target_link_tag','target')))))
            with self.assertRaises(b.NativeError): r.recover_object(blob,self.ids['surface'],kind,metadata,self.output)

    def test_dip_axis_flip_geometry_mask_and_array_controls(self):
        replacements=[(f.element('axis_flip_state',0),f.element('axis_flip_state',1)),
                      (f.element('radians',0),f.element('radians',0.1)),
                      (f.array('double',[140.,230.],'<f8'),f.array('double',[141.,230.],'<f8')),
                      (f.element('bitmask',b'\x1f'),f.element('bitmask',b'\x1f\x00')),
                      (f.array('node',range(1,7),'<f4'),f.array('node',[1,2,3],'<f4'))]
        for old,new in replacements:
            with self.assertRaises(b.NativeError): self.recover(doc=f.surface_doc().replace(old,new))

    def test_empty_exports_nothing_and_does_not_validate_geometry(self):
        record=self.recover(doc=f.surface_doc(mask=b'\x00'))
        self.assertEqual(record['status'],'empty_supported_object'); self.assertEqual(record['artifacts'],[])
        self.assertFalse(record['dataset_exported'])
        self.assertNotIn('qa_geometry_check',record); self.assertNotIn('model_bounds_check',record)
        self.assertFalse(self.output.exists())

    def test_identity_class_namespace_and_duplicates(self):
        # Re-run Metadata on parsed synthetic documents so QA participates in the
        # same constructor checks as ordinary surfaces, including duplicate IDs.
        for control in ('bad_uuid','duplicate_model','duplicate_project','wrong_class','namespace'):
            metadata,blob,kind,project,model=self.prepare()
            if control=='wrong_class':
                metadata.stubs[self.ids['surface']]['kind']='SurfaceSubject'
                with self.assertRaisesRegex(r.MissingMetadata,'UUID and class linkage'):
                    r.recover_object(blob,self.ids['surface'],kind,metadata,self.output)
                continue
            projects=list(b.read_documents(b.project_payload(project.read_bytes())))
            models=list(b.read_documents(b.decompress(model.read_bytes())))
            if control=='duplicate_model': models.append(models[0])
            elif control=='duplicate_project':
                entries=projects[0].child('class_infos').child('map').children
                entries.append(entries[0])
            elif control=='namespace': models[0].attrs['xmlns']='wrong'
            else:
                models[0].children=[c for c in models[0].children if c.name!='unique_tag']
                models[0].children.append(next(b.read_documents(f.frame(f.element('unique_tag','not-a-uuid')))))
            with patch.object(b,'read_documents',side_effect=[iter(projects),iter(models)]), self.assertRaises(b.NativeError):
                r.Metadata(project,model)


if __name__ == '__main__': unittest.main()
