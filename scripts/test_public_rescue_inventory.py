"""Public beta3 scope: source metadata inventory without fixture export imports."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import fieldviewer_rescue as rescue


class PublicInventory(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);source=self.root/'source';source.mkdir()
        self.pet=source/'Synthetic.pet';self.pet.write_bytes(b'synthetic PET')
        store=self.pet.with_suffix('.ptd');store.mkdir()
        self.model=store/'Model.ptd';self.model.write_bytes(b'synthetic Model metadata')
        self.data=store/'Data.ptd'
        with closing(sqlite3.connect(self.data)) as db:
            db.execute('CREATE TABLE synthetic(value INTEGER)');db.commit()
        self.output=self.root/'inventory'
        self.before={str(p):rescue.sha(p) for p in (self.pet,self.model,self.data)}

    def inventory(self,rows):
        meta=SimpleNamespace(ancestry=lambda identity:([dict(object_id='model',kind='ModelSubject')]+([dict(object_id='grid',kind='PillarGrid2')] if identity=='property' else [])))
        node=SimpleNamespace(name='ProjectSubject',get=lambda key:'synthetic version')
        with patch.object(rescue.probe.recovery,'Metadata',return_value=meta),patch.object(rescue.probe.binary,'decompress',side_effect=lambda b:b),patch.object(rescue.probe.binary,'read_documents',return_value=[node]),patch.object(rescue.probe,'registry',return_value=rows):
            return rescue.inventory_project(self.pet,self.output)

    def test_grid_and_property_metadata_only(self):
        result=self.inventory([dict(object_id='grid',kind='PillarGrid2'),dict(object_id='property',kind='FloatProperty')])
        self.assertEqual(result['status'],'inventory_verified')
        self.assertEqual(result['grids'][0]['linked_property_count'],1)
        self.assertFalse(result['payload_blobs_decoded']);self.assertFalse(result['rescue_export'])
        self.assertTrue(result['source_integrity_unchanged']);self.assertTrue(result['decoder_unchanged'])
        self.assertEqual(self.before,{str(p):rescue.sha(p) for p in (self.pet,self.model,self.data)})
        self.assertTrue((self.output/'RESCUE_INVENTORY.html').is_file())

    def test_project_without_grid(self):
        result=self.inventory([])
        self.assertEqual(result['status'],'inventory_verified');self.assertEqual(result['grids'],[])
        self.assertFalse(result['complete_model_qualified'])

    def test_export_fails_before_creating_output(self):
        with self.assertRaisesRegex(rescue.NativeError,'not included in public beta3'):
            rescue.export_rescue(self.pet,self.output,self.root/'unread-config.json')
        self.assertFalse(self.output.exists())
        self.assertEqual(self.before,{str(p):rescue.sha(p) for p in (self.pet,self.model,self.data)})


if __name__=='__main__':unittest.main()
