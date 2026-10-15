"""Contract intake cannot widen delivery scope or discard known rejection."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geoviewer_contract as contract
from geoviewer_delivery import publish_exports
from geoviewer_seismic_delivery import seismic_invalidations
from validate_geoviewer_contract import validate_contract
import test_geoviewer_incremental as live_fixtures


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class DeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root/'selected_data'
        self.exports = self.root/'selected_EXPORTS'; self.exports.mkdir()
        self.package = self.run/'extraction/package/Project'; self.package.mkdir(parents=True)
        self.marker = [dict(object_id='transport-1', receipt_signature='a'*64)]

    def row(self, path):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'SEGY')
        return dict(object_id='transport-1', name='Synthetic', category='seismic',
                    format='SEGY', path=os.path.relpath(path,self.exports).replace('\\','/'),
                    bytes=4, sha256=hashlib.sha256(b'SEGY').hexdigest(), status='exported',
                    numerical_qc='every_amplitude_and_trace_geometry')

    def index(self, rows=None, **kw):
        index=dict(files=rows or [], partial_files=[], status='delivery_indexed', **kw)
        write(self.exports/'FILE_INDEX.json',index)
        return index

    def test_neighboring_run_cannot_be_adopted(self):
        row=self.row(self.root/'another_data/seismic/foreign.segy')
        for package in [self.package,self.run/'report']:
            package.mkdir(parents=True,exist_ok=True)
            with self.subTest(package=str(package)),self.assertRaisesRegex(ValueError,'outside allowed scope'):
                contract.publish_contract(package,self.exports,self.index([row]))
        self.assertFalse((self.exports/contract.POINTER).exists())

    def test_own_generated_fallback_both_layouts_and_project_named_report(self):
        row=self.row(self.run/'seismic/cube/output.segy')
        for package in [self.package,self.run/'report',self.run/'extraction/package/report']:
            package.mkdir(parents=True,exist_ok=True)
            with self.subTest(package=str(package)):
                catalogue=contract.build_catalogue(package,self.exports,self.index([row]))
                artifact=catalogue['tables']['artifacts'][0]
                self.assertEqual(artifact['path_base'],'run')
                self.assertEqual(artifact['relative_path'],'selected_data/seismic/cube/output.segy')

    def test_unsupported_layout_cannot_adopt_common_root_seismic(self):
        package=self.run/'unknown/package';package.mkdir(parents=True)
        row=self.row(self.run/'seismic/output.segy')
        with self.assertRaisesRegex(ValueError,'outside allowed scope'):
            contract.publish_contract(package,self.exports,self.index([row]))

    def test_owned_export_remains_supported(self):
        row=self.row(self.exports/'seismic/output.segy')
        out=contract.build_catalogue(self.package,self.exports,self.index([row]))
        self.assertEqual(out['tables']['artifacts'][0]['path_base'],'exports')

    def test_accepted_invalid_contradiction_refuses_new_generation(self):
        row=self.row(self.exports/'seismic/output.segy')
        with self.assertRaisesRegex(ValueError,'contradicts invalid_seismic'):
            contract.publish_contract(self.package,self.exports,self.index([row],invalid_seismic=self.marker))
        self.assertFalse((self.exports/contract.POINTER).exists())

    def test_old_committed_contradiction_is_independently_rejected(self):
        row=self.row(self.exports/'seismic/output.segy')
        index=self.index([row],invalid_seismic=self.marker)
        # Emulate a generation produced by the old writer, not a tampered digest.
        with patch.object(contract,'validate_seismic_index'):
            contract.publish_contract(self.package,self.exports,index)
        with self.assertRaisesRegex(ValueError,'contradicts invalid_seismic'):
            validate_contract(self.exports/contract.POINTER)

    def test_old_committed_neighbor_path_is_independently_rejected(self):
        row=self.row(self.root/'another_data/seismic/foreign.segy')
        with patch.object(contract,'_base_and_path',return_value=('run','another_data/seismic/foreign.segy')):
            contract.publish_contract(self.package,self.exports,self.index([row]))
        with self.assertRaisesRegex(ValueError,'outside allowed scope'):
            validate_contract(self.exports/contract.POINTER)

    def test_valid_quarantine_without_accepted_payload_is_preserved(self):
        index=self.index(invalid_seismic=self.marker)
        contract.publish_contract(self.package,self.exports,index)
        result=validate_contract(self.exports/contract.POINTER)
        self.assertEqual(result['counts']['artifacts'],0)
        self.assertEqual(json.loads((self.exports/'FILE_INDEX.json').read_text())['invalid_seismic'],self.marker)

    def test_quarantine_schema_and_identity_binding_are_not_silently_ignored(self):
        invalid=[None,{},'bad',[{}],[dict(object_id='x',receipt_signature='bad')],
                 [dict(object_id=True,receipt_signature='a'*64)],self.marker*2]
        for markers in invalid:
            with self.subTest(markers=markers),self.assertRaises(ValueError):
                contract.build_catalogue(self.package,self.exports,self.index(invalid_seismic=markers))
        self.assertEqual(seismic_invalidations({}),[])

    def test_final_index_retains_live_quarantine_binding(self):
        fixture=live_fixtures.IncrementalDeliveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        inventory=fixture.seismic();fixture.delivery.refresh(package=fixture.package,inventory=inventory)
        receipt=Path(inventory['objects'][0]['receipt_path'])
        (receipt.parent/'volume.segy').write_bytes(b'wrong size')
        live=fixture.delivery.refresh(package=fixture.package,inventory=inventory)
        final=publish_exports(fixture.package,inventory,fixture.destination,checkpoint_index=live)
        self.assertEqual(final['invalid_seismic'],live['invalid_seismic'])
        self.assertEqual(final['invalid_seismic'][0]['receipt_signature'],hashlib.sha256(receipt.read_bytes()).hexdigest())
        self.assertFalse(final['files'])
        self.assertEqual(json.loads((fixture.destination/'FILE_INDEX.json').read_text())['invalid_seismic'],live['invalid_seismic'])

    def test_manifest_fingerprints_live_and_seismic_delivery_controls(self):
        pointer=contract.publish_contract(self.package,self.exports,self.index())
        manifest=json.loads((self.exports/pointer['manifest']).read_text())
        for name in ['geoviewer_incremental.py','geoviewer_seismic_delivery.py']:
            self.assertEqual(manifest['engine_script_sha256'][name],hashlib.sha256((Path(contract.__file__).parent/name).read_bytes()).hexdigest())


if __name__=='__main__':unittest.main()
