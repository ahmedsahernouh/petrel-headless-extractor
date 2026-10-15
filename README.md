![FV mark](docs/assets/fv-mark.svg)

# Field Viewer — Beta version

Petrel Headless Extractor · Technical version 1.0.0

By [Ahmed Saher Nouh](https://github.com/ahmedsahernouh) · [SaherLabs](https://saherlabs.dev/)

> **Part of a larger project:** Field Viewer is the publicly released, open-source extraction component of a larger, non-public project. The wider project is not included in this repository or release.

Inspect Petrel projects and recover supported native data without opening Petrel.
Original projects stay read-only; separate outputs include open datasets, HTML
reports, checksums, QC findings and explicit coverage gaps.

**First time using Field Viewer? [Start Here — PDF guide](Start%20Here.pdf) · [Download the HTML guide](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.1/Start.Here.html)**

Copies of `Start Here.pdf` and `Start Here.html` are in the main repository folder above.

**[Download Field Viewer — Beta version (1.0.0), including Start Here (Windows x64)](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.1/FieldViewer-1.0.0-beta.1-win64.zip)**
· [Checksum](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.1/FieldViewer-1.0.0-beta.1-win64.zip.sha256)
· [Release notes](docs/RELEASE_NOTES.md)

The standalone ZIP includes Python and pinned dependencies. It runs offline on
Windows 10/11 x64 with built-in Windows PowerShell, without Petrel, Ocean, system
Python or administrator access. GitHub's automatic **Source code** archives do
not include that runtime. The recommended ZIP includes **Start Here.pdf** and
**Start Here.html** in its root, beside **FieldViewer_data_extractor.bat**.

## Start Here — first-time users

Start with a simple data-flow map showing extracted files and examples of
destination tools for geoscience, analytics, ML/DL and AI agents/harnesses. Follow the illustrated
guide from extracting the downloaded ZIP and entering the
full `.pet` path through opening the HTML reports, reviewing QC and locating the
exported files. Red outline boxes highlight the inputs and controls.

- **[Read Start Here (PDF)](Start%20Here.pdf)**
- **[Download Start Here (HTML)](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.1/Start.Here.html)** — save locally and open in a browser; screenshots are embedded and it works offline.
- [Download Start Here (PDF)](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.1/Start.Here.pdf)

The guide uses the SLB demo saved in Petrel 2018.2. Select your own exact project
path and a separate output folder.
Review the reported limitations and unknown units before using exported data.

## Run and find results

1. Extract the **whole ZIP** into a short writable folder.
2. Keep `Project.pet` beside its complete matching `Project.ptd` directory. Close
   the project in Petrel while extracting. Choose a separate output folder.
3. Drag the exact `.pet` onto `FieldViewer_data_extractor.bat`, or use:

   ```bat
   FieldViewer_data_extractor.bat "D:\Projects\Example.pet" "D:\FV_Out" convert
   ```

4. First launch installs/checks the bundled runtime locally. No online installer
   is required. Later launches verify and repair managed files from that cache.
5. Open `OPEN_RESULTS_*.html` or `*_REPORT.html` in your selected output folder.
   Links lead to exports, QC, diagnostics, logs and supporting evidence.
   [Result access details](docs/RESULT_ACCESS.md).

Append `-ReportOnly` for report/inventory/previews without retained converted
 datasets. Full extraction and full seismic SHA-256 are the defaults. Agents and
scripts can append `-NoPause`; `-RunToCompletion` explicitly selects the default
without an elapsed-time cutoff. Use `--check -NoPause` for a dependency check.

Progress reports actual stages and activity. Stage position is not an overall
 time percentage, recovery rate or ETA. Review the final coverage and QC findings.

## Supported recovery and limits

- Supported native continuous/categorical well logs to CSV; LAS only when its
  required units and sampling are resolved.
- Validated regular grids to XYZ/CSV/ZMAP and explicit meshes to XYZ/CSV, retaining
  masks and unresolved metadata.
- Supported points, polygons, well heads and trajectory profiles, preserving
  identity and ordering. Readable saved workflows are not executed or qualified
  for re-import.
- Supported ZGY to SEG-Y with amplitude/geometry QC and exact axis sidecars.
- Report, inventory, export index, integrity manifests and support diagnostics.

Coverage follows observed binary profiles, not a universal Petrel year range.
Legacy/unsupported stores may yield evidence without native exports. Preserved
bytes and metadata are not decoded objects. Companion conversion is separate.

Unknown units stay explicit; values are never silently rescaled. CRS, datum,
scientific meaning and receiving-app acceptance need separate evidence. Complete
3D models and PaleoScan/OpendTect integration remain deferred. See
[capabilities](docs/GEOVIEWER_1_0.md), [native coverage](docs/BINARY_EXTRACTION_PURPOSE.md)
and [ZGY profile](docs/ZGY_TO_SEGY.md).

Each run creates a `*_SUPPORT.zip`. Recognized identifiers are replaced on a
best-effort basis; review before sharing. Nothing is uploaded automatically.
Contact: [ahmed-saher@hotmail.com](mailto:ahmed-saher@hotmail.com).

## Development and next release

[Build instructions](docs/BUILD.md) · [GUI plan](docs/NEXT_RELEASE_GUI_PLAN_20261012.md)

An optional GUI preview is under development. Planned **Desktop (GUI + CLI)**
and **Headless (CLI / AI agents)** editions share the extraction engine.
The GUI is planned for a future release.

Source project data and validation datasets are not bundled. Supported-profile
and receiving-software limits remain explicit.

## License

[Apache-2.0](LICENSE), copyright 2026 Ahmed Saher Nouh, with [author NOTICE](NOTICE).
Bundled dependencies retain their licenses and provenance manifests. Petrel and Ocean are SLB trademarks;
this independent project is not affiliated with or endorsed by SLB.

Contributions and feedback are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
If the tool helps your research, [citation details](CITATION.cff) are available.
