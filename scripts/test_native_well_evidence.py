# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Native identity/reference preservation and exact head ownership tests."""
from copy import deepcopy
import csv
import io
import unittest
import numpy as np
import petrel_native_binary as binary
import petrel_native_well_evidence as well
import test_geoviewer_contract as fixtures
from geoviewer_contract import POINTER
from geoviewer_delivery import publish_exports
from validate_geoviewer_contract import validate_contract
from export_petrel_native_spatial_zero_gui import enrich_well_heads_with_trajectory

WELL='c236a48c-cb49-4691-b2a5-a50a10a9cf4c'
PROVIDER='f4fb11e1-492d-46f8-9032-179da3aa6e85'
OTHER='e83f6875-3b7d-4e8e-99c8-3cd4b87b5270'
SPATIAL='07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'

def scalar(name,value):return binary.Node(name,content=[value])
def reference(name='working_reference_level'):
    return binary.Node(name,children=[scalar('name','KB'),scalar('offset',303.206),scalar('description',' Kelly bushing')])
def subject(tag=WELL):
    return binary.Node('WellTraceSubject',attrs={'xmlns':well.NAMESPACE,'Version':list(well.WELL_PROFILE)},children=[
        scalar('unique_tag',tag),scalar('name','DMS-1'),scalar('uwi_','Declared-UWI'),
        scalar('definite_survey_provider_tag_',PROVIDER),
        binary.Node('well_head_',children=[binary.Array('double',np.array([487300.91,2847959.79],dtype='<f8'))]),
        reference(),binary.Node('reference_levels',attrs={'Size':1},children=[reference('item')]),
        scalar('grid_convergence_factor',-.0009601847504585592)])
def head():
    return dict(well_name='DMS-1',x=487300.91,y=2847959.79,z=310.,model_subject_offset=100,
        trajectory_provider_object_id=PROVIDER,trajectory_provider_link_status=well.PROVIDER_STATUS,
        z_coordinate_source='native_Data_ptd_trajectory_first_station_after_XY_match',vertical_reference_status='not_resolved')


class NativeWellEvidenceTests(unittest.TestCase):
    def test_raw_declared_values_kept_without_calibration(self):
        evidence=well.decode_subjects([subject()]);row=evidence['subjects'][0]
        self.assertEqual(row['object_id'],WELL)
        self.assertEqual(row['uwi_'],'Declared-UWI')
        self.assertEqual(row['working_reference_level'],dict(name='KB',offset=303.206,description=' Kelly bushing'))
        self.assertEqual(row['reference_levels'][0],row['working_reference_level'])
        self.assertFalse(row['field_gaps'])
        self.assertNotIn('vertical_reference',row)
        self.assertNotIn('unit',row)
        self.assertNotIn('external_uwi',row)
        bound=well.bind_head_rows([head()],evidence)[0]
        self.assertEqual(bound['native_well_object_id'],WELL)
        self.assertEqual(bound['native_well_identity_status'],well.IDENTITY_STATUS)
        self.assertEqual(bound['z_coordinate_source'],head()['z_coordinate_source'])
        self.assertEqual(bound['z'],head()['z'])

    def test_no_proximity_name_or_substring_identity(self):
        for changes in ({'x':487300.910000001},{'well_name':'Other'},
                        {'trajectory_provider_object_id':OTHER},
                        {'trajectory_provider_link_status':'not_exact'},
                        {'trajectory_provider_link_status':'inexact'},
                        {'trajectory_provider_link_status':'name_candidate_exact'}):
            with self.subTest(changes=changes):
                row=head();row.update(changes)
                self.assertNotIn('native_well_object_id',well.bind_head_rows([row],well.decode_subjects([subject()]))[0])

    def test_duplicate_provider_or_head_never_resolves(self):
        evidence=well.decode_subjects([subject(),subject(OTHER)])
        self.assertEqual(len(evidence['subjects']),2)
        self.assertNotIn('native_well_object_id',well.bind_head_rows([head()],evidence)[0])
        evidence=well.decode_subjects([subject()])
        self.assertTrue(all('native_well_object_id' not in r for r in well.bind_head_rows([head(),head()],evidence)))

    def test_duplicate_native_uuid_excluded(self):
        e=well.decode_subjects([subject(),subject()])
        self.assertFalse(e['subjects'])
        self.assertEqual(e['rejected'][0]['reason'],'duplicate_native_well_UUID')

    def test_bad_identity_profile_or_head_rejected_per_subject(self):
        for mode in ('profile','uuid','duplicate_field','vector','nonfinite','bool_provider','float_provider'):
            with self.subTest(mode=mode):
                node=subject()
                if mode=='profile':node.attrs['Version'][0]=34
                if mode=='uuid':node.child('unique_tag').content=['not UUID']
                if mode=='duplicate_field':node.children.append(scalar('unique_tag',OTHER))
                if mode=='vector':node.child('well_head_').children[0].values=np.array([1.,2.,3.])
                if mode=='nonfinite':node.child('well_head_').children[0].values=np.array([float('nan'),2.])
                if mode=='bool_provider':node.child('definite_survey_provider_tag_').content=[False]
                if mode=='float_provider':node.child('definite_survey_provider_tag_').content=[0.0]
                self.assertFalse(well.decode_subjects([node])['subjects'])

    def test_invalid_optional_reference_does_not_destroy_identity(self):
        node=subject();node.child('working_reference_level').child('offset').content=[float('inf')]
        node.child('reference_levels').attrs['Size']=257
        node.child('uwi_').content=['x'*4097]
        e=well.decode_subjects([node]);row=e['subjects'][0]
        self.assertEqual({x['field'] for x in row['field_gaps']},{'working_reference_level','reference_levels','uwi_'})
        self.assertNotIn('reference_levels',row)
        self.assertEqual(well.bind_head_rows([head()],e)[0]['native_well_object_id'],WELL)

    def test_native_null_provider_preserved_without_guess(self):
        node=subject();node.child('definite_survey_provider_tag_').content=[0]
        e=well.decode_subjects([node])
        self.assertIsNone(e['subjects'][0]['definite_provider_id'])
        self.assertNotIn('native_well_object_id',well.bind_head_rows([head()],e)[0])

    def test_unparseable_model_leaves_original_head_evidence(self):
        rows=[head()];result,evidence=well.enrich_heads(b'bad model',rows)
        self.assertEqual(result,rows);self.assertEqual(evidence['status'],'unresolved')

    def test_provider_binding_survives_station_crosscheck_without_datum_inference(self):
        h=head();h.update(model_well_name_offset=10,provider_uuid_offset=20,z=None)
        bound=well.bind_head_rows([h],well.decode_subjects([subject()]))
        stations=[dict(object_id=PROVIDER,record_index=0,x=h['x'],y=h['y'],z=310.,md=0.)]
        heads,mappings,report=enrich_well_heads_with_trajectory(bound,stations,0.02)
        self.assertEqual(mappings[PROVIDER]['well_id'],WELL)
        self.assertEqual(mappings[PROVIDER]['well_identity_status'],well.IDENTITY_STATUS)
        self.assertEqual(heads[0]['z'],310.)
        self.assertEqual(heads[0]['vertical_reference_status'],'trajectory_start_z_reference_not_resolved')
        self.assertEqual(report['matched_XY_rows'],1)
        self.assertEqual(stations[0]['md'],0.)

    def test_native_identity_survives_missing_track_without_invented_Z(self):
        h=head();h.update(model_well_name_offset=10,provider_uuid_offset=20,z=None)
        bound=well.bind_head_rows([h],well.decode_subjects([subject()]))
        heads,mappings,report=enrich_well_heads_with_trajectory(bound,[],0.02)
        self.assertEqual(mappings[PROVIDER]['well_id'],WELL)
        self.assertIsNone(heads[0]['z'])
        self.assertEqual(report['matched_XY_rows'],0)


class WellContractTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.ContractTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.e=well.decode_subjects([subject()]);self.h=well.bind_head_rows([head()],self.e)[0]
        self.fx.context['objects'] += [dict(object_id=WELL,category='WellTraceSubject',name='DMS-1'),dict(object_id=PROVIDER,category='trajectory')]
        fixtures.write(self.fx.package/'01_project_metadata/project_context.json',self.fx.context)
        self.spatial=dict(model_well_head_decode=dict(native_well_metadata=self.e),objects=[dict(
            object_id=PROVIDER,blob_type='MdInclAzimTrajectoryProviderData',status='decoded',well_id=WELL,
            well_identity_status=well.IDENTITY_STATUS)])
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        self.headpath=self.fx.package/'02_wells/well_headers/native_well_heads.csv'
        self.headpath.parent.mkdir(parents=True)
        text=io.StringIO(newline='');writer=csv.DictWriter(text,fieldnames=list(self.h));writer.writeheader();writer.writerow(self.h)
        self.raw=text.getvalue().encode();self.headpath.write_bytes(self.raw)
        self.fx.add_row('well_headers','heads.csv',self.raw,'CSV');self.fx.rows[-1]['category']='well_headers'
        self.fx.add_row(PROVIDER,'trajectory.csv',b'md,inclination,azimuth\n1,0.1,0.2\n','CSV');self.fx.rows[-1]['category']='trajectories'

    def assessment(self):
        return next(x for x in validate_contract(self.fx.exports/POINTER,'opendtect')['assessments'] if x['entity_key'].endswith(':native:'+WELL))

    def test_native_well_reused_with_exact_row_ownership_no_false_cycle(self):
        self.fx.publish();entities=self.fx.table('entities')
        member=next(x for x in entities if x['source_id']==WELL)
        self.assertEqual(member['native_uuid'],WELL);self.assertIsNone(member['external_uwi'])
        self.assertEqual(member['membership_selector']['value'],'100')
        self.assertEqual(len(member['artifact_keys']),1)
        self.assertFalse(any(x['source_lane']=='derived_member' for x in entities))
        headart=next(x for x in self.fx.table('artifacts') if x['relative_path']=='heads.csv')
        self.assertIn(member['entity_key'],headart['owner_keys'])
        self.assertEqual(headart['owner_selectors'][0]['selector'],member['membership_selector'])
        result=validate_contract(self.fx.exports/POINTER,'opendtect')
        self.assertFalse(result['dependency_cycles'])
        self.assertNotIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',self.assessment()['reason_codes'])
        self.assertNotEqual(self.assessment()['receiving_state'],'qualified_for_planned_mapping')
        fields=[x for x in self.fx.table('metadata_evidence') if x['entity_key']==member['entity_key']]
        self.assertEqual(next(x['raw_value'] for x in fields if x['field']=='native_well_metadata')['uwi_'],'Declared-UWI')
        self.assertEqual(next(x['raw_value'] for x in fields if x['field']=='native_head_record')['z_coordinate_source'],head()['z_coordinate_source'])

    def test_indexed_head_must_match_consumed_head_bytes(self):
        self.fx.rows[-2]['sha256']='a'*64
        self.fx.publish()
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',self.assessment()['reason_codes'])

    def test_delivered_head_digest_binds_contract_without_qualifying_semantics(self):
        for item in self.fx.context['objects']:
            item.setdefault('name',item['object_id'])
        fixtures.write(self.fx.package/'01_project_metadata/project_context.json',self.fx.context)
        self.spatial.update(tool='export_petrel_native_spatial_zero_gui.py',
                            completed_at_utc='2026-10-05T00:00:00Z',native_well_head_rows=1)
        self.spatial['model_well_head_decode']['status']='decoded'
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        self.fx.index=publish_exports(self.fx.package,{'objects':[]},self.fx.exports)
        self.fx.publish()
        result=validate_contract(self.fx.exports/POINTER,'opendtect')
        head=next(x for x in self.fx.table('artifacts') if x['relative_path'].endswith('native_well_heads.csv'))
        self.assertEqual(head['recorded_sha256'],fixtures.hashlib.sha256(self.raw).hexdigest())
        self.assertNotIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',self.assessment()['reason_codes'])
        self.assertFalse(any('RECORDED_PAYLOAD_IDENTITY_MISSING' in x['reason_codes']
                             for x in result['assessments']))
        self.assertEqual(result['counts']['receiving_qualified'],0)
        self.assertNotEqual(self.assessment()['receiving_state'],'qualified_for_planned_mapping')

        # The exact old delivery omission remains detectable by the consumer.
        self.fx.index['files'][0]['sha256']=''
        self.fx.publish()
        old=validate_contract(self.fx.exports/POINTER,'opendtect')
        self.assertTrue(any('RECORDED_PAYLOAD_IDENTITY_MISSING' in x['reason_codes']
                            for x in old['assessments']))

    def test_same_size_head_change_blocks_without_bulk_hash_option(self):
        self.fx.publish();p=self.fx.exports/'heads.csv';raw=p.read_bytes();p.write_bytes(raw.replace(b'DMS-1',b'DMS-X'))
        a=self.assessment();self.assertIn('HEAD_METADATA_STALE',a['reason_codes'])
        self.assertNotEqual(a['receiving_state'],'qualified_for_planned_mapping')

    def test_conflicting_typed_head_not_merged_with_native_uuid(self):
        self.headpath.write_bytes(self.raw.replace(b'DMS-1',b'DMS-X'))
        self.fx.publish()
        self.assertTrue(any(x['source_lane']=='derived_member' for x in self.fx.table('entities')))
        self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',self.assessment()['reason_codes'])

    def test_inexact_status_cannot_become_exact_provider_relationship(self):
        self.headpath.write_bytes(self.raw.replace(well.PROVIDER_STATUS.encode(),b'inexact'))
        self.fx.publish()
        links=[x for x in self.fx.table('relationships') if x['relation']=='trajectory_provider']
        self.assertTrue(links);self.assertTrue(all(x['status']=='candidate' for x in links))

    def test_duplicate_native_metadata_rejected_before_generation(self):
        self.spatial['model_well_head_decode']['native_well_metadata']['subjects'].append(deepcopy(self.e['subjects'][0]))
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        with self.assertRaisesRegex(ValueError,'unique canonical UUIDs'):self.fx.publish()

    def test_malformed_model_binding_rejected(self):
        self.spatial['model_well_head_decode']['native_well_metadata']['source_model_sha256']='not a digest'
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        with self.assertRaisesRegex(ValueError,'Model hash conflicts'):self.fx.publish()

    def test_conflicting_model_binding_rejected(self):
        hashes={'project.pet':'a'*64,'Model.ptd':'b'*64,'Data.ptd':'c'*64}
        self.fx.native.update(source_hashes_before=hashes,source_hashes_after=hashes,source_unchanged=True)
        fixtures.write(self.fx.package/'07_workflows_reports/native_recovery/native_recovery_report.json',self.fx.native)
        self.spatial['model_well_head_decode']['native_well_metadata']['source_model_sha256']='d'*64
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        with self.assertRaisesRegex(ValueError,'Model hash conflicts'):self.fx.publish()


if __name__=='__main__':unittest.main()
