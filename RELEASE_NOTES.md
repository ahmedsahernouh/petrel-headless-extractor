# Field Viewer Desktop and Headless - Beta 3 (1.0.0-beta.3)

Standalone Windows x64 beta release, 21 October 2026. Extract the whole ZIP to a
short writable folder and open Start Here. Desktop: FieldViewer_Desktop.bat.
Headless: FieldViewer_data_extractor.bat. Python is bundled; Petrel is not required.

## Changes

- CRS and datum only mode reads stored PET/Model declarations without grid/seismic extraction.
- Long companion paths produce individual gaps while successful peer outputs remain available.
- Faster project/companion scanning, bounded report images, and corrected first-use GUI/runtime setup.
- Help > About includes LinkedIn, email, website and GitHub links.
- Help > Check for updates (third item) compares public GitHub releases, including beta numbers. No automatic install.
- Both editions include the CRS batch-audit CLI and agent integration example.
- Start Here begins with the project mindmap and includes three illustrative beta3 GUI captures.

## Scope and validation

Full extraction recovers supported native data and companions; unsupported formats
and ambiguous metadata remain visible as gaps. One large real-project run completed
with gaps: source integrity, output hashes and QC passed, including 750 native LAS,
718 surface XYZ files and five SEG-Y volumes. These are scoped observations, not
universal project coverage or receiving/scientific acceptance.

Exit 0 means completed within the selected scope; 10 means completed with gaps;
2 means failed. Read the final receipt, report and QC. CRS declarations do not
verify coordinates, datum selection or physical units. Clean-VM, DPI and all
receiving-application combinations have not been qualified.

Public beta3 includes RESCUE source-grid metadata inventory through
`fieldviewer_rescue.py --inventory-only`; it does not include static-model RESCUE
export, vendor helpers, profiles or project-specific research routines. The Desktop
RESCUE selection explains this limit. Full and CRS modes remain available.

## Notices and contact

See CONTACT.md, LICENSE, NOTICE and FieldViewer/THIRD_PARTY_NOTICES.md. Bundled
dependencies retain their own licenses. segyio 1.9.14 source and supplemental
LGPL/GPL notices are included under FieldViewer/third_party/segyio/.

Release: https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/tag/v1.0.0-beta.3

## Project summary report (beta3 refresh r12, 22 October 2026)

Project extraction now creates a concise main _REPORT.html with a searchable
Petrel-style data tree. Wells group their logs and trajectories; surfaces/grids,
faults, seismic, models and other discovered categories appear according to the
available catalogue and evidence. Select a dataset for its status, bounded preview
and available download formats. Metadata-only, unsupported and missing datasets
retain their status; the report does not add decoding capability.

The output root has one front report per run. Detailed reports, access pages,
logs, diagnostics and support ZIPs are kept in Details/. All technical evidence
remains available through Detailed report. Reports work
offline, with relative data links when their output folders are kept together.

This refresh is published separately as v1.0.0-beta.3-r12 because the original
beta3 release is immutable. The package version remains beta3, preparation r12.
Existing beta3 installations must download and extract the refreshed ZIP; the version checker
does not distinguish preparation revisions. Runtime, native decoders, Full/CRS
launchers and public RESCUE limits are retained from published r11.
