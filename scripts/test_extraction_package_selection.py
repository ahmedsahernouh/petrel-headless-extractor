# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Package finalization with sibling timing evidence. https://saherlabs.dev/"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import petrel_geoscience_tools as tools


class PackageSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.outputs = self.root/'run/package'
        self.outputs.mkdir(parents=True)

    def package(self, name='p_fixture', status='passed'):
        path = self.outputs/name
        receipt = path/'07_workflows_reports/portable_extractor/portable_extraction_run_summary.json'
        receipt.parent.mkdir(parents=True)
        tools.write_json(receipt, {'validation_status':status})
        return path

    def test_timing_file_does_not_count_as_package(self):
        package = self.package()
        journal = self.outputs/('pipeline_timings_'+'a'*32+'.json')
        journal.write_text('[]',encoding='utf-8')
        self.assertEqual(tools.extraction_package(self.outputs),package)
        self.assertEqual(journal.read_text(), '[]')

    def test_zero_multiple_and_incomplete_directories_remain_errors(self):
        (self.outputs/'journal.json').write_text('[]')
        with self.assertRaisesRegex(RuntimeError,'found 0'):
            tools.extraction_package(self.outputs)
        incomplete = self.outputs/'incomplete'
        incomplete.mkdir()
        with self.assertRaisesRegex(RuntimeError,'missing its completion summary'):
            tools.extraction_package(self.outputs)
        self.package()
        with self.assertRaisesRegex(RuntimeError,'found 2'):
            tools.extraction_package(self.outputs)

    def test_link_is_rejected_instead_of_followed(self):
        package = self.package()
        with patch.object(tools,'is_link',side_effect=lambda p:p==package):
            with self.assertRaisesRegex(RuntimeError,'Linked extraction output'):
                tools.extraction_package(self.outputs)

    def call_wrapper(self, summary_status='passed', bad_hash=False):
        package = self.package(status=summary_status)
        (self.outputs/'pipeline_timings_fixture.json').write_text('[]')
        source = self.root/'project.pet'
        source.write_text('synthetic')
        source.with_suffix('.ptd').mkdir()
        run = SimpleNamespace(dry_run=False, output=self.outputs.parent,
                              version={'petrel_version':'unknown'}, finish=lambda result,summary:result)
        def records(pkg, **kwargs):
            if not bad_hash:return []
            artifact=package/'value.csv'; artifact.write_text('x\n1\n')
            kwargs['validated_paths']['value.csv']=artifact
            return [{'export_file':'value.csv','sha256':'0'*64}]
        with patch.object(tools,'Run',return_value=run), \
             patch.object(tools.progress,'run_pipeline',return_value=0), \
             patch.object(tools,'manifest_records',side_effect=records):
            return tools.extract_portable_project({'project_file':str(source)})

    def test_wrapper_advances_to_integrity_after_backend_with_journal(self):
        result = self.call_wrapper()
        self.assertEqual(result['summary']['validation_status'],'passed')
        self.assertEqual(result['pipeline_summary']['validation_status'],'passed')

    def test_failed_backend_validation_is_not_promoted(self):
        with self.assertRaisesRegex(RuntimeError,'validation did not pass'):
            self.call_wrapper('failed')

    def test_artifact_hash_mismatch_still_fails(self):
        with self.assertRaisesRegex(RuntimeError,'package hash mismatch'):
            self.call_wrapper(bad_hash=True)


if __name__=='__main__': unittest.main()
