# Field Viewer - Beta 2

By Ahmed Saher Nouh / [SaherLabs](https://saherlabs.dev/).

[Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)
| [Beta 2 release](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/tag/v1.0.0-beta.2)

Field Viewer is the open-source extraction component of a larger, non-public
geoscience project. It recovers supported Petrel project data into open formats
without installing Petrel. Python and dependencies are bundled for offline use.

## First extraction

Extract the complete ZIP into a short writable folder. Both Start Here guides
are in the top-level application folder.

**Desktop:** double-click `FieldViewer_Desktop.bat`, browse to the exact `.pet`
file, select a short existing output folder such as `C:\FV`, choose Full
extraction or Report only and click Run. Use Reports / Main report / Open and
Folders / All exports / Open to inspect results when available.

**Headless:** use `FieldViewer_data_extractor.bat`, which is also included in
Desktop. From the top-level application folder:

```bat
FieldViewer_data_extractor.bat "D:\Projects\Example.pet" "C:\FV" convert -NoPause
```

Keep the exact `.pet` file beside its complete matching `.ptd` folder. Choose an
output outside the source project. Source files remain read-only. Full hashing
and run-to-completion are the defaults. Append `-ReportOnly` for reports,
inventory and previews without retained converted datasets. `--check -NoPause`
checks the bundled dependencies. Direct supported `.zgy` input uses the CLI.

## Results

Open the top-level `*_REPORT.html` or `OPEN_RESULTS_*.html`. Keep the report
beside its `_EXPORTS` and `_data` folders. The report links to data, file index,
QC, metadata, diagnostics and logs.

Exit 0 means completed; exit 10 means completed with stated gaps; exit 1 means
a fatal failure. Read coverage, QC and object metadata before using outputs.
Unknown units, CRS, datum and axis meaning remain explicit; values are never
silently rescaled. Complete reservoir/RESCUE export and universal format/version
coverage remain outside the supported profiles. Check receiving-software imports
and scientific interpretation separately.

For failures, retain the logs and support ZIP. Inspect the archive before
voluntarily sharing it. No diagnostic upload occurs automatically.

## Licensing

Copyright 2026 Ahmed Saher Nouh. See `LICENSE` and `NOTICE` for Apache-2.0 terms.
Bundled dependencies retain their licenses and notices. Source projects and
extracted datasets retain their original ownership and permissions.
