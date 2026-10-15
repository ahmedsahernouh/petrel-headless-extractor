"""Preserve distinct registry slots and truthful recovery/withholding in delivery."""
import hashlib
import math
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import geoviewer_metadata as metadata
import geoviewer_delivery as delivery
from test_petrel_native_recovery import element, frame, envelope


class WorkflowScopeTests(unittest.TestCase):
    def test_same_uuid_slots_have_distinct_definitions_and_full_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); project=root/'input'/'Fixture.pet'
            project.parent.mkdir(); project.write_text('synthetic workflow fixture')
            store=project.with_suffix('.ptd'); store.mkdir()
            tag='11111111-2222-3333-4444-555555555555'
            with closing(sqlite3.connect(store/'Data.ptd')) as db:
                db.execute('CREATE TABLE data(data_pk INTEGER,droid TEXT,name TEXT,version INTEGER,blob_type TEXT)')
                db.execute('CREATE TABLE blob_parts(data_fk INTEGER,part INTEGER,blob_data BLOB)')
                for pk,slot,label,version in [(1,'withheld_slot','Second',1),
                                              (2,'accepted_slot','First',1),
                                              (3,'accepted_slot','Older',0)]:
                    db.execute('INSERT INTO data VALUES(?,?,?,?,?)',(pk,'://Petrel/'+tag,slot,version,'Commands'))
                    blob=envelope(frame(element('Object',attrs={'Type':'Commands'},children=[element(label,'synthetic')])) )
                    db.execute('INSERT INTO blob_parts VALUES(?,?,?)',(pk,0,blob))
                db.commit()
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob('*') if p.is_file()}
            package=root/'package'; destination=package/'native_workflows'
            context={'native_decoders_applicable':True,'objects':[{'object_id':tag,'name':'Workflow <test>'}],'saved_version':'fixture'}
            receipt=metadata.export_workflows(project,destination,context)
            self.assertEqual(receipt['status'],'completed')
            self.assertEqual({r['registry_slot'] for r in receipt['objects']},{'accepted_slot','withheld_slot'})
            files=list(destination.glob('*.json'));self.assertEqual(len(files),2)
            for row in receipt['objects']:
                path=destination/row['artifacts'][0]['path']
                recovered=json.loads(path.read_text())
                self.assertEqual(recovered['registry_slot'],row['registry_slot'])
                self.assertEqual(recovered['source']['registry_droid'],'://Petrel/'+tag)
                self.assertEqual(recovered['object_id'],tag)
                self.assertEqual(recovered['registry_version'],1)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['artifacts'][0]['sha256'])
            metadata.write_json(package/'01_project_metadata/workflow_recovery.json',receipt)
            index=delivery.publish_exports(package,{'objects':[]},root/'exports')
            self.assertEqual(len(index['files']),2)
            self.assertEqual({r['object_id'] for r in index['files']},{tag})
            html=delivery.metadata_section(dict(context,workflows=receipt))
            self.assertIn('registry slot withheld_slot',html)
            self.assertNotIn('0 serialized entries',html)
            self.assertNotIn('Workflow <test>',html)
            self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_bounded_identity_names_and_preexisting_target_are_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            # Keep the parent near the qualified Windows budget, leaving only
            # the digest and a short display prefix.
            root_length=len(str(root).encode('utf-16-le'))//2
            destination=root/('d'*(220-root_length-1))
            targets=[metadata.workflow_target(destination,'😀'*40+' Same name','id',slot)
                     for slot in ('slot-one','slot-two')]
            self.assertNotEqual(targets[0],targets[1])
            self.assertTrue(all(len(str(p).encode('utf-16-le'))//2 <= 259 for p in targets))
            same=metadata.workflow_target(destination,'other display','id','slot-one')
            self.assertEqual(targets[0],same)  # The label is omitted when the path budget is tight.
            path=targets[0];path.parent.mkdir(parents=True)
            path.write_text('existing unequal content')
            with self.assertRaisesRegex(ValueError,'already exists'):
                metadata.write_workflow_json_exclusive(path,{'value':1})
            self.assertEqual(path.read_text(),'existing unequal content')
            interrupted=destination/'interrupted.json'
            with patch.object(metadata.os,'fsync',side_effect=OSError('write interrupted')):
                with self.assertRaisesRegex(OSError,'write interrupted'):
                    metadata.write_workflow_json_exclusive(interrupted,{'value':1})
            self.assertFalse(interrupted.exists())
            with self.assertRaisesRegex(ValueError,'no room'):
                metadata.workflow_target(Path('X:/'+'d'*226),'Name','id','slot')

    def test_nonfinite_scalar_array_and_attributes_use_strict_loss_aware_tags(self):
        from petrel_native_binary import Node, Array
        node=Node('root',attrs={'values':[1.,float('nan'),float('inf'),-float('inf')]},
                  content=[0.,float('nan'),[float('inf'),-float('inf')]],
                  children=[Array('array',np.array([1.,float('nan'),float('inf'),-float('inf')],dtype='<f8'))])
        encoded=metadata.typed(node)
        raw=json.dumps(encoded,allow_nan=False)
        decoded=json.loads(raw)
        self.assertEqual(decoded['content'][0],0.)
        for source,target in [(float('nan'),decoded['content'][1]),
                              (float('inf'),decoded['content'][2][0]),
                              (-float('inf'),decoded['content'][2][1])]:
            self.assertEqual(len(target['parsed_binary64_hex']),16)
            recovered=struct.unpack('>d',bytes.fromhex(target['parsed_binary64_hex']))[0]
            self.assertEqual(math.isnan(recovered),math.isnan(source))
            if not math.isnan(source):self.assertEqual(recovered,source)
        self.assertEqual(decoded['attributes']['values'][1],decoded['content'][1])
        self.assertEqual(decoded['children'][0]['values'][2],decoded['content'][2][0])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'invalid.json'
            with self.assertRaises(ValueError):
                metadata.write_workflow_json_exclusive(path,{'nan':float('nan')})
            self.assertFalse(path.exists())

    def test_empty_recovered_definition_differs_from_unattempted(self):
        html=delivery.metadata_section({'workflows':{'objects':[
            {'name':'Empty','status':'definition_recovered','command_count':0,'command_types':[]},
            {'name':'Unknown','status':'unsupported','reason':'<unsupported profile>'}]}})
        self.assertIn('0 serialized entries recovered',html)
        self.assertIn('not recovered (unsupported)',html)
        self.assertIn('&lt;unsupported profile&gt;',html)


if __name__=='__main__':
    unittest.main()
