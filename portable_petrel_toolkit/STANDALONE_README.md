# Field Viewer Data Extractor — Beta version (1.0.0)

Field Viewer is a Windows x64 beta for inspecting Petrel projects and recovering
supported native data without launching Petrel or Ocean. Original project files
stay read-only. Coverage follows tested binary profiles, not every object type or
a universal Petrel-version range.

## Start here

Extract the complete ZIP to a short writable folder. Keep these items together:

```text
FieldViewer_data_extractor.bat
Start Here.pdf
Start Here.html
FieldViewer/                     application and offline runtime cache
```

Open [Start Here (PDF)](../Start%20Here.pdf) or
[Start Here (HTML)](../Start%20Here.html) for the illustrated first-run guide.
Double-click the launcher and enter the full `.pet` path. Keep the complete
matching `.ptd` folder beside the project and choose an output folder outside it.
Close the source project in Petrel while it is being read.

The launcher prepares and checks bundled Python and dependencies from its local
offline cache. No system Python, Petrel installation, administrator access or
online installer is required. Retain the full package for dependency repair.

## Options

Full extraction and full seismic SHA-256 are enabled by default. Press Enter at
the two Yes/No prompts to accept these defaults. `-ReportOnly` retains reports,
inventory and previews without retained converted datasets. `-NoFullHash` skips
whole seismic hashes; that weaker integrity scope remains explicit.
`-NoPause` supports unattended use. `--check -NoPause` checks dependencies.
Direct `.zgy` input is also supported for admitted local ZGY profiles.

## Find and review results

Open the top-level `*_REPORT.html` or `OPEN_RESULTS_*.html`. Keep the report and
its `_EXPORTS` and `_data` folders together. The report links to exported files,
`FILE_INDEX.csv`, QC, metadata and diagnostics. Large ASCII and SEG-Y outputs can
take substantial time and storage.

Supported exports include native scalar logs, surface grids, points, polygons,
trajectories, well-head evidence, readable workflow definitions and ZGY-to-SEG-Y
conversion. Unsupported, rejected and empty objects remain distinct. Workflows
are not executed or qualified for native reimport. Full reservoir/RESCUE models
and universal receiving-application compatibility are not supported.

Unknown units, CRS, datum and axis meaning remain explicit. No unit is inferred
from a name or plausible value, and no silent rescaling is applied. Check numerical
and spatial QC before downstream use. Check scientific interpretation and test
imports into other software separately.

Exit 0 indicates completion, exit 10 completion with disclosed gaps, and exit 1
a fatal failure. Retain the process log and support ZIP for diagnosis. Inspect
the archive before voluntarily sharing it: redaction is limited and startup
fallback archives can be unredacted. No diagnostic upload occurs automatically.

## Documentation and licensing

[Repository](https://github.com/ahmedsahernouh/petrel-headless-extractor) ·
[Supported scope](https://github.com/ahmedsahernouh/petrel-headless-extractor/blob/main/docs/PREVIEW_SCOPE.md) ·
[Release notes](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/tag/v1.0.0)

Copyright 2026 Ahmed Saher Nouh / [SaherLabs](https://saherlabs.dev/).
See `LICENSE` and `NOTICE` for Apache-2.0 terms and attribution.
Bundled dependencies retain their own licenses and notices. Petrel source data
and extracted datasets retain their original ownership and permissions.
