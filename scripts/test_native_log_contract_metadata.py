# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Exact synthetic native-log metadata and non-promotion controls."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_native_recovery as recovery
import test_petrel_native_recovery as fixtures


class NativeLogContractMetadataTests(unittest.TestCase):
    def fixture(self, **kwargs):
        writer = fixtures.RecoveryTests('runTest')
        writer.setUp()
        self.addCleanup(writer.temp.cleanup)
        package = writer.fixture(**kwargs)
        with redirect_stdout(io.StringIO()):
            report = recovery.run(package)
        record = report['objects'][0]
        sidecar = next((package/'native_data').rglob('metadata.json'))
        return package, report, record, json.loads(sidecar.read_text(encoding='utf-8'))

    def test_continuous_null_and_nonuniform_positions_retain_native_evidence(self):
        package, _, record, sidecar = self.fixture()
        self.assertEqual(record['status'], 'decoded')
        for data in (record, sidecar):
            self.assertEqual(data['curve_kind'], 'continuous_scalar')
            self.assertEqual(data['index_type'], 'measured_depth')
            self.assertEqual(data['index_shape'], 'scalar_1d')
            self.assertEqual(data['value_shape'], 'scalar_1d')
            self.assertEqual(data['value_encoding'], 'float32')
            self.assertEqual(data['index_base'], 0)
            self.assertEqual(data['sampling_kind'], 'point_samples')
            self.assertEqual(data['sampling_regularity'], 'nonuniform')
            self.assertEqual(data['log_kind'], 'continuous_scalar')
            self.assertEqual(data['index_kind'], 'measured_depth')
            self.assertEqual(data['null_count'], 1)
            self.assertEqual(data['native_null_value'], recovery.FLOAT_NULL)
            self.assertEqual(data['md_unit'], data['depth_unit'])
            self.assertIsNone(data['md_reference'])
            self.assertEqual(data['native_source_locator']['data_pk'], 1)
            self.assertEqual(data['native_decoder_profile']['object_version'],
                             recovery.PROFILES['FloatWellLog'])
        lines = next(package.rglob('samples.csv')).read_text(encoding='utf-8').splitlines()
        self.assertEqual([line.split(',')[1] for line in lines[1:]], ['10.25', '11.25', '13.25'])
        self.assertEqual([line.split(',')[3] for line in lines[1:]], ['0', '1', '0'])

    def test_categorical_boundaries_keep_codes_and_unknown_labels(self):
        package, _, record, sidecar = self.fixture(
            kind='IntWellLog', doc=fixtures.log_doc('IntWellLog', char=True,
                                           values=[2, 255, 4], intervals=True))
        for data in (record, sidecar):
            self.assertEqual(data['curve_kind'], 'categorical_codes')
            self.assertEqual(data['value_encoding'], 'uint8')
            self.assertEqual(data['native_null_value'], 255)
            self.assertTrue(data['interval_records'])
            self.assertEqual(data['sampling_kind'], 'interval_records')
            self.assertIsNone(data['category_lookup'])
            self.assertEqual(data['category_lookup_status'], 'unresolved')
            self.assertIn('unresolved', data['interval_boundary_semantics'])
            self.assertIn('interval boundary direction', data['research_gaps'])
            self.assertEqual(data['las_status'], 'not_written')
        self.assertFalse(list(package.rglob('*.las')))
        self.assertEqual([line.split(',')[2] for line in
                          next(package.rglob('samples.csv')).read_text(encoding='utf-8').splitlines()[1:]],
                         ['2', '255', '4'])

    def test_unknown_unit_and_md_reference_do_not_promote_to_las(self):
        package, report, record, sidecar = self.fixture(metric=False)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(record['status'], 'missing_metadata')
        self.assertEqual(record['unit_status'], 'unresolved')
        self.assertIsNone(sidecar['md_unit'])
        self.assertIsNone(sidecar['md_reference'])
        self.assertIsNone(sidecar['unit'])
        self.assertEqual(sidecar['sampling_kind'], 'point_samples')
        self.assertEqual(sidecar['sampling_regularity'], 'nonuniform')
        self.assertFalse(list(package.rglob('*.las')))
        declarations=sidecar['native_unit_evidence']
        self.assertEqual(declarations['unit_resolution_status'],'unresolved')
        self.assertFalse(declarations['project_matches_existing_metric_gate'])
        self.assertEqual(declarations['project']['source_role'],'project_display_preferences_not_stored_sample_units')
        template=declarations['template']
        self.assertEqual(template['fields']['initial_unit']['raw_value'],'gAPI')
        self.assertEqual(template['fields']['is_predefined']['raw_value'],True)
        self.assertIn(record['template_id'],template['locator'])

    def test_native_recovery_delivery_and_catalogue_keep_semantic_evidence(self):
        from geoviewer_delivery import publish_exports
        from geoviewer_contract import POINTER
        from geoviewer_contract_publication import publish_preparation, verify_publication_binding
        from validate_geoviewer_contract import validate_contract
        package, _, record, _ = self.fixture()
        destination = package.parent/'CONTRACT_EXPORTS'
        inventory = {'objects': [], 'counts': {}}
        index = publish_exports(package, inventory, destination)
        bundle = publish_preparation(package, destination, package.parent, inventory, file_index=index)
        self.assertEqual(verify_publication_binding(bundle, package.parent)['status'], 'verified')
        pointer = json.loads((destination/POINTER).read_text())
        folder = (destination/pointer['manifest']).parent
        nodes = [json.loads(line) for line in (folder/'entities.jsonl').read_text().splitlines()]
        entity = next(n for n in nodes if n['source_id'] == record['object_id'])
        fields = {v['field']:v for v in map(json.loads, (folder/'metadata_evidence.jsonl').read_text().splitlines())
                  if v['entity_key'] == entity['entity_key']}
        self.assertEqual(fields['log_kind']['raw_value'], 'continuous_scalar')
        self.assertEqual(fields['sampling_kind']['raw_value'], 'point_samples')
        self.assertEqual(fields['sampling_regularity']['raw_value'], 'nonuniform')
        self.assertEqual(fields['md_unit']['raw_value'], 'm')
        self.assertEqual(fields['native_source_locator']['raw_value']['data_pk'],1)
        self.assertEqual(fields['native_unit_evidence']['raw_value'],record['native_unit_evidence'])
        self.assertEqual(fields['project_units']['raw_value'],record['project_units'])
        self.assertIn('/objects/0/', fields['native_decoder_profile']['locator'])
        assessment = next(a for a in validate_contract(destination/POINTER,'opendtect')['assessments'] if a['entity_key']==entity['entity_key'])
        codes = assessment['receiving_assessment']['reason_codes']
        self.assertIn('MD_REFERENCE_UNRESOLVED',codes)
        for resolved in ('LOG_KIND_UNSUPPORTED','MD_UNIT_UNRESOLVED','LOG_INDEX_SEMANTICS_UNRESOLVED',
                         'LOG_INTERVAL_SEMANTICS_UNRESOLVED','NULL_SEMANTICS_UNRESOLVED'):
            self.assertNotIn(resolved,codes)
        self.assertNotEqual(assessment['receiving_state'],'qualified_for_planned_mapping')

    def test_unit_declarations_preserve_flags_factors_and_missing_without_conversion(self):
        from petrel_native_binary import Node
        template=Node('ContTemplateSubject',attrs={'Version':[1,2]},children=[
            Node('initial_unit',content=['ft']),Node('unit_is_customized',content=[True]),
            Node('conversion_factor',content=[.3048]),Node('unit_offset',content=[0.]),
            Node('measurement_link',children=[Node('tag',content=['unqualified'])])])
        evidence=recovery.unit_declarations(template,'Model.ptd/unique_tag=test',('is_predefined',))
        self.assertEqual(evidence['fields']['conversion_factor']['raw_value'],.3048)
        self.assertEqual(evidence['fields']['unit_offset']['raw_value'],0.)
        self.assertEqual(evidence['fields']['is_predefined']['status'],'absent')
        self.assertEqual(evidence['fields']['measurement_link']['status'],'nested_declaration_not_interpreted')
        self.assertNotIn('unit',evidence)
        self.assertEqual(template.get('initial_unit'),'ft')

    def test_metric_project_is_not_a_resolved_unit_for_qa_scalar(self):
        subject='QualityAssuranceAttrSubject'
        _, _, record, sidecar=self.fixture(kind='RegValGrid2',surface=True,
            surface_subject=subject,model_version=recovery.QA_MODEL_PROFILES[subject])
        for data in (record,sidecar):
            evidence=data['native_unit_evidence']
            self.assertTrue(evidence['project_matches_existing_metric_gate'])
            self.assertEqual(evidence['unit_resolution_status'],'unresolved_QA_scalar_semantics')
            self.assertEqual(evidence['unit_resolution_status'],data['unit_status'])
            self.assertIsNone(data['unit'])

    def test_unit_declaration_bounds_and_unsupported_values_are_explicit(self):
        from petrel_native_binary import Node
        node=Node('CustomTemplate',children=[Node('unit',content=['x'*4097]),
            Node('unit_factor',content=[float('inf')]),Node('unit_binary',content=[b'raw'])])
        fields=recovery.unit_declarations(node,'fixture')['fields']
        self.assertTrue(all(x['status']=='unsupported_or_excessive_scalar' for x in fields.values()))
        node.children=[Node('unit_'+str(i),content=['m']) for i in range(65)]
        self.assertEqual(recovery.unit_declarations(node,'fixture')['status'],'declaration_field_bound_exceeded')
        self.assertEqual(recovery.unit_declarations(None,'fixture')['status'],'record_absent')

    def test_duplicate_and_non_scalar_unit_evidence_does_not_break_recovery(self):
        from petrel_native_binary import Node
        node=Node('CustomTemplate',children=[Node('unit_factor',content=[1.]),
            Node('unit_factor',content=[2.]),Node('unit_offset',content=[0.,1.])])
        fields=recovery.unit_declarations(node,'fixture')['fields']
        self.assertEqual(fields['unit_factor']['status'],'ambiguous_duplicate_declaration')
        self.assertEqual(fields['unit_factor']['count'],2)
        self.assertEqual(fields['unit_offset']['status'],'non_scalar_declaration_not_interpreted')

    def test_sampling_regularity_does_not_overflow_into_uniform_claim(self):
        from unittest.mock import patch
        import numpy as np
        binary = fixtures.b
        doc = fixtures.log_doc()
        node = next(binary.read_documents(fixtures.frame(doc)))
        original = recovery.scalar_array
        def array(n, child, size=None):
            if n.name == 'md': return np.array([-1e308, 1e308, -1e308])
            return original(n, child, size)
        with patch.object(recovery,'scalar_array',side_effect=array):
            md, _, _, info = recovery.decode_log(node,'FloatWellLog')
        self.assertEqual(md.tolist(),[-1e308,1e308,-1e308])
        self.assertEqual(info['sampling_regularity'],'undetermined_numeric_range')
        self.assertFalse(info['md_strictly_increasing'])


if __name__ == '__main__':
    unittest.main()
