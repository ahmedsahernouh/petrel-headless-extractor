# Publishing a Field Viewer release

Each published package has a version, an exact source commit and a SHA-256
checksum. The standalone ZIP includes both Start Here guides in its root.

## Prepare the release

1. Choose a new version for each changed published package. Update citation and
   release metadata together. Keep beta releases marked as prereleases.
2. Review the source, Git history, guides and package for publication. Keep demo
   material public and preserve third-party notices.
3. Build using [BUILD.md](BUILD.md). Check ZIP integrity, managed-file hashes,
   dependency checks and the guide links. Record which exact package underwent
   a full project trial and which later changes affect only documentation.
4. Commit the final source and create its release tag. Use a signed tag when
   signing is configured. Retain the build inputs, receipts and checksums.

## Publish and verify

1. Create a draft GitHub release at the final tag.
2. Attach the ZIP, its checksum, both guides, validation evidence and the release
   manifest. The manifest records the source commit and asset hashes.
3. Publish with repository release immutability enabled, then download the assets
   and compare their hashes. Keep subsequent package changes in a new version.

## Citation and archiving

The root [CITATION.cff](../CITATION.cff) supplies the author and software metadata.
When Zenodo is connected to this repository, archive a finalized release and add
its assigned DOI to the citation metadata for that version. Verify the archived
files and metadata before linking the record from the repository.

[GitHub release immutability](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
and [Zenodo archiving](https://help.zenodo.org/docs/github/archive-software/github-upload/)
describe the platform steps.
