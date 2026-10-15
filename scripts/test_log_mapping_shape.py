# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bound-catalogue log mapping must respect stored shape and semantic evidence."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

import test_geoviewer_receiving_profiles as fixtures
from geoviewer_receiving_profiles import PROFILE_REVISION


class LogMappingShapeTests(unittest.TestCase):
    setUp = fixtures.ProfileIntegrationTests.setUp
    add_row = fixtures.ProfileIntegrationTests.add_row
    publish = fixtures.ProfileIntegrationTests.publish
    table = fixtures.ProfileIntegrationTests.table
    republish = fixtures.ProfileIntegrationTests.republish
    decisions = fixtures.ProfileIntegrationTests.decisions
    report = fixtures.ProfileIntegrationTests.report
    log_assessment = fixtures.ProfileIntegrationTests.log_assessment

    def ready(self):
        fixtures.ProfileIntegrationTests.ready_log(self)
        self.log.update(index_shape='scalar_1d', value_shape='scalar_1d',
                        interval_records=False, curve_kind='continuous_scalar',
                        index_type='measured_depth', value_role='native_scalar_values',
                        blob_type='FloatWellLog')
        self.republish()

    def assessment(self, **kw):
        return self.log_assessment(self.report(self.decisions(**kw), profile=kw.get('profile', 'opendtect')))

    def test_complete_scalar_log_remains_qualified_in_both_profiles(self):
        self.ready()
        for profile in ('opendtect', 'paleoscan'):
            a=self.assessment(profile=profile)
            self.assertEqual(a['receiving_state'], 'qualified_for_planned_mapping')
            self.assertEqual(a['receiving_assessment']['reason_codes'], [])

    def test_missing_or_nonscalar_shapes_never_qualify(self):
        self.ready()
        for field, code in (('value_shape','LOG_VALUE_SHAPE_UNSUPPORTED'),
                            ('index_shape','LOG_INDEX_SHAPE_UNSUPPORTED')):
            for value in (None, 'array_2d', 'vector', True, ['scalar_1d'], {'kind':'scalar_1d'}):
                with self.subTest(field=field,value=value):
                    self.log[field]=value;self.republish()
                    a=self.assessment()
                    self.assertNotEqual(a['receiving_state'], 'qualified_for_planned_mapping')
                    expected=code.replace('_UNSUPPORTED','_UNRESOLVED') if value is None else code
                    self.assertIn(expected,a['receiving_assessment']['reason_codes'])
                    self.assertEqual(a['receiving_state'], 'blocked_metadata' if value is None else 'unsupported_target_mapping')
            self.log[field]='scalar_1d'

    def test_raw_semantic_contradictions_cannot_be_hidden_by_summary_labels(self):
        self.ready(); original=copy.deepcopy(self.log)
        contradictions=(('interval_records',True),('interval_records',1),
                        ('curve_kind','array'),('curve_kind','categorical_codes'),
                        ('index_type','time'),('value_role','native_codes'),('blob_type','IntWellLog'))
        for field,value in contradictions:
            with self.subTest(field=field,value=value):
                self.log.clear();self.log.update(original);self.log[field]=value;self.republish()
                a=self.assessment()
                self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')
                self.assertIn('LOG_SOURCE_SEMANTICS_CONFLICT',a['receiving_assessment']['reason_codes'])

    def test_conflicting_shape_observations_remain_blocked(self):
        self.ready()
        self.native['objects'].append(dict(self.log,value_shape='array_2d'))
        self.republish();a=self.assessment()
        self.assertIn('CONFLICTING_VALUE_SHAPE',a['receiving_assessment']['reason_codes'])
        self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')

    def test_missing_shape_may_use_attributed_exact_source_evidence(self):
        self.ready();self.log.pop('value_shape');self.republish()
        assertion={'value_shape':dict(value='scalar_1d',scope='exact_source_entity',evidence_index=0)}
        a=self.assessment(record={'source_assertions':assertion})
        self.assertEqual(a['receiving_state'],'qualified_for_planned_mapping')
        self.assertEqual(a['receiving_assessment']['source_assertions'],assertion)
        self.assertFalse(any(x['field']=='value_shape' and x['entity_key']==self.keys['log-one'] for x in self.table('metadata_evidence')))

    def test_shape_assertion_does_not_override_stored_array_shape(self):
        self.ready();self.log['value_shape']='array_2d';self.republish()
        a=self.assessment(record={'source_assertions':{'value_shape':dict(value='scalar_1d',scope='exact_source_entity',evidence_index=0)}})
        self.assertIn('SOURCE_DECISION_CONFLICT',a['receiving_assessment']['reason_codes'])
        self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')

    def test_blocked_mapping_keeps_numerical_payload_and_catalogue_unchanged(self):
        self.ready();self.log['interval_records']=True;self.republish()
        expected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()}
        a=self.assessment()
        self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')
        self.assertEqual(expected,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()})
        self.assertEqual(len(self.index['files']),len(self.rows))

    def test_old_preparation_revision_cannot_silently_reuse_decisions(self):
        self.ready();p=self.decisions();d=json.loads(p.read_text())
        d['profile_revision']='opendtect-7.0.10-odbind-1.0.0-preparation/4'
        fixtures.write(p,d)
        self.assertNotEqual(PROFILE_REVISION['opendtect'],d['profile_revision'])
        with self.assertRaisesRegex(ValueError,'generation and profile'):self.report(p)


if __name__=='__main__':unittest.main()
