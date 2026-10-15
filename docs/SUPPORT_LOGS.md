# Support diagnostics — 1.0.0

Website: https://saherlabs.dev/  
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor

Full seismic SHA-256 defaults to **Yes** for both `.pet` extraction and direct `.zgy` conversion. Enter accepts Yes. Choose `n` interactively or add `-NoFullHash` to the main BAT to skip those full-file reads. `-FullHash` remains supported. Hashing and complete numerical conversion QC are independent.

After a successful, partial, failed or normally cancelled run, send **one `*_SUPPORT.zip`** beside the report. The console prints its path, and the report links to it. Compression is offline; nothing is uploaded. The original readable/structured logs remain available locally.

The ZIP contains a README, environment summary, contents/redaction manifest and diagnostic files: startup/dependency checks; run options; application, Python, OS and dependency versions; disk capacity; stage timing; child commands, stdout/stderr and exit codes; object outcomes, retry evidence, OS error codes and tracebacks; extraction/QC/conversion receipts; rotated logs and fallback logs. Log files are streamed into ZIP compression. The collector does not hash or read the project payloads again.

Only allowlisted diagnostic filenames are collected. Native stores, converted datasets, sample arrays, report figures, screenshots and workflow definitions are excluded. Recognized names, paths, UUIDs and explicit credential fields are replaced in the sharing copy, consistently within that ZIP. No reverse mapping or complete environment-variable dump is included. **This is best-effort replacement, not guaranteed anonymity:** inspect the sharing copy before sending it; unfamiliar free text can contain sensitive details.

If a failure occurs before the report/run folder exists, the launcher prints the ZIP path in a temporary diagnostics folder. If full collection is unavailable or fails, Windows/.NET creates a **startup-only, unredacted** ZIP and labels it accordingly. This can reflect missing Python, a rejected collector checksum or a collector process error; it does not by itself mean extraction failed. `support_collector.json` records the reason and phase, with collector stdout/stderr retained when a child was launched. Review that archive before sharing. If output storage fails, ZIP creation attempts a temporary-folder fallback. Diagnostic failures do not turn accepted extraction output into a failure.

Collector verification uses Windows' built-in .NET SHA-256 implementation, so a
terminal's altered PowerShell module path does not require `Get-FileHash` to load.
An unverified collector is never launched. The normal bundle includes the collector
receipt as captured before collection; the retained session receipt records its
final exit status afterward. Bundle contents/CRC and the actual extraction/QC
receipts remain the evidence of successful collection and extraction respectively.

Closing the console forcibly, killing the process, power loss, or failure of both normal and temporary storage can prevent final packaging. The startup session folder and original logs are retained. A developer can rebuild a ZIP from a retained session using the bundled runtime:

```powershell
FieldViewer\runtime\python.exe -B FieldViewer\scripts\geoviewer_support.py --session "C:\path\to\retained_support_session"
```

Choose a session without an existing `SUPPORT.zip`, or preserve/rename the previous archive first; the builder does not overwrite it. A rebuilt bundle reflects the available partial evidence, not a resumed extraction.

The command above uses the standalone folder name. Use the runtime and script
paths actually present in the complete Field Viewer 1.0.0 package. The package
SHA-256 identifies the exact archive used for any diagnostic review.
## Incomplete support diagnostics — 2 October 2026

If collection fails after opening a ZIP member, `CONTENTS.json` records that
member under `unavailable`, with `collection_state: incomplete_diagnostic`,
`complete_source_evidence: false`, and its uncompressed `retained_bytes` count.
The member may be empty or contain a useful partial, sanitized log. It is not
listed as successfully collected evidence, and its length is not the original
source-file length. Errors before a member is created use `not_collected`.
Original local logs and prior support ZIPs remain unchanged.

Current-source checks include a real local Windows sharing lock, preserved prior
ZIP, refreshed failed/successful run receipts, ZIP CRC checks, and a simulated
midstream read failure. This verifies archive accounting and freshness in the
synthetic fixture. These checks cover archive accounting and the stated failure
paths; receiving-machine and scientific validation remain untested.

# 27 September 2026: fallback event reconciliation

The trial analyzer reads the final run receipt's exact `structured_events` path
when logging moved to an in-output `_scratch/GeoViewer_diagnostics_*/EVENTS.jsonl`
file. It normalizes mapped-drive aliases, validates containment, deduplicates
event identities and joins stage/retry evidence only within the same run/process.
Missing, malformed or outside-output references remain explicit findings; it does
not search other drives for logs or invent stage durations. Existing raw logs and
historical diagnostics remain unchanged. The J0926 retained replay recovered
22,994 unique events and the final completion event; its previous unmatched-stage
finding was an analyzer discovery gap, not evidence that extraction stopped there.

## 27 September 2026: source fixes under verification, version 1.0.0

Child diagnostic and progress frames now use one synchronized write including the
newline. The reader can recover consecutive prefixed JSON frames from older joined
lines, with limits on line length and frame count. Invalid remainders remain explicit
warnings and raw child logs are preserved. Direct pipe PID/stream fields describe
the observed transport; forwarded origin claims are retained separately in
`child_reported_metadata`, including nested metadata. These are different evidence
scopes, not interchangeable process identities.

Package QC now parses both snapshot-listed companion LAS files and native LAS files
bound to accepted recovery declarations. `las_files`, `native_las_files` and
`companion_las_files` distinguish the scopes. Missing units remain unresolved and
do not block numeric parsing. Parse failures identify their native owners, so failed
objects are withheld from accepted checkpoint indexes while raw files and unrelated
exports survive. Pending, unregistered or integrity-rejected native LAS files are
not counted as parsed. File-integrity checks and geological acceptance remain distinct.

The existing diagnostic stream also records paired `operation_started` and
`operation_finished` events with operation IDs, measured durations and failures for
inventory, preflight/hashing, manifest validation, native binding, LAS parsing,
receipt finalization and verification. Report figures add aggregate times for
artifact reads/hashes, numeric plotting, inventory and seismic previews. Nested
timings overlap; do not add them to estimate total duration. `las_qc_outcome` records
counts and bounded error details; full file/object results remain in QC `result.json`.
These events travel with the usual local support logs. Hashing defaults and source
integrity requirements are unchanged. The changes postdate the frozen trial engine.

## 30 September 2026: isolated hash progress and workload visibility

Isolated `_hash_only` workers emit start, throttled progress, completion and error
frames with the exact source, declared file size, actual bytes read, elapsed time
and measured file-local ETA. Completion is emitted only after the source file-state
check passes. Disabled full hashing emits no hash operation. Parent logs retain
the frames through the existing bounded child-event parser. A read failure or
source-state change remains failed, even if all expected bytes were read.

The project seismic inventory/report separates known available bytes and missing
counts for proven project links, unlinked companions and user-declared replacements.
Missing-reference sizes are unknown. Native-store size alone is not the total I/O
workload: the retained Largeo trial read roughly 956 GiB of existing SEG-Y companions
for integrity, with no new seismic conversion. Hash percentages/ETA describe one
file pass, not extraction completion. Stage counts also describe unequal operations.
No hashing default, source-preservation rule or conversion QC was weakened.
