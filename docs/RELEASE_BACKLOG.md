# Release requirements and implementation status

## 1.0.1 metadata isolation correction

Requested **2026-09-17** following diagnosis of missing native exports. Isolate
ambiguous model-subject metadata without disabling independent native decoders;
retain per-record reasons and candidate IDs in logs/report; distinguish readable
containers from complete metadata. Correct nested-project seismic discovery and
conversion-gap accounting. Legacy storage and complete 3D RESCUE remain separate
capability work. See [maintenance scope](GEOVIEWER_1_0_1.md).

## 1.0.0 initial public release

Requested **2026-09-17**: publish the current tested functionality as the **1.0.0 initial public release**, with matching application, CLI, report, receipt and package version labels. Include Apache-2.0 LICENSE, original-author NOTICE and current licensing/usage documentation in the rebuilt standalone ZIP. Earlier 0.x development tags and assets retain their historical terms. This release identity and packaging update does not add RESCUE support or broaden validated Petrel decoding profiles.

## NEXT-26: Apache-2.0 and original-author attribution

Registered **2026-09-16**; implementation explicitly requested **2026-09-17**. Status: **implemented in source, report attribution and standalone packaging**. The project now uses **Apache License 2.0 with a NOTICE file** preserving attribution to **Ahmed Saher Nouh**. Previously published releases through v0.8.1 remain MIT. See [licensing and attribution](LICENSING.md).

The subsequent instruction to update the license supersedes registration-only status. Both LICENSE/NOTICE copies, source copyright/SPDX headers, toolkit metadata, documentation, the report attribution section and the standalone builder are updated together. This source change does not replace existing release ZIPs or retrospectively change their licensing.

The previous MIT license already required retention of its copyright and permission notice. Apache-2.0 supplies an explicit NOTICE attribution mechanism: relevant notices must accompany distributed derivatives, with permitted placement in a NOTICE file, accompanying source/documentation, or the customary notices display. It does not promise perpetual prominent credit, require an author banner on every screen/report, or make the original author the main author of someone else's subsequent contributions. NOTICE is informational and cannot add license restrictions. See [Apache-2.0, sections 4 and 6](https://www.apache.org/licenses/LICENSE-2.0).

Original-project attribution included in NOTICE:

```text
GeoViewer_data_extractor
Copyright 2026 Ahmed Saher Nouh

Original project creator and principal author: Ahmed Saher Nouh
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
```

Implementation requirements:

- Apply the unmodified Apache-2.0 text to owner-controlled project code, with the copyright/author NOTICE. Check any contributed or incorporated third-party code before changing its licensing; retain its required notices and terms. Bundled dependencies keep their own licenses.
- Update both project LICENSE copies, documentation and report license wording together. Include LICENSE and NOTICE in source and standalone ZIP distributions and verify their presence after clean extraction. Use appropriate copyright/SPDX identifiers on owner-controlled source files. Follow the [Apache application guidance](https://www.apache.org/foundation/license-faq.html#Apply-My-Software).
- Keep the requested website/repository-only CLI banner. Put author credit in NOTICE and the report's attribution section without imposing an additional downstream UI requirement.
- Preserve historical MIT releases and the permissions already granted for those versions. State the effective release explicitly; do not retrospectively relabel old ZIPs or receipts.
- Explain that Apache-2.0 permits commercial redistribution and proprietary derivatives; it does not require downstream versions to stay free of charge or publish their modifications. See the [Apache redistribution FAQ](https://www.apache.org/foundation/license-faq.html#Distribute-changes).
- Keep FieldViewer/GeoViewer branding separate from copyright licensing; do not assert trademark registration. Do not add custom advertising or mandatory permanent-banner clauses while calling the result standard Apache-2.0.

## Next maintenance update: robustness, diagnostics and map usability

Registered **2026-09-16**, following the supplied 0.8.0 Windows finalization failure and oversized overlapping well-marker screenshot. **Implementation authorized 2026-09-16 for 0.8.1; implemented and validated; see the version-specific validation record.** The user explicitly prioritizes robustness. The detailed [correction plan](ROBUSTNESS_LOGGING_MAP_PLAN.md) contains failure policy, bounded retries, checkpoint/partial-result handling, logging contracts, map controls and acceptance gates. It takes priority over new RESCUE development; the existing RESCUE plan remains deferred.

| ID | Priority | Planned scope |
|---|---|---|
| NEXT-22 | P0 | Safe finalization; isolate object/category failures; continue independent recovery; stop honestly for systemic/integrity failures; preserve validated partial outputs/report/index. |
| NEXT-23 | P0 | Comprehensive lifecycle/error/process logs; top-level diagnostic summary; retries/OS details; resilient log collection; clear aggregate and per-format outcomes. |
| NEXT-24 | P1, next-maintenance gate | Constant screen-size well/point markers with independent size/opacity controls, readable labels, coincident-object selection, fit-visible controls and stable grid/axes. |
| NEXT-25 | P1, follow-on | Explicit checkpoint reconciliation and resume/retry of unfinished work after identity/integrity checks; deeper diagnostics and manual support bundle. Durable checkpoints belong in NEXT-22/23 first. |

The incident demonstrates incomplete filesystem-failure isolation and missing primary-error propagation in 0.8.0. Existing NEXT-14/NEXT-17 delivery below describes implemented pieces, not comprehensive robustness. The subsequent user instruction authorized implementation. See [the 0.8.1 guide](GEOVIEWER_0_8_1.md) for delivered scope; full resume and redacted support bundles remain follow-on work.

User clarification: **routine isolated errors must not block the application**. NEXT-22 requires automatic bounded recovery or an explicit local failure while independent exports and report/index delivery continue without an intervention prompt. Error counts or child exit codes alone must not trigger a whole-run abort. Acceptance scenario R00 in the correction plan makes this behavior mandatory. Shared storage/integrity failures retain the controlled-stop boundary.

## Next version after v0.7.0: registered requests and recommendations

Registered **2026-09-15** from the user's report review and follow-up instructions. **Implementation authorized 2026-09-16 for version 0.8.0**, with Petrel 2024 analysis the primary priority. This supersedes the earlier registration-only instruction. The original requirements below are retained as the scope record; the status table identifies delivered behavior and remaining work.

Native projects remain read-only, and Petrel/Ocean are never launched. Project names, usernames, private paths, UUIDs and source data belong in the private project handover, not in this public backlog. Historical statements below describing the executable as unchanged apply to the registration date, not to 0.8.0.

### 0.8.0 disposition

| IDs | Implemented scope | Remaining boundary |
|---|---|---|
| NEXT-01, 02, 10 | Native recorded version/build/save time, separate filesystem time, saved-by/history evidence, project license field and software/dependency notices in the report. | Absent evidence stays unknown; no entitlement inference or fabricated version estimate. |
| NEXT-03 | Native base/virtual seismic counts separated from physical files; UUID-local association, recorded external references and per-file outcomes. | A user-supplied relocated external-data search root is still planned. Original remote hosts are not probed automatically. |
| NEXT-04 | Numerically readable ZGY with unknown physical axis/units can export with unspecified headers and exact native-axis sidecar/textual metadata. | Receiving software needs explicit axis configuration. Fractional known-time intervals outside the current header profile need a separate extended-header implementation. |
| NEXT-05, 06 | Preview/recovery/integrity distinctions, explicit preview statistics, full log CSV explanation; rejected spatial objects no longer link to a shared successful CSV. | Broader status vocabulary remains in existing detailed receipts; do not equate package QC success with complete geological recovery. |
| NEXT-07, 08 | Typed/framed Points3, modern object-ID slots, verified surface Boolean field, integral float-encoded categorical logs; preserved polygon segments and numeric export checks. | Unobserved object versions, general fault topology and unsupported surface layouts remain explicit gaps. |
| NEXT-09 | Full seismic SHA-256 independently optional/off; conversion/readback independent; per-stage/object/hash timings. | Existing non-seismic integrity passes remain; comprehensive I/O deduplication is deferred. |
| NEXT-11 | [Complete RESCUE export plan](RESCUE_EXPORT_PLAN.md), component inventory and independent-reference acceptance gates. | No complete 3D model exporter ships in 0.8.0. |
| NEXT-12, 19 | Observed 2024.5.0 profiles, regression fixtures and standalone acceptance; modern-first capability guide. | One 2024.5 fixture is not all-2024 support; a separate 2023 fixture is still needed. |
| NEXT-13, 21 | GeoViewer_data_extractor name, FV mark and visual FieldViewer affiliation; separate runtime/repository. | No FieldViewer code integration or registered-trademark claim. |
| NEXT-14 | Top-level text/event logs, bootstrap transcript, child output, stage/object timing, exceptions and report links. | Private paths and data may appear in local debug evidence; public release evidence is sanitized. |
| NEXT-15, 20 | Identified nested toolkit trees pruned, shorter package/run names, ProviderPath and SQLite UNC fixes, source/output separation retained. | No arbitrary Windows path-length guarantee; unrecognized nested software is not silently excluded. |
| NEXT-16 | Readable Commands JSON, original serialized order/references and foldable report entry list. | Execution, native re-import and inferred control-flow diagrams are not validated. |
| NEXT-17 | Layout preflight before native decoders, early/failed-run report, independent supported categories continue. | Legacy distributed numeric decoding is still unsupported; successful inventory is not complete recovery. |
| NEXT-18 | Separate searchable converted index, category folders, object-specific shallow files and file-index CSV/JSON. | Hard-link fallback reports original paths if the filesystem cannot support shallow links. Unsupported full models/faults are inventory entries only. |

See [0.8.0 usage and limitations](GEOVIEWER_0_8.md) and [release validation](VALIDATION.md).

### Preserved product requirements

- **Next-release software name, requested 2026-09-16: `GeoViewer_data_extractor`**, with this exact capitalization and underscores. It replaces Petrel Extractor / Petrel Headless Extractor as the product name in the next version; see NEXT-21. Current release artifacts remain unchanged.
- Recover native Petrel/binary data into usable open outputs without Petrel or Ocean. SEG-Y is the main seismic output; ASCII/text is preferred for other recovered data. Generic open-to-open conversions such as CSV-to-LAS are not a new priority.
- **Primary compatibility priority, clarified 2026-09-16:** recover data directly from Petrel **2023, 2024 and later** projects for recipients who do not have those releases. Modern-version recovery takes priority over legacy decoding. End-user access to the originating Petrel release, project resaving or downgrading must not be prerequisites. These are targets requiring validation, not current blanket support claims.
- One main BAT at ZIP root beside the support folder. Project extraction and direct ZGY conversion use that entry point. Keep the offline bundled runtime, dependency checks/repair, progress bar and elapsed timer.
- The full visual report and complete searchable, foldable inventory are always included. Put the report beside its matching data folder, prominently show its path, and update its per-dataset outcomes through completion.
- Dataset conversion stays optional and selected by default. Full seismic SHA-256 stays optional and unselected by default. A skipped full hash does not remove seismic from inventory or previews.
- Preserve the website and project-repository CLI attribution, in that order; do not restore the removed personal-name/profile lines.
- Preserve native polygon segment/part identity, vertex ordering, closure and gaps. Show well logs and recovered surface grids in the report, with links to their actual outputs.

### Registered work items

The following table preserves the original scope. The disposition table above governs current implementation status. "Requested" identifies a user requirement; "recommended" identifies an improvement included in the user's registration request.

| ID | Origin | Scope and intended result |
|---|---|---|
| NEXT-01 | Requested | Prominent project identity, Petrel version and save/creation information, with native evidence or labelled estimates. |
| NEXT-02 | Requested; clarified report location | License information in the HTML report, with Petrel/project information separated from extractor and dependency notices. |
| NEXT-03 | Requested after count discrepancy | Reconcile native seismic objects, local files and external references; explicitly declare availability and missing links. |
| NEXT-04 | Requested; supersedes earlier restriction | Export readable seismic with unresolved domain/units instead of treating missing interpretation metadata as a blanket blocker. |
| NEXT-05 | Requested/recommended | Separate source availability, export/recovery, preview, numerical QC, units, integrity and manifest registration; remove ambiguous status wording. |
| NEXT-06 | Requested/recommended | Explain log sample CSVs and distinguish complete-data statistics from limited preview statistics. |
| NEXT-07 | Recommended after user-reported failures | Replace heuristic Points3 checkpoint recovery with validated typed/framed decoding; correct per-object names, links and map availability. |
| NEXT-08 | Requested/recommended | Investigate remaining surface-grid geometry profiles and improve grid figures, statistics and coverage reporting. |
| NEXT-09 | Requested/recommended | Honour hashing independently of conversion applicability; improve timing and avoid redundant reads where integrity evidence permits. |
| NEXT-10 | Requested/recommended | Enrich report metadata, figures, object coverage and history while retaining the full foldable inventory. |
| NEXT-11 | Requested export plan | Develop and validate complete native 3D reservoir-grid export to a RESCUE package as a separate workstream. |
| NEXT-12 | Recommended | Synchronize capability descriptions, version labels, documentation and regression/portable acceptance evidence with actual shipped behavior. |
| NEXT-13 | Requested; visual branding only | Add the existing FV icon and FieldViewer brand identity to the extractor's presentation while retaining its separate repository, code and runtime. |
| NEXT-14 | Requested; diagnostic detail recommended | Save a detailed process log for debugging, performance evaluation and explaining per-dataset outcomes, with prominent report/CLI links. |
| NEXT-15 | Recommended after confirmed v0.7.0 failure | Exclude nested/versioned extractor installations from companion discovery and handle long output paths with useful failure evidence. |
| NEXT-16 | Requested; feasibility and validation required | Investigate read-only extraction of saved Petrel workflows into readable definitions, inventories and report diagrams, with explicit limits on re-importability. |
| NEXT-17 | Recommended after confirmed stage-6 failure | Detect native storage layout before invoking decoders; distinguish unsupported layouts from missing/copied-file failures and retain an honest partial report. |
| NEXT-18 | Requested; high-priority usability requirement | Separate the complete project inventory from the decoded/converted-data index; provide shallow export folders, meaningful filenames and direct report links for every available output category. |
| NEXT-19 | Requested research and planning; modern-first priority clarified | Prioritize direct recovery from verified 2023/2024 and later projects through observed container/storage/object profiles and per-category validation; retain legacy support as a secondary workstream. |
| NEXT-20 | Confirmed during modern-project investigation | Correct mapped-drive/UNC path normalization before containment checks and expensive work; preserve source/output separation and useful diagnostics. |
| NEXT-21 | Requested; next version only | Rename the software to `GeoViewer_data_extractor` across the launcher, report, documentation and release presentation, preserving technical Petrel format/version references. |

### NEXT-01: project identity and chronology

Show original and latest recorded Petrel version, recorded build date, creation event, last native save, saved-by account and project comments where present. Separate the source Petrel version, extractor release and decoder component version. Do not present an internal serializer version as a Petrel marketing version or build number.

Prefer explicit native version fields and `ProjectSerializer.saved_time`, cross-checked against native Save history where available. File modification time is a separately labelled fallback, not silently a native save event; copied-file creation time is not project creation. Keep report-generation time separate. Preserve UTC/offset evidence and label unknown time zones rather than adopting the current machine's zone. For estimates, show **Recorded / Estimated / Unknown**, the evidence and a qualitative confidence level; the user permits clearly labelled guesses, not fabricated certainty.

Provide a foldable history timeline for recorded creation, load/save, account, CRS and unit-setting changes. These are recorded events and account identifiers, not independently established ownership or a complete activity log.

### NEXT-02: license information in the report

Add a **License information** section to the HTML report. Display available Petrel license/module metadata only when supported by project evidence, with source and scope. When absent, state **Not recorded in the project**. A plugin/module reference, username or saved version does not establish current license entitlement. Keep this distinct from the extractor's open-source license and bundled third-party notices, and link those notices from the report. Do not require a license-server connection or installed Petrel for report generation.

### NEXT-03: complete seismic accounting and external references

Read supported native `Model.ptd` seismic subject/file-reference records as well as the `.pet` hierarchy and selected project store. Associate files with native object UUIDs and readable Petrel names. Keep original recorded paths separate from current resolved paths; do not turn a historic `storage_ok` field into a current availability claim.

The overview, seismic section and foldable inventory must explain the same population: registered objects, associated local ZGY, associated existing SEG-Y, unresolved external references, unlinked nearby files, previews, new conversions and failures. Keep distinct object/file counts and handle shared references without double-counting. An existing SEG-Y reference is already an open format, not a newly converted ZGY.

Show each object's name, ID, format, original/current paths, current accessibility, metadata state, preview and export outcome. Clearly declare external references missing at their recorded location. Allow an explicit relocated-data root to be supplied for controlled resolution, retaining ambiguity instead of silently accepting a filename-only match. Preserve neighboring-project exclusions and do not search unrelated drives automatically.

### NEXT-04: export with unresolved metadata

The user's clarified default is **recover the data and declare the uncertainty**. When amplitudes and numeric geometry can be decoded and represented correctly, missing domain, amplitude/vertical/horizontal units or CRS alone must not prevent export. Use **Exported - metadata unresolved**, alongside independent numerical QC and integrity results. This planned policy supersedes the earlier blanket metadata-blocking rule in this backlog and the current purpose/profile instructions; those operational documents and checks must be reconciled when implementation is authorized. The current executable is unchanged by this registration.

Preserve decoded amplitude values, trace order, inline/crossline identities, numeric XY, native sampling origin/increment and available provenance. Do not infer milliseconds, metres, feet or time/depth, resample, reproject, reverse signs or override conflicting known metadata silently. Separate amplitude units, vertical domain/units, horizontal units and CRS. Use standard unspecified codes only where permitted; retain otherwise unrepresentable or uninterpreted native metadata in clearly labelled SEG-Y textual/extended metadata and sidecars. Unknown-domain export is not a validated time-to-depth conversion.

Test the supported SEG-Y header/revision profile and receiving-reader behavior, including unknown interval/units, so the file retains mandatory structural information without assigning false physical units. The user may later investigate or supply interpretation metadata. Actual read/write, structural, integrity or numerical read-back failures still prevent acceptance of the affected output; missing interpretation metadata alone does not. Add a validated depth-domain profile separately from the existing time profile.

### NEXT-05: truthful statuses, registration and links

Present source, recovery/export, preview, numerical QC, semantic metadata and integrity as separate fields. Use specific reasons in every affected table/tree/card and propagate final seismic failure reasons into the object catalogue, not just its status. Distinguish:

- **Source missing at recorded path**: an unavailable referenced file, not a decoder failure or proof that no relocated copy exists.
- **Decode failed / ambiguous layout**: parsing or validation stopped; do not imply source corruption merely from a rejected profile.
- **Unsupported profile**: the current decoder does not support that representation.
- **Exported - metadata unresolved**: values were recovered, with interpretation metadata still unknown.
- **Preview ready / not generated / failed**: object-specific plotting results, independent of export success.
- **Not listed in main manifest**: a registration issue, not automatically missing or failed data.

Reconcile native recovery artifacts/receipts with the main export manifest and file-tree status. Keep recorded checksums separate from newly verified checksums and numeric equality separate from unit/CRS acceptance. Account explicitly for generated/control files, including manifests that cannot hash themselves. Show only links to accepted outputs that contain records for the particular object. Failed point objects must not inherit a shared CSV link or a generic `see_spatial_map` claim from other successfully decoded objects.

### NEXT-06: sample CSVs and preview scope

Use readable well/curve/object labels and explain the meaning of `samples.csv`: all recovered records for one curve, not a small preview. Explain `sample_index`, measured depth `md`, `raw_value` and `is_null`; link the adjacent metadata, identify unresolved units and show total/valid/null counts. Preserve missing markers and original sample positions in outputs while excluding nulls from plots and statistics. Display CSV numeric recovery even where LAS or semantic unit resolution is unavailable.

For seismic, label counts and amplitude ranges as **preview-patch statistics**. The current bounded window can be 1 inline x 256 crosslines x 512 samples (131,072 finite samples when all values are finite); that is not the full cube's sample/trace count. Show full cube dimensions, preview location/shape, decimation/clipping and the statistics scope separately. A generic amplitude-unit label must not be confused with vertical-axis or XY units. Label an inventory/metadata link accurately rather than presenting it as the seismic file or a SEG-Y export.

### NEXT-07: native point decoding and spatial reporting

Investigate the supplied Points3 failures at point index 168 using declared BXML block lengths and typed arrays instead of coordinate-plausibility-based byte skipping. Read-only inspection has parsed complete arrays with the existing structured reader; this is a development lead, not a completed exporter or independent numerical validation. Validate declared counts, XYZ ordering, null slots, framing boundaries and any additional fields, then test the affected objects and regression cases before accepting exports. Retain original files unchanged and never salvage guessed coordinates silently.

Resolve actual point-object names from exact native identity links. Correct preview/output eligibility per object as specified in NEXT-05. Preserve the validated polygon segment/part ordering, explicit closure and gap handling, and verify that spatial changes do not regress those fixes.

### NEXT-08: additional surface-grid profiles

Investigate grids rejected by the existing unrotated/unflipped/context restrictions, including inherited geometry, with representative evidence and explicit transforms. Extend only representations whose numeric geometry can be validated. Preserve node/cell definitions, nulls, axis order and native value signs. Export suitable regular grids to ZMAP and XYZ/CSV, explicit/irregular geometry to appropriate XYZ/CSV, without forcing an incompatible ZMAP grid. Show unsupported cases with their actual reason and name.

Add dimensions, increments, rotation, extent, defined/null coverage, value ranges and output links; retain mapped previews and full-grid statistics. Contours and additional views are recommendations where geometry supports them. Surface-grid support must remain distinct from 3D reservoir-grid support.

### NEXT-09: integrity and performance

When full seismic SHA-256 is selected, perform and record it even if SEG-Y conversion is blocked or disabled, provided the source is accessible. Report requested, pending, completed, skipped and failed hashes with their actual scope. Do not label a requested-but-unperformed hash `not_requested`, or imply a one-pass checksum proves before/after identity. Conversion numerical QC remains independent of this optional full-file hash.

Investigate repeated non-seismic artifact hashing and avoid redundant reads only where an unchanged snapshot and retained verification evidence justify reuse. Show phase durations, elapsed time, appropriate progress/estimates, output sizes and per-dataset outcomes. Do not equate overall report/package completion with universal conversion success.

### NEXT-10: report content and layout

Recommended order: **project identity -> recovery/availability summary -> spatial overview -> category figures -> complete foldable inventory -> history and detailed evidence**. Keep search, filters, lists and usable relative links.

Include project units as recorded separately from decoded-array units; CRS/datum evidence and uncertainty; dataset extents/footprints; source and output size totals; seismic sampling/geometry/datatype and output estimates; well IDs/heads/trajectories and log coverage/gaps; surface statistics; polygon segments/closure and point counts; fault/horizon presence and topology limitations. Show what actually became usable, metadata-only objects, omissions and the required next information per object. Detect/report data-quality issues only where supported by the data, without inventing scientific findings or silently combining incompatible coordinates.

### NEXT-11: complete 3D reservoir grid to RESCUE

This is an **export development plan**, not a claim that the surface exporter already supports reservoir models, and not a guarantee that the complete exporter will ship in the next release. The report/reliability improvements can ship independently once separately authorized and validated. Keep full RESCUE export pending until the following stages pass:

1. **Discover and link the model.** Identify native 3D grid objects (including supported `PillarGrid2` representations), properties and associations. Establish dimensions, pillar/corner geometry, IJK order, active cells, faults/connections, zones/layers and local refinements. Retain names, units, CRS and unresolved metadata.
2. **Validate decoding.** Use representative fixtures and a trusted reference export to verify geometry, ordering, continuous/categorical properties, missing values and topology. A cell-centre CSV is not a complete 3D grid export. No Petrel/Ocean runtime dependency is introduced into the standalone product; creation of any new Petrel-authored reference is outside this registration.
3. **Write the full package.** Assess RESCUE writer/library availability, redistribution licenses, version compatibility and offline packaging. Prefer an ASCII profile matching the user's preference; evaluate binary as an additional tested option. Keep all related RESCUE files together, preserving supported properties, selected faults/transmissibility multipliers, well trajectories and active local grid sets. Identify unsupported components explicitly rather than advertising a partial model as complete.
4. **Read back and report.** Independently read the package and compare cell counts, geometry, active flags, indexing, property values/nulls, units and connections with the source/reference. Test receiving-software import for the declared profile. Add accurate 3D views, slices, property statistics, inventory and package links using the recovered geometry.

Installed Petrel 2018 documentation describes native RESCUE export, but that is version-scoped evidence for Petrel's own export route, not proof of a standalone exporter. Public background: [Energistics RESCUE standard](https://energistics.org/rescue-standards).

### NEXT-12: consistency and acceptance when implementation is authorized

Update obsolete capability text, including "project-linked SEG-Y conversion not integrated", and keep launcher, report, package/release metadata, capability registry and docs accurate. Distinguish release versus component versions rather than changing them solely to make numbers match. Reconcile the new unknown-metadata policy in `AGENTS.md`, the purpose contract and ZGY documentation at implementation time.

Acceptance should cover actual object/file count reconciliation; unresolved and relocated external references; unknown-domain/unit SEG-Y preservation and truthful headers; optional hashing on rejected conversions; exact log/point/grid read-back; per-object links and missing/failed states; source preservation; portable report links/filters; and a relocated offline BAT/dependency-repair run. Use sanitized/synthetic fixtures in the public repository and keep supplied project evidence private. Do not rerun full suites or extraction for this documentation-only registration.

### NEXT-13: FieldViewer visual brand affiliation

Registered **2026-09-15** following the user's request to add the FV (FieldViewer) icon and place this product under the FieldViewer brand name and trademark identity, visually only. The user reiterated **"don't change code now"**. Status remains **pending; planning only**.

- Reuse the existing **FV monogram**: white FV lettering inside a rounded square with a blue-to-green gradient (`#378add` to `#1d9e75`, 135 degrees). Retain FieldViewer capitalization and compatible typography/colors. This is the requested FV mark, distinct from the separate crosshair symbol seen in the FieldViewer menu header.
- The earlier proposed display identity **FieldViewer - Petrel Headless Extractor** is superseded by NEXT-21: the software name is **`GeoViewer_data_extractor`**. The existing FV icon / FieldViewer family affiliation remains a separate visual-branding request, not an alternative product name or authorization to integrate the codebases. Use any family attribution as secondary text.
- Planned placements: HTML report header, browser tab icon, report footer, README/documentation header and release presentation. Use a plain-text brand label in the terminal while preserving the previously requested website and project-repository attribution. Do not restore the removed personal-name/profile lines.
- Package the visual mark for offline use when implementation is authorized. Reports must not depend on the FieldViewer application, external image/font servers or private-repository access. Use an embedded/vector or bundled image asset with readable small-size rendering and an accessible text label.
- Keep the standalone extractor repository, source tree, dependency/runtime boundary, release lifecycle and functionality separate from the FieldViewer application. This request does not authorize code integration, shared services, a submodule, application launch or changes to the FieldViewer project.
- Record FieldViewer as the requested brand/trademark identity without claiming that registration or legal status has been verified. Do not add an unverified registered-mark symbol or invent trademark ownership/legal notices. Branding does not replace the existing software license or third-party notices.
- Future acceptance: consistent mark/name across planned surfaces; readable report/print/small-icon presentation; offline rendering; retained website/repository links; unchanged extraction behavior and no FieldViewer runtime dependency. Only planning documents are changed now; no icon assets, HTML, BAT or other source code are generated or modified during registration.

### NEXT-14: detailed process logging and problem evaluation

Registered **2026-09-15**: the user requested a detailed log of the process for debugging and evaluating problems, explicitly **"don't change code now"**. Status: **pending; planning only**. The following is the proposed logging contract, not a claim that the current release implements it.

- **Always retain a readable run log.** Start at BAT/bootstrap entry, before dependency checking or Python initialization, and continue through extraction, conversion, report generation and final QC. Proposed visible output: `<run>_PROCESS.log` beside `<run>_REPORT.html`, with detailed machine-readable events under the matching data folder (for example `logs/events.jsonl`). Before an output root is selected, use a writable local bootstrap log; carry it into the run when possible and display its location if startup fails. Show the actual log path in the terminal and report, including on failure, without requiring a completed report to find it.
- **Identify the run and settings.** Record a unique run ID, UTC timestamps with explicit timezone, elapsed durations, extractor/build identity, Windows/Python/dependency versions, selected source/output paths and effective user options. Include report-only/conversion selection, seismic hashing selection, decoder/export profile and metadata overrides. Capture relevant environment and dependency-repair results, not an unrestricted environment dump.
- **Explain every stage and dataset.** Record stage start/end, duration, work counts and outcome; object UUID/name/category; original and resolved file references; selected parser/profile and supported version; metadata available or unresolved; export/skip/reject decisions and exact reason; output locations, record/null counts and QC results. Separate missing source, unsupported layout, decode ambiguity, metadata uncertainty, numerical mismatch, write failure and registration problems as in NEXT-05. Log hash requested/performed/skipped/failed independently of conversion, with scope and duration.
- **Preserve actionable errors.** Retain warnings, exception types/messages, full tracebacks and chained causes, child-process stdout/stderr, exit codes and relevant redacted command arguments. Correlate them with the run, stage and dataset rather than leaving an unexplained pipeline exit number. Include parser offsets, expected/observed counts and framing/profile details when available; do not dump entire binary payloads or seismic/log samples. Keep the terminal concise while writing diagnostic detail to disk.
- **Make slow or interrupted work understandable.** Emit rate-limited progress/heartbeat events showing current operation, last completed work unit, files/bytes processed and measured throughput where available. Track dependency setup, inventory, decoding, hashing, conversion, report generation and QC durations separately. Flush records incrementally, preserve completed events after recoverable failures, and record cancellation when the process can handle it. An abrupt process termination or power loss may prevent a final event: missing completion must mean interrupted/unknown, never inferred success. Expose logging-write failures rather than silently claiming complete diagnostics.
- **Bound the overhead.** Record useful stage/object/chunk summaries rather than every sample or loop iteration. Offer optional deeper diagnostic verbosity for repeat investigations, with controlled log growth and preservation of error evidence. Logging must not trigger additional full seismic reads, enable hashing, repeat conversions or require network access.
- **Summarize problems in the report.** Add a foldable Process log / Diagnostics section with stage timing, warnings/errors, per-object failure reasons, links to the full readable log and structured events, and any suggested next action. Distinguish observed failure evidence from a suspected cause; do not invent a diagnosis. The final summary must reconcile successful, skipped, unavailable and failed datasets with the inventory and receipts, even when the report itself completes successfully.
- **Support deliberate sharing.** Keep detailed local logs for investigation. A proposed optional diagnostic bundle should include version/settings summaries, logs and relevant receipts, exclude source datasets and credentials, and provide a redacted copy for sharing (paths, usernames and project/object names can be sensitive). Never upload logs automatically or overwrite the original local evidence with a redacted copy.

Future acceptance should exercise bootstrap/dependency failure, an unsupported native layout, a missing external source, a conversion/QC failure, successful partial recovery, handled cancellation and an abruptly interrupted run. Confirm readable UTF-8 logs, preserved child-process errors, correct run/object correlation, truthful completion states, usable relative report links, redaction and bounded overhead. Implementation, test execution, extraction and release publication remain deferred.

### NEXT-15: nested toolkit exclusion and long-path handling

Status: **pending; diagnostic review and registration only**. A supplied v0.7.0 extraction log confirms a companion-stage failure at `target.parent.mkdir`: **WinError 206, filename or extension too long**. The attempted destination included `09_source_companions/<versioned extractor folder>/PetrelExtractor/.agents/skills/...`. The extractor had classified files from its own installation as project companions. Existing source inspection shows toolkit detection checks only recognized layouts directly at the project root; it misses an installation under a versioned wrapper directory within that root. This is a confirmed discovery/exclusion defect, not evidence of native Petrel data corruption.

- Exclude the actual running toolkit root and runtime, and identify additional nested toolkit installations through reliable package signatures. Prune their trees before companion enumeration, profiling, copying or hashing. Do not exclude genuine project data merely because a directory is named `scripts`, `runtime` or resembles a release filename. Preserve neighboring Petrel project/store exclusions.
- Address path-length handling separately for genuine companions: assess projected destination paths, supported Windows/runtime behavior and portable shorter output naming with preserved source-to-output mapping. Report exact operation and affected path on failure; do not assume enabling an OS setting alone fixes all package/runtime paths.
- Distinguish isolated companion-file failures from fatal output-root or preservation/integrity failures. Retain evidence and present partial results truthfully where continuing is safe; never silently call a failed copy preserved or successful. Link the underlying child error into NEXT-14 diagnostics rather than leaving only the generic pipeline exit code.
- Document the current workaround: keep the entire extractor distribution outside the project source directory, with its BAT and support folder together, and choose a short output root outside the source directory. Remove the installation from the scanned source location by moving it, not by leaving a duplicate there. This avoids the demonstrated self-ingestion; it does not establish that later extraction stages will succeed.
- Future regression cases: a versioned distribution nested below a project root, a renamed/nested active toolkit, multiple recognizable toolkit copies, ordinary similarly named project directories, and a genuine long companion path. Verify that excluded toolkit files are not ingested, genuine companions are preserved or explicitly reported failed, and diagnostic/report outcomes remain accurate.

No code fix, source relocation, extraction rerun, build or publication is performed during this registration.

### NEXT-16: extract and document saved Petrel workflows

Registered **2026-09-16** following **"consider extraction of workflows if possible"**, with the explicit follow-up **"no code change yet"**. Status: **pending; feasibility/planning only**. This concerns existing saved project workflows, not generation of new workflows or execution of their commands.

- **Discover workflow definitions.** Investigate version-scoped links between project hierarchy entries, workflow identity records and command payloads in native stores. Retain names, IDs, hierarchy, source record locations and available description/version metadata. Deduplicate stored versions and distinguish live saved definitions from old records, plain keyword hits, execution logs and externally generated plans. Current keyword/region mapping alone is not complete workflow recovery.
- **Recover supported structure and settings.** Where validated, extract ordered commands, command types/names, enabled/disabled state, comments, variables with types/scopes/values, parameters, loops/conditions/nesting, subworkflow calls and input/output object or file references. Resolve exact native IDs to names when possible and show unresolved references explicitly. Do not infer step order, branch logic or parameter values from nearby strings or assume unsupported commands are empty.
- **Write open, readable outputs.** Proposed deliverables under `07_workflows_reports/workflows/`: a workflow inventory CSV, one structured JSON definition and readable TXT/Markdown summary per supported workflow, and associated evidence/limitations. Preserve verified original workflow-export files if present. Ordinary JSON/XML/text summaries are documentation/interchange representations; they are not automatically Petrel-importable workflows or executable Python scripts. A native re-importable export is a separate feasibility item requiring a known format and independent round-trip validation before that capability is advertised.
- **Include workflows in the full report and inventory.** Add a foldable Workflows branch, count summary and per-workflow views listing commands, parameters, variables, referenced datasets/files and missing dependencies. Provide offline flow diagrams for verified sequencing/branching and links to the readable outputs. Clearly separate `metadata only`, `partially decoded`, `definition decoded` and independently established native re-import support. An extracted definition does not prove that its steps ran or that the referenced geological outputs exist.
- **Keep the product's existing selection model.** Workflow inventory and available bounded report detail belong to the mandatory report. Persistent workflow-definition exports follow the optional conversion/extraction selection, enabled by default; no new independent BAT or FieldViewer application dependency. Unknown layouts remain listed with reasons and must not prevent unrelated data recovery.
- **Inspect without executing.** Never launch Petrel or Ocean, run extracted scripts/System commands, evaluate expressions, follow external executable links, or patch native workflow records as part of headless extraction. Display recovered content as escaped text and keep local paths/command arguments out of any public examples or shared diagnostic bundle unless appropriately redacted. Preserve source stores unchanged.
- **Validate before claiming support.** Use existing mapped reference workflows as candidate fixtures, comparing names, command counts/order, values, references and unsupported sections against trusted Petrel-authored definitions. Cover disabled steps, nesting/conditions/loops, subworkflow references, missing plugins/objects/files and duplicate/version records. Keep partial results explicit; do not promote a bounded mapped-command experiment into a general cross-version decoder. New Petrel-authored reference capture or re-import testing is outside this registration and requires its own execution authorization.

Feasibility evidence exists in the private Petrel automation project's native workflow mapping work, but a general standalone workflow-definition exporter has not been established by this review. No workflow extraction, native-file edit, code change, test execution or release is performed now.

### NEXT-17: native-store preflight and partial reporting

Registered **2026-09-16**, status **pending; diagnostic review only**. A supplied v0.7.0 log confirms that companion handling completed, then native spatial decoding stopped with `FileNotFoundError: Data.ptd was not found` at the expected copied path `08_native_project/ptd_store/Data.ptd`. The decoder opens this fixed path before per-object handling and returns exit code 2 on failure; the wrapper stops before the final report. The log establishes absence at the expected package path, not the source project's storage layout, corruption, or the cause of that absence. A project filename containing a Petrel version is not layout evidence.

Follow-up direct, read-only comparison of the supplied original and 2018-saved copies now confirms the layout incompatibility for this pair. The original store contains many GUID-named `.ptd` files and no `Model.ptd`/`Data.ptd`; its project header is rejected by the current native compression-envelope parser. The saved copy records Petrel `2018.2`, uses a supported project/model container, and has SQLite `Data.ptd` with the required `data` and `blob_parts` tables. Earlier validation on a demo originating in 2010 but saved in 2018.2 does not validate the original legacy storage. Correct any capability wording or tests that equate a filename/origin year with the tested saved format. Legacy discovery/header/model/payload decoding needs its own fixtures and profiles; adding a filename fallback alone is insufficient.

- Detect actual native-store layout and required signatures/tables before selecting spatial, log, surface or workflow decoders. Verify referenced source locations and compare native-copy inventory/manifest evidence with the package. Distinguish a supported store, unsupported storage profile, intentionally absent/not-applicable store, source missing/incomplete, copy failure and unreadable/corrupt data. Do not infer an empty project from zero SQLite registry rows.
- Invoke each decoder only for a validated matching profile. Unsupported layouts should produce a visible unavailable/unsupported capability result while other independent recovery and the mandatory inventory/report continue. Do not rename GUID-named stores to `Data.ptd`, manufacture empty databases or borrow a neighboring project's store to satisfy the filename check.
- Keep missing required source/copy/integrity errors visible as failures, rather than downgrading all exceptions to warnings. Preserve verified partial outputs and generate a diagnostic report where safe, with failed/not-run stages and affected categories clearly distinguished from successful extraction. No successful final receipt if mandatory integrity checks failed.
- Assess older or distributed storage layouts as separate parser work only after inspecting the actual store and trusted reference evidence. Do not advertise general legacy-project decoding from this error alone. Correct reporting and capability detection can precede development of new parsers.
- Future acceptance: a genuinely supported SQLite store; a project with another verified storage layout; absent source data; a missing copied file despite a manifest entry; invalid schema; unreadable store; and report-only mode. Confirm source preservation, clear per-category reasons, available inventory/report delivery and accurate overall success/partial/failure status.

This registration changes planning documents only. No new decoding, extraction rerun, file substitution, code change, build or release is authorized by it.

### NEXT-18: obvious open-format deliverables and file discovery

Registered **2026-09-16** after the user asked where extracted 2D grids are saved and how users can find files for loading into other software without difficult searching. Status: **pending; planning only**.

User clarification on **2026-09-16** makes this a **high-priority requirement**: shallow nesting and easy access are essential; the report must link converted grids, polygons, logs, faults, seismic and complete models when supported; indexing all project objects must be separate from indexing decoded/converted data objects. This strengthens NEXT-18 without authorizing code changes now.

Current native grid exports are nested under `<run>_data/extraction/package/<package>/native_data/<object>/`: `surface.zmap` for supported regular grids, `surface.xyz`, `nodes.csv` and metadata/auxiliary files. The existing report's **Object catalogue and data links** section exposes artifact links, but the directory depth and repeated generic filenames make bulk importing difficult. Preview images and `grid_preview.npz` are not substitutes for full numeric exports.

- Provide two clearly labelled, separately searchable report indexes with prominent navigation near the top: **Full project inventory** and **Decoded / converted data**. The first retains the complete foldable native object hierarchy, metadata-only/unsupported/missing objects, external references and recovery outcomes. The second indexes actual completed data exports, organized by category and object, with all available formats grouped under that object. Keep independent counts for discovered source objects, objects with exported data and output files; several formats or sidecars for one object must not inflate the exported-object count. Cross-link the two views by exact source object identity.
- Use a clearly named `<run>_EXPORTS` folder directly beside the top-level `<run>_REPORT.html`, separate from internal evidence. Prefer `<run>_EXPORTS/<category>/<meaningful-file>` for single-file outputs. Permit one additional named object/bundle directory where related components must stay together, especially complete models; do not repeat the internal `extraction/package/native_data` nesting. Organize all supported output categories, including seismic, surface grids, polygons, points, well headers/logs/trajectories/tops, faults, workflows and models. Show empty/unsupported categories in the report rather than creating misleading empty deliverable folders.
- Use meaningful object-based filenames with stable collision-safe IDs, so a surface can be identified after copying it into another application. Preserve the full native object identity in the index/metadata and deterministic source-to-output mappings. Retain one authoritative copy of each large numeric dataset rather than duplicating seismic/grids just to make them easier to find; update all relative report/receipt/manifest links coherently when implementation is authorized.
- Make **Decoded / converted data** the direct route to loadable files: category filters, object/well/model-name search, format filters, direct per-file links and a searchable `FILE_INDEX.csv`/HTML view. Include object name/ID, parent well/model where applicable, relative file path, format, size, dimensions/counts, source identity, numerical validation status, domain/units/CRS known or unresolved, and applicable import notes. Display the relative folder and file path for selecting it from another application's import dialog, alongside the link. Avoid requiring users to follow a receipt or metadata JSON to reach the actual data file.
- Give every available format its own clearly named link. Examples are **ZMAP / XYZ / CSV** for surfaces, **ASCII / CSV** for polygons or points (with segment/order information), **LAS / CSV** for logs where each is actually available, **SEG-Y** for completed seismic conversion, and the validated geometry/connectivity files for faults when supported. For future complete models, link the entire required RESCUE/model bundle and component index, preserving properties and dependencies; a single partial component must not be labelled a complete model. Unsupported formats/categories remain explicit and receive no fake output link.
- Include an object in the decoded/converted index when it has at least one completed, usable numeric/definition export. Keep metadata-only objects, previews, raw native backups, unconverted source references and incomplete `.partial` artifacts out of this export count. A valid CSV with unresolved units remains accessible and clearly labelled. If one format succeeds and another fails, expose the successful file and the failed-format reason rather than hiding the whole object or calling every output successful. Distinguish native decoding from conversion of an already-open companion file in provenance/counts.
- For surface imports, identify ZMAP as the regular-grid output and XYZ as ASCII `X Y VALUE`; describe the actual CSV columns, null values, axis/value sign convention, units/CRS status and related metadata. Do not infer elevation, units or CRS merely to make an import look complete. Keep regular grids distinct from irregular XYZ meshes, and link all auxiliary geometry/mask files needed to understand a representation.
- The terminal should print both the report and exported-data locations at completion or partial completion. If no numeric datasets were exported or report-only was selected, state that explicitly instead of implying a missing folder is an error. Keep the report, exports and supporting evidence portable together, and preserve offline operation.
- Future acceptance: from the report a user can select a category/object and open the required data format without browsing the internal package tree. Check this across every supported category, not only grids. Verify that the full inventory includes non-exported objects while the export index includes only real completed outputs; counts reconcile by object and file; duplicate names and multi-format exports remain unambiguous; filenames remain unique; representative import/read-back verifies full-resolution values and geometry; metadata uncertainty remains visible; all links survive relocating the run; and no failed/partial artifact is presented as a successful deliverable. Preserve related files together for multi-file models and test the empty/report-only states.

No files are moved, renamed, re-exported or duplicated now. This is a deferred output-layout and report-navigation requirement only.

### NEXT-19: compatibility across Petrel versions

Registered **2026-09-16**, status **pending; research and planning only**. See the [Petrel version compatibility research and plan](PETREL_VERSION_COMPATIBILITY_PLAN.md) for primary sources, evidence limits, architecture, phased work and acceptance gates.

Use detected container/storage/object signatures alongside recorded saved-version metadata. Do not infer native support from origin year or filename, equate Petrel's own upgrade support with extractor compatibility, or claim a universal supported-year range. The user's clarification on **2026-09-16** supersedes the earlier legacy-first sequence: prioritize NEXT-17 preflight/partial-report resilience, then actual **2023/2024** recovery fixtures and category validation, then later releases. Preserve older working profiles, but a new legacy reader is secondary and must not gate modern-version work. Keep ZGY compatibility independent of native-project layout. Unknown units/domain alone remain governed by NEXT-04.

Report category-level coverage and unsupported profiles through the separate inventory/export indexes in NEXT-18. A user-controlled Save As on a separate copy may be an optional legacy fallback, with upgrade messages and object/value comparisons; it is neither a required runtime dependency nor presumed lossless. It does not satisfy the primary use case where the recipient lacks modern Petrel. Acceptance must demonstrate direct standalone extraction of supported modern datasets into usable open outputs; a 2018 demo or modern inventory-only result does not establish that outcome. Broader support requires actual fixture and numerical evidence. No code, executable, project or extraction output changes are made by this registration.

### NEXT-20: mapped-drive and UNC filesystem paths

Registered **2026-09-16**, status **pending; no implementation**. A supplied v0.7.0 run failed at the pre-copy containment check with `GetFullPath` reporting an unsupported path format. Read-only reproduction confirmed that resolving a network source through PowerShell `.Path` can produce a provider-qualified string (`Microsoft.PowerShell.Core\FileSystem::...`) that the .NET filesystem-path API rejects. Python resolves the mapped source to UNC before passing it to that launcher. This is a path-handling defect, independent of the project's saved Petrel version and the separate native grid-profile gap.

- Normalize actual filesystem paths consistently at the Python/PowerShell boundary. Evaluate filesystem `ProviderPath` or the appropriate provider-to-filesystem API, rather than passing provider-qualified strings to `System.IO.Path`. Reject non-filesystem providers explicitly.
- Apply canonical source/output comparison consistently to aliases of the same network location. Keep the containment protections; do not bypass them to make network inputs pass. Review other affected path helpers, without blindly rewriting unrelated path operations.
- Perform this preflight before expensive source hashing/copying. Preserve both user-supplied and normalized paths in local diagnostics, with a clear cause and stage. Full seismic hashing remains an independent option.
- Acceptance: local, mapped and UNC source paths; local/mapped/UNC output combinations; spaces and Unicode; equivalent mapped/UNC locations; missing/inaccessible sources; output inside source; and report-only mode. Verify both legitimate network extraction and rejection of unsafe recursive source/output overlap.

A complete source folder copied to a separate local path can avoid the demonstrated network-path failure, but does not fix unsupported object schemas. No files were moved, code changed or extraction retried during diagnosis. Public evidence excludes private paths/network identities.

### NEXT-21: next-release software name

Registered **2026-09-16**, status **pending; next version only**. The user selected **`GeoViewer_data_extractor`** as the exact software name, replacing Petrel Extractor / Petrel Headless Extractor. Retain the capitalization and underscores; do not silently substitute FieldViewer, GeoViewer Data Extractor or another display spelling.

When the next update is implemented, apply the name consistently to the CLI/banner/help text, HTML report title/header, user documentation and examples, release title/package presentation, and the main launcher (`GeoViewer_data_extractor.bat`). Reconcile shipped user instructions with the actual renamed entry point and preserve the one-main-BAT layout. Keep the existing website and project-repository attribution. Coordinate with NEXT-12 and the separate FV visual affiliation in NEXT-13.

Continue using Petrel where it identifies the source software, saved version, native format or compatibility scope. This naming decision does not claim support for additional source applications. Historical releases, diagnostic evidence and existing output links retain their accurate historical names. Preserve compatibility for internal identifiers/receipts where needed; do not globally replace technical identifiers merely for branding.

The request schedules the software rename for the next version. It does not rename the GitHub repository or URL, move the local checkout, alter the FieldViewer project, or republish existing assets now. Only planning records change at this stage. Future acceptance should check consistent naming, launcher instructions and report links in a clean extracted package without changing extraction behavior.

### Modern 2024.5 fixture findings for NEXT-08 and NEXT-19

Read-only investigation of a supplied project verified native saved-version metadata **2024.5.0** and successfully parsed its project/model containers and expected SQLite registry. Bounded in-memory probes accepted six sampled log payloads, three polygon payloads with segment structure, three point payloads and three trajectory payloads. A local ZGY header and a central 64-sample block were read. These are preliminary reader checks, not full extraction, independent numerical validation or release-wide support.

All six sampled surface payloads were rejected because they include an additional `is_known_consistent` field absent from the current decoder's exact field allowlist, despite matching its accepted payload version tuples. NEXT-08 must investigate and validate that schema variant, masks, geometry, bounds/cache semantics and numerical read-back; do not simply suppress the field check. This fixture strengthens NEXT-19's requirement to use structural evidence alongside saved-release and payload-version labels. Keep the separate NEXT-20 startup error and this per-category decode gap distinct. Workflow command records and a 3D model/property registry are present, but complete workflow/RESCUE export remains unvalidated.

## v0.7.0: native surface grids and report maps

The requested grid retrieval adds the observed older RegValGrid2 profile, packed node/cell definitions, exact numeric XYZ/CSV exports and ZMAP for regular grids. Reports show native grid cells with conservative gap masking, full-grid statistics, downloadable files and a separate count for numeric grids whose units remain unresolved. Report-only mode writes bounded previews without grid ASCII datasets. Explicit XYZ meshes are not forced into regular ZMAP geometry. General 3D reservoir grids and unrecognized layouts remain outside this profile; see [validation and limits](NATIVE_LOGS_SURFACES.md).

## v0.4.0 native recovery

Implemented native well logs to LAS/CSV and validated native surfaces to XYZ/CSV in the normal `convert` pipeline. The website/repository-only CLI banner is retained. See [profile and validation](NATIVE_LOGS_SURFACES.md). Remaining work is native unit/geometry inheritance, further version fixtures, categories and interval semantics, ZGY project linkage, then faults/grids/properties. Generic open-to-open conversions remain outside the core priority.

## Next update after v0.2.5: website and project repository CLI attribution

Registered on 2026-09-14. Status: implemented for v0.3.0 and checked through actual BAT startup output. v0.2.5 remains unchanged.

Original request: "remove my name (first line in the cli) keep only the website, not now, in the next update (register)".

Clarification on 2026-09-14: "also remove the third line, keep only the website (2nd line and the project repo 4th line)". This supersedes the earlier website-only interpretation.

In the BAT startup attribution block, remove line 1 (personal name/SaherLabs) and line 3 (personal GitHub profile). Keep line 2 (website) and line 4 (project repository), in this order:

```text
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
```

Keep the normal product/version heading, progress, timer and operational messages. This request concerns displayed CLI attribution only; documentation, source comments and license attribution are outside its scope.

Both launchers retain the website and project repository lines. The relocated acceptance test checks the original launcher's first two lines; the ZGY launcher is exercised separately. Version/progress/timer messages remain.

## Conversion expansion and capability accuracy â€” registered 2026-09-14

Status: priorities superseded by the user clarification: native/binary recovery only. See BINARY_EXTRACTION_PURPOSE.md. Historical broad research and bounded tests retained below; open-to-open additions are not planned. See the [28-route matrix](CONVERSION_CAPABILITIES.md), [applicability review](CONVERSION_REVIEW_2026-09-14.md) and [probe results](conversion_probe_results.json). These entries do not claim functionality in v0.2.5.

- P0: make feature availability explicit in CLI/report/docs from a shared registry. Distinguish conversion, preservation, inventory, unavailable dependency and unresolved metadata. Fix projected shapefile coordinates being emitted under a `.geojson` extension without WGS84 reprojection.
- P1: direct-file ZGY-to-SEG-Y with metadata preflight, bounded blocks, size estimate, progress/timer, failure receipts and receiving-application validation. Dependencies and synthetic float/int8/ZFP probes passed; real demo metadata remains unresolved. Do not silently infer time/depth or CRS.
- Superseded, out of scope: schema-mapped scalar CSV/Excel-to-LAS 2.0; synthetic value/unit/null/step test passed. Add SEG-Y header/navigation CSV and harden/promote the development ZMAP/XYZ converter.
- P2: DLIS/LIS scalar logs; separate multidimensional log-array export; IRAP/Surfer surfaces; GeoPackage; TSurf/VTK; typed ASCII and labelled array adapters. Validate pinned Windows packages and representative input variants.
- P3/P4: VDS/SGZ, reservoir models and Energistics object adapters; keep SEG-D and unknown native layouts unadvertised until specific evidence exists.
- Performance: offer conversion of one selected file without whole-project preservation; estimate uncompressed output size; separate raw file hashes from numerical QC; clearly label any future fast-inventory evidence as weaker than full SHA-256.

Acceptance requires source preservation, geometry/domain/units/null/precision checks, representative failure tests, bounded memory, relocated/offline runtime validation and accurate partial/unsupported states. A successful import or writer call alone is insufficient. The attribution request above is included in v0.3.0.

## Corrected binary-first update

CSV-to-LAS and new generic ASCII/SEG-Y table reformatting were removed from the implementation. v0.3.0 adds a beta ZGY-to-SEG-Y entry point and corrects native coverage counts: companion logs/tops, unlinked surfaces and legacy ZGY reports are excluded; spatial counts use successful object statuses and unique IDs. Native FloatWellLog/IntWellLog and surface payload decoders take priority next; faults/grids/properties follow with validated geometry and identity. See [purpose](BINARY_EXTRACTION_PURPOSE.md).

## Visual report before automatic project conversion â€” 2026-09-14

User priority: strong visual report with figures, maps, plots, statistics, lists, links and visualizations, including a full foldable data inventory tree. Implemented for v0.5.0: see [visual report](VISUAL_REPORT.md). Automatic project-linked ZGY conversion remains the subsequent integration step. Output contract: SEG-Y for seismic; documented ASCII/text for other data.

User clarification: full report/inventory is basic and always included. Dataset conversion is optional, selected by default. v0.5.0 adds Y/n selection and `-ReportOnly`; temporary preview datasets are discarded.

## Unified entry point, optional seismic hashing and report delivery — 2026-09-14

Implemented for v0.6.0: one root BAT beside the support folder; selected-project/store ZGY conversion in the same run; full seismic hashing optional and off by default. Report and full inventory remain mandatory, conversions on by default. Full `*_REPORT.html` is directly beside its matching `*_data` directory and updates after each seismic dataset. No raw seismic duplication. Unlinked companions and unresolved metadata are explicitly listed. Historical notes above describe earlier releases.

Also fixes a v0.5.0 whole-run abort when optional trajectory-name enrichment encounters an unsupported `Model.ptd` LZ4 envelope. The strict decoder remains unchanged; unavailable metadata is recorded in the report and independent spatial outputs are retained. Synthetic regression coverage verifies source preservation and visible, escaped report findings.

## v0.6.1: polygon segments and well-log visibility

Follow-up in v0.6.2: the supplied report showed that sorting alone did not fix the native geometry. Replaced polygon marker scanning with typed, length-framed decoding; preserved segment ordinals, vertex gaps and native closure; added a per-object map selector. See [validation and limits](NATIVE_POLYGONS.md).

Polygon previews group by object/part/explicit segment ID, order by vertex index, split at missing or invalid vertices, and clip before display decimation. Existing native `part_index` remains the segment grouping when no separate `segment_id` is provided. Never connect separate segments or infer topology from XY proximity.

The size-mismatch issue was traced to an observed multi-block `Model.ptd` stream. v0.6.1 supports its length framing with independent block dictionaries, bounded expansion and strict BXML validation. A local project now yields 1,008 numeric log CSVs and report tracks; units remain unresolved, so LAS stays blocked. Unsupported surface object versions remain visible. See [profile and evidence](NATIVE_LOGS_SURFACES.md). Further unit, object-version and geometry work still requires independent validation.
