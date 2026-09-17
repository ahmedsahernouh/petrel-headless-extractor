# GeoViewer_data_extractor 0.8.0

Read GEOVIEWER_0_8.md for the current layout, modern profiles and unknown-axis seismic import instructions.

# GeoViewer_data_extractor 0.8.0 usage

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Extract the whole standalone release ZIP. The top level contains only:

```text
GeoViewer_data_extractor.bat
GeoViewer/                 application, scripts and offline runtime cache
```

Double-click the BAT and enter your exact `.pet` or `.zgy` path, or drag the file onto it. Keep the BAT beside its support folder; it need not be beside the project. For `.pet`, keep the complete same-name `.ptd` folder beside the project. Close the project in Petrel during the read.

The full visual report and foldable inventory are always included. Dataset conversion is enabled by default: press Enter at `Convert supported data as well? [Y/n]`, or type `n` for report-only. Full seismic hashing is **off by default**: press Enter at `Calculate full seismic SHA-256? [y/N]`, or type `y` for thorough checksums.

Supported native surface grids are included in the same run: XYZ/CSV, plus ZMAP for regular grids, with maps under the report's **Surfaces** filter. Every discovered grid remains searchable in the inventory. The object catalogue links to its files and metadata. Native unit labels remain unresolved where they cannot be verified; no automatic unit conversion is performed. ASCII output can be much larger than the binary project, so select an output drive with enough free space. See [native grid profiles and ZMAP registration](NATIVE_LOGS_SURFACES.md).

Results appear directly in the selected output root:

```text
Example_<run>_REPORT.html        open this full report
Example_<run>_EXPORTS/           open files + FILE_INDEX.csv
Example_<run>_LOG.txt            detailed process log
Example_<run>_EVENTS.jsonl        structured events
Example_<run>_data/              figures, snapshots and receipts
```

The report is a full HTML document, not a redirect. It becomes available before seismic conversion and is refreshed after each dataset; reload it to see updates. Keep the report and its matching data folder together when moving results. Source seismic references need access to the original files; converted SEG-Y and figures are in the result data folder.

## Commands

```bat
GeoViewer_data_extractor.bat "E:\Projects\Example.pet" "E:\Results" -NoPause
GeoViewer_data_extractor.bat "E:\Projects\Example.pet" "E:\Results" -ReportOnly -NoPause
GeoViewer_data_extractor.bat "E:\Projects\Example.pet" "E:\Results" -FullHash -NoPause
GeoViewer_data_extractor.bat "E:\Projects\Example.ptd\cube.zgy" "E:\Results" -NoPause
GeoViewer_data_extractor.bat "E:\Projects\Example.ptd\cube.zgy" -Inspect -NoPause
GeoViewer_data_extractor.bat -Capabilities -NoPause
GeoViewer_data_extractor.bat --check -NoPause
```

Choose an output root outside the source directory and all native stores. Each run gets a unique report/data pair, with no overwriting of previous results. `-NoPause` is for unattended use. The legacy `convert`, `copy` and `inventory` positional modes remain accepted; `-ReportOnly` explicitly disables dataset conversion.

## Seismic conversion and hashing

One BAT handles both project extraction and exact-file ZGY conversion. A project run attempts supported ZGY in the selected store and explicit file/path/URI fields recovered from XML or the validated native BXML project profile. Missing references, unreadable files, unsupported profiles and unlinked companions remain visible. Nearby seismic is not automatically assigned to the selected project. See [ZGY profile](ZGY_TO_SEGY.md).

With hashing off, no whole ZGY/SEG-Y SHA-256 passes or raw seismic copies are added by project preservation/QC stages. The report still includes filename, size, available metadata/geometry, bounded previews, conversion status and output links. Its checksum field says **Not calculated — full hashing disabled**. Fast mode records file identity/size/timestamps and, on Windows, holds a read-only lease while converting. These checks are weaker than full byte-identity proof and are labelled accordingly. Conversion still checks every decoded amplitude and key trace header; this necessary numerical QC can itself take substantial time.

`-FullHash` adds full source SHA-256 before/after conversion and a SEG-Y checksum. Report-only full hashing calculates source inventory checksums. Ordinary application/runtime and non-seismic package integrity checks remain enabled. Full hashes detect byte changes and identify files; they do not prove CRS or geological correctness.

## Dependencies and failures

Windows 10/11 x64 with Windows PowerShell is the target. The BAT installs/repairs bundled Python, plotting and conversion dependencies inside `GeoViewer/runtime` from its verified offline cache. No system Python, pip, administrator access, Petrel, Ocean or network download is required. Cloud SeismicStore is not configured and is unnecessary for local files.

Keep the whole release together. If extraction is incomplete or Windows reports Path too long, cancel and extract again to a shorter writable directory without skipping files. Missing application scripts or a damaged repair cache require re-extraction. A running older release is not updated by unpacking a new one.

Run logs and receipts are inside the result data folder. Per-dataset conversion failures stay in the report while other supported cubes continue. Partial SEG-Y files are not accepted outputs. Exit zero for a project means the extraction/report/QC workflow completed, not that every proprietary object converted. Receiving-software import, CRS, geological acceptance and untested Petrel versions remain separate.

## Progress and timer

The console shows elapsed time and stage progress. Conversion/QC uses measured trace counts. When full hashing is selected, byte progress and Hash ETA apply to that hash pass, not the whole job. Report-only still reads the bounded data needed for figures. No ETAs are invented for unknown stages.

The current shapefile GeoJSON derivative retains source coordinates without reprojection; projected coordinates are not RFC 7946 WGS84 GeoJSON. See the backlog for that separate correction.

## Maintenance update 0.8.1

Open `GEOVIEWER_0_8_1.md` for the new failure handling, diagnostic summary, partial-completion exit code 10, and map controls. Start with the top-level report; its converted-file index and diagnostics links lead directly to accepted data and process evidence. Full resume and complete RESCUE export remain future work.

## License and author attribution

GeoViewer_data_extractor is licensed under **Apache License 2.0**.
Copyright 2026 Ahmed Saher Nouh. See `LICENSE` and `NOTICE` for the license and
original-project author attribution, including [SaherLabs](https://saherlabs.dev/)
and the [project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor).
Bundled dependencies keep their own licenses; see `THIRD_PARTY_NOTICES.md`.
Petrel source data and extracted datasets retain their original ownership and
permissions. Previously published releases through v0.8.1 remain MIT; see
`LICENSING.md` in this distribution for the transition scope.
