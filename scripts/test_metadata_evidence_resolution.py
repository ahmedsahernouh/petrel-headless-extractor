# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Metadata uncertainty and provenance must not become preparation authority."""
import copy
import unittest
import test_geoviewer_receiving_profiles as fixtures
from geoviewer_metadata_evidence import SCHEMA, annotate_rows, resolve_values, status_is, summarize_rows

class MetadataResolutionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.ProfileIntegrationTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.fx.ready_log()

    def assess(self):return self.fx.log_assessment(self.fx.report(self.fx.decisions()))['receiving_assessment']

    def test_unresolved_spellings_cannot_qualify_md_reference(self):
        for value in (' UNKNOWN ','Unresolved','not_resolved','unresolved; no datum calibration'):
            with self.subTest(value=value):
                self.fx.log['md_reference']=value;self.fx.republish();a=self.assess()
                self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')
                self.assertIn('MD_REFERENCE_UNRESOLVED',a['reason_codes'])

    def test_null_description_requires_text_not_boolean_or_integer(self):
        self.fx.log['null_semantics']={'representation':True,'source_rule':1};self.fx.republish()
        self.assertNotEqual(self.assess()['receiving_state'],'qualified_for_planned_mapping')

    def test_conflicting_md_units_have_explicit_conflict_diagnostic(self):
        fixtures.write(self.fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
            dict(objects=[dict(object_id='log-one',status='decoded',md_unit='ft')]))
        self.fx.republish()
        self.assertIn('CONFLICTING_MD_UNIT',self.assess()['reason_codes'])

    def test_duplicate_matching_validation_evidence_is_not_a_conflict(self):
        fixtures.write(self.fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
            dict(objects=[dict(object_id='log-one',status='decoded',unit='m/s',unit_status='validated_metric_profile')]))
        self.fx.republish()
        self.assertEqual(self.assess()['receiving_state'],'qualified_for_planned_mapping')

    def test_conflicting_source_cannot_be_silently_selected_by_override(self):
        fixtures.write(self.fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
            dict(objects=[dict(object_id='log-one',status='decoded',md_unit='ft')]))
        self.fx.republish()
        decision=self.fx.decisions(record={'source_assertions':{
            'md_unit':dict(value='m',scope='exact_source_entity',evidence_index=0)}})
        a=self.fx.log_assessment(self.fx.report(decision))
        self.assertIn('CONFLICTING_MD_UNIT',a['receiving_assessment']['reason_codes'])
        self.assertIn('SOURCE_DECISION_CONFLICT',a['receiving_assessment']['reason_codes'])
        self.assertEqual(a['metadata_resolution']['md_unit']['status'],'conflict')
        self.assertIsNone(a['metadata_resolution']['md_unit']['value'])
        self.assertEqual(set(a['metadata_resolution']['md_unit']['alternatives']),{'m','ft'})
        self.assertEqual(len(a['metadata_resolution']['md_unit']['locators']),2)

    def test_unknown_native_value_stays_null_when_user_assertion_is_applied(self):
        self.fx.log['md_reference']=' Unresolved ';self.fx.republish()
        decision=self.fx.decisions(record={'source_assertions':{
            'md_reference':dict(value='KB',scope='exact_source_entity',evidence_index=0)}})
        a=self.fx.log_assessment(self.fx.report(decision))
        self.assertEqual(a['receiving_state'],'qualified_for_planned_mapping')
        self.assertIsNone(a['metadata_resolution']['md_reference']['value'])
        view=a['receiving_assessment']['effective_source_fields']['md_reference']
        self.assertEqual(view['value'],'KB');self.assertEqual(view['provenance'],'user_override')
        row=next(r for r in self.fx.table('metadata_evidence') if r['entity_key']==self.fx.keys['log-one'] and r['field']=='md_reference')
        self.assertEqual(row['raw_value'],' Unresolved ');self.assertIsNone(row['normalized_value'])
        self.assertEqual(row['evidence_status'],'unresolved')

    def test_property_unit_cannot_supply_md_unit_or_vertical_reference(self):
        self.fx.log.pop('md_unit');self.fx.log.pop('md_reference');self.fx.republish()
        a=self.assess()
        self.assertIn('MD_UNIT_UNRESOLVED',a['reason_codes'])
        self.assertIn('MD_REFERENCE_UNRESOLVED',a['reason_codes'])
        self.assertIsNone(a['effective_source_fields']['md_unit']['value'])
        self.assertIsNone(a['effective_source_fields']['vertical_unit']['value'])

    def test_conflicting_status_is_not_ignored_while_duplicate_status_is_allowed(self):
        fixtures.write(self.fx.package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json',
            dict(objects=[dict(object_id='log-one',status='decoded',unit='m/s',unit_status='unresolved')]))
        self.fx.republish();a=self.assess()
        self.assertIn('STORED_UNIT_PROFILE_UNVALIDATED',a['reason_codes'])
        self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')

    def test_previous_rule_decision_must_be_rebound_not_silently_reused(self):
        path=self.fx.decisions();data=fixtures.json.loads(path.read_text())
        data['profile_revision']='opendtect-7.0.10-odbind-1.0.0-preparation/2';fixtures.write(path,data)
        with self.assertRaisesRegex(ValueError,'not bound'):
            self.fx.report(path)


class MetadataEvidenceUnitTests(unittest.TestCase):
    def rows(self, values, field='md_reference', method='source_recorded'):
        return [dict(entity_key='one',field=field,raw_value=v,locator=f'receipt.json#/objects/{i}/{field}',
                     resolution=method) for i,v in enumerate(values)]

    def test_legacy_rows_are_read_only_and_keep_raw_placeholder_strings(self):
        rows=self.rows([' UNKNOWN ',None,'unresolved; no calibration']);before=copy.deepcopy(rows)
        view=annotate_rows(rows);summary=summarize_rows(rows)['one']['md_reference']
        self.assertEqual(rows,before);self.assertTrue(all(r['normalized_value'] is None for r in view))
        self.assertTrue(all(r['evidence_schema']==SCHEMA and r['unresolved_reason'] for r in view))
        self.assertEqual([r['raw_value'] for r in view],[r['raw_value'] for r in rows])
        self.assertEqual(summary['status'],'unresolved');self.assertEqual(summary['unresolved_observations'],3)

    def test_types_and_order_do_not_create_silent_precedence(self):
        for values in ([True,1],['m','ft'],[{'count':True},{'count':1}]):
            with self.subTest(values=values):
                self.assertEqual(resolve_values(values)['status'],'conflict')
                self.assertEqual(resolve_values(values[::-1])['status'],'conflict')
                self.assertIsNone(resolve_values(values)['value'])
        self.assertEqual(resolve_values(['m','m'])['value'],'m')

    def test_observation_roles_are_distinct_and_never_inferred(self):
        expected={'source_recorded':'recorded_source_value','exact_native_record':'exact_native_record',
            'validated_native_template_link':'validated_native_template_link',
            'exact_correlated_companion':'exact_correlated_companion',
            'inherited_project_setting':'inherited_project_setting','user_override':'user_override',
            'derived_from_bound_native_receipt':'derived_value',
            'index_declaration_not_independent_validation':'candidate',
            'typed_native_declarations_not_calibrated_datums':'native_declaration'}
        for method,status in expected.items():
            with self.subTest(method=method):
                row=annotate_rows(self.rows(['m'],method=method))[0]
                self.assertEqual(row['evidence_status'],status)
                self.assertEqual(row['resolution_method'],method)
                self.assertEqual(row['normalized_value'],'m')
                self.assertNotIn('confidence',row)

    def test_unit_and_crs_labels_do_not_imply_validation(self):
        rows=self.rows(['m'],'unit')+self.rows(['EPSG:32639'],'crs')
        view={r['field']:r for r in annotate_rows(rows)}
        self.assertEqual(view['unit']['evidence_status'],'native_declaration')
        self.assertEqual(view['crs']['evidence_status'],'candidate')
        rows+=self.rows(['validated_metric_profile']*2,'unit_status')
        rows+=self.rows(['verified']*2,'crs_status')
        view={r['field']:r for r in annotate_rows(rows)}
        self.assertEqual(view['unit']['evidence_status'],'validated_native_profile')
        self.assertEqual(view['crs']['evidence_status'],'validated_source_reference')

    def test_nonfinite_or_empty_values_cannot_be_normalized_as_known(self):
        values=[float('nan'),float('inf'),{},[],{'step':float('inf')}]
        self.assertEqual(resolve_values(values)['status'],'unresolved')
        self.assertTrue(all(r['normalized_value'] is None for r in annotate_rows(self.rows(values))))
        self.assertTrue(status_is({'s':['verified','verified']},'s','verified'))
        self.assertFalse(status_is({'s':['verified','unresolved']},'s','verified'))

    def test_unsupported_evidence_major_is_explicit(self):
        row=self.rows(['m'])[0];row['evidence_schema']='geoviewer.metadata-evidence/2'
        with self.assertRaisesRegex(ValueError,'Unsupported metadata evidence schema'):
            summarize_rows([row])

    def test_bound_seismic_metadata_does_not_silently_override_native_observation(self):
        fx=fixtures.SeismicPreparationTests();fx.setUp()
        a=fixtures.assess_mapping(profile='opendtect',node=dict(entity_key='cube',category='seismic'),
            fields={'seismic_conversion':[fx.plan],'conversion_qc':[fx.qc],'crs':['EPSG:32640']},
            relations={},artifacts=[],core_reasons=[],decision=fx.decision,entity_keys={'cube'})
        self.assertIn('CONFLICTING_CRS',a['reason_codes'])
        self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')
        self.assertIsNone(a['effective_source_fields']['crs']['value'])
        self.assertEqual(a['effective_source_fields']['crs']['source_resolution']['status'],'conflict')

if __name__=='__main__':unittest.main()
