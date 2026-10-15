# Field Viewer: supported beta scope

Field Viewer Beta version (1.0.0) runs on Windows x64. It reads supported Petrel
project layouts without launching Petrel or Ocean. Keep the complete `.pet` and
matching `.ptd` folder together and select a separate output directory.

## Supported outputs

- Native scalar logs in CSV; LAS where required metadata and sampling are resolved.
- Supported regular grids in XYZ/CSV/ZMAP and explicit grids in XYZ/CSV.
- Supported point, polygon, trajectory and well-head profiles, with object identities.
- Supported local ZGY conversion to SEG-Y with numerical and geometry checks.
- HTML reports, inventories, file indexes, checksums and local diagnostics.

Support follows tested binary profiles, not a universal Petrel-version range.
Each object has its own result. Unsupported, missing, rejected and empty objects
remain distinct from recovered payloads. Workflows are readable definitions;
execution and native reimport are not qualified.

## Review before using the data

Unknown units, CRS, datum and axis meaning remain explicit. Display preferences
or plausible values do not establish physical calibration. No automatic rescaling
is applied. Check the final report and metadata before downstream calculations.
Check geological interpretation and receiving-application imports separately.
Complete reservoir/RESCUE models,
general fault reconstruction and universal automatic resume are not supported.

## First run

Extract the complete ZIP to a short writable folder. Open `Start Here.pdf` or
`Start Here.html` beside `FieldViewer_data_extractor.bat`. The launcher prepares
the bundled runtime from its offline cache. No system Python, Petrel installation,
administrator access or online installer is required.

Exit 0 indicates completion; exit 10 indicates completion with disclosed gaps;
exit 1 indicates a fatal run failure. Keep the HTML report and sibling output
folders together. Large ASCII and SEG-Y outputs can require substantial storage
and processing time. Inspect the local support ZIP before voluntarily sharing it;
automated redaction is limited and source datasets must not be included without
permission. No diagnostic upload occurs automatically.

Repository: <https://github.com/ahmedsahernouh/petrel-headless-extractor>
