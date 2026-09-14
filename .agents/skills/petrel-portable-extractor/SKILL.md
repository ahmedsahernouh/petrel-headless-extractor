---
name: petrel-portable-extractor
description: Inspect and extract a Petrel project with the portable read-only, zero-GUI toolkit, preserving provenance and reporting proprietary decoding limits.
---

# Petrel Portable Extractor

Read `README.md` and `AGENTS.md` before running tools. Run the doctor first. Require the exact `.pet` path, matching `.ptd` directory, and an output root outside the source project.

Use `scripts/invoke_portable_petrel_extract.ps1` with `CompanionMode=inventory`, `copy`, or `convert`. Keep source projects read-only and never launch Petrel. Prefer `convert` only when the user wants preserved companion copies and supported open-format derivatives.
In `convert` mode, the evidence-gated native spatial stage may decode validated `Model.ptd` `WellTraceSubject` names/well-head XY fields, `Points3`, `Polygons3`, and three trajectory-provider BXML layouts. Require the native-spatial JSON report to show declared-count and structural validation. Treat the well-head X/Y values as native while keeping CRS and units unresolved; attach trajectory-start Z only when the report records an X/Y match. Preserve duplicate native names as separate subjects. Treat well-top rows as native picks only when the report records a unique Petrel-ASCII XYZ calibration; labels remain calibration-derived. Do not infer CRS from coordinate magnitudes.
When multiple Petrel projects share a directory, require the companion report to list neighboring `.pet` and `.ptd` stores as excluded; never mix them into the target package.

After every run, inspect:

- `PROJECT_REPORT.html` for the portable project overview, saved images, spatial preview, extraction coverage, and searchable data tree
- `00_manifest/export_manifest.csv`
- `00_manifest/companion_source_inventory.csv`
- `99_unexported_or_manual/portable_unsupported_inventory.csv`
- `07_workflows_reports/portable_extractor/companion_capability_report.json`
- `07_workflows_reports/native_spatial_zero_gui/native_spatial_decode_report.json` when `convert` mode attempted native decoding
- `02_wells/well_headers/native_well_heads.csv` when native Model.ptd well-head fields were decoded
- latest validation report

Report converted, preserved, degraded, failed, and unsupported counts separately. A successful file conversion does not resolve CRS, semantic completeness, scientific validity, approval, or officiality. Native Petrel arrays remain metadata-only unless a validated decoder is named in the evidence.
