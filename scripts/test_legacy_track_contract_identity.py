# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Legacy WellTrace3 uses the well UUID without becoming a head artifact."""
from copy import deepcopy
import unittest
import test_geoviewer_contract as fixtures
from geoviewer_contract import POINTER
from validate_geoviewer_contract import validate_contract

TAG='e83f6875-3b7d-4e8e-99c8-3cd4b87b5270'
OTHER='c236a48c-cb49-4691-b2a5-a50a10a9cf4c'
SPATIAL='07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'

class LegacyTrackIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.ContractTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.fx.context['objects'].append(dict(object_id=TAG,category='WellTraceSubject',name='Same'))
        fixtures.write(self.fx.package/'01_project_metadata/project_context.json',self.fx.context)
        self.spatial=dict(objects=[dict(object_id=TAG,blob_type='WellTrace3',status='decoded',native_profile=[25,2,0,1,1],
            well_name_linkage=dict(well_name='Same',well_name_mapping_status='native_WellTrace3_same_WellTraceSubject_UUID_exact'))])
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        self.fx.add_row(TAG,'track.csv',b'md,x,y,z\n0,1,2,3\n100,5,6,7\n','CSV');self.fx.rows[-1]['category']='trajectories'

    def split(self):
        nodes=[x for x in self.fx.table('entities') if x['source_id']==TAG]
        self.assertEqual(len(nodes),2)
        return next(x for x in nodes if x['category']=='well'),next(x for x in nodes if x['category']=='trajectory')

    def test_same_native_uuid_has_distinct_conceptual_well_and_track(self):
        self.fx.publish();well,track=self.split()
        self.assertEqual(well['native_uuid'],TAG);self.assertEqual(track['native_uuid'],TAG)
        self.assertNotEqual(well['entity_key'],track['entity_key'])
        self.assertEqual(track['identity_role'],'legacy_stored_track')
        self.assertFalse(well['artifact_keys']);self.assertEqual(len(track['artifact_keys']),1)
        self.assertIsNone(well['external_uwi']);self.assertIsNone(track['external_uwi'])
        link=next(x for x in self.fx.table('relationships') if x['relation']=='source_well')
        self.assertEqual((link['source_key'],link['target_key']),(track['entity_key'],well['entity_key']))
        self.assertEqual(link['status'],'exact_id_reference')
        self.assertFalse(validate_contract(self.fx.exports/POINTER)['dependency_cycles'])

    def test_head_and_track_files_route_by_role_not_shared_uuid(self):
        self.fx.add_row(TAG,'head.csv',b'x,y\n1,2\n','CSV');self.fx.rows[-1]['category']='well_headers'
        self.fx.publish();well,track=self.split()
        arts={x['relative_path']:x for x in self.fx.table('artifacts')}
        self.assertEqual(arts['head.csv']['owner_keys'],[well['entity_key']])
        self.assertEqual(arts['track.csv']['owner_keys'],[track['entity_key']])

    def test_legacy_identity_stable_when_index_and_context_reordered(self):
        self.fx.publish();before={x['relative_path']:x['artifact_key'] for x in self.fx.table('artifacts')}
        self.fx.rows.reverse();self.fx.context['objects'].reverse()
        fixtures.write(self.fx.package/'01_project_metadata/project_context.json',self.fx.context)
        self.fx.publish();after={x['relative_path']:x['artifact_key'] for x in self.fx.table('artifacts')}
        self.assertEqual(before,after)

    def test_duplicate_well_subjects_do_not_become_exact_association(self):
        self.fx.context['objects'].append(deepcopy(self.fx.context['objects'][-1]))
        fixtures.write(self.fx.package/'01_project_metadata/project_context.json',self.fx.context)
        self.fx.publish();self.split()
        self.assertFalse(any(x['relation']=='source_well' for x in self.fx.table('relationships')))
        self.assertTrue(any(x['code']=='LEGACY_WELL_IDENTITY_UNRESOLVED' for x in self.fx.table('gaps')))

    def test_duplicate_track_receipts_reject_ambiguous_identity(self):
        self.spatial['objects'].append(deepcopy(self.spatial['objects'][0]));fixtures.write(self.fx.package/SPATIAL,self.spatial)
        with self.assertRaisesRegex(ValueError,'Duplicate legacy trajectory'):self.fx.publish()

    def test_conflicting_explicit_well_link_rejected(self):
        self.spatial['objects'][0]['well_id']=OTHER;fixtures.write(self.fx.package/SPATIAL,self.spatial)
        with self.assertRaisesRegex(ValueError,'Legacy trajectory well identity conflicts'):self.fx.publish()

    def test_matching_display_name_cannot_supply_missing_well_uuid(self):
        self.fx.context['objects'][-1]['object_id']=OTHER
        fixtures.write(self.fx.package/'01_project_metadata/project_context.json',self.fx.context)
        self.fx.publish()
        self.assertFalse(any(x['relation']=='source_well' for x in self.fx.table('relationships')))
        track=next(x for x in self.fx.table('entities') if x['source_id']==TAG)
        self.assertEqual(track['category'],'trajectory')
        self.assertTrue(any(x['code']=='LEGACY_WELL_IDENTITY_UNRESOLVED' for x in self.fx.table('gaps')))

    def test_inventory_subject_type_does_not_override_index_track_role(self):
        self.fx.rows[-1]['subject_type']='WellTraceSubject'
        self.fx.publish();well,track=self.split()
        self.assertFalse(well['artifact_keys']);self.assertEqual(len(track['artifact_keys']),1)

    def test_zero_uuid_is_not_native_legacy_identity(self):
        self.spatial['objects'][0]['object_id']='00000000-0000-0000-0000-000000000000'
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        with self.assertRaisesRegex(ValueError,'canonical native UUID'):self.fx.publish()

    def test_declared_provider_reference_routes_to_shared_uuid_track_role(self):
        self.spatial['model_well_head_decode']=dict(native_well_metadata=dict(schema='geoviewer.native-well-evidence/1',
            subjects=[dict(object_id=TAG,name='Same',definite_provider_id=TAG)]))
        fixtures.write(self.fx.package/SPATIAL,self.spatial)
        self.fx.publish();well,track=self.split()
        link=next(x for x in self.fx.table('relationships') if x['relation']=='declared_definite_provider')
        self.assertEqual((link['source_key'],link['target_key']),(well['entity_key'],track['entity_key']))
        self.assertFalse(validate_contract(self.fx.exports/POINTER)['dependency_cycles'])

if __name__=='__main__':unittest.main()
