# Multi-project diagnostic evidence

[Website](https://saherlabs.dev/) · [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

The crawler/runner add-on gathers available diagnostic evidence automatically
from each extraction, including failed and partial runs. It keeps the extractor
at **1.0.0**. These additions run when using `GeoViewer_run_extractions.bat`;
the separate main extractor retains its existing per-run support logging.

The aim is to make future decoder improvements, bug investigations and performance
work evidence-driven across many projects. Logs do not train a model or change a
decoder automatically. A developer can study the collected evidence later.

## One place to open, one file to collect

Open the crawler campaign's `INDEX.html`, then **Cross-project diagnostics and
improvement evidence**. The analysis is at `LOGS/ANALYSIS/REPORT.html`.

`CAMPAIGN_LOGS.zip` contains the comparison, individual diagnostics, original logs,
support bundles, campaign index and saved native file inventories. It is rebuilt
at the end of a runner or collection invocation. It contains project names and
paths: it is a local **unredacted** debugging archive. It is never uploaded.
Known credential fields in derived summaries are replaced, but the archive is
not a guaranteed anonymized sharing copy. Source binary files and converted
datasets are not included. Review the archive before external sharing.

## Evidence recorded for each attempt

| Area | Information retained when available |
| --- | --- |
| Reproducibility | Exact command, configuration, source/output locations, extractor manifest hash, analyzer hash, attempts, exit code and receipt outcome |
| Version and storage layout | Recorded saved/original Petrel version, build metadata, save evidence, detected layout; unknown values stay unknown |
| Native inventory | Object identifiers/types/categories and metadata completeness separately from attempted decoder objects; native file sizes, timestamps and paths from discovery |
| Decoding | Object type, registry version, serialization/decoder profile, success/partial/unsupported/failure status, unresolved metadata, reasons, tracebacks and evidence locations |
| Conversion and validation | Artifact metadata, output formats/categories, numerical QC, declared units/CRS findings, physical seismic conversion outcomes separately from native object inventory |
| Time | Overall duration, available stage durations, slowest timed objects, individual hash calls, bytes processed and repeated hash passes |
| Resource use | Sampled process-tree CPU time, memory, logical I/O counters, output free space and capacity decisions |
| Missing evidence | Unavailable counters, absent receipts/logs, malformed or oversized diagnostic records, unrecorded profiles and incomplete inventories |

`LOGS/<case-id>/attempt_001/` contains:

- `CASE_DIAGNOSTICS.json`: case summary and performance evidence.
- `OBJECT_OUTCOMES.jsonl`: final available decoder results, deduplicated by lane,
  object ID and type. Receipt results take precedence over repeated object events.
- `INVENTORY_OBJECTS.jsonl`: recorded project inventory, a separate denominator.
- `ERROR_OCCURRENCES.jsonl`: error/warning/gap evidence with original reasons and
  normalized signatures. Repeated events can appear here; these are not unique
  failed-object counts.
- `DIAGNOSTIC_RECEIPTS.json`: relevant metadata and receipts without known data
  arrays, sample payloads or binary dumps.
- `ATTEMPT.json`, collected originals and the normal per-extraction support ZIP.

The case's parent folder also retains the full child console and
`process_metrics_001.jsonl`. The available environment and dependency versions
come from the extractor's original environment record/support bundle.

## Compare projects without misleading counts

`LOGS/ANALYSIS/` provides:

- `REPORT.html`: readable comparison and evidence links.
- `CAMPAIGN_ANALYSIS.json`: complete machine-readable comparison and suggested
  investigation areas, each tied to cases or issue signatures.
- `CASES.csv`: outcomes, versions, sizes, durations, repeated hashing and memory.
- `VERSION_TYPE_MATRIX.csv`: recorded Petrel version/layout, data type and outcome.
- `DECODER_PROFILES.csv`: registry and decoder/serialization profiles by version,
  type and outcome. Unknown profiles remain explicit.
- `ISSUE_GROUPS.csv`: normalized recurring issues ranked by **distinct cases
  affected**, with occurrence counts and example evidence preserved separately.

Only the latest attempt per project contributes to comparisons; earlier attempts
remain available. Byte-identical excluded duplicates and projects not yet tried
are still visible in the campaign list, but are not counted as successful tests.
Inventory objects, decoder-selected records and physical seismic files have
different scopes. There is deliberately no universal project recovery percentage.

Issue groups and suggested investigations are clues, not established root causes.
Different data, storage and project sizes can explain differences between runs;
a version association alone does not prove a Petrel-version bug. Validate the
affected decoder against independent numeric references.

## Sampling and evidence limits

The runner samples its launched extraction process tree every **10 seconds** by
default. Set `diagnostic_sample_seconds` (1–3600) in the configuration before the
crawl to change this. Existing saved campaigns without the setting use 10 seconds.
Counters use Windows process APIs without administrator rights. Failed sampling
is recorded and does not stop extraction.

CPU and I/O figures are sampled cumulative observations. Short-lived children
and peaks can be missed, working sets may share pages, and process I/O is not a
measurement of physical disk or SMB throughput. Output free-space changes can
include activity by other programs. Stage/hash intervals may overlap; do not sum
them as independent time. Repeated hashes may be required integrity checks.
See [Windows process memory API](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo).

The analysis reads known diagnostic files and crawler metadata, not native stores
or converted sample arrays. JSON documents over 64 MiB, JSONL lines over 4 MiB,
and malformed records generate explicit evidence gaps; originals remain available.
A proprietary binary layout cannot always be diagnosed from logs alone. Some
decoder work will still need an authorized sample and independent reference export.

Analysis errors are recorded while original log collection continues. A disk
outage or forced termination can still prevent final collection. To rebuild the
analysis from already available results **without rerunning extraction**:

```powershell
.\GeoViewer_batch_tests.bat -Mode collect -Resume "D:\path\Campaign_..."
```

Recollection cannot recreate CPU/memory samples that were never recorded or
missing source-time metadata. It marks those fields unavailable.
# Trial repairs, 18–20 September 2026 (version remains 1.0.0)

The local 20 September CSV repair adds `csv_write_started`, `csv_write_progress`, `csv_write_completed`, and `csv_write_failed` events for spatial tables. Progress reports rows processed, total rows, elapsed time and the temporary file's observed size at most once per five seconds between bounded row batches. Bytes can lag buffered rows. A one MiB buffer reduces small write calls; publication uses a temporary sibling file and replacement only after a successful close. An interrupted process may leave a temporary file; only the final file has completed publication. Scientific QC and project coverage remain in their separate report fields.

`ARCHIVE_STATUS.json` records when `CAMPAIGN_LOGS.zip` was built and whether it is complete, partial, or failed. Each new ZIP includes `ARCHIVE_CONTENTS.json`. Locked transient `.lock` and `.tmp` files are excluded deliberately. Other unreadable logs are listed explicitly. A failed archive replacement retains the previous ZIP and original logs; inspect the status receipt before sharing an older archive.

Parent timeouts now appear in the grouped error report with their exception, time limit, PID and last recorded stage. Stage-array receipts are accepted. Informational readiness messages are not errors. `conversion_requested` and `conversion_tested` separately record the scenario selection and the conversion result.

The dedicated local runner enables bounded Python stack snapshots every 30 seconds in `LOGS/<case>/trace_<attempt>/`; runner snapshots are in the session log folder. These survive a killed extraction and do not depend on writing to the output share. They include call sites, selected path/object identifiers and process identities, without native payloads or arbitrary local variables. The main campaign ZIP includes them. Stack samples locate waits but do not prove their underlying cause; native code holding the Python interpreter lock may prevent a sample. Normal standalone runs do not enable this sampler.

The PowerShell package-copy and validation hash readers now aggregate reads through a one MiB buffer. Full SHA-256 coverage remains unchanged. The isolated regression script `scripts/test_petrel_hash_buffering.ps1` checks digest equivalence, handle cleanup, missing-file errors and reduced underlying read calls without executing extraction. Share timing depends on cache state and concurrent activity; this change does not promise a fixed speedup. Existing installed packages require an explicit rebuild or recorded local deployment to receive source changes, and active extractions keep their original engine files.

Native log/surface output planning budgets both final files and temporary LAS, ZMAP, preview and atomic JSON paths. Existing short output names remain stable. Long paths use compact staging and, where necessary, a shorter label with a reversible encoding of the complete object UUID; original names and UUIDs remain in metadata. Resume recognizes both naming forms and rejects duplicate committed outputs. Roots that cannot fit even the compact form report the required shortening explicitly. This fixes output allocation, not unsupported native payload layouts.

Log CSV/LAS and surface CSV/XYZ/ZMAP writers also use one MiB text buffers to reduce small writes on shared storage. Numerical read-back and close-before-publication checks remain in place; throughput has not been benchmarked.

## Workbook conversion bounds (21 September trial repair)

Optional Excel companion conversion emits `companion_workbook_started`,
`companion_workbook_sheet_started`, `companion_workbook_progress`, and a terminal
`companion_workbook_completed`, `companion_workbook_limited`, or
`companion_workbook_failed` event. Events include the source, declared sheet
dimensions, processed rows/cells and elapsed time. Declared dimensions are hints
from the workbook, not independently verified data counts. Progress is limited
to one event per five seconds between rows. CSV output uses a one MiB buffer.

The default per-workbook conversion budget is 300 seconds or 5,000,000 visited
cells, including blank cells. The companion script accepts
`--workbook-max-seconds` and `--workbook-max-cells` to adjust positive limits.
Time checks are cooperative between rows; they cannot interrupt a blocked OS
read/write. The test runner's process timeout remains the outer safeguard.

At a budget limit the already hash-verified original workbook remains in
`09_source_companions`. Newly created CSV derivatives are removed, no partial
CSV is registered as converted, and the inventory and manual-follow-up list
record `preserved_conversion_limited` with the reason. Cleanup failures are
logged explicitly. Existing output directories are never overwritten. These
bounds apply to optional conversion of open Excel companions, not native
Petrel log/surface/seismic recovery. A source workbook therefore remains usable
even when its CSV expansion is intentionally omitted.

Package QC resolves its root once and retains the real-path containment checks
for each manifest artifact. It compares manifest digests with the hashes just
collected for that same QC invocation, then rehashes the inputs at completion
to detect mutation. No hash evidence is reused across runs, and final receipt
verification remains unchanged. This removes an immediate duplicate read pass
without disabling full hashing or weakening linked-path rejection.

The receipt auditor and post-pipeline package validator also reuse canonical
artifact paths within their single verification pass. Each artifact still gets
a fresh SHA-256 and receipt byte-size comparison; input hashes and the artifact
manifest hash remain checked. A later audit of a timed-out run is separate
evidence of current file integrity. It does not retroactively complete the
original extraction, final QC, report delivery or scientific validation.

## Stage clocks and support collection

Future pipeline invocations write unique `pipeline_timings_<invocation>.json`
journals before unbounded stages start. Bounded native/companion stages retain
their existing monotonic clocks in `stage_budgets.json`. Both carry the category,
invocation ID, measuring process identity and terminal timestamp when observed.
The diagnostic collector exposes these in `STAGE_TIMINGS.csv` and its report.
Intervals may overlap and must not be summed as total runtime. A start without
a terminal record remains incomplete; missing historical clocks are not inferred.

The single support ZIP includes recognized timing journals with the same
redaction and source-data exclusions as other diagnostics. Originals remain
local and unchanged. Stage-clock measurements prove elapsed execution, not
successful numerical conversion or recovery of every project object.
