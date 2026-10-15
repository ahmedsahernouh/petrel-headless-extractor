# Field Viewer batch project trials

[Website](https://saherlabs.dev/) · [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

This separate development add-on uses the standalone **1.0.0** extractor without
replacing its files or changing its version. It is not included in the public
standalone ZIP. Its script discovers projects and runs the queue. Petrel and
Ocean are never launched; source projects are read-only.

## Start

Extract the batch add-on next to the main extractor BAT. The layout is:

```text
FieldViewer_data_extractor.bat
FieldViewer/                   existing complete extractor package
GeoViewer_crawl_projects.bat   stage 1: discovery and saved lists only
GeoViewer_run_extractions.bat  stage 2: read a saved list and extract
GeoViewer_batch_tests.bat      compatibility entry point; now defaults to crawl-only
GeoViewerBatch/                new runner, configuration and instructions
```

1. Run **GeoViewer_crawl_projects.bat** with
   `-ExtractorDirectory ".\FieldViewer"`. It checks/repairs the existing
   runtime, searches the connected drives, measures native project pairs and
   verifies duplicates. It writes `PROJECTS.csv`, `INDEX.html`, the machine-readable
   `CAMPAIGN.json` list and per-project file inventories, then **stops without
   extracting any project**. This is the potentially long discovery phase.
2. Review the list whenever convenient. Keep the campaign directory together;
   `PROJECTS.csv` is the readable view and `CAMPAIGN.json` is the runner's input
   and durable status record. Editing the CSV alone does not change the queue.
3. Later, run **GeoViewer_run_extractions.bat** with the same
   `-ExtractorDirectory` value. It reads the latest
   **completed** crawler list recorded in `GeoViewerBatch\LATEST_CRAWL.json`,
   prints the exact selected list, and extracts its eligible projects sequentially.
   It does **not** rediscover projects or repeat duplicate comparisons. You can
   instead pass `-List` as shown below to select a campaign explicitly.

The runner checks the listed native pair's current file list, sizes and times
before each extraction; that targeted source check is separate from drive-wide
discovery. Completed trials stay recorded and are skipped on subsequent runs.
No inference or AI connection is needed for either stage. Keep Petrel closed
for these source projects. Run normally so mapped drives remain visible.

Only a completed crawl updates `LATEST_CRAWL.json`; if another crawl is still
in progress, the pointer continues to identify the previous completed crawl.
Select an explicit list when managing several campaigns. With no completed or
explicit list, the runner stops with instructions; it never starts a scan itself.

Extraction destinations must be set in the campaign configuration. Before each project the script selects the available configured root
with the most free space, subject to its admission estimate. Each trial gets a
new folder; previous trials are never overwritten or automatically deleted.
Full conversion and full seismic SHA-256 are enabled. Temporary extraction files
go under that trial's output folder, not the normal local Windows TEMP. The
initial runtime check still uses ordinary TEMP for its small bootstrap logs.

The small campaign index and collected logs are stored under
`GeoViewerBatch\Campaigns\Campaign_<timestamp>_<id>`. The directory is printed in
the console. Configure `campaign_root` if those logs should live elsewhere.

## Optional commands

Run these from the folder containing the two BAT files:

```powershell
# Discovery, measurements and duplicate checks only; no extraction.
.\GeoViewer_crawl_projects.bat -ExtractorDirectory ".\FieldViewer"

# Run the saved queue, at most three eligible projects in this invocation.
.\GeoViewer_run_extractions.bat -ExtractorDirectory ".\FieldViewer" -List "D:\path\Campaign_...\CAMPAIGN.json" -MaxProjects 3

# Continue projects not previously attempted; completed/failed trials stay recorded.
.\GeoViewer_run_extractions.bat -ExtractorDirectory ".\FieldViewer" -List "D:\path\Campaign_...\CAMPAIGN.json"

# Explicitly repeat failed/interrupted trials in NEW attempt folders.
.\GeoViewer_run_extractions.bat -ExtractorDirectory ".\FieldViewer" -List "D:\path\Campaign_...\CAMPAIGN.json" -RetryFailed

# Recollect diagnostics after an interruption, without starting an extraction.
.\GeoViewer_batch_tests.bat -Mode collect -Resume "D:\path\Campaign_..."
```

Use `-ExtractorDirectory "D:\path\FieldViewer"` if installed separately. Arguments
are passed as subprocess argument arrays, so spaces and non-ASCII names are
supported. Do not change the extractor build midway through a campaign; create a
new campaign when evaluating a different build. A campaign lock prevents two
runners from executing the same queue simultaneously.
The old `GeoViewer_batch_tests.bat` now defaults to discovery only. Its explicit
`-Mode run -Resume ...` and `-Mode collect -Resume ...` commands remain available.

## Findings and logs

- `INDEX.html`: readable trial overview with per-project report links.
- `PROJECTS.csv`: exact .pet/.ptd locations, byte sizes, file counts, duplicate
  relationships, saved version when the extractor records it, outcome and report.
- `CAMPAIGN.json`: complete queue, selected configuration, drive aliases,
  capacity checks, commands, attempt timings, exit codes and diagnostic paths.
- `inventories/`: file sizes and modification times within each exact native pair.
- `LOGS/`: one central directory containing scan errors, doctor results, full
  child console output, ordinary extractor logs/receipts and each support ZIP.
- `CAMPAIGN_LOGS.zip`: compressed central evidence for subsequent analysis.

The runner also creates [detailed case diagnostics](CASE_DIAGNOSTICS.md) and a
cross-project report linked from `INDEX.html`: recorded versions/decoder profiles,
object outcomes, recurring errors, conversion/QC metadata, stage/hash timings,
and sampled CPU, memory and I/O. Open `LOGS/ANALYSIS/REPORT.html` to compare cases.
These summaries and saved file inventories are included in `CAMPAIGN_LOGS.zip`.
Missing evidence stays explicit; retries and repeated object events do not inflate
the recovery comparison. Diagnostic-analysis failures do not stop extraction.

The aggregate ZIP includes original logs, project names and paths and is therefore
**unredacted**. It contains no source stores or converted datasets and is never
uploaded. Review before sending outside your own environment. The individual
extractor support ZIPs keep their existing best-effort identifier replacement.
If a finished run has only the launcher's startup ZIP, the batch retries the
existing hash-verified support collector to recover the full available diagnostic
bundle. Its own raw log collection remains available if that also fails.
Failed runs with no receipt are never counted as successful. Exit 10 with a
matching receipt means completed with gaps, not complete data recovery.
The batch itself returns 10 when its saved queue contains failures/gaps/deferred
or invalid projects or discovery access errors; it prints the outcome counts.
Zero means no such issue was recorded, not universal data recovery. A limited
`-MaxProjects` invocation can intentionally leave queued projects for later.

## Discovery and duplicate rules

The crawler inspects filesystem drives visible to this Windows session and
already connected SMB mappings. It does not enumerate other hosts or discover
every share on a server. Add accessible, unmapped UNC paths in `extra_roots`.
Mapped-drive aliases and overlapping roots are collapsed. Read failures are
logged; accessible drives continue. Junctions/symlinks, Windows recycling/system
metadata, native .ptd subtrees during project discovery, recognized extraction
outputs and configured destination/toolkit/campaign folders are excluded. The
log states exclusions and access failures; this is not an assurance that every
file on inaccessible or excluded storage was searched. ZIP/RAR archives are not
opened. Each .pet must have a matching sibling `<stem>.ptd` directory.

Sizes are logical `.pet + matching .ptd` bytes, not the entire common parent,
allocated disk usage or sizes of externally referenced resources. Native store
entries are inspected without decoding numerical data. Projects with incomplete
native inventories are listed but not extracted unattended.

Only byte-identical native pairs with identical relative file structure and
identical adjacent companion-file content are excluded as duplicates. Candidate
groups are first filtered by native file sizes/structure and the .pet SHA-256;
full native and adjacent-file hashing is limited to those groups. This can still
take substantial time for large copies. Same names alone never remove a project.
Saving in another Petrel version changes its bytes and retains a separate case.
Unreadable or uncertain duplicate candidates are retained. Every excluded copy
remains in the CSV with its canonical trial ID; sources are never deleted.

Duplicate verification covers the native pair and adjacent companion files,
not independently inventoried external resources beyond that directory. It
cannot assess scientific/semantic equivalence or completeness of linked data.
Parent-relative external references and disconnected dependencies may still
need separate deliberate trials. Use a separate campaign with specific roots
to exercise such copies individually.

## Space, stopping and recovery

Before each project, the default admission estimate requires **50 GiB reserve
plus three times the native-pair size**. This is a heuristic, not a complete
forecast: compressed seismic expansion and external/companion data can exceed
it. Configure `reserve_free_gib` and `space_multiplier`; the extractor also has
its own conversion-space checks. If neither output can accommodate a project,
it is marked `deferred_space`; smaller eligible projects can continue. No outputs
are removed to make space. A network outage can still interrupt filesystem
operations; reconnection/resume inside a half-written dataset is not promised.

To stop cleanly after the current project, create an empty file named
`STOP_AFTER_CURRENT.txt` in the campaign directory. Remove it yourself before
resuming. Ctrl+C requests child-process cancellation and retains available
diagnostics; a child that does not stop within 20 seconds is terminated with its
own process tree. Forced termination/power loss can leave incomplete logs.

Queue resume skips prior outcomes unless `-RetryFailed` is explicit. An
interrupted attempt is marked accordingly; retry starts a new extraction, not
an in-place dataset resume. Changed native sizes/times/file lists require a new
scan. Interrupted discovery also requires a new scan and preserves partial
findings. Project timeouts are off by default; `timeout_hours` optionally sets
a real elapsed-time limit, not a guessed no-output stall threshold.
The child PID is checkpointed; a surviving child blocks resuming execution to
avoid overlapping a prior extraction. A reused PID may conservatively block a
resume as well; the message identifies it for inspection.

## Configuration

Edit `GeoViewerBatch\campaign_config.json` before creating a campaign. Examples:
set `scan_connected_drives` to false and `extra_roots` to specific directories
for a controlled subset; add paths in `exclude_roots` to omit backups. The saved
campaign retains its configuration on resume. No arbitrary native version
support is inferred from successful filesystem access or a green process exit.
`diagnostic_sample_seconds` defaults to 10; it controls background process-counter
sampling, not extraction or hashing. See the diagnostics guide for interpretation.

Validation uses synthetic project trees and fake extraction children, including
failures, completion with gaps, duplicates, changed source, insufficient space,
log collection and queue restart. The all-drive campaign remains a user-started
operation; no full-drive trial is launched during development.
