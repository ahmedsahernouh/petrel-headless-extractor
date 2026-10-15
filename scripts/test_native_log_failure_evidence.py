# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Rejected payloads retain bounded source evidence without becoming exports."""
from contextlib import redirect_stdout
import io
import json
import sqlite3
import unittest

import numpy as np
import petrel_native_binary as binary
import petrel_native_log_evidence as evidence
import petrel_native_recovery as recovery
import test_petrel_native_recovery as fixtures


def scalar(name, value):
    return binary.Node(name, content=[value])


def item(name='Limestone'):
    return binary.Node('color_item', attrs={'Version': 3}, children=[
        scalar('name', name), scalar('level', recovery.FLOAT_NULL),
        scalar('parent_item', 2147483647)])


def table():
    undefined = item('UNDEF'); undefined.name = 'undef_item'
    return binary.Node('DiscColorTableSubject', attrs={
        'xmlns': evidence.NAMESPACE, 'Version': evidence.TABLE_PROFILE}, children=[
        scalar('unique_tag', 'table-id'), binary.Node('color_table_style', attrs={
            'Type': 'ColorTableStyle', 'Version': evidence.STYLE_PROFILE}, children=[
            binary.Node('color_items', attrs={'Size': 2}, children=[item(), item('Shale')]),
            scalar('min_discrete_code', 7), undefined])])


class NativeLogFailureEvidenceTests(unittest.TestCase):
    def test_legend_retains_order_and_raw_base_without_assigning_codes_or_nulls(self):
        out = evidence.category_legend([table()], 'template-id', 'table-id')
        self.assertEqual(out['status'], 'recorded_native_legend')
        self.assertEqual([r['label'] for r in out['items']], ['Limestone', 'Shale'])
        self.assertEqual([r['native_ordinal'] for r in out['items']], [0, 1])
        self.assertEqual(out['raw_min_discrete_code'], 7)
        self.assertEqual(out['code_mapping_status'], 'unvalidated')
        self.assertTrue(all('code' not in r for r in out['items']))
        self.assertNotIn('null_value', out)

    def test_missing_duplicate_identity_profile_and_count_are_not_accepted(self):
        for tables in ([], [table(), table()]):
            self.assertEqual(evidence.category_legend(tables, 'template-id', 'table-id')['status'], 'unresolved')
        for mutation in ('profile', 'count', 'base', 'duplicate_name_field', 'label_bound'):
            t = table(); style = t.child('color_table_style')
            if mutation == 'profile': t.attrs['Version'] = [999]
            elif mutation == 'count': style.child('color_items').attrs['Size'] = 3
            elif mutation == 'base': style.child('min_discrete_code').content = [True]
            elif mutation == 'duplicate_name_field': style.child('color_items').children[0].children.append(scalar('name', 'Other'))
            else: style.child('color_items').children[0].child('name').content = ['x' * 1025]
            with self.subTest(mutation=mutation):
                self.assertEqual(evidence.category_legend([t], 'template-id', 'table-id')['status'], 'unresolved')

    def test_duplicate_display_labels_preserve_distinct_ordinals(self):
        t = table(); t.child('color_table_style').child('color_items').children[1].child('name').content = ['Limestone']
        out = evidence.category_legend([t], 'template-id', 'table-id')
        self.assertEqual(len(out['items']), 2)
        self.assertEqual([r['native_ordinal'] for r in out['items']], [0, 1])

    def diagnostic(self, **kwargs):
        node = binary.object_document(fixtures.envelope(fixtures.frame(fixtures.log_doc(**kwargs))))
        with self.assertRaises(binary.NativeError) as caught:
            recovery.decode_log(node, kwargs.get('kind', 'FloatWellLog'))
        result = caught.exception.native_log_diagnostics
        json.dumps(result, allow_nan=False)
        return result

    def test_nonfinite_values_and_indexes_and_length_are_separate(self):
        out = self.diagnostic(values=[float('nan'), float('inf'), float('-inf')])
        self.assertEqual((out['nonfinite_md_count'], out['nonfinite_value_count']), (0, 3))
        self.assertEqual((out['nan_value_count'], out['positive_infinity_value_count'], out['negative_infinity_value_count']), (1, 1, 1))
        self.assertTrue(out['array_lengths_equal'])
        out = self.diagnostic(md=[float('nan'), 2., 3.])
        self.assertEqual(out['first_nonfinite_md_positions'], [0])
        self.assertEqual(out['nonfinite_value_count'], 0)
        out = self.diagnostic(md=[1., 2.], values=[1., 2., 3.])
        self.assertFalse(out['array_lengths_equal'])

    def test_int_nan_is_nonfinite_not_a_fractional_code(self):
        out = self.diagnostic(kind='IntWellLog', values=[1., float('nan'), 2.])
        self.assertEqual(out['nan_value_count'], 1)

    def test_rejection_receipt_preserves_identity_and_cannot_export(self):
        writer = fixtures.RecoveryTests('runTest'); writer.setUp()
        self.addCleanup(writer.temp.cleanup)
        package = writer.fixture(doc=fixtures.log_doc(values=[1., float('nan'), 2.]))
        with redirect_stdout(io.StringIO()): report = recovery.run(package)
        record = report['objects'][0]
        self.assertTrue(report['source_unchanged'])
        self.assertEqual(record['status'], 'unsupported_layout')
        self.assertEqual(record['well_id'], writer.ids['well'])
        self.assertEqual(record['template_id'], writer.ids['template'])
        self.assertEqual(record['native_source_locator']['data_pk'], 1)
        self.assertEqual(record['log_array_diagnostics']['nan_value_count'], 1)
        self.assertEqual(len(record['blob_sha256']), 64)
        self.assertEqual(record['artifacts'], [])
        self.assertNotIn('numeric_round_trip', record)
        self.assertFalse(list(package.rglob('samples.csv')))
        self.assertIsNone(recovery._failure_metadata.get())

    def test_failure_metadata_does_not_leak_to_next_unresolved_object(self):
        writer = fixtures.RecoveryTests('runTest'); writer.setUp()
        self.addCleanup(writer.temp.cleanup)
        package = writer.fixture(doc=fixtures.log_doc(values=[1., float('nan'), 2.]))
        other_id = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        db = sqlite3.connect(package/'08_native_project/ptd_store/Data.ptd')
        try:
            with db:
                db.execute('INSERT INTO data VALUES(3,?,?,1,?)',
                           ('://Petrel/'+other_id, 'Unresolved second log', 'FloatWellLog'))
                db.execute('INSERT INTO blob_parts VALUES(3,0,?)',
                           (fixtures.envelope(fixtures.frame(fixtures.log_doc())),))
        finally:
            db.close()
        with redirect_stdout(io.StringIO()): report = recovery.run(package)
        records = {r['object_id']: r for r in report['objects']}
        first, second = records[writer.ids['log']], records[other_id]
        self.assertEqual(first['log_array_diagnostics']['nan_value_count'], 1)
        self.assertEqual(second['status'], 'missing_metadata')
        for field in ('well_id', 'template_id', 'native_source_locator',
                      'blob_sha256', 'native_category_legend', 'log_array_diagnostics'):
            self.assertNotIn(field, second)
        self.assertEqual(second['artifacts'], [])
        self.assertTrue(report['source_unchanged'])
        self.assertIsNone(recovery._failure_metadata.get())

    def test_malformed_optional_legend_does_not_reject_valid_numeric_codes(self):
        writer = fixtures.RecoveryTests('runTest'); writer.setUp()
        self.addCleanup(writer.temp.cleanup)
        package = writer.fixture(kind='IntWellLog', doc=fixtures.log_doc(
            'IntWellLog', values=[1., recovery.FLOAT_NULL, 3.], intervals=True))
        element = fixtures.element
        table_id = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        attrs = {'xmlns': recovery.NAMESPACE}
        subject = element('WellLogSubject', children=[
            element('unique_tag', writer.ids['log']), element('template', writer.ids['template'])], attrs=attrs)
        template = element('DiscTemplateSubject', children=[
            element('unique_tag', writer.ids['template']), element('is_predefined', False),
            element('default_color_table_tag', table_id)], attrs=attrs)
        malformed = element('DiscColorTableSubject', children=[element('unique_tag', table_id)],
                            attrs=dict(attrs, Version=evidence.TABLE_PROFILE))
        (package/'08_native_project/ptd_store/Model.ptd').write_bytes(
            fixtures.envelope(fixtures.frame(subject, template, malformed)))
        with redirect_stdout(io.StringIO()): report = recovery.run(package)
        record = report['objects'][0]
        self.assertEqual(record['native_category_legend']['status'], 'unresolved')
        self.assertIn('color_table_style', record['native_category_legend']['reason'])
        self.assertEqual(record['status'], 'missing_metadata', record.get('reason'))
        self.assertEqual(record['numeric_round_trip'], 'exact')
        self.assertEqual(record['sample_count'], 3)
        self.assertEqual(record['null_count'], 1)
        self.assertTrue(list(package.rglob('samples.csv')))
        self.assertIsNone(record['category_lookup'])
        self.assertTrue(report['source_unchanged'])

    def test_legend_and_failed_payload_evidence_reaches_catalogue_without_promotion(self):
        from test_geoviewer_contract import ContractTests, write
        fixture = ContractTests('runTest'); fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        record = fixture.native['objects'][0]
        legend = evidence.category_legend([table()], 'template-id', 'table-id')
        record.update(native_category_legend=legend, status='unsupported_layout',
                      log_array_diagnostics={'nan_value_count': 4})
        write(fixture.package/'07_workflows_reports/native_recovery/native_recovery_report.json', fixture.native)
        fixture.publish()
        fields = {e['field']: e['raw_value'] for e in fixture.table('metadata_evidence') if e['entity_key'].endswith(':native:grid-a')}
        self.assertEqual(fields['native_category_legend'], legend)
        self.assertEqual(fields['log_array_diagnostics']['nan_value_count'], 4)
        from validate_geoviewer_contract import validate_contract
        from geoviewer_contract import POINTER
        out = validate_contract(fixture.exports/POINTER)
        self.assertEqual(out['counts']['source_ready'], 0)


if __name__ == '__main__':
    unittest.main()
