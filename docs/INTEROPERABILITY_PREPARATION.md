# Receiving preparation and retained copies

The read-only preparation validator checks export prerequisites and target
mappings. It does not create receiving objects or execute imports or transforms.

## Preparation progress

The existing diagnostic event stream now includes `contract_operation` records
for catalogue discovery and metadata assembly, table reads/writes, reference
validation, profile assessment and human-report rows. Hash passes distinguish
`metadata_hash` from the optional full-payload `artifact_hash` operation.
Normal extraction uses its existing diagnostic sink. For a standalone validator
invocation, `PETREL_PROGRESS_EVENTS=1` enables the same structured console events;
unset it to keep the normal compact CLI output.

Each record has an `operation_id`, phase, unit, actual completed count, known
total or null, elapsed time, state, and `scope=current_operation_only`.
Intermediate records are throttled to at most one every two seconds per
operation and are emitted only when actual work advances. Started, completed,
failed and interrupted records remain distinct. A table-read completion follows
its digest/count checks. A hash completion means that pass consumed the reported
bytes; the separate assessment determines whether the digest matches a receipt.

Processed catalogue rows include unsupported and unresolved objects. They do
not count recovered values, exported objects or receiving-qualified entities.
Operation counts and nested elapsed spans must not be added into a workflow
percentage or ETA. Default preparation still does not hash bulk artifacts.
Opt-in hashes remain fresh for each profile; the added counters do not cache or
replace any integrity checks. Operation timings are recorded; throughput has
not been benchmarked.

## Versioned preparation profiles

The source contract remains `geoviewer.source-contract/1`; assessments are
`geoviewer.contract-validation/2`. The profiles currently enforced by
`scripts/geoviewer_receiving_profiles.py` are:

| Profile revision | Candidate preparation scope | Explicit boundaries |
|---|---|---|
| `opendtect-7.0.10-odbind-1.0.0-preparation/7` | Scalar logs into an explicitly selected existing well; horizon Z lattice; properties on exact qualified parent geometry; complete regular seismic cubes | Source-inspected route only. No runtime/license qualification or implicit creation of wells/tracks/markers/time-depth. Unsupported fault/Horizon2D/culture routes remain explicit. |
| `paleoscan-2023.1.1-file-preparation/7` | Candidate file preparation for logs, wells/tracks, surfaces/properties, seismic and separately evidenced markers/checkshots/polygons | Based on installed guide/API references. API entitlement and receiving import remain unchecked. A surface reference volume requires an exact source relationship and selected target binding. |

Rule revision 3 added typed uncertainty, explicit required-field conflicts and
agreement across repeated validation observations. The current source prerequisites
use `source-prerequisites/6`. Receiving rule revision 5 added scalar log shapes
and source-representation contradiction checks. Revision 6 requires both accepted
available node masks for all surface-property joins, including reviewed assertions;
the registration method label cannot waive this requirement. See
[unit declarations and mask checks](NATIVE_UNIT_DECLARATIONS.md).
Decisions made for earlier receiving revisions must be explicitly rebound and reviewed under revision 7; old decision files and historical
generations are never rewritten. These internal rule revisions do not change
the product release version (1.0.0).

Case/whitespace variants of reserved unknown tokens and documented unresolved
prefixes cannot supply a datum/reference. Null-representation and source-rule
descriptions must be non-placeholder strings. Incompatible observations for a
required field produce `CONFLICTING_<FIELD>` and no selected value. An assertion
cannot silently choose among conflicting native/receipt observations, including
CRS differences between native declarations and bound seismic metadata. Agreeing
duplicate validation statuses are accepted; a differing or unresolved status is
not discarded. Optional metadata remains visible without blocking a route that
does not need it.

Every assessment exposes `metadata_resolution`; receiving assessments also
expose `effective_source_fields`. A supplied missing-field assertion is labelled
`user_override` with its decision evidence. It does not change the native raw
value or turn that assertion into recovered native metadata. See the
[typed evidence contract](INTEROPERABILITY_CONTRACT.md#typed-metadata-evidence).

These requirements follow the preparation profile design. A route in this table does not imply
that the native decoder has recovered every field required for it. Unsupported
semantics and absent payloads still block the corresponding entity.

## Explicit decisions with evidence

```powershell
python scripts/validate_geoviewer_contract.py "D:\run\project_EXPORTS\INTEROPERABILITY_CURRENT.json" --profile opendtect --decisions "D:\review\decisions.json" --retention-view "D:\review\retention.json" --output "D:\review\new-assessment.json"
```

Both optional inputs are separate review artifacts. Omit `--retention-view` for
physical inspection without a declared pruning policy. Omit `--decisions` to
report missing receiving decisions. `--hash-artifacts` independently requests
fresh bulk hashes; the default checks sizes/state and bound small metadata.

A decisions document has schema `geoviewer.preparation-decisions/1`, exact
`generation`, committed `manifest_sha256`, `profile_revision`, and an `entities`
map keyed by exact catalogue entity keys. Every entity decision records:

- `reviewer`, `method`, and a nonempty `evidence` list of local relative `path`,
  fresh `sha256` and descriptive provenance. The evidence path stays inside the
  decisions directory. JSON, text, Markdown and CSV references are allowed,
  bounded to 2 MiB each and 16 MiB in aggregate.
- Exact `target_version`, `target_api_version` and `target_object_id`. For an
  OpendTect log, `well_target` records `source_entity_key`, `target_id`, and
  `state: existing`. An exact target identity is required; display names may match
  across different objects.
- Where required, a `survey` with explicit CRS, units, vertical domain/reference
  or OWT/TWT convention, and node lattice or complete seismic geometry.
- Optional `source_assertions`, each containing `value`,
  `scope: exact_source_entity` and `evidence_index`. Only allowed semantic fields
  can be supplied. Known conflicting source values block the mapping. Assertions
  cannot waive payload, source identity, publication or numerical QC failures.

Evidence hashes prove the selected reference bytes, not their scientific truth.
Reviewer attribution and source scope remain visible. Do not manufacture a
reference just to make a mapping qualify. Exact stored units, MD references,
null/interval semantics, topology and seismic conventions need appropriate
calibration evidence. The original source evidence is never overwritten.

Positive and negative complete decision examples are exercised in
`scripts/test_geoviewer_receiving_profiles.py`; their synthetic references are
deliberately labelled as test fixtures, not geoscience validation.

## Interpreting the assessment

Receiving states distinguish `qualified_for_planned_mapping`, missing payload,
metadata/relationship/QC blockers, unsupported target mappings and
`requires_derived_mapping`. A derived proposal must state the operation, losses
and prerequisites. It does not execute resampling/reprojection or make a lossy
mapping equivalent to original data.

Log preparation requires exact source-well binding, stored MD/value units and
reference, explicit `index_shape` and `value_shape` of `scalar_1d`, continuous
point-sample semantics and null semantics. Missing shapes remain unresolved;
known array/vector shapes are unsupported by these scalar routes. Recorded
`interval_records`, `curve_kind`, `index_type`, `value_role` and native `blob_type`
must not contradict the selected scalar representation. A summary label or
decision cannot erase an interval flag, categorical code role or array declaration.
Absent shape evidence may use an attributed, exact-source assertion; existing
conflicting shape evidence cannot be overridden. This blocks only the proposed
mapping and preserves numeric exports. Optional
CRS/display names do not block a log mapping into an already bound well. Categorical
or interval data cannot silently become continuous logs. Geometry requires the
appropriate role and registration. Property nodes need their exact parent,
validated node registration/count and a qualified parent mapping. Seismic checks
cover receipt-bound axes/geometry, component selection, full trace/sample QC and
the generated binary/trace-header profile. Missing components cannot be zero-filled.

The generic `source_state` remains separate from the mapping-specific assessment:
it reports original recorded source prerequisites. Attributed receiving decisions
can support a particular mapping while that generic source assessment remains
blocked. Consumers must use the selected profile's `receiving_assessment` and
reason codes, not treat either state as an executed import.

## Retention view

`scripts/geoviewer_contract_retention.py` provides `build_retention_view` and
`load_retention_view`. A `geoviewer.retention-view/1` document is bound to the
original generation and manifest hash and contains exactly one row for every
original artifact. Each row preserves original identity, path base/path, expected
size/hash, index section and publication status, plus separate retention status
(`retained`, `pruned`, `unavailable`, `external_reference`), reason and evidence
reference. Original outcomes cannot be replaced by the retention disposition.

The validator still inspects current files. A claimed retained file that is missing
or has a wrong size produces a conflict. An intentionally pruned or external-only
row does not become a usable payload. No view is inferred merely because files are
missing. The view requires declared retention evidence; it does not perform cleanup
or verify a complete archive. Loading rejects duplicate identities/JSON members,
unknown/tampered original fields, unsafe paths and bounds over 64 MiB/200,000 rows.

## Evidence and remaining acceptance

Anonymized validation records current source hashes and 132 tests: zero failures/errors, one Windows
symlink privilege skip. Three preserved pre-fix failures cover independent state
axes. Anonymized validation verifies that new generations also
identify the profile and retention modules.

Malformed nested seismic records produce an explicit validation error. Invalid
target identity types, nonfinite lattice determinants, noninteger QC counts and
malformed component mappings block qualification. Retained fixtures reproduce the earlier false qualification and crash. The 129- and 132-test stages remain distinct historical checks.

Anonymized validation tests the three profiles against
a retained metadata fixture: 6,114 entities, 3,597 original artifacts, eight
retained sidecars and 3,589 deliberately pruned artifacts. All profiles correctly
report no qualified payload mapping in that copy. Positive preparation cases are
synthetic; real metadata calibration, legacy package adaptation, the complete
normal-publication matrix and current-source complete-project proof remain open.
Both already-bound trial engines predate these changes. Release readiness and
advanced-review closure remain unproven.

The subsequent native-log and normal-publication stage is recorded separately in
Anonymized validation records 234 tests, zero failures/errors,
one Windows symlink privilege skip (233 passed), with 27 exact source/test hashes.
Its synthetic decoder-to-delivery tests carry observed sampling/null/interval and
source-profile facts through the catalogue while keeping unknown MD reference
blocked. Publication tests cover accepted, empty, partial and report-only flows,
immutable assessment bindings, relocation and tamper controls. The normal launcher
finalization test uses synthetic extraction/preflight receipts; it is not a full
real-project run. The earlier 132-test evidence remains a historical stage.

To verify a normal run's publication receipt from Python:

```python
import json
from pathlib import Path
from geoviewer_contract_publication import verify_publication_binding

run = Path(r"D:\run\project_data")  # directory containing RUN_RESULT.json
receipt = json.loads((run / "RUN_RESULT.json").read_text(encoding="utf-8"))
binding = verify_publication_binding(receipt["interoperability_contract"], run)
```

This checks the pointer, manifest, immutable assessment bytes and any bound human
report pages, not bulk
availability, numerical values, source-project integrity or a receiving import.
Use the validator separately for current artifact state and profile prerequisites.

## Searchable preparation evidence

Normal finalization now links to `interoperability/<generation>/PREPARATION.html`
alongside the existing full inventory and decoded-data report. The HTML is derived
from the same committed catalogue and three recorded assessments. It does not
read native payloads or qualify a receiving application independently.

The index separates entity counts from distinct artifacts, groups counts by
category and source lane, and searches names, UUIDs, categories, assessment states
and diagnostic codes. Fifty matching objects are visible per page. Evidence links
open bounded detail pages containing identities, recorded artifact availability,
the preferred full payload when one is recorded, field-level reasons, next checks,
raw metadata and relationships. Missing payloads remain explicit.

Shared diagnostics are grouped by code, field, reason and next action. Each group
counts distinct affected entities across profiles and links to its exact
population. Resolving a shared blocker does not necessarily resolve the other
blockers on those objects; population counts are not a predicted import count.
Shared artifacts can appear in several category rows, so category artifact counts
must not be summed as a global file count.

The receipt binds the index and every auxiliary HTML page to the generation and
manifest, with fresh page sizes and hashes. Keep all pages listed in `human_report`
together with the assessment JSONs. Older receipts without this optional view
remain readable. Metadata input and total HTML output each have a 256 MiB bound;
oversize reports fail explicitly without silently truncating the inventory or
publishing a partial human view. No external scripts or services are required.

Anonymized validation records 227 tests: 224 passed and three Windows symlink privilege skips. A retained metadata-only
catalogue yielded 6,410 exact object links, 129 detail pages and one blocker page;
zero payload artifacts, ready mappings or receiving imports were invented. The
index is 4,516,707 bytes; all HTML totals 105,988,214 bytes. Browser checks cover
pagination, exact UUID and empty search, category expansion, a 6,410-object shared
blocker population and the matching detail link. Large assessment JSONs and the
blocker population page still warrant performance work. No current-source full
project run or receiving import is established by these checks.


## Dependency corrections

Source prerequisite revision 6 and receiving revision 7 require one distinct, correctly typed well identity with exclusively exact links. Duplicate citations of the same exact target are allowed; candidate, mixed-status, competing or wrong-category links remain unresolved. Earlier generation-bound decisions with an old profile revision are rejected and require explicit reassessment. Contract schemas and application version remain unchanged.

A source payload dependency requires a currently available accepted full representation for the target category, recorded numerical QC and a valid recorded SHA-256. Metadata, previews and partial artifacts cannot satisfy it. Fresh same-size mutation detection still requires `--hash-artifacts`; a size-only inspection never claims fresh SHA verification.

Capability checks preserve log-only preparation against a reviewed exact existing destination well even if source head bytes are unavailable. A planned PaleoScan well instead requires its own qualified parent mapping and matching target identity. OpendTect planned-well creation remains unsupported. A PaleoScan surface reference binding requires the selected exact seismic source payload and its nonwaivable QC/integrity prerequisites; separately verified existing volumes have no bypass route. Surface properties still require qualified parent geometry and both masks. Partial publication remains blocked where prerequisites are unresolved.

Receiving application imports have not been run for these preparation checks. Unknown physical metadata remains explicit; no unit inference or rescaling is introduced.


Current release scope is in [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
