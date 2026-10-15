# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_preparation_report as renderer
from geoviewer_assessment_shards import LIST_FIELDS, write_sharded_assessment
from geoviewer_contract import SCHEMA
from geoviewer_contract_publication import verify_publication_binding
import test_contract_publication as publication_fixture


def inputs(count=2):
    nodes=[dict(entity_key='e'+str(i),source_id='native-'+str(i),name='Object '+str(i),
                category='well',source_lane='native') for i in range(count)]
    tables=dict(entities=nodes,artifacts=[],relationships=[],metadata_evidence=[],gaps=[])
    reports={}
    for p in renderer.PROFILES:
        rows=[dict(entity_key=n['entity_key'],category='well',source_state='source_blocked',
            receiving_state='source_only' if p=='source' else 'blocked_metadata',
            artifact_count=0,state_axes={'publication':'no_accepted_artifacts'},receiving_assessment=None) for n in nodes]
        reports[p]=dict(profile=p,generation='g',manifest_sha256='a'*64,assessments=rows,
            artifact_availability=[],diagnostics=[],counts=dict(entities=count,artifacts=0,source_ready=0,
                source_blocked=count,qualified_for_planned_mapping=0,receiving_qualified=0,receiving_imports_executed=0))
    return tables,reports


class HumanPreparationReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'report.html'
    def render(self,tables,reports):
        return renderer.write_report(self.path,tables=tables,assessments=reports,generation='g',manifest_sha256='a'*64)
    def committed_flat_inputs(self, *, sharded_assessments=False):
        tables,reports=inputs()
        tables['metadata_evidence']=[dict(entity_key='e0',field='large_recorded_value',
                                          value='recorded-'*3000)]
        specs={}
        for name,rows in tables.items():
            raw=b''.join((json.dumps(row,ensure_ascii=False)+'\n').encode('utf-8') for row in rows)
            filename=name+'.jsonl'
            (self.path.parent/filename).write_bytes(raw)
            specs[name]=dict(path=filename,rows=len(rows),sha256=hashlib.sha256(raw).hexdigest())
        manifest=dict(schema=SCHEMA,generation='g',tables=specs)
        manifest_path=self.path.parent/'manifest.json'
        raw=json.dumps(manifest,ensure_ascii=False).encode('utf-8')
        manifest_path.write_bytes(raw)
        manifest_hash=hashlib.sha256(raw).hexdigest()
        for profile,report in reports.items():
            report['manifest_sha256']=manifest_hash
            if sharded_assessments:
                report['schema']='geoviewer.contract-validation/2'
                report['profile_revision']='test'
                for field in LIST_FIELDS:
                    report.setdefault(field,[])
            destination=self.path.parent/('validation_'+profile+'.json')
            if sharded_assessments and profile!='source':
                write_sharded_assessment(destination,report)
            else:
                destination.write_text(json.dumps(report),encoding='utf-8')
        return manifest_path,reports,manifest_hash
    def publish_flat(self,manifest_path,reports,manifest_hash):
        return renderer.publish_human_report(self.path,manifest_path,reports,'g',manifest_hash)
    def test_literal_source_strings_are_escaped_and_diagnostics_searchable(self):
        tables,reports=inputs();attack='</script><img src=x onerror=alert(1)>'
        tables['entities'][0]['name']=attack
        tables['metadata_evidence']=[dict(entity_key='e0',field='literal',raw_value=attack)]
        reports['opendtect']['diagnostics']=[dict(entity_key='e0',code='UNIT_MISSING',reason=attack,next_action='Read exact native template')]
        self.render(tables,reports);html=''.join(p.read_text(encoding='utf-8') for p in self.path.parent.glob('*.html'))
        self.assertNotIn(attack,html);self.assertIn('&lt;img',html)
        self.assertIn('UNIT_MISSING',html);self.assertIn('Read exact native template',html)
        self.assertIn('id="query"',html);self.assertNotIn('<script src=',html)
    def test_prepared_mapping_count_does_not_become_an_import_count(self):
        tables,reports=inputs()
        reports['opendtect']['assessments'][0]['receiving_state']='qualified_for_planned_mapping'
        reports['opendtect']['counts']['qualified_for_planned_mapping']=1
        self.render(tables,reports);html=self.path.read_text()
        self.assertIn('<th>opendtect</th><td>0</td><td>2</td><td>1</td><td>0</td><td>0</td>',html)
        self.assertIn('No OpendTect or PaleoScan import was executed',html)
    def test_shared_artifact_lists_both_owners_without_inflating_file_count(self):
        tables,reports=inputs()
        tables['artifacts']=[dict(artifact_key='shared',entity_key='e0',owner_keys=['e0','e1'],relative_path='head.csv',index_section='partial_files',original_status='checkpoint_only_partial')]
        for r in reports.values():
            r['counts']['artifacts']=1;r['artifact_availability']=[dict(artifact_key='shared',available=False,current_identity_scope='unavailable')]
            for row in r['assessments']:row['artifact_count']=1
        result=self.render(tables,reports);html=self.path.read_text()
        self.assertEqual(result['artifacts'],1);self.assertEqual(html.count('1 owned artifact records'),2)
        detail=next(self.path.parent.glob('*.details_*.html')).read_text()
        self.assertIn('checkpoint_only_partial',detail);self.assertIn('unavailable',detail)

    def test_category_counts_keep_lanes_and_shared_artifacts_distinct(self):
        tables,reports=inputs(3)
        tables['entities'][2]['source_lane']='companion'
        tables['artifacts']=[dict(artifact_key='shared',entity_key='e0',owner_keys=['e0','e1'])]
        for r in reports.values():
            r['counts']['artifacts']=1;r['artifact_availability']=[dict(artifact_key='shared',available=False)]
            for row in r['assessments'][:2]:row['artifact_count']=1
        self.render(tables,reports);html=self.path.read_text(encoding='utf-8')
        self.assertIn('<th>well</th><td>native</td><td>2</td><td>1</td><td>0</td><td>0</td><td>0</td>',html)
        self.assertIn('<th>well</th><td>companion</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td>',html)
        self.assertIn('3 catalogue entities · 1 distinct artifacts',html)

    def test_shared_diagnostics_count_entities_once_across_profiles(self):
        tables,reports=inputs(2)
        for report in reports.values():
            report['diagnostics']=[dict(entity_key='e'+str(i),code='UNIT_REQUIRED',field_or_link='stored_unit',severity='error',reason='Unresolved unit',next_action='Calibrate exact profile') for i in range(2)]
        self.render(tables,reports)
        blockers=self.path.with_name('report.blockers.html').read_text(encoding='utf-8')
        self.assertEqual(blockers.count('<section id="diagnostic-'),1)
        self.assertIn('2 affected entities',blockers)
        self.assertEqual(blockers.count('#object-'),2)
        self.assertIn('Calibrate exact profile',blockers)

    def test_preferred_payload_keeps_recorded_identity_and_rejects_wrong_owner(self):
        tables,reports=inputs(1)
        tables['artifacts']=[dict(artifact_key='full',entity_key='e0',relative_path='actual.csv',payload_role='full_payload')]
        for r in reports.values():
            r['counts']['artifacts']=1;r['artifact_availability']=[dict(artifact_key='full',available=True,current_identity_scope='size_and_state')]
            r['assessments'][0].update(artifact_count=1,preferred_artifact_key='full')
        self.render(tables,reports)
        html=self.path.with_name('report.details_0001.html').read_text(encoding='utf-8')
        self.assertIn('Preferred full payload',html);self.assertIn('actual.csv',html);self.assertIn('size_and_state',html)
        other=Path(self.temp.name)/'wrong.html';reports['source']['assessments'][0]['preferred_artifact_key']='not-owned'
        with self.assertRaisesRegex(ValueError,'not owned'):
            renderer.write_report(other,tables=tables,assessments=reports,generation='g',manifest_sha256='a'*64)
        self.assertFalse(other.exists())
    def test_stale_generation_profile_hash_and_counts_refuse_publication(self):
        for key,value in [('generation','old'),('manifest_sha256','b'*64),('profile','paleoscan'),('counts',{'entities':99})]:
            with self.subTest(key=key):
                tables,reports=inputs();reports['source'][key]=value
                with self.assertRaises(ValueError):self.render(tables,reports)
                self.assertFalse(self.path.exists())
    def test_duplicate_missing_entity_and_wrong_ownership_are_rejected(self):
        for variant in ('duplicate','missing_assessment','ownership'):
            tables,reports=inputs()
            if variant=='duplicate':tables['entities'].append(tables['entities'][0])
            if variant=='missing_assessment':reports['source']['assessments'].pop()
            if variant=='ownership':reports['source']['assessments'][0]['artifact_count']=1
            with self.assertRaises(ValueError):self.render(tables,reports)
    def test_empty_inventory_remains_honest(self):
        tables,reports=inputs(0);r=self.render(tables,reports)
        self.assertEqual(r['entities'],0);self.assertIn('Empty inventory is not proof',self.path.read_text())
    def test_resource_failure_has_no_final_or_partial_leftover(self):
        tables,reports=inputs()
        with patch.object(renderer,'MAX_REPORT_BYTES',100):
            with self.assertRaisesRegex(ValueError,'byte bound'):self.render(tables,reports)
        self.assertEqual(list(self.path.parent.iterdir()),[])
    def test_flat_detail_overflow_publishes_complete_bounded_summary(self):
        manifest_path,reports,manifest_hash=self.committed_flat_inputs(sharded_assessments=True)
        original={p.name:p.read_bytes() for p in self.path.parent.iterdir() if p.is_file()}
        with patch.object(renderer,'MAX_REPORT_BYTES',12000):
            result=self.publish_flat(manifest_path,reports,manifest_hash)
        html=self.path.read_text(encoding='utf-8')
        self.assertIn('bounded summary',html)
        self.assertEqual((result['entities'],result['artifacts'],result['details']), (2,0,[]))
        self.assertIn('2 catalogue entities and 0 distinct artifacts',html)
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        for name,spec in manifest['tables'].items():
            self.assertIn('href="'+name+'.jsonl"',html)
            self.assertIn(spec['sha256'],html)
            self.assertIn(name+': '+str(spec['rows'])+' rows',html)
        for profile in renderer.PROFILES:
            self.assertIn('href="validation_'+profile+'.json"',html)
            self.assertIn('<th>'+profile+'</th><td>0</td><td>2</td>',html)
        for profile in ('opendtect','paleoscan'):
            envelope=json.loads((self.path.parent/('validation_'+profile+'.json')).read_text())
            for field in envelope['list_order']:
                for member in envelope['lists'][field]['members']:
                    self.assertIn('href="'+member['path']+'"',html)
                    self.assertIn(member['sha256'],html)
        self.assertEqual({p.name:p.read_bytes() for p in self.path.parent.iterdir()
                          if p.is_file() and p.name in original},original)
        self.assertEqual(list(self.path.parent.glob('*.details_*.html')),[])
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
        before=self.path.read_bytes()
        with patch.object(renderer,'MAX_REPORT_BYTES',12000):
            with self.assertRaisesRegex(ValueError,'already exists'):
                self.publish_flat(manifest_path,reports,manifest_hash)
        self.assertEqual(self.path.read_bytes(),before)
    def test_flat_fallback_does_not_mask_hash_or_assessment_failures(self):
        errors={'malformed_table':'Expecting value',
                'table_hash':'Report table identity/count mismatch',
                'coverage':'Human report entity coverage mismatch',
                'count':'Human report assessment count contradiction',
                'linked_assessment':'Summary linked assessment binding mismatch'}
        for failure,expected_error in errors.items():
            with self.subTest(failure=failure):
                manifest_path,reports,manifest_hash=self.committed_flat_inputs()
                if failure=='malformed_table':
                    (self.path.parent/'metadata_evidence.jsonl').write_bytes(b'not-json\n')
                elif failure=='table_hash':
                    with (self.path.parent/'metadata_evidence.jsonl').open('ab') as stream:
                        stream.write(b'{}\n')
                elif failure=='coverage':
                    reports['source']['assessments'].pop()
                elif failure=='count':
                    reports['source']['counts']['source_blocked']=1
                else:
                    (self.path.parent/'validation_source.json').write_text('{}',encoding='utf-8')
                with patch.object(renderer,'MAX_REPORT_BYTES',12000):
                    with self.assertRaisesRegex(ValueError,expected_error):
                        self.publish_flat(manifest_path,reports,manifest_hash)
                self.assertFalse(self.path.exists())
                self.assertEqual(list(self.path.parent.glob('*.details_*.html')),[])
                self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
                for p in self.path.parent.iterdir():
                    if p.is_file():p.unlink()
    def test_flat_fallback_respects_summary_byte_bound(self):
        manifest_path,reports,manifest_hash=self.committed_flat_inputs()
        with patch.object(renderer,'MAX_REPORT_BYTES',100):
            with self.assertRaisesRegex(ValueError,'Summary report exceeds explicit output byte bound'):
                self.publish_flat(manifest_path,reports,manifest_hash)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob('*.details_*.html')),[])
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
    def test_flat_fallback_rejects_tampered_sharded_assessment_member(self):
        manifest_path,reports,manifest_hash=self.committed_flat_inputs(sharded_assessments=True)
        envelope=json.loads((self.path.parent/'validation_opendtect.json').read_text())
        member=envelope['lists']['assessments']['members'][0]
        with (self.path.parent/member['path']).open('ab') as stream:
            stream.write(b'[]\n')
        with patch.object(renderer,'MAX_REPORT_BYTES',12000):
            with self.assertRaisesRegex(ValueError,'Assessment physical row frame invalid'):
                self.publish_flat(manifest_path,reports,manifest_hash)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob('*.details_*.html')),[])
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
    def test_many_entities_are_all_present_with_pagination(self):
        tables,reports=inputs(2000);r=self.render(tables,reports);html=self.path.read_text()
        self.assertEqual(r['entities'],2000);self.assertEqual(html.count('<tr class="entity"'),2000)
        self.assertEqual(html.count('<tr class="entity" hidden>'),1950)
        self.assertIn('Object 1999',html);self.assertIn('const size=50',html)
    def test_final_report_is_not_overwritten(self):
        tables,reports=inputs();self.render(tables,reports);before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'already exists'):self.render(tables,reports)
        self.assertEqual(self.path.read_bytes(),before)


class HumanReportBindingTests(unittest.TestCase):
    setUp=publication_fixture.ContractPublicationTests.setUp
    add_row=publication_fixture.ContractPublicationTests.add_row
    table=publication_fixture.ContractPublicationTests.table
    publish=publication_fixture.ContractPublicationTests.publish
    def test_changed_html_fails_receipt_binding_and_old_bundle_stays_readable(self):
        bundle=self.publish();report=Path(bundle['human_report']['path'])
        self.assertIn('PREPARATION.html',bundle['human_report']['relative_path'])
        self.assertEqual(verify_publication_binding(bundle,self.run)['status'],'verified')
        report.write_bytes(report.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'Human report binding failed'):verify_publication_binding(bundle,self.run)
        old=copy.deepcopy(bundle);old.pop('human_report')
        self.assertEqual(verify_publication_binding(old,self.run)['status'],'verified')

    def test_changed_detail_page_fails_receipt_binding(self):
        bundle=self.publish();human=bundle['human_report']
        page=Path(human['path']).parent/human['details'][0]['filename']
        page.write_bytes(page.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'detail binding failed'):verify_publication_binding(bundle,self.run)


if __name__=='__main__':unittest.main()
