# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Preparation contract controls; synthetic metadata is not real import evidence."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from geoviewer_contract import POINTER, canonical
from geoviewer_contract_retention import build_retention_view
from geoviewer_receiving_profiles import PROFILE_REVISION, assess_mapping, load_decisions
from validate_geoviewer_contract import validate_contract, main
import test_geoviewer_contract as fixtures
write = fixtures.write


class ProfileIntegrationTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    add_row = fixtures.ContractTests.add_row
    publish = fixtures.ContractTests.publish
    table = fixtures.ContractTests.table

    def ready_log(self):
        hashes = {'08_native_project/project_file/a.pet': 'a' * 64,
                  '08_native_project/ptd_store/Model.ptd': 'b' * 64,
                  '08_native_project/ptd_store/Data.ptd': 'c' * 64}
        self.native.update(source_hashes_before=hashes, source_hashes_after=hashes, source_unchanged=True)
        # Prove the owner is a native well; a name-only unknown reference is not
        # enough for the exact-identity route. No source head payload is added.
        self.native['objects'].append(dict(object_id='well-one', subject_type='WellSubject',
                                          status='metadata_only'))
        self.log = dict(object_id='log-one', subject_type='WellLogSubject', status='decoded', well_id='well-one',
                        unit='m/s', unit_status='validated_metric_profile', md_unit='m', md_reference='stored measured depth from KB',
                        log_kind='continuous_scalar', index_kind='measured_depth', sampling_kind='point_samples',
                        index_shape='scalar_1d', value_shape='scalar_1d',
                        null_semantics={'representation': 'empty CSV field', 'source_rule': 'native validity mask'})
        self.native['objects'].append(self.log)
        self.add_row('log-one', 'logs/samples.csv', b'md,value\n100,2100\n101,2200\n', 'CSV')
        self.rows[-1]['category'] = 'logs'
        self.republish()

    def republish(self):
        write(self.package / '07_workflows_reports/native_recovery/native_recovery_report.json', self.native)
        self.publish()
        self.pointer = json.loads((self.exports / POINTER).read_text())
        self.keys = {e['source_id']: e['entity_key'] for e in self.table('entities')}

    def decisions(self, profile='opendtect', record=None, source_id='log-one'):
        proof = Path(self.temp.name) / 'reviewed-reference.json'
        proof.write_bytes(canonical({'scope': 'synthetic exact-entity independent-reference placeholder; not a scientific fixture'}))
        item = dict(target_version='7.0.10' if profile == 'opendtect' else '2023.1.1',
                    target_api_version='1.0.0' if profile == 'opendtect' else None,
                    target_object_id='chosen-target-object', reviewer='fixture reviewer', method='synthetic exact identity comparison',
                    evidence=[dict(path=proof.name, sha256=hashlib.sha256(proof.read_bytes()).hexdigest(), description='Synthetic reference')],
                    well_target=dict(source_entity_key=self.keys.get('well-one'), target_id='existing-destination-well', state='existing'))
        item.update(record or {})
        data = dict(schema='geoviewer.preparation-decisions/1', generation=self.pointer['generation'],
                    manifest_sha256=self.pointer['manifest_sha256'], profile_revision=PROFILE_REVISION[profile],
                    entities={self.keys[source_id]: item})
        path = Path(self.temp.name) / 'decisions.json'
        write(path, data)
        return path

    def report(self, decisions=None, profile='opendtect', **kw):
        return validate_contract(self.exports / POINTER, profile, decisions=decisions, **kw)

    def log_assessment(self, report):
        return next(a for a in report['assessments'] if a['entity_key'] == self.keys['log-one'])

    def test_existing_well_scalar_log_qualifies_without_optional_crs_or_display_name(self):
        self.ready_log(); decision = self.decisions()
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()}
        report = self.report(decision)
        a = self.log_assessment(report)
        self.assertEqual(a['receiving_state'], 'qualified_for_planned_mapping')
        self.assertEqual(a['receiving_assessment']['reason_codes'], [])
        self.assertEqual(report['counts']['qualified_for_planned_mapping'], 1)
        self.assertEqual(report['counts']['receiving_imports_executed'], 0)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()})
        # The unqualified unrelated surface does not block this log.
        self.assertGreater(report['counts']['source_blocked'], 0)

    def test_missing_or_planned_well_never_assumed_existing(self):
        self.ready_log()
        for target, code in [(None, 'WELL_TARGET_BINDING_UNRESOLVED'),
                ({'source_entity_key': self.keys['well-one'], 'target_id': 'new', 'state': 'planned'}, 'TARGET_DEPENDENCY_CREATION_UNSUPPORTED'),
                ({'source_entity_key': self.keys['grid-a'], 'target_id': 'same-name', 'state': 'existing'}, 'WELL_TARGET_BINDING_UNRESOLVED')]:
            with self.subTest(code=code):
                a = self.log_assessment(self.report(self.decisions(record={'well_target': target})))
                self.assertNotEqual(a['receiving_state'], 'qualified_for_planned_mapping')
                self.assertIn(code, a['receiving_assessment']['reason_codes'])

    def test_wrong_target_identity_types_never_qualify(self):
        self.ready_log()
        for target in (True, 42, ['target']):
            a = self.log_assessment(self.report(self.decisions(record={'target_object_id': target})))
            self.assertIn('TARGET_OBJECT_DECISION_REQUIRED', a['receiving_assessment']['reason_codes'])

    def test_profile_versions_and_categorical_interval_semantics_are_not_guessed(self):
        self.ready_log()
        a = self.log_assessment(self.report(self.decisions(record={'target_version': '2026'})))
        self.assertIn('TARGET_PROFILE_DECISION_REQUIRED', a['receiving_assessment']['reason_codes'])
        self.log.update(log_kind='categorical', sampling_kind='interval_boundaries')
        self.republish()
        a = self.log_assessment(self.report(self.decisions()))
        self.assertEqual(a['receiving_state'], 'unsupported_target_mapping')
        self.assertIn('LOG_INTERVAL_SEMANTICS_UNRESOLVED', a['receiving_assessment']['reason_codes'])

    def test_metadata_assertion_is_attributed_and_conflict_does_not_overwrite_source(self):
        self.ready_log(); self.log.update(unit='unknown', unit_status='unresolved'); self.republish()
        assertion = {'unit': dict(value='m/s', scope='exact_source_entity', evidence_index=0)}
        path = self.decisions(record={'source_assertions': assertion})
        a = self.log_assessment(self.report(path))
        self.assertEqual(a['receiving_state'], 'qualified_for_planned_mapping')
        self.assertEqual(a['receiving_assessment']['source_assertions'], assertion)
        self.assertEqual(next(e['raw_value'] for e in self.table('metadata_evidence') if e['entity_key'] == self.keys['log-one'] and e['field'] == 'unit'), 'unknown')
        self.log.update(unit='ft/s'); self.republish()
        a = self.log_assessment(self.report(self.decisions(record={'source_assertions': assertion})))
        self.assertIn('SOURCE_DECISION_CONFLICT', a['receiving_assessment']['reason_codes'])

    def test_decisions_require_exact_generation_complete_evidence_and_no_qc_override(self):
        self.ready_log(); path = self.decisions(); pristine = json.loads(path.read_text())
        edits = [lambda x: x.update(generation='old'), lambda x: x.update(manifest_sha256='0' * 64),
                 lambda x: x['entities'][self.keys['log-one']]['evidence'][0].update(sha256='f' * 64),
                 lambda x: x['entities'][self.keys['log-one']]['evidence'][0].update(path='../originals/file.json'),
                 lambda x: x['entities'][self.keys['log-one']].update(source_assertions={'numerical_qc': {'value': True}}),
                 lambda x: x['entities'].update(unrelated=x['entities'][self.keys['log-one']])]
        for change in edits:
            data = copy.deepcopy(pristine); change(data); write(path, data)
            with self.assertRaises(ValueError): self.report(path)
        write(path, pristine)
        reference = path.parent / 'reviewed-reference.json'; reference.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'): self.report(path)

    def test_wrong_types_cannot_stand_in_for_units_and_references(self):
        self.ready_log(); self.log.update(md_unit=True, md_reference=17, unit=False); self.republish()
        a = self.log_assessment(self.report(self.decisions()))
        self.assertIn('MD_UNIT_UNRESOLVED', a['receiving_assessment']['reason_codes'])
        self.assertIn('STORED_VALUE_UNIT_UNRESOLVED', a['receiving_assessment']['reason_codes'])

    def test_latest_source_failure_cannot_reuse_older_accepted_file(self):
        self.ready_log(); self.log['status'] = 'conversion_failed'; self.republish()
        a = self.log_assessment(self.report(self.decisions()))
        self.assertIn('SOURCE_RECOVERY_NOT_COMPLETE', a['receiving_assessment']['reason_codes'])
        self.assertNotEqual(a['receiving_state'], 'qualified_for_planned_mapping')

    def test_unrelated_partial_category_does_not_disqualify_accepted_log(self):
        self.ready_log()
        row = self.rows.pop(0); row['status'] = 'checkpoint_only_partial'
        self.index['partial_files'].append(row); self.index.update(status='partial', partial=True)
        self.republish(); report = self.report(self.decisions())
        self.assertEqual(self.log_assessment(report)['receiving_state'], 'qualified_for_planned_mapping')
        surface = next(a for a in report['assessments'] if a['entity_key'] == self.keys['grid-a'])
        self.assertNotEqual(surface['receiving_state'], 'qualified_for_planned_mapping')
        self.assertEqual(self.log_assessment(report)['state_axes']['original_run_publication'], 'partial')

    def test_property_needs_qualified_geometry_parent_not_matching_name(self):
        self.ready_log()
        registration = dict(dimensions=[2, 3], origin_xy=[100, 200], i_basis=[1, 0], j_basis=[0, 1], index_order='i_major_j_minor')
        parent, prop = self.native['objects'][:2]
        for obj in (parent, prop):
            obj.update(status='decoded', unit='m', unit_status='validated_metric_profile', crs='EPSG:32639', crs_status='verified',
                       horizontal_unit='m', vertical_domain='depth', vertical_unit='m', vertical_reference='MSL', grid_registration=registration)
        parent['surface_role'] = 'horizon_geometry'; prop['surface_role'] = 'mapped_property'
        prop['node_registration'] = dict(parent_entity_key=self.keys['grid-a'], method='native node keys + exact coordinates', count=6, validated=True)
        self.add_row('grid-b', 'grids/property_node_definitions.csv', b'i,j,defined\n', 'CSV')
        self.republish()
        survey = dict(target_id='survey', crs='EPSG:32639', horizontal_unit='m', vertical_domain='depth', vertical_unit='m',
                      vertical_reference='MSL', grid_registration=registration)
        path = self.decisions(source_id='grid-b', record={'survey': survey})
        report = self.report(path)
        a = next(a for a in report['assessments'] if a['entity_key'] == self.keys['grid-b'])
        self.assertIn('PARENT_MAPPING_NOT_QUALIFIED', a['receiving_assessment']['reason_codes'])
        # Matching names do not permit the log to substitute for source geometry.
        prop['parent_id'] = 'log-one'; prop['node_registration']['parent_entity_key'] = self.keys['log-one']
        self.context['objects'][1]['parent_id'] = 'log-one'; write(self.package / '01_project_metadata/project_context.json', self.context)
        self.republish(); a = next(a for a in self.report(self.decisions(source_id='grid-b', record={'survey': survey}))['assessments'] if a['entity_key'] == self.keys['grid-b'])
        self.assertIn('PARENT_GEOMETRY_UNRESOLVED', a['receiving_assessment']['reason_codes'])

    def test_property_masks_are_required_independent_of_join_method_and_assertions(self):
        # Exercise a fully qualified preparation, then remove only one mask.
        # A quoted method name or reviewed source assertion cannot bypass it.
        for method in ('native node keys + exact coordinates', 'other declared method', 'reviewed_assertion'):
            for owner in ('grid-a', 'grid-b'):
                for state in ('missing', 'partial', 'not_exported', 'pruned'):
                    with self.subTest(method=method, owner=owner, state=state):
                        fx = ProfileIntegrationTests(); fx.setUp()
                        try:
                            fx.ready_log()
                            grid = dict(dimensions=[2,3], origin_xy=[100,200], i_basis=[1,0],
                                        j_basis=[0,1], index_order='i_major_j_minor')
                            parent, prop = fx.native['objects'][:2]
                            for obj in (parent, prop):
                                obj.update(status='decoded', unit='m', unit_status='validated_metric_profile',
                                           crs='EPSG:32639', crs_status='verified', horizontal_unit='m',
                                           vertical_domain='depth', vertical_unit='m', vertical_reference='MSL',
                                           grid_registration=grid)
                            parent['surface_role']='horizon_geometry'; prop['surface_role']='mapped_property'
                            registration=dict(parent_entity_key=fx.keys['grid-a'], method=method, count=6, validated=True)
                            if method != 'reviewed_assertion': prop['node_registration']=registration
                            fx.add_row('grid-b','grids/property_node_definitions.csv',b'i,j,defined\n','CSV')
                            fx.republish()
                            survey=dict(target_id='survey',crs='EPSG:32639',horizontal_unit='m',
                                        vertical_domain='depth',vertical_unit='m',vertical_reference='MSL',grid_registration=grid)

                            retention_path=None
                            def assessment():
                                path=fx.decisions(source_id='grid-b',record={'survey':survey})
                                decision=json.loads(path.read_text())
                                record=decision['entities'][fx.keys['grid-b']]
                                decision['entities'][fx.keys['grid-a']]=dict(record,target_object_id='parent-horizon')
                                if method == 'reviewed_assertion':
                                    record['source_assertions']={'node_registration':dict(value=registration,
                                        scope='exact_source_entity',evidence_index=0)}
                                write(path,decision)
                                return next(a for a in fx.report(path,retention_view=retention_path)['assessments'] if a['entity_key']==fx.keys['grid-b'])

                            self.assertEqual(assessment()['receiving_state'],'qualified_for_planned_mapping')
                            row=next(r for r in fx.rows if r['object_id']==owner and 'node_definitions' in r['path'])
                            if state=='missing':
                                (fx.exports/row['path']).unlink()
                            elif state=='partial':
                                fx.rows.remove(row); fx.index['partial_files'].append(row); fx.republish()
                            elif state=='pruned':
                                artifacts=fx.table('artifacts')
                                dispositions=[dict(artifact_key=a['artifact_key'],
                                    status='pruned' if a['relative_path']==row['path'] else 'retained',
                                    reason='Synthetic mask retention',evidence_reference='fixture-retention-receipt') for a in artifacts]
                                retention_path=Path(fx.temp.name)/'retention.json'
                                write(retention_path,build_retention_view(generation=fx.pointer['generation'],
                                    manifest_sha256=fx.pointer['manifest_sha256'],artifacts=artifacts,dispositions=dispositions))
                                (fx.exports/row['path']).unlink()
                            else:
                                row['status']='checkpoint_only_partial'; fx.republish()
                            result=assessment()
                            self.assertIn('NODE_MASK_UNAVAILABLE',result['reason_codes'])
                            self.assertIn('NODE_MASK_UNAVAILABLE',result['receiving_assessment']['reason_codes'])
                            self.assertNotEqual(result['receiving_state'],'qualified_for_planned_mapping')
                        finally:
                            fx.doCleanups()

    def test_pruned_copy_preserves_outcome_and_retained_claim_does_not_create_availability(self):
        self.ready_log(); decision = self.decisions(); artifacts = self.table('artifacts')
        log = next(a for a in artifacts if a['entity_key'] == self.keys['log-one'])
        dispositions = [dict(artifact_key=a['artifact_key'], status='pruned' if a is log else 'retained',
                             reason='Selected evidence-copy retention', evidence_reference='fixture-retention-receipt') for a in artifacts]
        view = build_retention_view(generation=self.pointer['generation'], manifest_sha256=self.pointer['manifest_sha256'],
                                   artifacts=artifacts, dispositions=dispositions)
        path = Path(self.temp.name) / 'retention.json'; write(path, view)
        (self.exports / log['relative_path']).unlink()
        report = self.report(decision, retention_view=path)
        a = self.log_assessment(report)
        self.assertEqual(a['receiving_state'], 'blocked_missing_payload')
        self.assertTrue(any(d['code'] == 'ARTIFACT_PRUNED' for d in report['diagnostics']))
        self.assertEqual(next(a for a in self.table('artifacts') if a['artifact_key'] == log['artifact_key'])['original_status'], 'exported')
        dispositions[-1]['status'] = 'retained'
        for d in dispositions: d['status'] = 'retained'
        write(path, build_retention_view(generation=self.pointer['generation'], manifest_sha256=self.pointer['manifest_sha256'], artifacts=artifacts, dispositions=dispositions))
        report = self.report(decision, retention_view=path)
        self.assertTrue(any(d['code'] == 'RETENTION_STATE_CONFLICT' for d in report['diagnostics']))
        self.assertEqual(self.log_assessment(report)['receiving_state'], 'blocked_missing_payload')

    def test_pruning_does_not_rewrite_recorded_qc_or_metadata_axes(self):
        self.ready_log()
        before = self.log_assessment(self.report())['state_axes']
        (self.exports / 'logs/samples.csv').unlink()
        after = self.log_assessment(self.report())['state_axes']
        self.assertEqual(before['numerical_qc'], 'recorded_validated')
        self.assertEqual(after['numerical_qc'], before['numerical_qc'])
        self.assertEqual(after['metadata'], before['metadata'])
        self.assertEqual(after['metadata_reason_codes'], before['metadata_reason_codes'])
        self.assertEqual(after['publication'], before['publication'])
        self.assertNotEqual(after['availability'], before['availability'])
        self.assertEqual(after['integrity'], 'unavailable')

    def test_partial_object_publication_axis_is_not_accepted_only(self):
        self.ready_log()
        row = dict(self.rows[-1], path='logs/checkpoint.csv', status='checkpoint_only_partial')
        (self.exports / row['path']).write_bytes((self.exports / 'logs/samples.csv').read_bytes())
        self.index['partial_files'].append(row)
        self.index['status'] = 'partial'
        self.republish()
        a = self.log_assessment(self.report())
        self.assertEqual(a['state_axes']['publication'], 'accepted_and_partial_artifacts')
        self.assertIn('PARTIAL_PUBLICATION_PRESENT', a['reason_codes'])

    def test_relocated_profile_and_cli_report_are_read_only(self):
        self.ready_log(); decision = self.decisions()
        moved = Path(self.temp.name) / 'moved'; shutil.move(str(self.run), moved)
        pointer = moved / 'EXPORTS' / POINTER
        result = validate_contract(pointer, 'opendtect', decisions=decision)
        self.assertEqual(result['counts']['qualified_for_planned_mapping'], 1)
        out = Path(self.temp.name) / 'profile-report.json'
        self.assertEqual(main([str(pointer), '--profile', 'opendtect', '--decisions', str(decision), '--output', str(out)]), 0)
        self.assertEqual(json.loads(out.read_text())['counts']['receiving_imports_executed'], 0)
        self.assertEqual(main([str(pointer), '--decisions', str(decision), '--output', str(moved / 'report.json')]), 2)

    def test_surface_role_lattice_and_derived_mapping(self):
        self.ready_log()
        grid = self.native['objects'][0]
        registration = dict(dimensions=[2, 3], origin_xy=[100, 200], i_basis=[1, 0], j_basis=[0, 1], index_order='i_major_j_minor')
        grid.update(status='decoded', unit='m', unit_status='validated_metric_profile', crs='EPSG:32639', crs_status='verified',
                    horizontal_unit='m', vertical_domain='depth', vertical_unit='m', vertical_reference='MSL, positive down',
                    surface_role='horizon_geometry', grid_registration=registration)
        self.republish()
        survey = dict(target_id='survey-one', crs='EPSG:32639', horizontal_unit='m', vertical_domain='depth',
                      vertical_unit='m', vertical_reference='MSL, positive down', grid_registration=registration)
        def assess(extra=None):
            r = self.report(self.decisions(source_id='grid-a', record={'survey': survey, **(extra or {})}))
            return next(a for a in r['assessments'] if a['entity_key'] == self.keys['grid-a'])['receiving_assessment']
        self.assertEqual(assess()['receiving_state'], 'qualified_for_planned_mapping')
        survey['grid_registration'] = {**registration, 'origin_xy': [101, 200]}
        self.assertIn('SURVEY_LATTICE_MISMATCH', assess()['reason_codes'])
        proposed = assess({'derived_mapping': dict(operation='resample to different lattice', losses=['original nodes no longer exact'], prerequisites=['separate validated derivation'])})
        self.assertEqual(proposed['receiving_state'], 'requires_derived_mapping')
        grid['surface_role'] = 'mapped_property'; self.republish()
        self.assertIn('SURFACE_VALUE_ROLE_UNRESOLVED', assess()['reason_codes'])

    def test_paleoscan_profile_is_distinct_and_no_universal_runtime_claim(self):
        self.ready_log(); result = self.report(self.decisions(profile='paleoscan'), profile='paleoscan')
        self.assertEqual(self.log_assessment(result)['receiving_state'], 'qualified_for_planned_mapping')
        self.assertEqual(result['profile_revision'], 'paleoscan-2023.1.1-file-preparation/7')
        self.assertIn('no runtime', result['receiving_profile']['evidence_basis'])

    def test_streamed_table_count_and_row_bounds(self):
        self.ready_log()
        with patch('validate_geoviewer_contract.MAX_TABLE_ROWS', 1):
            with self.assertRaisesRegex(ValueError, 'row bound'): self.report()
        with patch('validate_geoviewer_contract.MAX_ROW_BYTES', 32):
            with self.assertRaisesRegex(ValueError, 'byte/row bounds'): self.report()


class SeismicPreparationTests(unittest.TestCase):
    def setUp(self):
        trace = {'inline': (189, 4), 'crossline': (193, 4), 'x': (181, 4), 'y': (185, 4),
                 'coordinate_scalar': (71, 2), 'coordinate_units': (89, 2), 'sample_count': (115, 2),
                 'sample_interval_us': (117, 2), 'delay_ms': (109, 2)}
        self.plan = dict(crs='EPSG:32639', crs_verified=True, horizontal_unit='m', domain='time', source_vertical_unit='ms', time_convention='TWT',
            size=[2, 3, 4], inline=[100, 1], crossline=[200, 1],
            native_axis=dict(origin=0, increment=2, count=4), components=[dict(index=0, name=None)],
            geometry=dict(storage_kind='regular_3d_cube', full_rectangular_storage=True, trace_order='inline_major_crossline_minor', inline_count=2, crossline_count=3,
                          trace_count=6, origin_xy=[100, 200], inline_basis=[1, 0], crossline_basis=[0, 1]),
            segy_header_profile=dict(schema='fieldviewer.generated-segy/1', revision='1.0', endian='big', sample_format_code=5,
                                     trace_fields={k: dict(byte=b, width=w) for k, (b, w) in trace.items()}))
        self.qc = dict(binary_headers_verified=True, headers_verified=True, all_decoded_samples_exact=True, traces=6, samples_verified=24)
        self.decision = dict(target_version='7.0.10', target_api_version='1.0.0', target_object_id='seismic-target',
            component_mapping=[dict(source_index=0, source_name=None, target_name='chosen-amplitude')],
            survey=dict(target_id='survey', crs='EPSG:32639', horizontal_unit='m', vertical_domain='time', vertical_unit='ms',
                        time_convention='TWT', geometry=copy.deepcopy(self.plan['geometry'])))

    def assess(self, reasons=None):
        return assess_mapping(profile='opendtect', node=dict(entity_key='cube', category='seismic'),
            fields={'seismic_conversion': [self.plan], 'conversion_qc': [self.qc]}, relations={}, artifacts=[],
            core_reasons=reasons or [], decision=self.decision, entity_keys={'cube'})

    def test_explicit_single_component_plan_and_no_import_claim(self):
        a = self.assess(); self.assertEqual(a['receiving_state'], 'qualified_for_planned_mapping')
        self.assertIn('no runtime', a['qualification_scope']); self.assertIn('independently staged', a['storage_guidance'])

    def test_no_dropped_added_or_named_by_guess_components(self):
        for mapping in ([], self.decision['component_mapping'] * 2,
                        [dict(source_index=0, source_name='invented-native-name', target_name='x')]):
            self.decision['component_mapping'] = mapping
            self.assertIn('SEISMIC_COMPONENT_MAPPING_UNRESOLVED', self.assess()['reason_codes'])

    def test_old_header_qc_and_partial_sample_coverage_are_blocked(self):
        self.qc.pop('binary_headers_verified')
        self.assertEqual(self.assess()['receiving_state'], 'blocked_qc')
        self.qc.update(binary_headers_verified=True, samples_verified=20)
        self.assertIn('SEISMIC_QC_COUNT_CONFLICT', self.assess()['reason_codes'])

    def test_time_convention_and_axis_are_not_inferred_from_lattice(self):
        self.decision['survey']['time_convention'] = 'OWT'
        self.assertIn('SURVEY_CONTEXT_CONFLICT', self.assess()['reason_codes'])
        self.plan['native_axis']['increment'] = 0
        self.assertIn('SEISMIC_AXIS_UNRESOLVED', self.assess()['reason_codes'])

    def test_missing_payload_or_stale_metadata_is_never_qualified(self):
        self.assertEqual(self.assess(['NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD'])['receiving_state'], 'blocked_missing_payload')
        self.assertEqual(self.assess(['SEISMIC_METADATA_STALE'])['receiving_state'], 'blocked_qc')

    def test_malformed_seismic_records_have_explicit_validation_error(self):
        original = copy.deepcopy(self.plan)
        for field in ('geometry', 'native_axis', 'segy_header_profile'):
            with self.subTest(field=field):
                self.plan = {**original, field: ['invalid record']}
                with self.assertRaisesRegex(ValueError, 'Expected metadata object'):
                    self.assess()
        self.plan = original
        self.qc = ['invalid record']
        with self.assertRaisesRegex(ValueError, 'Expected metadata object'):
            self.assess()

    def test_nonfinite_geometry_and_malformed_counts_components_cannot_qualify(self):
        for target in (True, 42, ['survey']):
            self.decision['survey']['target_id'] = target
            self.assertIn('SURVEY_DECISION_REQUIRED', self.assess()['reason_codes'])
        self.decision['survey']['target_id'] = 'survey'
        self.plan['geometry']['inline_basis'] = [1e308, 0]
        self.plan['geometry']['crossline_basis'] = [0, 1e308]
        self.decision['survey']['geometry'] = copy.deepcopy(self.plan['geometry'])
        self.assertIn('SEISMIC_GEOMETRY_UNRESOLVED', self.assess()['reason_codes'])
        self.plan['native_axis']['count'] = 'four'
        self.decision['component_mapping'][0]['target_name'] = ['amplitude']
        codes = self.assess()['reason_codes']
        self.assertIn('SEISMIC_AXIS_UNRESOLVED', codes)
        self.assertIn('SEISMIC_QC_COUNT_CONFLICT', codes)
        self.assertIn('SEISMIC_COMPONENT_MAPPING_UNRESOLVED', codes)


if __name__ == '__main__':
    unittest.main()
