# Visual project report and complete inventory tree

[Website](https://saherlabs.dev/) · [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Version 0.5.0 prioritizes a usable visual report before automatic project-wide ZGY conversion. The intended extraction workflow is one BAT, one project, SEG-Y for supported seismic and documented ASCII/text outputs for other data. Automatic project-linked SEG-Y conversion remains subsequent work.

Run the normal project BAT, then open `PROJECT_REPORT.html`. **The full report and complete inventory are always included. Dataset conversion is optional and enabled by default.** At the prompt `Convert supported data as well? [Y/n; Enter = Yes]`, press Enter for conversion or type `n` for report-only.

For unattended report-only runs, use `run_portable_petrel_extract.bat "C:\Projects\Example.pet" "C:\PetrelOut" -ReportOnly -NoPause`. Report-only mode still preserves the native snapshot, inventories companions, and prepares supported native previews in disposable scratch space. It keeps figures, statistics, metadata and inventory tables; it discards temporary log/surface/spatial datasets. Those objects are labelled `preview_only`, never counted as exported datasets. Source links point to preserved binaries. The selected mode appears at the top of the report. The report works offline without a web server. Keep the whole extraction package together so its relative data links work after moving it.

## Included

- **Complete foldable data inventory:** all discovered subject UUIDs from the preserved `.pet`, native registry IDs and decoder/file records. Expand/collapse all and search names, folders, UUIDs, types or statuses. Explicit native parent UUIDs preserve the hierarchy. Unresolved placement, missing parents and cyclic relationships are retained and labelled. This is the full discovered inventory, not a claim that every native payload is decoded.
- **File inventory tree:** the package's files, folders, sizes, validation states and links, separately from the geological object tree.
- **Coverage charts:** decoded object counts against registry IDs, plus log/surface decoder outcomes. These are not a whole-project completion percentage.
- **Log figures:** measured-depth sample tracks and value distributions, preserving original positions; nulls are excluded. Categorical plots show codes/boundary records, not inferred intervals or thickness.
- **Surface figures:** coloured native XY node maps and distributions. Units, domain/sign, undefined nodes and unresolved CRS are explicit. Preview points do not infer contours, triangulation or fault connections.
- **Seismic figures:** a bounded central-inline ZGY patch and its amplitude histogram when a readable ZGY is present in the preserved native store. Axes use zero-based crossline/sample indices. Patch statistics and display clipping are labelled; previewing does not count as SEG-Y conversion.
- **Object catalogue:** recovery state, preview state, valid-record counts, value ranges, reasons and links to CSV, LAS, XYZ, metadata and figures.
- PNG and SVG downloads for individual figures, source links, machine-readable figure statistics/hashes, saved project images, the existing interactive XY overview and browser print/save-to-PDF.

## Performance and evidence

Default budgets: 64 log figures, 36 surface figures and 3 ZGY previews. Every discovered object remains in the data tree/catalogue even if no figure is generated. Numeric CSV previews are bounded at 128 MiB and 2 million rows per object. Log plots display at most 2,500 points; surfaces at most 6,000. Statistics and histograms use all valid records read for that object. Each seismic patch reads at most 1 × 256 × 512 decoded samples; its statistics are patch-only. Compressed-reader caching can require more I/O than the returned sample array.

CSV hashes must match the native decoder receipt before plotting and remain unchanged afterwards. Native log/surface figures require a completed/partial receipt with source preservation confirmed. Missing or changed artifacts produce a visible per-object preview issue. ZGY previews check file size/modification time around the read; they do not add a full-volume hashing pass. Existing extraction hashes remain separate provenance evidence.

Figures and `visual_report.json` are written to a new directory under `07_workflows_reports/visuals/`. Matplotlib and its fonts/dependencies ship in the offline cache; no CDN, map tiles or internet connection is needed. The [Matplotlib non-interactive backends](https://matplotlib.org/stable/users/explain/figure/backends.html) generate PNG/SVG without opening Petrel or a plotting window.

For an existing package:

```powershell
runtime\python.exe -B scripts\report_petrel_project_audit.py --export-package "C:\PetrelOut\existing_package"
```

This adds report artifacts and refreshes `PROJECT_REPORT.html`; it does not re-run native extraction or modify `.pet`/`.ptd`. The report is a QC/exploration aid. Coordinate-system correctness, receiving-software import and geological acceptance remain separate checks.
