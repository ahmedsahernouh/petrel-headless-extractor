# Release validation

## GeoViewer_data_extractor 1.0.0 — Initial public release

Validated **2026-09-17** against the exact standalone ZIP:
`b0e6431d54768e64c559065b62c32bef08302b3e283d083585ca65e4bb8bd4fe`
(`GeoViewer-1.0.0-win64.zip`, 59,329,445 bytes).

- **157 packaged regression tests**, **36 standalone acceptance checks**, and
  **7 polygon/grid checks** passed. The latter include an independent ZMAP readback.
- Actual BAT runs exercised offline installation, missing/corrupt dependency and
  Python repair, cache failures, an active runtime, incomplete extraction,
  paths containing spaces, unsupported layout reporting, log/grid extraction,
  report-only mode, project-linked and direct ZGY conversion, interactive entry,
  tamper rejection and restored-bundle integrity checks.
- The extracted package's metadata, CLI and generated report identified 1.0.0.
  LICENSE and NOTICE matched the source copies and their manifest hashes. The
  report displayed Apache License 2.0 and Ahmed Saher Nouh's original-author credit.
  The current guide and licensing document were verified inside the ZIP.
- Synthetic source hashes stayed unchanged. Tests used relocated directories on
  the same Windows host, unavailable network proxies and an intentionally invalid
  system-Python environment; no second-machine or physically disconnected-network
  claim is made. Runtime/dependency preflight also passed.

This release changes version identity, licensing, documentation and packaging;
changed production Python decoders/converters differ only in version literals.
The earlier real-project evidence below remains development-version evidence;
no new complete private-project extraction was performed for this release.
Complete RESCUE export and broader native profile coverage are not introduced.
The release's `VALIDATION.json` provides a sanitized check list and artifact identity.

## GeoViewer_data_extractor 0.8.1

A modern project recording **Petrel 2024.5.0** was tested through the actual BAT
with deliberate Windows output locks. A persistent directory lock exhausted five
delayed retries, recorded one isolated finalization failure and continued the
remaining **319 log/grid objects**, the report, accepted-file index and SEG-Y
conversion. The affected object never entered the accepted-file index.

A separate transient file lock reproduced **WinError 5**. Finalization recovered
on attempt five without decoding or writing the dataset again. All **306 logs,
69 grids, 111 spatial objects and 18 well headers** were recovered. All **799
native CSV/XYZ/ZMAP/LAS artifacts** matched the prior successful baseline byte
for byte. Native source hashes and extraction/QC receipt verification passed.
The available seismic volume converted with every amplitude and trace geometry
checked. Four unavailable external references remain explicit gaps; they do not
invalidate the available conversion. The report links **1,290 accepted files**
in **489 export groups**.

The exact final ZIP passed **157 packaged regression tests**, offline bootstrap,
actual-BAT conversion/report-only checks and an independent ZMAP readback.
Additional CLI and dependency-repair evidence, artifact hashes and the small
final launcher change are distinguished by tested ZIP identity in the release's
`VALIDATION.json`. No second physical machine or physically disconnected-network
claim is made.

Headless Edge tests used the actual report markup. Eighteen well markers stayed
within one CSS pixel of the selected diameter during repeated zooming, fitting
and resizing; well labels and grid text retained their screen sizes. A separate
exact-overlap fixture kept both wells individually selectable. Real-project
screenshots, paths, object identities and raw logs remain private.

Full cross-run resume, manual support-bundle redaction and complete 3D RESCUE
export remain follow-on work. The release does not expand the native decoder's
claimed version coverage. [Usage and remaining boundaries](GEOVIEWER_0_8_1.md).

## GeoViewer_data_extractor 0.8.0

The modern fixture records **Petrel 2024.5.0** in native metadata. Its complete standalone run recovered **306 logs, 69 surface grids, 66 polygon payloads, 26 point payloads, 19 trajectory providers and 18 well headers**, plus one readable saved workflow definition. The available ZGY converted to SEG-Y with every decoded amplitude and trace geometry checked. Four external SEG-Y references were absent from the supplied store; they are reported separately from the five base and two virtual seismic object definitions. Native numeric readback does not establish geological correctness or resolve unknown units.

An independent **Microsoft XmlDictionaryReader** comparison matched **329 arrays/masks across 95 payloads**: all 69 modern surface payloads and all 26 point XYZ arrays. This checks complete arrays, not selected coordinates. Container decompression/framing is shared with the bounded native reader; point attributes/IDs and geological/CRS interpretation are outside this comparison. Synthetic controls independently inspect SEG-Y bytes/headers and import regular-grid ZMAP outputs with zmapio.

The delivered runtime passed **143 regression tests**. Actual BAT tests cover relocated Windows ZIP extraction, absent system Python, unavailable package indexes/proxies, offline first-run installation and dependency repair, missing/corrupt cache, active-runtime repair rejection, incomplete extraction, spaces in paths, source/output overlap, tampering, unsupported-layout inventory, native logs/grids, segmented polygons, report-only mode and integrated/direct ZGY conversion. The exact artifact identities, executed acceptance results and any documentation-only rebuild are recorded in the release's `VALIDATION.json`. Tests run on the same Windows host; no second-machine or physically disconnected-network claim is made.

Source projects remain unchanged. Private data and project-identifying reports/logs/screenshots are not distributed. Complete 3D RESCUE, general fault topology, legacy distributed numeric decoding and blanket 2023/2024 support are **not** established. See [the observed-profile guide](GEOVIEWER_0_8.md) and [remaining requirements](RELEASE_BACKLOG.md).

## Earlier releases

Version 0.7.0 expands native surface-grid profiles and adds ZMAP exports and grid maps. Its controls cover packed masks and padding, original index ordering, native/model bounds, missing coordinate context, unknown units, null/cell gaps, memory/space limits, independent ZMAP reading, changed preview artifacts, and report-only output boundaries. Microsoft `XmlDictionaryReader` agreed on all numeric arrays and mask bytes for 213 supplied-project grids. Independent ZMAP imports cover 18,461,876 node positions across five regular grids. Full-run recovery counts, report checks and the exact final standalone ZIP's acceptance results are in the attached `VALIDATION.json`; private data stays local.

Version 0.4.0 adds native well-log and surface recovery to project extraction. The [profile and aggregate evidence](NATIVE_LOGS_SURFACES.md) document private native numerical checks, sparse independent LAS reference comparisons, regular-grid geometry calibration and the unresolved cases. Its synthetic regression suite covers literal/dictionary NBFX records, bounded framing, corruption, units, source mutation, masks, inherited geometry, irregular MD, null collisions, latest-version selection, partial output and report counts. The standalone acceptance harness runs these tests from the delivered relocated runtime. Release-specific executed results are attached as `VALIDATION.json`.

By [Ahmed Saher Nouh](https://github.com/ahmedsahernouh) · [SaherLabs](https://saherlabs.dev/) · [GitHub repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

The initial standalone package passed 12 acceptance checks on Windows x64, including extraction of two locally supplied projects, on 2026-09-12. Those projects are not distributed. The public release is rebuilt with generic path defaults and the project license, then validated separately before upload.

The acceptance harness covers full bundle verification, an actual BAT extraction with spaces and `&` in paths, missing stores, source/output overlap, unsupported native layout rejection, companion exclusion, native spatial controls, the portable doctor, tampering rejection, and source hashes. Release-specific results are in the attached `VALIDATION.json`.

Version 0.2.0 missed Explorer's extra ZIP-stem folder during Extract All. A real Windows extraction failed with `0x80010135: Path too long`, leaving 3,397 manifest files missing, including every launcher/helper script. Version 0.2.1 uses short ZIP/root names, checks the nested extraction path budget, tests an incomplete package with its PowerShell launcher missing, and exercises the interactive project/output prompts. The BAT reports incomplete extraction before attempting PowerShell and retains startup errors on screen unless `-NoPause` is requested. These are regression checks for the observed failure, not a claim that every possible extraction location is supported.

Version 0.2.2 adds offline first-run installation with no Python present, deletion/corruption repair, missing Python executable recovery, missing/corrupt cache rejection, and a numerical ZFP round-trip. The cache and repair logs are excluded from project companion ingestion.

Tests relocate the ZIP, remove system Python from PATH, set invalid Python environment overrides, disable pip indexes, and use unavailable HTTP proxies. They run on the same Windows host: no second physical machine or physically disconnected network is claimed. Successful checks establish execution and integrity, not geological interpretation, correct CRS, universal format coverage, or Petrel re-import.

Version 0.2.3 fixes a MemoryError in companion text detection: slicing `read_bytes()` still loaded the entire file before slicing. Detection, header inspection, text profiling and PNG header inspection now use bounded reads. Regression controls forbid unbounded reads, cover a giant single-line text profile, retain exact small-file line counts, and verify that oversized files never reach copying or conversion and keep an honest inventory status.

Version 0.2.4 adds SaherLabs and maintainer GitHub attribution to the BAT banner, project documentation, source comment headers and toolkit metadata. Validation checks the displayed links, packaged file hashes, runtime preflight and unchanged extraction logic. The v0.2.3 extraction acceptance results remain the functional baseline; the full project-extraction suite is not repeated for attribution edits.

Version 0.2.5 adds stage completion, elapsed time, measured hash progress and hash ETA. Controls exercise exact streamed hash bytes, failure without false completion, long elapsed times, child progress events, silent-child heartbeats, complete logs, nonzero exits, and timeout cleanup of the owned process tree. The relocated-ZIP acceptance harness also checks all 12 stages, success/failure completion behavior, saved timing and the bundled progress controls. Release-specific executed results are attached as `VALIDATION.json`.

Version 0.3.0 adds beta ZGY-to-SEG-Y conversion and corrects native coverage reporting. Its 20 binary/coverage controls cover float32, scaled int8/int16 and ZFP inputs; independent SEG-Y byte/header/sample checks; rotated geometry; metadata conflicts and missing units; unsupported sampling/depth; source overlap/change; disk failure and cancellation. Report controls exclude companions, rejected/empty objects and duplicate IDs from native recovery counts. A read-only check against historical demo evidence reconciled 72 decoded spatial objects: 39 polygons, 6 point sets and 27 trajectories.

The v0.3.0 acceptance harness exercises the delivered conversion BAT, metadata inspection, interactive input and the main BAT's ZGY routing, in addition to offline dependency installation/repair and project-extraction regressions. The release also checks an isolated native project subset; its data is not distributed. See the attached `VALIDATION.json` for executed checks and exact ZIP/source identity. Native well logs, general surfaces, faults, grids and properties remain incomplete. No full real seismic cube conversion, receiving-application import or complete terabyte-scale project extraction is claimed for this release.
