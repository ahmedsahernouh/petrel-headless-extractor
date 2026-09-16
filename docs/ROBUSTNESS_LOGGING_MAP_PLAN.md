# Robustness, diagnostics and spatial-map correction plan

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Registered 2026-09-16. **Implementation authorized by the user on 2026-09-16; maintenance work targets 0.8.1.** Baseline: released GeoViewer_data_extractor 0.8.0, commit `fe0696a3a699261680474f6bbca28e808ad6807f`. The detailed design below is the baseline plan; current delivered scope and remaining work are in [the 0.8.1 guide](GEOVIEWER_0_8_1.md). The user prioritizes robustness, more comprehensive logging, and adjustable point/well symbols. These corrections precede complete 3D RESCUE development.

## 1. Evidence and scope

Observed in a supplied 0.8.0 run: native spatial extraction completed, followed by multiple successful native log exports. Windows then returned `WinError 5` when renaming one recovered log's `.partial` directory to its final directory. The error was retained in the nested native recovery JSON, while the main console reported only a pipeline exit code. Private paths, names, UUIDs, project data and screenshots are excluded from this public plan.

The 0.8.0 baseline code explains the failure propagation:

- `scripts/petrel_native_recovery.py::recover_object` writes/checks output and metadata before `output.rename(final_output)`. Artifact hashing/registration follows that rename. An object is therefore not fully committed merely because its samples were checked.
- `run` catches parser/QC errors per object, but catches `OSError` outside the object loop. A rename failure aborts subsequent objects, and the failing object is not appended to the normal object results. The final reason is not printed with the recovery-report path. Full-run recovery checkpoints are not written after every object.
- `scripts/invoke_portable_petrel_extract.ps1` exits on the child's nonzero code. `scripts/petrel_geoscience_tools.py::extract_portable_project` converts it into a generic pipeline exception. Both native spatial extraction and log/surface recovery appear under the same outer stage heading.
- `scripts/standalone_petrel_extract.py` catches this exception before normal QC/seismic/index completion. Its fallback report emphasizes metadata and can replace a richer report. Failure-result/report/log writing can itself fail, masking the first error.
- `scripts/petrel_progress.py::run_pipeline` drains merged stdout/stderr. A malformed structured line or logging failure can escape the reader loop and close the pipe. Source stream identity is lost; child exit and cancellation evidence are incomplete on exceptional paths.
- `scripts/geoviewer_diagnostics.py` flushes useful text/JSONL events, but lacks a protected event schema, durable object lifecycle records and a consolidated root-cause summary. Final receipt replay can duplicate live outcome events.
- `scripts/report_petrel_project_audit.py::render_spatial_svg` writes fixed SVG radii (`1.65` points, `4.2` wells). JavaScript changes the `viewBox` without compensating those radii or label sizes, and permits unbounded zoom. The existing non-scaling stroke affects outlines, not filled-circle radius. Grid strokes also grow. Coordinates rounded to two SVG decimals can lose separability at deep zoom.

The screenshot shows oversized overlapping well symbols; it does not establish incorrect native coordinates. Identify display scaling, genuinely coincident coordinates and any unresolved coordinate context separately. The process that caused access denial remains unknown: no antivirus, Explorer, permission or source-corruption attribution is established.

## 2. Priorities and deliverables

| ID | Priority | Deliverable | Completion condition |
|---|---|---|---|
| NEXT-22 | P0, release blocker | Safe output finalization, object/category isolation and useful partial results | An isolated locked object cannot abort independent valid exports; unsafe run-wide failures stop honestly |
| NEXT-23 | P0, release blocker | End-to-end structured diagnostics and one accessible problem summary | A receiving-machine failure can be diagnosed from the top-level summary/log without hunting through nested receipts |
| NEXT-24 | P1, required usability correction | Stable, adjustable symbols and readable dense spatial views | Map zoom cannot inflate markers; overlapping objects remain discoverable at their true locations |
| NEXT-25 | P1, follow-on recovery | Checkpoint reconciliation and explicit resume/retry of unfinished objects | Validated work is reusable after interruption without silently trusting changed inputs or incomplete outputs |

Implement durable checkpoints for NEXT-22/23. Complete cross-invocation resume only after its own acceptance tests; it must not delay the first validated robustness maintenance release. No new decoder, RESCUE writer, Petrel/Ocean execution, automatic upload or source modification is part of this correction.

## 3. Failure policy: continue only where dependencies are sound

**User clarification, 2026-09-16: isolated routine errors must not block the application.** Automatic recovery and useful partial completion are the default. Handle an error at the smallest affected scope (file, format, object or category), record it, and continue independent work without asking the user to intervene. A locked output folder, failed preview, missing optional metadata, unavailable alternate format, shallow-link failure or recoverable diagnostic-sink error must not terminate the full extraction. Retain the valid data index, available report content and clear warning/error summary. Do not silently discard the error or label incomplete data successful.

Escalation requires evidence that continued work is unsafe or impossible for the affected scope. Error counts, exhausted retries or a nonzero child exit code alone are not a reason to abort every category. Stop only dependent work where possible; reserve whole-run shutdown for genuinely shared failures such as unusable run storage or source/ownership integrity problems. A system-wide stop still preserves previous outputs and attempts a useful final status/report. This is a required acceptance condition, not optional best-effort behavior.

| Condition | Intended handling | Reported outcome |
|---|---|---|
| Candidate transient rename/sharing denial on an owned pending object | Close owned handles, retry only finalization within a bounded budget; continue independent objects if still blocked | `finalization_failed` with operation, OS code, attempts and retained pending path |
| Persistent object-specific access denial, absent external file or unsupported payload | Record one object outcome and continue other objects; skip dependents of that object | `write_failed`, `source_missing`, `unsupported_layout`, or `blocked_by_dependency` |
| Numerical, geometry or mask readback mismatch | Reject affected format/bundle; no retries of the same decoder as a cure | `validation_failed`; retain diagnostic evidence outside accepted exports |
| An alternative output format fails, while another independently validates | Commit independently useful formats separately; preserve common identity/metadata | Per-format results, object `partial`; counts must identify which usable files exist |
| Shared model/metadata/database inaccessible or corrupt | Mark affected category unavailable; other categories continue only if their input dependencies are independent | Category failure plus explicit blocked-object counts; no fabricated empty category |
| Destination already exists, ownership ambiguous, reparse/containment conflict | Do not overwrite or delete it; stop that object, or the run if its workspace ownership is uncertain | `destination_conflict` / `unsafe_output_path` |
| Disk full, lost/unwritable run volume, unrecoverable memory/resource exhaustion, inability to persist essential receipts | Stop scheduling writes; terminate only owned workers if necessary; preserve completed evidence | Run `failed`; best-effort minimal diagnostics at a disclosed fallback path |
| Source fingerprint changes or a committed output's integrity fails | Stop dependent work, quarantine affected outputs, preserve prior evidence for inspection | Integrity failure, never a passed source check |
| User cancellation / timeout / unexpected child crash | Persist current states, drain output where possible, stop the owned process tree with a bounded wait | `cancelled`, `timed_out`, or `interrupted`; completed objects remain identifiable |
| HTML/map rendering failure | Preserve accepted data and last valid report; write a minimal result/index/diagnostic fallback | Report generation failure separately from data recovery |

Do not catch every exception and blindly continue. Unexpected decoder errors are isolated only if shared state remains valid. Label unattempted objects distinctly from attempted failures and empty supported objects. CRS/units uncertainty stays separate from I/O and numerical validity; retain already allowed unknown-metadata exports.

Keep two aggregates: **execution status** (`completed`, `partial`, `failed`, `cancelled`, `interrupted`) and **coverage/QC** (requested, discovered, applicable, attempted, committed, unavailable, failed, not attempted; numeric checks; source-integrity scope). A completed report is not complete data recovery. Report-only runs must not count intentionally unrequested conversion as failure. Do not count diagnostic replays or retries as extra objects.

Proposed launcher exit contract: `0` completed without requested-export gaps; `10` completed with gaps; `1` fatal/unexpected run failure; `130` handled cancellation. Confirm these values against every BAT/PowerShell/Python caller before implementation. Raw child codes are retained alongside a validated structured child result, never interpreted as geological coverage. Update all wrappers together so a legitimate partial outcome can reach downstream reporting and independent categories. Unsupported/missing requested objects are gaps; unknown units alone are not failure if the allowed output validates. Abrupt process death may leave only an interrupted journal, not a fabricated final exit receipt.

## 4. Safe output finalization

1. Preflight the selected output root before copying large data: source/output separation, owned workspace, actual write/flush/close/rename/remove capability in a newly created probe directory, path budget, free space and estimated required storage. Inspect only relevant filesystem facts; never alter ACLs or security settings. Unknown output size is explicitly an estimate. Recheck free space during large writes.
2. Give every run/object/attempt a unique identity and bounded relative path. Claim the run directory exclusively. Never reuse a `.partial` directory based only on its name. Keep original native IDs/names in receipts; shortened filesystem names are presentation only.
3. Record object start before read/decode. Use explicit lifecycle phases: reading, decoding, writing, validating, finalizing, committed or failed. Each requested format has its own outcome where independent; a multi-file grid/model format remains one indivisible required-file bundle.
4. Write to an owned staging directory. Explicitly close every CSV/LAS stream, iterator, archive, SQLite cursor, NumPy mapping/NPZ container and preview reader before finalization. Do not rely on garbage collection or sleeps to release our handles. Audit library ownership rules.
5. Complete readback and artifact hashes before promotion; preserve the manifest/QC evidence in staging. Keep expensive conversion out of the retry loop. Source and destination must be inside the owned output run, on the expected volume, with destination absent and no unsafe links/reparse points. Do not replace an existing destination.
6. Retry only candidate transient filesystem finalization errors (initially Windows 5/32/33), conditional on those checks. Proposed policy: first attempt plus five delays of **0.2, 0.5, 1, 2 and 4 seconds**; maximum retry sleep 7.7 seconds. Access denied is not proof of transience. Log every attempt and elapsed wait; other errors go straight to classification. Persistent denial produces a failed object, not an infinite retry.
7. Bound aggregate retry time (initial proposal: 30 seconds per category). After three consecutive output failures, perform an owned output-health check: continue with per-object failures only if storage remains sound; otherwise stop affected work. A healthy probe does not prove the blocked folder is accessible. Retain both findings.
8. After successful rename, append the committed object record and publish only manifest-backed artifacts. If death occurs between rename and journal commit, classify the object as uncommitted until a later reconciliation verifies its staged receipt, hashes and source identity. A directory name is not proof of success. Flush terminal transitions; use filesystem sync at commit boundaries where supported and describe power-loss limits honestly.
9. If finalization is still blocked, retain the pending data for diagnosis but exclude it from import-ready indexes and success totals. An optional single deferred finalization pass at category end may reuse the already validated staged files only after checking their receipt/hashes and remaining retry budget. Do not re-decode or claim success from a renamed folder alone.

Apply the same ownership/error discipline to report replacement, result/checkpoint writes, export-index writes and shallow hard-link delivery. Preserve the last valid report if a newer report cannot replace it. A failed shallow link can point to a valid canonical export with an explicit storage note; it must not reclassify the underlying data as failed or silently copy huge datasets.

## 5. Isolation, checkpoints and report completion

- First implementation keeps the existing offline sequential pipeline and validated numerical checks. Split spatial, logs and surfaces into explicit substages/results rather than hiding them under one heading. Category subprocess isolation can be used for native-library failures, with only owned workers terminated and no unbounded per-object process proliferation.
- An object error must include identity **before** serialization/finalization can throw. Catch it at the object boundary, preserve operation context and append a terminal result. Systemic failures propagate with that context and the list of dependent categories.
- Persist a compact append-only object journal after each terminal result; keep atomic snapshots at stage boundaries. Avoid rewriting the entire ever-growing recovery JSON per sample or per object. The journal is authoritative; snapshots are rebuildable summaries. Malformed final journal lines after a crash are retained as evidence and never treated as committed objects.
- Try final source-state verification after handled errors/cancellation where possible. Record `passed`, `failed`, `not_completed` or `not_requested` with scope. Never replace an unavailable final check with `source_mutated=false`. This adds no forced full seismic hash when the user selected hashing off.
- Build partial visual reports and shallow indexes from committed validated records, even after an unrelated category fails. Preserve the full inventory, available figures, actual data links and error list; never downgrade a richer valid report to metadata only. Pending artifacts remain in diagnostics, not the import-ready index.
- Keep top-level report, file index and diagnostic links synchronized. Always show a clear end state and counts of committed, failed, unavailable and unattempted objects/formats. Finalization itself is guarded: preserve the primary error when reporting/logging raises a second error.

NEXT-25 resume design: explicit selection of a prior run through the main entry point; no automatic overwrite. Verify run ownership and exclusive lock, journal schema, tool/decoder compatibility, source identities, options and committed artifact hashes. Reuse only proven compatible objects; retry failed/unattempted ones in new attempts. Retain the old attempts and their errors. Changed native stores/options require invalidation of dependent work or a new run. With seismic hashing off, disclose the weaker source fingerprint and do not claim byte-identical provenance. A interrupted seismic file restarts unless a separately validated chunk-resume protocol exists. No manual promotion of `.partial` folders is recommended.

## 6. Comprehensive, accessible logging

### User-facing files

Retain familiar top-level `<run>_LOG.txt` and `<run>_EVENTS.jsonl` paths. Add a short `<run>_DIAGNOSTICS.txt` beside `<run>_REPORT.html`, containing the latest state, primary error(s), affected object/category/format, exact operation/OS message, attempts, accepted-output counts and next action. It must be enough to identify the reported rename failure without requesting nested files. Link a foldable **Problems and diagnostics** panel from the report's first screen; list every problem with direct object/export links and a filterable stage timeline.

Bootstrap starts a session log before Python installation or output-root selection. Carry its session ID and file into the run. If startup fails, display the actual bootstrap-log path. Where normal output storage fails, attempt a small session-owned local fallback under the user temp directory, then console stderr as last resort; disclose fallback and logging loss explicitly. Do not claim guaranteed report creation on a full/unavailable disk.

### Event contract

| Field group | Required information |
|---|---|
| Correlation | Schema version, run/session/attempt/event IDs, monotonic sequence, UTC time, monotonic run duration, severity, stage/substage, component, PID and parent PID |
| Operation | Object ID/name/category, decoder/profile/version, input reference, output format, lifecycle phase and operation name; per-object duration distinct from run duration |
| Work | Object index/total where known, samples/rows/bytes checked, null counts, output bytes, actual byte/file rates, individual read/write/hash/validation duration |
| Error | Exception class, message, complete traceback and chained cause, errno/winerror, source/destination path, path lengths, current attempt/delay/budget, classification and chosen action |
| Output evidence | Artifact relative paths/sizes/hashes, numerical/readback results, source-integrity scope, staging/final/commit status and unresolved metadata |
| Environment | Extractor/build/runtime/dependency versions, relevant Windows/filesystem facts, source/output identity, free-space samples, effective user options; allowlist only |

Protect reserved fields from accidental overwrite. Emit separate event types for `object_started`, `object_phase`, `retry_scheduled`, `object_committed`, `object_failed`, `stage_finished` and `run_finished`; receipt imports must be explicitly labelled and deduplicated by identity/attempt. Log skipped hashes independently from completed hashes and conversion. Do not dump environment variables, credentials, native payloads or per-sample values.

Emit child stdout and stderr with stream/PID provenance, structured start/exit/error and redacted arguments. Drain both continuously with bounded memory. A malformed JSON event is retained as raw text with a parser warning, while draining continues. A log-sink failure must not break the child's pipe and masquerade as a decoder failure: switch to the fallback sink or coordinate a controlled stop if durable evidence cannot be retained. Bound reader joins, child termination and pipe draining; preserve timeouts and secondary cleanup errors alongside the original failure.

Log object/substage heartbeats every 5-10 seconds for long work. Distinguish a live parent heartbeat from confirmed child progress and flag stale progress without falsely declaring a hang. Use operation-specific configurable time limits and user cancellation; a large seismic conversion must not be killed solely because it is quiet or passes a fixed small duration. Progress percentages use measured work units, with unknown totals shown as unknown; stage completion is not a time estimate. Show recovered-with-gaps and cancelled outcomes rather than a misleading complete/success bar.

Always-on detail covers every lifecycle transition and exception. Optional debug mode adds bounded parser offsets, field/count expectations, slow-operation details and filesystem diagnostics. No extra full seismic reads or payload dumps. Rotate verbose raw logs into numbered segments with a segment index; retain critical errors and the authoritative lifecycle journal. Record dropped/throttled debug records explicitly. Measure overhead on representative fixtures; target under 5% added elapsed time for normal diagnostics, reporting measurements rather than assuming it.

Optional manual **Create support bundle** produces local copies of the diagnostic summary, relevant receipts, log segments, event history and environment/option summary. Default sharing copy pseudonymizes names, usernames, paths, IDs, references and traceback/command text consistently. Include a contents/redaction manifest; scan all nested text fields. Exclude source data, raw samples, screenshots, large workflow definitions and credentials by default. Preserve original local evidence. No upload or notification is automatic; the user can review the bundle before sharing. Do not advertise anonymization as guaranteed.

## 7. Spatial-map corrections

The design goal is stable symbols in **CSS screen pixels**, while geographic positions and polygon geometry retain true coordinate relationships. Simply reducing the initial radius is insufficient because current zoom will enlarge it again. SVG `non-scaling-stroke` protects stroke width only. Use the actual screen transform (`getScreenCTM`) to compensate marker radius, labels and offsets on every zoom, fit and resize; do not depend on unimplemented `non-scaling-size`. See [MDN vector-effect](https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/vector-effect) and [screen-transform API](https://developer.mozilla.org/en-US/docs/Web/API/SVGGraphicsElement/getScreenCTM).

| Control | Proposed default and range |
|---|---|
| Well symbol diameter | 8 px; slider/numeric input 2-16 px |
| Point symbol diameter | 2 px; slider/numeric input 0.5-8 px |
| Layer opacity | Wells 80%, points 35%; independently adjustable 10-100% |
| Well labels | Auto: selected/hovered and non-overlapping labels; explicit None / Selected / Auto / All options |
| Polygon line width | 1.25 px, screen-constant; independent optional adjustment |
| View controls | Fit all, fit visible layers, fit wells, fit selected object, reset view and reset display styles separately |

All controls work offline and by keyboard, show their current values and have visible labels. Keep settings for the current report session without requiring browser storage access. Optional persistence is best-effort and must not break a file-opened report.

- Keep well IDs/names and complete native coordinates in the data model, avoiding early two-decimal projected-coordinate rounding. Fit/zoom/pan use the actual SVG viewport transform, including aspect-ratio padding. Clamp zoom to finite sensible bounds; preserve cursor anchor during wheel zoom. Test resize and browser zoom. Keep axis labels/ticks and grid strokes readable; recompute ticks for the visible native extent instead of magnifying a static grid indefinitely.
- Display point and well counts by total, visible, sampled and coincident/clustered status. Preview sampling is explicit and never changes full exports. Retain the existing polygon segment/part separation and ordering; do not turn segments back into XY scatter or join unrelated endpoints.
- Distinguish near-overlap on screen from exact coordinate coincidence. Clicking overlapping wells opens a selectable list with IDs, names, coordinates and export links. Optional count clusters may group dense previews by screen proximity, recomputed as the view changes; they must retain access to every member. Do not silently deduplicate wells, jitter geological coordinates or invent spatial separation. An expanded display must identify any screen-only offsets.
- Apply object/category filtering before fit calculations. A hidden distant layer must not control **Fit visible**. Unknown/incompatible coordinate contexts remain labelled and are not silently merged or reprojected. Extreme outliers remain listed and selectable; do not discard them to make the map look cleaner.
- Retain the lightweight offline SVG implementation for the correction. If later point-density benchmarks justify a canvas layer, preserve exact object picking, sampling disclosure and export links; a renderer replacement is not required for the first fix.

## 8. Implementation order and code ownership

| Phase | Scope | Primary files |
|---|---|---|
| A | Event/result schema, object lifecycle and shared bounded filesystem finalization; reproduce the Windows failure | `geoviewer_diagnostics.py`, `petrel_native_recovery.py`, new small shared filesystem helper if needed |
| B | Per-object/category boundaries, durable journal, wrapper partial-status handling, partial report/index completion and fallback diagnostics | `invoke_portable_petrel_extract.ps1`, `petrel_geoscience_tools.py`, `petrel_progress.py`, `standalone_petrel_extract.py`, `geoviewer_delivery.py`, spatial/seismic workers |
| C | Stable marker sizing, display controls, coincident-object selection, view fitting and UI verification | `report_petrel_project_audit.py`, `petrel_visual_report.py`, report tests |
| D | Explicit cross-run checkpoint reconciliation/resume, support-bundle UX and deeper optional diagnostics | Main launcher, journal/recovery helpers and diagnostic/report integration |

The contract in sections 3-6 applies across all phases. Phases A-C are next-maintenance release gates. Basic single-file diagnosis and last-good-result preservation cannot be deferred to phase D. Implement a supported result schema before changing exit semantics, and migrate all internal callers together. Keep private MCP/server changes out of the standalone scope unless separately requested.

## 9. Acceptance tests required before release

These are planned checks, **not executed results**. Use synthetic fixtures for public tests and private fixtures only in local evidence.

| ID | Scenario | Required result |
|---|---|---|
| R00 | Mix isolated rename denial, failed preview, missing optional metadata and failed shallow link in an otherwise healthy run | No application-wide abort or intervention prompt; independent exports finish, accepted files stay accessible, partial report/index and all errors are visible |
| R01 | Hold a real Windows handle that denies rename; release during retry | Commit succeeds without re-decoding; attempts/durations/OS details are visible |
| R02 | Keep that handle locked throughout the budget | One object fails finalization; subsequent logs, grids and independent seismic run; report/index remain useful |
| R03 | Persistent access denial / destination collision / unsafe link | Bounded failure; no overwrite, ACL modification, broad deletion or endless retry |
| R04 | Multiple consecutive failures / full or disconnected output disk | Distinguish local object failure from shared storage failure; controlled stop and honest fallback diagnostics |
| R05 | Fail numerical readback or one alternative format | Failed artifacts excluded; independently valid formats retained; no relaxed scientific checks |
| R06 | Crash before rename, after rename before commit, or mid-journal append | Previous commits survive; pending/ambiguous object never appears accepted without reconciliation |
| R07 | Ctrl+C, timeout or worker crash during a large object | Owned process tree stops within bounds, pipes drain, no orphan writers; previous outputs remain indexed |
| R08 | Source changes / output hash mismatch / exclusive run-lock collision | Dependent reuse rejected; no false source-integrity success or concurrent mutation |
| R09 | Report/index replacement fails or diagnostics sink fails while handling another error | First error preserved; last valid report retained; secondary failure and fallback location visible |
| R10 | Standard user, default long output root, short root, spaces/Unicode and Windows paths | Successful baseline or preflight explanation before expensive work; no administrative privilege requirement |
| L01 | Reproduce the supplied rename failure | Top-level diagnostic summary alone contains object, failed operation, WinError, attempts, scope and recovery decision |
| L02 | Invalid structured child line / long lines / mixed stdout-stderr / non-ASCII message | Continued draining and complete actionable evidence; no child failure caused by the logger |
| L03 | Bootstrap failure before run creation / logging rotation / abrupt kill | Discoverable session log; intact completed events; no invented completion after truncation |
| L04 | Per-object retries and final receipt replay | Unique logical object counts, ordered lifecycle, separate run and object durations, consistent console/JSON/report totals |
| L05 | Hashing off, optional conversion off and unknown units | No forced seismic hash/conversion; correct source/QC scope and planned coverage semantics |
| L06 | Redacted support bundle and diagnostic overhead | Private tokens removed from nested errors/paths, source files excluded, originals preserved; bounded overhead measured |
| M01 | Screenshot-style dense wells; zoom/fit/pan/resize | Symbol diameters remain within 1 CSS px of selected values over supported zoom range |
| M02 | Points/wells sliders, opacity and labels in combination | Independent effects, readable values, keyboard access and reset; no coordinate/data changes |
| M03 | Exact co-location and near-co-location | Every well remains selectable; overlap is explained without fake separation or deduplication |
| M04 | Hidden outlier layers, one point, empty layer, very small extent | Fit behaves predictably; finite viewBox and no clipped or huge symbols |
| M05 | Multiple polygon segments / large sampled point cloud / unknown coordinate context | Segment identity retained, responsive display, explicit preview counts/context and unchanged exported values |
| M06 | Offline Edge/Chrome, narrow and wide windows, browser zoom and saved local HTML | No external resources; functional controls, markers, report links and useful print fallback |
| E01 | Known modern project: clean run and injected failure run | Compare accepted data/numerical QC with baseline; fault injection alters only affected outcomes and proves continuation |
| E02 | Final standalone ZIP on a receiving Windows machine | Test shipped bytes through the actual BAT with normal user permissions and injected denial, not only the developer checkout |

NEXT-25 adds explicit interrupted-run resume tests for unchanged/changed source, changed decoder/options, tampered output, concurrent attempts and interrupted seismic. No resume capability is claimed until these pass.

## 10. Evidence references and planning validation

Windows rename access/handle constraints: [Microsoft FILE_RENAME_INFORMATION](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information). This supports the bounded-finalization design; it does not identify the blocking process in the reported incident.

Local source inspection, supplied diagnostic JSON/log and screenshot are the direct planning evidence. Existing KB orientation calls passed; the KB query returned version-mismatched 2009 Petrel notes, which are not evidence for these standalone 2024.5/report behaviors. No new parser/version capability is inferred from retrieval.

Planning validation is limited to document links, source references, consistency and scoped Markdown diffs. The subsequent implementation instruction authorized functional/fault-injection/browser/extraction tests; executed results are tracked in release validation. Preserve the published 0.8.0 package and original failure evidence.
