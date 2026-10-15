# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Points identity tables survive publication without becoming coordinate payloads."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

import geoviewer_delivery as delivery
from geoviewer_contract import publish_contract, POINTER
from validate_geoviewer_contract import validate_contract
import test_points3_identity_stream as binary_fixtures
import export_petrel_native_spatial_zero_gui as spatial


class IdentityDeliveryTests(unittest.TestCase):
    def test_native_token_to_index_retains_duplicate_and_missing_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); package=root/'data'/'package'; exports=root/'EXPORTS'
            values=dict(zip(binary_fixtures.FIELDS,(True,99,1,'same',44,b'\x00\xff',5,6)))
            payload=binary_fixtures.frame(binary_fixtures.document(
                [binary_fixtures.valued_identity(2,values)]*3,count=3))
            points,metadata=spatial.decode_points3(payload); points[1]=None
            rows=spatial.point_identity_export_rows('point-object',7,points,metadata['point_identities'])
            path=package/'05_spatial/points/native_points_identity.csv'
            spatial.write_csv_if_rows(path,rows,tuple(rows[0]))
            report=dict(tool='export_petrel_native_spatial_zero_gui.py',completed_at_utc='2026-10-02T00:00:00Z',
                        objects=[dict(object_id='point-object',blob_type='Points3',status='decoded')],
                        polygon_vertex_rows=0,point_vertex_rows=0,point_identity_rows=3,trajectory_rows=0,native_well_head_rows=0)
            rp=package/'07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json'
            rp.parent.mkdir(parents=True);rp.write_text(json.dumps(report))
            index=delivery.publish_exports(package,{'objects':[]},exports)
            self.assertEqual(len(index['files']),1)
            row=index['files'][0];self.assertEqual(row['category'],'point_identities');self.assertEqual(row['record_count'],3)
            with (exports/row['path']).open(newline='',encoding='utf-8-sig') as stream: out=list(csv.DictReader(stream))
            self.assertEqual([x['point_index'] for x in out],['0','1','2'])
            self.assertEqual([x['xyz_present'] for x in out],['True','False','True'])
            self.assertEqual([x['native_identifier_first'] for x in out],['99']*3)
            self.assertEqual(out[1]['native_os_lmd_database_nbfx_hex'],'a0020000ff')
            pointer=publish_contract(package,exports,index)
            manifest=json.loads((exports/pointer['manifest']).read_text())
            table=(exports/pointer['manifest']).parent/manifest['tables']['artifacts']['path']
            self.assertEqual(json.loads(table.read_text())['role'],'point_identity_metadata')
            result=validate_contract(exports/POINTER)
            self.assertIn('NO_AVAILABLE_ACCEPTED_FULL_PAYLOAD',result['assessments'][0]['reason_codes'])
            # Partial-stage publication still uses its completed table/count gate.
            checkpoint=delivery.publish_exports(package,{'objects':[]},root/'checkpoint',spatial_checkpoint=True)
            self.assertEqual(checkpoint['files'][0]['record_count'],3)
            report['point_identity_rows']=4;rp.write_text(json.dumps(report))
            failed=delivery.publish_exports(package,{'objects':[]},root/'bad_checkpoint',spatial_checkpoint=True)
            self.assertEqual(failed['files'],[])


if __name__=='__main__': unittest.main()
