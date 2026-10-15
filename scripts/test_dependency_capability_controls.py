"""Positive planned-parent and stale-decision controls for dependency guards."""
import copy
import json
import unittest
import test_contract_dependency_guards as fixtures


class CapabilityControls(unittest.TestCase):
    setUp = fixtures.DependencyGuardTests.setUp
    assess = fixtures.DependencyGuardTests.assess
    log = fixtures.DependencyGuardTests.log
    add_dependency_artifact = fixtures.DependencyGuardTests.add_dependency_artifact

    def test_planned_well_requires_same_target_and_qualified_parent(self):
        self.add_dependency_artifact()
        tables = self.catalogue['tables']
        next(x for x in tables['entities'] if x['entity_key'] == self.well_key)['source_status'] = 'decoded'
        values = dict(crs='EPSG:32639', crs_status='verified', horizontal_unit='m',
            vertical_domain='depth', vertical_unit='m', depth_unit='m', vertical_reference='MSL',
            well_identity='reviewed-exact-well',
            head_semantics=dict(xyz_roles=['easting','northing','depth'], vertical_reference='MSL', source_locator='synthetic-exact-head'))
        template = tables['metadata_evidence'][0]
        tables['metadata_evidence'] += [dict(template, entity_key=self.well_key,
            field=k,raw_value=v) for k,v in values.items()]
        path = self.fx.decisions(profile='paleoscan',record=dict(well_target=dict(
            source_entity_key=self.well_key,target_id='planned-well',state='planned')))
        data = json.loads(path.read_text('utf-8'))
        parent = copy.deepcopy(data['entities'][self.log_key])
        parent['target_object_id'] = 'planned-well'
        parent.pop('well_target')
        data['entities'][self.well_key] = parent
        path.write_text(json.dumps(data),'utf-8')
        report = self.assess(profile='paleoscan',decisions=path)
        self.assertEqual(self.log(report)['receiving_state'],'qualified_for_planned_mapping')
        parent['target_object_id'] = 'different-well'
        path.write_text(json.dumps(data),'utf-8')
        self.assertIn('PLANNED_WELL_MAPPING_NOT_QUALIFIED',
            self.log(self.assess(profile='paleoscan',decisions=path))['receiving_assessment']['reason_codes'])

    def test_previous_profile_decisions_are_rejected(self):
        for profile, old in [('opendtect','opendtect-7.0.10-odbind-1.0.0-preparation/6'),
                             ('paleoscan','paleoscan-2023.1.1-file-preparation/6')]:
            with self.subTest(profile=profile):
                path=self.fx.decisions(profile=profile)
                decision=json.loads(path.read_text('utf-8'));decision['profile_revision']=old
                path.write_text(json.dumps(decision),'utf-8')
                with self.assertRaisesRegex(ValueError,'generation and profile'):
                    self.assess(profile=profile,decisions=path)


if __name__=='__main__':unittest.main()
