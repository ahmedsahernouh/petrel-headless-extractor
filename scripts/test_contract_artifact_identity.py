"""Artifact identity must not depend on FILE_INDEX row order or acceptance state."""
import unittest
from geoviewer_contract import build_catalogue
from test_geoviewer_contract import ContractTests, write


class ArtifactIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture=ContractTests('test_multiple_representations_one_entity_and_roles')
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)

    def identities(self):
        f=self.fixture
        write(f.exports/'FILE_INDEX.json',f.index)
        rows=build_catalogue(f.package,f.exports,f.index)['tables']['artifacts']
        return {(r['entity_key'],r['path_base'],r['relative_path'],r['role'],r['format']):r['artifact_key'] for r in rows}

    def test_reordered_index_does_not_change_artifact_identity(self):
        before=self.identities()
        self.fixture.rows.reverse()
        self.assertEqual(before,self.identities())

    def test_partial_to_accepted_preserves_artifact_identity(self):
        f=self.fixture
        row=f.rows.pop(0)
        f.index['partial_files']=[dict(row,status='checkpoint_only_partial')]
        before=self.identities()
        f.rows.append(row)
        f.index['partial_files']=[]
        self.assertEqual(before,self.identities())

    def test_duplicate_owned_representation_is_rejected(self):
        self.fixture.rows.append(dict(self.fixture.rows[0]))
        with self.assertRaisesRegex(ValueError,'Duplicate owned artifact'):
            self.identities()


if __name__=='__main__':
    unittest.main()
