# GeoViewer source interoperability contract, version 1

This is an additive source catalogue for the existing committed `FILE_INDEX.json`, including explicitly empty inventory-only delivery. Source packages are read-only during normalization. The 22-field `FILE_INDEX.csv` interface remains unchanged. No consumer import, receiving survey, native project write, unit inference, CRS inference, or scientific acceptance is implied.

## Publication

`geoviewer_contract.publish_contract(package: Path, destination: Path, file_index: dict, inventory: dict | None = None) -> dict` runs after a committed final `FILE_INDEX.json` exists. `package` is the extraction package and `destination` is its sibling EXPORTS directory in a bounded run tree. It rejects a mismatched index argument, linked paths, and paths outside declared bases. It reads project context, native recovery and spatial recovery receipts when present, plus the supplied seismic inventory. It does not modify these inputs and does not hash bulk payloads.

The publisher writes `INTEROPERABILITY/generations/<random-id>/` with `entities.jsonl`, `artifacts.jsonl`, `relationships.jsonl`, `metadata_evidence.jsonl`, `gaps.jsonl`, and `manifest.json`. After all files exist, it atomically replaces `INTEROPERABILITY_CURRENT.json`. The pointer binds the manifest digest; the manifest binds each table digest and row count, the canonical `FILE_INDEX.json` digest, receipt byte digests, the revision scope, and the path bases. An interrupted generation without a pointer is inert. Readers must start from the current pointer. Old generations are historical; selecting their manifest after a newer commit is rejected.

Large native recovery receipts use a separate, explicit input allowance of 768 MiB. Intake hashes every original byte, parses each ordered object under a 32 MiB per-value and 128-level nesting bound, rejects duplicate JSON keys, and retains every field consumed by the catalogue. The consumed projection is capped at 2 GiB; this is a serialized projection budget, not a process-memory guarantee or a limit on the complete Data.ptd SQLite file. This does not change the 256 MiB per-table, 1,000,000-row, or 2 MiB per-row output limits. Other metadata inputs retain their earlier 256 MiB bound.

When `metadata_evidence` alone exceeds a single table limit, the publisher uses `geoviewer.source-contract/2` and an `ordered-jsonl-shards/1` descriptor. Each shard retains the original 256 MiB/1,000,000-row bounds; the logical table is limited to eight shards, 2 GiB and 2,000,000 rows. The manifest records every ordered shard path, byte count, row count and SHA-256, plus the aggregate byte count, row count and SHA-256 over the exact concatenated shard bytes. Validator and publication checks reject missing, changed, duplicate, reordered, escaping or oversized shards. Other tables remain single files. Small generations continue to use the unchanged `/1` pointer and single-file descriptors; older readers reject the `/2` pointer instead of misreading it. Every evidence row and original JSON locator remains intact.

For a `/2` generation, the preparation HTML is a bounded summary with explicit links to the complete verified table shards. It does not render every metadata row; the full source evidence remains in the lossless contract tables and is validated before summary publication. The page does not upgrade source readiness or claim a receiving import.

Complete profile assessments continue as flat `geoviewer.contract-validation/2` JSON when they fit the existing 256 MiB reader limit. Larger assessments use a `geoviewer.contract-validation-shards/1` manifest with explicit `ordered-canonical-framed-jsonl/1` members for every list field. Each logical item is a complete frame when it fits; an oversized item is losslessly encoded as ordered 1 MiB byte fragments with an item SHA-256 and a separate 32 MiB logical-item limit. Every physical row remains at most 2 MiB, every member at most 256 MiB and one million physical rows, and the whole assessment at most 24 members, 4 GiB and two million physical rows/items. The manifest binds physical bytes/rows and logical items with per-member, per-list and aggregate digests and counts. Readback reconstructs every fragmented item, verifies exact entity/artifact coverage and count semantics, and rejects missing, reordered, duplicated, malformed or tampered fragments. A small compatibility alias points to the complete manifest rather than duplicating the large payload. Failed or incomplete members never become an accepted assessment.

The project key is revision scoped. When the native recovery receipt records matching before/after SHA-256 values for the PET, Model.ptd and Data.ptd source files and `source_unchanged=true`, those three values define the source revision. Paths and export order do not enter this identity. Otherwise the fallback hashes sorted context object IDs, categories, parent IDs and model versions with the project name. That fallback is **content-only and unresolved**: distinct projects with identical sparse metadata can collide. Neither scope asserts a durable project ID across edits. The manifest identifies its scope and binds current engine-script byte hashes plus source-receipt digests. Native entity IDs and artifact IDs are separate. A UUID-shaped native object ID is never an external UWI. Names are display fields and never merge identities. Existing native `well_id` and parent IDs become exact ID relationships; proximity/name candidates do not. Native well heads without a UUID are scoped by `model_subject_offset` and their exact aggregate CSV row selector. An exact provider UUID is represented separately from the well-head member; a name-based provider candidate stays candidate.

The catalogue keeps original delivery status and index section (`files` versus `partial_files`) on each artifact. A current copy may have been pruned after successful original publication. A currently missing file therefore blocks availability without rewriting its historical export outcome. Geometry, surface property values, QA scalars, node masks, cell topology/masks, previews, and metadata are separate roles. `cells.csv`, masks and previews are never full geometry payloads. An attribute or QA value is never a horizon Z. Unit declarations and display units are recorded evidence only; an unresolved source unit stays unresolved. Receipt locators use the actual package-relative file and JSON pointer. Companion provenance is inferred from explicit source lane, companion category or registered companion ID, and never becomes a native UUID.

For the two supported full `RegValGrid2` profiles, the catalogue can derive a
regular node-lattice descriptor from the recorded exact native readback. A
property-to-surface registration additionally requires a unique exact native
parent UUID, a `SurfaceSubject` parent, recorded unchanged source integrity,
and identical origin, basis vectors, dimensions and integer-node order in the
two full receipts. Shared names or bounds alone are insufficient. This derives
historical evidence and does not reread grid payloads or infer units, CRS, physical
Z, survey mapping or an import result. The common lattice includes undefined
positions: consumers must use both native node masks, which may differ. Missing
accepted masks block current availability/receiving preparation without erasing
the historical registration. Conflicting input declarations remain unresolved.

Paths use `exports`, `package`, or `run` bases with POSIX relative paths. `run` is permitted only for seismic fallback artifacts. The manifest stores package/run locations relative to EXPORTS so a complete run tree can move together. Readers reject escapes and linked ancestry. For a legitimate copied run, keep EXPORTS and package in the same relative layout. A package-only copy cannot make missing sibling artifacts available.

Newly generated artifact keys use `source-revision-owner-portable-representation/1`:
the revision-scoped owning entity plus the hash of path base, portable relative
path, role and format. Index row order and accepted/partial state do not change
that identity. Duplicate owned representations are rejected. This does not merge
hardlink aliases or establish a persistent project identity across edits, and a
renamed/reorganized representation can receive a new key. Historical ordinal
keys and their committed generations remain readable and are never rewritten.

Older outputs without a catalogue pointer can be inspected with the separate
[read-only legacy adapter](INTEROPERABILITY_LEGACY.md). Its derived assessment
does not create a historical generation or satisfy RUN_RESULT publication binding.

### Source project identity limits

Source hash roles use case-insensitive PET/Model.ptd/Data.ptd leaf names, without
following their original absolute paths. Duplicate entries for a role are an
error, even when their hashes match; entry order must never select a source.
Incomplete receipts retain the unresolved content-only fallback and cannot
satisfy the validator's source-revision prerequisite.

Three retained PET/Model pairs
all contain ProjectSubject tag `1d9a2dd1-dd1d-4676-a92e-6057e64d33c2`, despite
different source bytes and saved versions. It is therefore not a unique project
identifier. Evidence is in
The validation record is retained outside this public repository.
Do not merge revisions or related snapshots using that tag, an active view tag,
names or timestamps. Persistent lineage requires independently evidenced source
semantics or an explicit governed lineage assertion; neither is invented here.

## Typed metadata evidence

Polygon source evidence uses one `polygon_geometry_evidence` summary plus one
`polygon_part:<native collection ordinal>` observation for each part. These
observations retain the exact receipt fields/locators without embedding vertex
arrays or all segments into one JSONL row. `PolygonsSubject` maps to the
`culture_polygon` entity class while `native_subject_type` retains the original
context category. `polygon_part_metadata` artifacts describe parts; only actual
geometry artifacts satisfy geometry availability. Native flags, repeated-endpoint
observations, unresolved ring/hole roles and unexported attributes are separate.
See [native part evidence](NATIVE_POLYGONS.md).

New metadata rows include the additive `geoviewer.metadata-evidence/1` view.
Original `raw_value`, `field`, `entity_key`, `locator` and legacy `resolution`
remain unchanged. The source-contract major and FILE_INDEX interface stay at
their existing versions. The publisher and read-only legacy assessment bind
the metadata helper's source digest. Unsupported evidence schemas are rejected;
old rows without this extension are interpreted in memory without rewriting
historical generations. Unknown optional fields remain ignorable.

| Added field | Meaning |
|---|---|
| `evidence_schema` | `geoviewer.metadata-evidence/1` |
| `value_type` | Raw JSON null, boolean, integer, number, string, array or object |
| `normalized_value` | Same typed known value, or JSON null for an absent, reserved placeholder, empty or nonfinite value; no unit/CRS conversion |
| `evidence_status` | Nature of the observation, as defined below |
| `resolution_method` | Original method label, retained verbatim |
| `scope` | Recorded scope, or source-entity observation; artifact declarations have a separate scope |
| `normalization_scope` | Explicit limit: typed representation, not semantic calibration or inheritance |
| `unresolved_reason` | `MISSING_PLACEHOLDER_OR_INVALID_METADATA_VALUE`, or null |

Observation statuses distinguish `recorded_source_value`, `native_declaration`,
`validated_native_profile`, `validated_source_reference`, `candidate`,
`derived_value` and `unresolved`. Explicitly supplied method labels also preserve
`exact_native_record`, `validated_native_template_link`,
`exact_correlated_companion`, `inherited_project_setting` and `user_override`.
These labels are not invented from names or coordinates. A validated profile or
reference status reflects recorded receipt declarations, not a fresh scientific
calibration. Unknown method labels stay visible as recorded observations.

Per-entity assessment `metadata_resolution` groups observations by exact entity
and field: `status` is `recorded`, `conflict` or `unresolved`; `value` is null
unless known observations agree; `alternatives`, `unresolved_observations`,
`reason_code`, evidence statuses, locators and scopes preserve the disagreement.
JSON types are significant: true and 1 are not interchangeable. The rule has no
implicit project-over-object precedence. Agreeing duplicate observations do not
create conflicts; unresolved observations remain counted alongside known ones.
MD, physical Z, horizontal units, property units and time references are never
copied into one another. A structured record may still contain unresolved
members; each member carries its own calibration status.

For example, raw `" Unresolved "` remains that exact string while its normalized
value is null and its observation status is unresolved. A preparation decision
may separately assert `"KB"` with exact-entity evidence; the effective value is
labelled user override and the raw source observation stays unresolved. No
confidence score, EPSG guess, transformation or receiving import is implied.

## Read-only validation

Example:

```powershell
python scripts/validate_geoviewer_contract.py "D:\run\project_EXPORTS\INTEROPERABILITY_CURRENT.json" --profile opendtect --output "D:\review\validation.json"
```

Profiles are `source`, `opendtect`, and `paleoscan`. The same source contract is used for all three. The validator checks pointer, manifest, table, index and receipt identities; entity and relationship references; accepted versus partial rows; current paths and sizes; and conservative per-entity source prerequisites. `--hash-artifacts` opts into fresh payload SHA-256 checks. Without it, current payload identity is only a size/state observation and can miss same-size changes. Full seismic rehashing is never automatic. An available file with recorded numerical QC is still not a scientifically accepted object.

The standalone CLI uses the same bounded assessment writer as normal extraction.
It stages the report in a private sibling directory and verifies complete flat
or sharded readback, including every list item and all base fields, before
publishing the requested output. Small reports remain flat JSON. For larger
reports the requested JSON is the shard manifest; keep all named sibling JSONL
members with it. Members are committed before the manifest, without overwriting
existing reports or members. Windows uses exclusive same-volume rename and does
not require hardlink support. An interrupted draft without a final manifest is
not an accepted report. The output remains separate from the source run, and
decision/retention input does not alter source truth. This CLI correction has
separate candidate verification; earlier contract checks do not cover it.

The `geoviewer.contract-validation/2` report has separate entity and artifact counts, per-entity source and receiving assessments, preferred available full artifact, diagnostic codes, evidence locators and dependency order. `state_axes` separately records source recovery, current availability, metadata, relationships, source-revision identity, original publication, historical numerical QC and current file integrity. Pruning an accepted payload changes its availability; it does not rewrite its recorded numerical QC. Missing or failed seismic QC cannot be reported as validated. A partial category does not disqualify an unrelated accepted object. Folder ancestry is not a payload dependency; exact provider, source-well and materialization links remain visible. Surface properties also require their exact parent geometry to qualify for the receiving mapping.

Versioned receiving profiles now evaluate explicit preparation decisions. `qualified_for_planned_mapping` means that the specified preparation checks pass, not that the receiving application ran or accepted the data. The legacy `receiving_qualified` and new `receiving_imports_executed` counts remain zero because this validator performs no receiving execution. Generic `source_state` uses only recorded source evidence and remains conservative; a receiving assessment can additionally use separately attributed decisions for its particular mapping. Neither decision nor qualification rewrites the source catalogue. See [preparation and retention usage](INTEROPERABILITY_PREPARATION.md).

A size-only current file check is labelled as such and can miss same-size byte changes. Structural failures return exit code 2 and do not create a success report. A valid report with blocked entities returns zero; inspect its assessments and diagnostics. The CLI creates a new report outside the source run and refuses to overwrite an existing path. Tables are streamed with explicit 256 MiB/table, one-million-row/table and 2 MiB/row limits. Receiving decisions and retention views have separate bounded loaders. The limits and dependency algorithm have regression coverage; complete-project throughput has not been benchmarked.

This version deliberately does not resolve survey lattice registration, external UWI, well datum/MD conventions, categorical log labels, property node joins, unsupported topology, or target runtime availability from filenames or display settings. Follow the source locators to research these gaps with calibrated reference data. Only accepted `FILE_INDEX` rows are candidates for a full payload; inventory and recovery entries remain visible even without an accepted export.
# Current-source supplement — 2 October 2026

The current-source normalizer additionally distinguishes `seismic_transport`
entities from native UUID entities. Only the discovery receipt's exact native
UUID association creates an exact `materialized_payload` link. `owner_keys` on
an artifact lists its owners without duplicating that physical artifact;
`entity_key` remains its primary owner for older readers. Reported original
source paths are provenance only and are never followed by contract validation.

Receipt-bound `seismic_conversion` and `seismic_source` evidence retain the full
small metadata sidecars, including unknown native axes. The normalizer reads at
most 1 MiB per sidecar and verifies receipt/index digest and size. The manifest's
`metadata_bindings` checks these small files on later validation, independent of
the optional bulk-artifact hashing flag. Changed or pruned metadata blocks the
affected mappings; historical export status remains unchanged.

New conversions describe the generated `fieldviewer.generated-segy/1` header
profile, coordinate basis, trace ordering, scalar component and acquisition-header
limitations. This does not retroactively supply headers or semantics to older
files. Time convention and vertical reference remain unknown without source proof.

`point_identities` index rows map to `point_identity_metadata`, never a full XYZ
payload. The CSV preserves point ordinal, missing XYZ slots, native serialization
values and their exact scalar NBFX bytes; scientific point-attribute meanings and
external industry identifiers remain unresolved. Current-source implementation and
remaining requirements are tracked in phase readiness.

## Native log evidence and normal publication

Native scalar-log recovery now carries the exact MD/value array encodings, sample
order, native null sentinel and mask meaning, interval flag, sampling regularity,
Data.ptd row locator and decoder version profile into object metadata and catalogue
evidence. Continuous scalar samples and categorical interval records remain
different kinds. `md_unit` aliases the existing validated depth unit; the change
does not extend the unit profile or infer MD datum, interval direction or category
code meanings. These absent meanings remain explicit gaps. Extreme finite coordinates
whose differences overflow do not acquire a false uniform-sampling label.

`native_category_legend` is optional native template metadata in
`metadata_evidence.jsonl`. It records the exact default colour-table identity,
supported serialization profiles, source locators, label ordinals and raw level,
parent and minimum-code fields. `code_mapping_status` remains `unvalidated`;
consumers must not calculate a lookup from these fields or interpret the display
undefined item as a log null declaration. Duplicate display labels retain separate
ordinals. `category_lookup` remains unresolved until independently calibrated.
This metadata does not promote source or receiving qualification.

Rejected log receipts may also retain `log_array_diagnostics` and the source
identities established before the failure. Counts distinguish unequal lengths,
nonfinite MD, NaNs and infinities in values. They are bounded failure evidence,
not recovered arrays, accepted artifacts or numerical QC. A later object cannot
inherit a previous object's failure metadata.

Numeric checkpoints now carry an additive `resume_binding` with exact source,
declared decoder/runtime and option identity. A resume request cannot reuse an
older implementation solely because its product version matches. This binding does not retroactively qualify historical receipts. Resume requires exact source, decoder/runtime and option identity before a checkpoint can be reused.

`geoviewer_contract_publication.publish_preparation` integrates the source catalogue
and three profile assessments with launcher finalization. Report-only runs emit an
explicit metadata-only `geoviewer.inventory-index/1` with zero payload rows. They
still expose inventory entities; converted datasets are absent. Conversion
mode requires the actual committed index and cannot replace an index failure with
an empty success. Inventory-only mode refuses to overwrite existing payload rows.

`RUN_RESULT.interoperability_contract` uses
`geoviewer.preparation-publication/1`, binding the current pointer, manifest and
each profile assessment by hash and generation. Assessment files live under
`interoperability/<generation>/`; the older single-name files are convenience
aliases and are not the authoritative binding. `verify_publication_binding`
checks these small records and rejects stale generations, altered assessment
counts, changed bytes and escaped paths. Relative paths support moving the whole
run layout. This receipt check does not rehash artifacts or establish scientific
correctness. A failed assessment leaves diagnostic catalogue evidence but does
not return a successful publication binding.

## Native well identity and reference declarations

The supported typed `WellTraceSubject` Model profile
`[33,3,14,1,2,21,0,0,1]` now supplies native well UUID, name, definite-provider
UUID, stored head XY, raw `uwi_`, working-reference and reference-level records,
and the raw convergence scalar. These declarations appear in
`metadata_evidence.jsonl` as `native_well_metadata`, with their Model document
locator, compressed Model SHA-256, and field-specific gaps. Unsupported profiles
and ambiguous identities remain unresolved. A conflicting Model hash blocks
publication when the catalogue has an authoritative source-revision binding.

A header row joins a native well only through a unique provider reference,
identical native name and identical XY. Name or proximity alone cannot establish
identity. Exact native links reuse the well entity, while older rows without
typed identity retain offset-scoped membership. An aggregate header artifact can
serve multiple wells through explicit row selectors only when its declared digest
and size match the consumed table. Its small table is checked even without bulk
artifact hashing; changed bytes emit `HEAD_METADATA_STALE`. All links require the
literal exact-link status: an `inexact` status cannot qualify by substring.

`declared_definite_provider` records a native association, not a prerequisite for
the head to exist. A decoded track can separately carry a `source_well`
dependency. This avoids an artificial head/track dependency cycle. Station order
and values are unchanged; this addition supplies identity and source evidence.

`native_head_record` preserves stored head XY and separate trajectory-start Z
provenance. A stored UWI string is not independently verified external UWI/API
authority. Reference labels such as KB and their numerical offsets do not
establish calibrated units, sign, MD zero, elevation datum or equivalence to head
Z. The catalogue's resolved fields remain unknown until evidence proves those
meanings. Duplicate providers, unsupported profiles and unresolved references do
not acquire receiving qualification.

`trajectory_station_evidence` describes the complete decoded native station
sequence without changing it: count/order, stored-MD range, negative values,
adjacent equal or decreasing MD pairs, and min/max adjacent steps. Exact numeric
spacing uniformity is distinct from proof of complete sampling; irregular steps
do not establish missing stations. Empty/single-station arrays do not claim
monotonicity, and fewer than three stations do not claim uniform spacing.
Supported modern profiles retain their existing radians/degrees distinction;
legacy raw angles remain unresolved. Datum and grid/true/magnetic azimuth
reference are not inferred. Cumulative 3D distance remains a separate derived
geometry measure and never substitutes for stored measured depth. Raw native
legacy flags and coordinate/angle-unit status also reach metadata evidence.
These statistics alone do not supply qualified `track_semantics`.

## Missing native vertex evidence

Current point/polygon auxiliary tables preserve the raw components of slots
already classified as missing by the supported native decoder. Their indexed
categories are `point_missing_vertices` and `polygon_missing_vertices`, with
artifact role `missing_vertex_evidence`. They cannot qualify as full geometry.
The existing finite geometry remains separate and must retain its original
vertex ordinals. See [the raw-slot encoding and limits](NATIVE_POLYGONS.md#missing-vertex-components-2-october-2026).

The catalogue records `missing_vertex_slots` from an explicit decoder count, or
for an older polygon report derives it from exact nonnegative per-part counts.
Each observation retains its report locator and the derivation method. A known
positive count produces `NATIVE_VERTEX_GAPS_UNRESOLVED` in source and receiving
preparation. Generic metadata overrides cannot suppress it or authorize gap
filling. This check introduced rule identities `source-prerequisites/4`,
`opendtect-7.0.10-odbind-1.0.0-preparation/4`, and
`paleoscan-2023.1.1-file-preparation/4`; the product version is unchanged.
The receiving profiles subsequently advanced to revision 5 for explicit log
shape and source-consistency checks, then revision 6 (source prerequisites 5)
for mandatory surface-property mask availability across all join methods;
see [current profiles](INTEROPERABILITY_PREPARATION.md).
Historical committed generations are not rewritten. This provides evidence and
conservative preparation checks, not a new decoder profile or receiving import.

## Legacy well and track roles

`WellTrace3` payloads can share the exact UUID of a `WellTraceSubject`. New
catalogues keep the conceptual well at `revision:<revision>:native:<UUID>` and
its stored track at `revision:<revision>:native_trajectory:<UUID>`. Both preserve
the original `native_uuid`; the track declares `identity_role=legacy_stored_track`.
The role-qualified key is not a newly invented native object or an external UWI.
Entity counts therefore distinguish conceptual wells from their track entities;
they are not counts of independent wells or geological datasets.

Exactly one typed `WellTraceSubject` in project context must corroborate the
shared UUID before an exact `source_well` relation is emitted. Duplicate subjects
or missing exact identity produce `LEGACY_WELL_IDENTITY_UNRESOLVED`; display-name
matches cannot repair it. Duplicate track receipts and conflicting explicit well
claims reject catalogue generation. Definite-provider references point to the
track role, and no self-dependency is introduced.

Indexed trajectory representations belong to the track; a distinct header
representation still belongs to the well. A track file cannot supply a missing
well head simply because the legacy source UUID is shared. No head is synthesized
from the first station. Existing committed generations remain readable and
unchanged; the read-only legacy adapter derives this corrected view without
rewriting historical files or receipts.


## Evidence availability

References to local validation records describe historical verification
records. Those records and source projects are not distributed in this public
repository. They are not public reproducible fixtures or receiving-app acceptance.
Later seismic declaration profiles are separate development work and are not
part of the Field Viewer 1.0.0 public package.
