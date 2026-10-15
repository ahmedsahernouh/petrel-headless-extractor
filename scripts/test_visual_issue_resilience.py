# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Warnings from report callbacks must remain visible without blocking a report."""
from copy import deepcopy
import unittest

import petrel_visual_report as visual


class VisualIssueResilienceTests(unittest.TestCase):
    def render(self, issues):
        audit = {'visual_report': {'summary_path': 'summary.json', 'issues': issues,
                 'objects': [{'object_id': 'decoded-object', 'status': 'decoded',
                              'reason': 'Original conversion outcome'}]}}
        before = deepcopy(audit)
        result = visual.render_section(audit, lambda audit, path: path)
        self.assertEqual(audit, before)
        self.assertIn('data-base-count="1">1</b>', result)
        self.assertIn('Original conversion outcome', result)
        self.assertIn('href="summary.json"', result)
        return result

    def test_structured_diagnostics_keep_category_and_object_fallback(self):
        result = self.render([
            {'category': 'Surface', 'object_id': 'id-1', 'reason': 'No preview'},
            {'object_id': 'id-2', 'reason': 'Missing metadata'},
            {'reason': 'General limitation'},
        ])
        self.assertIn('<strong>Surface</strong>: No preview', result)
        self.assertIn('<strong>id-2</strong>: Missing metadata', result)
        self.assertIn('<strong>Data</strong>: General limitation', result)
        self.assertIn('<b>3</b><span>Preview issues', result)

    def test_plain_callback_warning_is_readable_and_escaped(self):
        result = self.render(['Finalization <warning> & "details"'])
        self.assertIn('Finalization &lt;warning&gt; &amp; &quot;details&quot;', result)
        self.assertNotIn('<warning>', result)
        self.assertIn('<b>1</b><span>Preview issues', result)

    def test_mixed_malformed_entries_remain_visible(self):
        result = self.render([{'reason': 'valid'}, 'callback warning', None, 42,
                              False, ['<nested>'], {}])
        for text in ('valid', 'callback warning', 'Malformed issue (NoneType): None',
                     'Malformed issue (int): 42', 'Malformed issue (bool): False',
                     'Malformed issue (list)', '&lt;nested&gt;', 'Diagnostic reason missing'):
            self.assertIn(text, result)
        self.assertIn('<b>7</b><span>Preview issues', result)
        self.assertNotIn('<nested>', result)

    def test_malformed_collection_is_explicit_including_null_and_empty_dict(self):
        for value in (None, {}, {'reason': '<warning>'}, '<warning>', 12, False, ()):
            with self.subTest(value=value):
                result = self.render(value)
                self.assertIn(f'Malformed issues collection ({type(value).__name__})', result)
                self.assertIn('<b>1</b><span>Preview issues', result)
                self.assertNotIn('<warning>', result)

    def test_bad_fields_and_html_are_visible_without_execution(self):
        result = self.render([
            {'category': '<category>', 'reason': '<script>alert("x")</script>'},
            {'object_id': '<object>', 'reason': None},
            {'category': ['<label>'], 'reason': {'detail': '<nested>'}},
        ])
        for text in ('&lt;category&gt;', '&lt;object&gt;', '&lt;script&gt;',
                     'Malformed diagnostic reason (NoneType)',
                     'Malformed diagnostic label (list)', 'Malformed diagnostic reason (dict)'):
            self.assertIn(text, result)
        for raw in ('<category>', '<object>', '<script>', '<label>', '<nested>'):
            self.assertNotIn(raw, result)

    def test_empty_or_absent_issues_have_zero_warnings(self):
        result = self.render([])
        self.assertNotIn('Data and preview limitations', result)
        self.assertIn('<b>0</b><span>Preview issues', result)
        result = visual.render_section({'visual_report': {'summary_path': 'summary.json'}},
                                       lambda audit, path: path)
        self.assertIn('<b>0</b><span>Preview issues', result)


if __name__ == '__main__':
    unittest.main()
