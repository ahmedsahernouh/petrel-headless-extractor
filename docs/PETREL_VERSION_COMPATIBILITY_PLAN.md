# Petrel version compatibility: research and implementation plan

Research date: **2026-09-16**. Status: **implemented observed profiles in 0.8.0; broader compatibility work continues**. The user subsequently authorized implementation and modern-project extraction. The original research below is retained, with the executed evidence added here; it does not establish blanket cross-version support.

## 0.8.0 implementation evidence

A complete run of the supplied **2024.5.0** fixture now succeeds without Petrel or Ocean: all 306 native log payloads, 69 surface grids, 66 polygon payloads, 26 point payloads and 19 trajectory providers were recovered under observed profiles. The available local ZGY converted with full amplitude/trace-geometry readback; four externally referenced SEG-Y sources are absent from the supplied store. One saved Commands definition is exported as readable JSON. Native model/property/fault inventory is retained, but complete model topology is not exported.

The fixes include filesystem ProviderPath normalization, SQLite read-only UNC URI handling, validated modern surface fields, typed Points3 framing and float-encoded integral categorical logs. Missing units remain explicit. Unknown seismic axes retain native values in the sidecar/text header and require configuration in receiving software.

The original distributed legacy layout is detected and reported without calling the modern numeric decoder. This is an inventory/diagnostic improvement, not legacy numeric recovery. The preliminary probe descriptions below predate implementation; [validation](VALIDATION.md) records release acceptance, while [the requirements disposition](RELEASE_BACKLOG.md) retains outstanding work. No separate 2023 fixture or full 3D RESCUE export has been validated.

## Decision

Version differences are a real extraction risk, but they should be handled through **detected storage and object-format profiles**, rather than one assumed layout or a blanket supported-year range. Keep the extractor standalone and read-only. An unsupported object must remain visible in the report while independent supported objects can still be recovered. A project that cannot be fully interpreted must receive an honest coverage report, not an empty-project or complete-extraction claim.

**User clarification, 2026-09-16:** the primary product value is extracting usable data from projects saved in **Petrel 2023, 2024 and later releases**, when the recipient does not have access to the corresponding Petrel version. Legacy projects remain relevant, but legacy recovery is a secondary workstream. This supersedes the earlier legacy-first implementation sequence prompted by the reported failure.

The first priorities are (1) layout detection and partial reporting, (2) direct recovery and validation against actual 2023/2024-saved projects, and (3) expansion to later releases through representative fixtures. Maintain older working profiles and handle legacy failures honestly, but do not make completing a legacy reader a prerequisite for modern-version support. Opening or resaving in Petrel cannot be a required step: lack of access to that release is the main use case.

Success means a recipient can run the standalone extractor without the source Petrel release or Ocean, inspect the report and directly access validated SEG-Y and appropriate ASCII/text outputs. It does not mean reconstructing or downgrading a whole project into an older Petrel-native format. The mandatory report and inventory remain part of the core deliverable; modern-version inventory alone is not the final data-recovery objective. No 2023/2024 support is established merely by naming these targets.

## Evidence and its limits

| Evidence | Finding | Planning implication |
|---|---|---|
| Direct read-only comparison of a supplied original project and its 2018.2-saved copy | The original uses distributed GUID-named stores and lacks `Model.ptd`/`Data.ptd`; the current project-envelope reader also rejects its header. The saved copy has readable model framing and SQLite `Data.ptd` with `data`/`blob_parts`. | This is a demonstrated layout mismatch, not a misplaced copied database. Legacy discovery, metadata and payload reading need their own profiles. Private identities and measurements remain in the private handover. |
| Existing [native recovery validation](NATIVE_LOGS_SURFACES.md) | The demo originated in 2010 but was saved in 2018.2. Existing logs/surfaces use specific object-version and geometry profiles. | An origin year or project filename is not proof that the original serializer is supported. Preserve established validation without expanding its scope. |
| SLB Petrel 2014.6 release notes, Project Compatibility, p. 25 [S1] | SLB documents opening older projects in Petrel 2014, while projects saved in 2014 cannot be opened by earlier releases. | Petrel's own migration capability does not establish compatibility of a third-party binary reader. Loading and saving are distinct operations. |
| SLB Petrel/Studio 2021.1 announcement, corrected compatibility notice [S2] | The page explicitly retracts its announced forward-compatibility feature; it states that projects saved in 2022 and newer cannot be opened by earlier versions. | Do not plan against the superseded promise or assume one installed Petrel release can normalize every future project. |
| Installed SLB Petrel 2018 help [S3] | Documents project-version visibility, CRS upgrade handling, older seismic restructuring and custom-object upgrades. Some older seismic representations cannot survive that upgrade without returning to source data. | Save As is an optional migration path with object-level checks, not a presumed lossless repair. These are version-scoped historical examples, not a claim about every current release. |
| OpenZGY source inspected locally [S4] | The installed reader dispatches separate header structures for ZGY file-format versions 1–4. | ZGY has its own format/codec compatibility axis. Test it separately from the Petrel project container. This source inspection is not an end-to-end test of every ZGY version. |

The reviewed sources do **not** establish the exact Petrel release in which the observed distributed layout changed to the observed SQLite layout, a complete public native-store specification, or compatibility of every object in every release. Do not invent a year boundary. The legacy project's exact saved marketing version remains unverified; its 2010 label is supplied provenance. The 2018.2 field is native evidence. Before/after numerical equivalence has not been established by the comparison.

## First modern fixture: preliminary evidence, 2026-09-16

A supplied project's native metadata confirms **2024.5.0**. The current reader parsed its project/model containers and expected SQLite registry. Small representative log, polygon, point and trajectory payloads decoded in memory, and one local ZGY header/sample block was readable. Six sampled grids were rejected because of an additional `is_known_consistent` field, even though their payload version tuples match accepted profiles. This provides a concrete modern schema case for NEXT-08; full extraction and output read-back were not performed. The user's actual run failed earlier because of the separate mapped-drive/UNC normalization bug in NEXT-20.

These observations support investigating reuse of existing readers rather than assuming every modern dataset needs a completely new parser. They do not establish all-category or all-2024-project support. Source identities, probe selections and detailed evidence remain in the private handover. No 2023 fixture has been established by this case.

## Four separate compatibility layers

| Layer | Detect and retain | Reader/export decision |
|---|---|---|
| Project identity and container | Recorded latest/original version fields, build label, save history, source of each fact, file signatures, serializer/compression framing | Select a verified project-container profile. Keep recorded, estimated and unknown version values distinct; do not treat serializer counters as marketing versions. |
| Storage and object discovery | Distributed files, model store, database signature/schema, internal references, external references, mixed stores and plugin-owned objects | Select a storage adapter using observed evidence. Names such as `Data.ptd` are insufficient by themselves. A partially upgraded project may contain multiple representations. |
| Individual payload | Object class and version tuple, scalar/array types, framing, compression, masks, geometry, topology and references | Apply a matching decoder profile with bounds and structural validation. The same class name in a different release does not prove an identical payload. |
| Meaning and output | Units, CRS, domain, sign, nulls, order, parent relationships and chosen interchange format | Use common writers and numerical read-back checks. Retain unknown metadata explicitly under NEXT-04; do not invent units or alter coordinates. Actual decode/geometry/write failures still block the affected output. |

A saved version is a useful clue and report field, not the sole parser selector. A known payload profile encountered in an untested Petrel release may be evaluated using the same structural and numerical checks, with **release not independently validated** retained in the result. Conversely, a known release can contain an unsupported object or plugin profile. Never silently try arbitrary decoders until plausible numbers appear.

## Planned execution and reporting

1. **Preflight before expensive work:** inspect bounded headers, directory structure, references and read-only schema as applicable. Record the evidence, candidate profile, reason for selection and per-category capability. A metadata preflight must not read an entire seismic cube merely to identify its format. Full seismic hashing remains independently optional and off by default.
2. **Produce the report foundation early:** establish source-file inventory and all reliably decoded object identities. If the native hierarchy cannot be read completely, label object coverage **unknown/incomplete** and expose unclassified stores. Zero discovered database rows must not mean zero geological objects.
3. **Run independent supported categories:** isolate expected unsupported-profile results from genuine missing-source, copy, integrity and read/write failures. Retain completed valid exports; isolate incomplete outputs. Source changes or other failures that invalidate shared evidence must stop affected work and remain failures, not be converted to warnings.
4. **Finish a diagnostic report whenever it can safely be written:** use complete, partial or failed outcomes, with failed/not-run categories and actionable reasons. A partial report must not yield a complete-success receipt. Preserve the same distinction in the CLI, logs and manifests (NEXT-05, NEXT-14, NEXT-17).
5. **Expose compatibility beside the two indexes:** show latest recorded Petrel version, detected storage family, decoder/profile revision, validated categories, unsupported profiles and unknown metadata. Keep **Full project inventory** separate from **Decoded / converted data**, with direct links to completed shallow exports (NEXT-18).

Proposed report wording for the observed failure: **Legacy distributed storage detected. Native log/grid decoding is not yet supported for this layout. Inventory coverage: partial. Other independently readable datasets: listed below.** Do not call a structurally different source corrupted merely because the modern decoder rejected it.

## Implementation sequence and acceptance gates

### Phase 1 — stop compatibility gaps from destroying the report

Implement NEXT-17 preflight and safe partial-report handling first. Remove the unconditional assumption that every source has the modern database, across spatial recovery, logs/surfaces and later stages. Do not manufacture an empty database, rename an unrelated store or skip a failing stage and call the project fully extracted.

Acceptance cases: supported modern layout; the supplied unsupported legacy layout; unknown container; missing referenced source; a file missing only from the copied package; incorrect database schema; unsupported individual object; mixed successful/failed outputs; and report-only mode. Validate accurate inventory coverage, source preservation, independent category continuation and report links. This phase improves resilience; it does not itself implement legacy recovery.

### Phase 2 — establish modern-version recovery first

Build the primary fixture set from projects actually last saved in **2023 and 2024**, recording the precise saved-version/build evidence where available. A 2018-saved demo is a useful regression fixture, not a substitute for either target. An original old project that was later saved in a modern release is a distinct migration case; include modern-authored objects as well as upgraded ones when possible. Do not assume that all modern releases retain the observed 2018 storage schema.

First inspect their container/storage signatures and category/object versions read-only, then map actual gaps against existing readers. Reuse verified profiles when the structure matches and validation passes; implement distinct profiles where it differs. Prioritize **seismic/ZGY, 2D surface grids, well logs, well geometry, polygons and points**, with fault geometry, workflows and complete 3D models tracked separately according to their recovery/validation requirements. Preserve polygon segment identity and ordering.

Obtain trusted reference exports where already available or later supplied by a project owner with the relevant release. Those are development/validation evidence, not a requirement for every end user to have modern Petrel. Compare complete values, dimensions, null masks, axes, coordinates, curve sampling and topology, rather than relying on visually plausible plots. Declare the exact tested categories and profiles for each release.

Progress gates: verified source-version evidence -> inventory/identity mapping -> typed decoding -> accepted open output -> independent numerical/geometry comparison -> relocated standalone acceptance. Demonstrate extraction with no dependency on installed Petrel, its shell extensions or a license server. Keep unsupported objects visible at every gate. No complete 2023/2024 coverage or delivery date is promised before the required fixtures and validation exist.

### Phase 3 — expand by verified fixtures

Create a compatibility matrix keyed by **recorded saved release/build + container/storage signature + object class/version + decoder revision + output format + fixture evidence**. Include known gaps and explicit not-tested rows. Record source-version provenance and distinguish an original project from an upgraded copy.

Prioritize separate **2023 and 2024** fixture rows, followed by later source releases as representative projects become available. Add 2022 and other intermediate releases according to user demand and evidence of reusable profiles. Retain existing older fixtures as regressions and the supplied legacy pair as a secondary research case. These dates are test targets, not asserted binary-format boundaries; do not collapse modern coverage into an unverified generic `2022+` claim. Newer model types and plugin objects require their own fixtures even if their project container is already readable.

Test small representative files with both normal and edge cases: empty objects, nulls, rotated/irregular geometry, segmented polygons, duplicate names, chunked/compressed payloads and truncated or mismatched framing. Keep licensed/private projects out of the public repository; publish sanitized synthetic tests, profile definitions and aggregate validation results. Rerun established category regressions when shared container or array readers change. Package and dependency versions should be pinned and recorded with results.

### Secondary workstream — the demonstrated legacy family

Retain the supplied original and 2018-saved pair for a separately validated legacy reader. Map the original serializer, registry, names/IDs, references and distributed-file framing before payload recovery. This work must not delay the primary 2023/2024 recovery objectives. Shared improvements such as preflight, reporting and supported payload readers can benefit both tracks.

Match objects by retained IDs where available; otherwise record an explicit mapping supported by names, parentage, dimensions and geometry. Do not assume Save As preserves every identifier. Compare values, counts, null masks, axes, coordinates, units and segment topology. Use existing Petrel-authored exports and independent readers where available; a resaved copy is supporting evidence, not the sole ground truth. Native byte hashes normally differ after a save and cannot establish numerical equivalence. Legacy support remains bounded by its own fixtures and acceptance gates.

### Category-specific acceptance across both workstreams

The categories below are validation dimensions, not a requirement to finish legacy work before modern seismic, grids or logs. Complete-model and workflow support retain their separate feasibility gates.

| Category | Compatibility focus | Acceptance requirement |
|---|---|---|
| ZGY -> SEG-Y | ZGY header version, compression codec, numeric sample type, geometry and sampling; independent external-reference resolution | Read-back values and trace geometry, with unknown metadata labelled. Do not require the native project parser merely to inspect or convert an independently identified ZGY. Do not claim that all older embedded seismic is ZGY. |
| 2D grids -> ZMAP/XYZ/CSV | Grid payload versions, regular versus explicit geometry, axis order/rotation and node/cell masks | Preserve full values and geometry. Use only target formats able to represent that grid; do not flatten an unsupported mesh into an apparently regular grid. |
| Logs and well data -> ASCII/CSV, LAS where applicable | Curve type, sampling/boundary semantics, native nulls, inherited units, well and trajectory references | Per-curve value and depth validation; retain usable unknown-unit numeric exports without inventing LAS semantics. |
| Points, polygons and faults -> ASCII plus required topology | Segment/part IDs, vertex order, closure, mesh connectivity, reference relationships | No connection across unrelated segments; a point cloud alone is not a complete fault surface. |
| Workflows -> readable definitions | Command schema versions, variables, order, conditions and object references | Separate complete definitions from hints; no execution or native re-importability claim (NEXT-16). |
| 3D models -> complete RESCUE plan | Grid family, topology, faults, active cells, properties, units and dependencies | Establish complete recoverability and target-format representability first. Conventional grids and newer model families must not be assumed interchangeable. A partial geometry export cannot be called a whole model (NEXT-11). |

## Optional user-controlled migration fallback

If direct recovery of a particular legacy category is still unsupported, a user may choose to open **a separate complete copy** in a compatible licensed Petrel release and save it as a new project. This is an external, manual fallback, not a runtime requirement or automatic extractor operation.

This optional legacy fallback does not solve the primary modern-project access problem: the target user may not have the originating release. Modern acceptance must therefore demonstrate direct extraction without requiring the user to open, resave, downgrade or export the project in Petrel first. If direct recovery is unsupported, disclose that gap; do not describe a dependency on unavailable modern Petrel as satisfying the standalone requirement.

Retain the original and both provenance records. Capture Petrel's upgrade messages, compare object inventories and validate target datasets before relying on the new copy. Check external references as well as the project/store pair. If an upgrade drops or transforms an object, retain that fact; the saved copy must not silently replace the original evidence. Do not prescribe an intermediate release without source-specific compatibility evidence. No project upgrade is performed under this plan.

## Sources and research scope

- **[S1]** [SLB Petrel 2014.6 Release Notes](https://www.software.slb.com/-/media/software-media-items/support/product-documents/petrel/2014/petrel-2014-6-release-notes.pdf), 30 June 2015, p. 25, Project Compatibility. Historical application behavior, not a native binary specification.
- **[S2]** [SLB Petrel and Studio 2021.1 announcement](https://www.software.slb.com/software-news/software-top-news/petrel/petrel-studio-2021), dated 6 August 2021, corrected note under Project forward compatibility, checked 16 September 2026. The correction takes precedence over the preceding announcement.
- **[S3]** Installed SLB Petrel 2018 Help Center: *Display a project's Petrel version*; *Project upgrade*; *Reuse of projects saved using Petrel releases prior to 2007.1*; *Custom Domain Object upgrader*. Read locally; source files are not redistributed. Specific locators and detailed observations are retained in the private handover. Application/reference-project guidance must not be generalized into third-party parser support.
- **[S4]** [OSDU OpenZGY project](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/seismic/open-zgy), with read-only inspection of the installed `openzgy/impl/meta.py`, `FileHeader` and versioned header dispatch. The upstream web code view was not retrievable during this review; the version-dispatch observation comes from installed source, not a claim about upstream HEAD or universal runtime coverage.

Local KB retrieval used a current index and passed its query audit. Results were version-scoped design/operation notes; retrieval matching the words “2010” and “2018” did not establish legacy runtime compatibility. The private fixture comparison and specific source passages take precedence over ambiguous older capability wording.

Before claiming the primary target support, the unresolved work is representative 2023/2024 and later fixtures, their container/payload mapping, object-specific semantics and independent validation. Legacy serializer mapping remains secondary. The release should publish a category/profile compatibility matrix rather than “all Petrel versions supported.”
