# Field Viewer dedicated testing runner — engine 1.0.0

[Website](https://saherlabs.dev/) · [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

This separate development add-on is not included in the public standalone ZIP.
The goal is to learn which native data the extractor can recover, where it fails,
and which work dominates elapsed time. A thousand discovered paths are not a
thousand distinct or equally useful tests. This add-on prepares controlled test
waves from the existing crawler catalogue, keeping the released decoder at 1.0.0.

## Delivered workflow

1. **Prepare:** drag the crawler's `CAMPAIGN.json` onto
   `GeoViewer_prepare_tests.bat`. This reads saved inventories and duplicate
   decisions only; it does not read project payloads, scan drives or hash ZIPs.
   It creates a new, independent testing plan and prints `TEST_PLAN.html`.
2. **Profile:** run `GeoViewer_profile_tests.bat`. Up to 32 diverse cases receive
   metadata inspection: recorded Petrel version, layout, object inventory and
   native payload-type counts. No data conversion or full seismic hashing occurs
   in this scenario. A profile has a three-minute limit by default.
3. **Extract:** run `GeoViewer_run_tests.bat`. It chooses up to eight previously
   profiled cases using the newly recorded versions/types plus drive, size and
   path diversity. It runs the existing full extractor, report and numerical QC,
   with full seismic hashing **on** by default.
4. **Evaluate:** use `TEST_PLAN.html`, `INDEX.html` and
   `LOGS/ANALYSIS/REPORT.html`; collect `CAMPAIGN_LOGS.zip`. A case is useful even
   when a parser rejects it, but rejection is not successful recovery.

The scripts remember the most recently prepared test plan. `-Plan` selects an
explicit plan, `-MaxProjects` limits this invocation, and `-RetryFailed` explicitly
retries failures in new output folders. Completed cases are skipped. A later
invocation resumes the remaining jobs in the same wave; it does not silently
expand to all projects. To change selection settings or build identity, prepare
a new plan using `-Config` and an explicit JSON configuration.

```powershell
.\GeoViewer_prepare_tests.bat -List "D:\path\Campaign_...\CAMPAIGN.json" -Config "D:\path\test-lab-config.json"
.\GeoViewer_profile_tests.bat -MaxProjects 8
.\GeoViewer_run_tests.bat -MaxProjects 3
# For a reviewed wave whose progressing cases must not be stopped by elapsed time:
.\GeoViewer_run_tests.bat -RunToCompletion
# Collect available evidence without running tests:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\GeoViewerTestLab\launch_geoviewer_test_lab.ps1 -Mode collect -Plan "D:\path\TestLab_..."
```

A minimal configuration is `{"output_roots": ["E:\\GeoViewerTrials"]}`; choose an existing output volume with enough free space. Optional `priority_path_patterns` is an empty list by default.

The add-on sits beside the existing complete `GeoViewer` package:

```text
FieldViewer/                      existing embedded Python and extraction engine
GeoViewer_prepare_tests.bat
GeoViewer_profile_tests.bat
GeoViewer_run_tests.bat
GeoViewerTestLab/                 testing orchestration, plans and diagnostics
```

## Selection and existing crawler ownership

Preparation requires a JSON configuration with explicit `output_roots` and may use an incomplete crawler snapshot. The test plan records the
original `discovery_complete` flag and snapshot time. Incomplete crawls remain
labelled incomplete. Its own queue is separate. Newly discovered projects are not added to a
frozen plan, and the active crawler is never stopped or modified by preparation.

All candidates and reasons remain in `CANDIDATES.json` / `CANDIDATES.csv`:

- Invalid/incomplete native pairs are retained as ineligible, with their reasons.
- Temporary/development fixtures are excluded by explicit, configurable path
  rules, so hundreds of repeated fixtures do not dominate real-project trials.
- Duplicates already confirmed in the crawler's log are retained as excluded
  duplicates. No new full-file duplicate hashes are calculated by this add-on.
- Matching native relative filenames/sizes create **possible-duplicate selection
  groups**. One representative per group enters the initial wave. This is not a
  proof of identity; other copies remain candidates for later deliberate tests.
- A deterministic greedy selection favors new drives, size classes, file evidence,
  unusual paths and any explicitly configured priority paths. A year in a filename is explicitly
  an unverified selection hint, never a Petrel saved-version claim.
- Recorded versions and payload types from profiling guide the extraction wave.
  This is practical diversity sampling, not a statistically representative survey.

No project-specific priority paths are configured by default. If a test wave needs them, supply `priority_path_patterns` in its reviewed configuration. Profiles may find unknown versions; unknowns remain visible and may still enter extraction tests. A profile does not validate any
numerical decoder.

## Avoiding repeated runtime scans

Each active profile/extraction session runs the required toolkit doctor and a full
bundle/import/ZFP preflight **once**. Each case then runs in a new process using
that receipt. It checks the manifest and small code/runtime-entrypoint metadata;
it does not rehash or repair thousands of dependency files per case.

The receipt is tied to the toolkit manifest, machine, Python executable and the
live session owner's PID plus creation time. A changed engine/entrypoint, expired
owner or changed manifest stops the testing session. Restarting the runner starts
a newly verified session. This is a local performance mode: it is not continuous
cryptographic verification of every installed dependency. Do not update the
toolkit or dependencies while a testing session is active. If dependencies need
repair, run the normal main BAT with `--check -NoPause` once before testing.

The worker adapter reuses the released `standalone_petrel_extract.py` and replaces
only its repeated preflight call in that child process. It does not modify the
installed engine files, disable source-integrity checks or remove numerical QC.
Each case records that its preflight was reused and links the session receipt.
Normal single-project BAT behavior remains available for installation and launch
regression tests; those tests are distinct from this warm-runtime campaign.

## Limits and operational behavior

Default settings are in `geoviewer_test_lab.py` and copied into every plan:

| Setting | Default |
| --- | --- |
| Metadata-profile wave | 32 cases, 180 seconds each |
| Full-extraction wave | 8 cases |
| Largest native pair admitted to initial extraction wave | 20 GiB |
| Sum of native pair sizes in extraction wave | 40 GiB |
| Per-extraction timeout | 2 hours |
| Session scheduling budget | 4 hours; stops between cases |
| Output roots | Required in `-Config`; no default drive paths |
| Output reserve | 50 GiB plus 3 × native size for full extraction |
| Seismic source hashing during full extraction | On |
| Concurrency | One case |
| Process-counter sampling | Every 10 seconds |

Native-size limits are **not** hard limits on physical output space. Companion
files, referenced data and seismic expansion can exceed them. Metadata profiling
reserves free space but does not reserve three native copies, because it writes
only diagnostic metadata. A hard per-case time limit kills only that child tree
and records the interruption; the four-hour session limit is checked between
cases and can therefore be exceeded by the last started case.

`-RunToCompletion` is an explicit per-invocation opt-in. The equivalent saved
plan setting is `"run_to_completion": true`. It removes the session, per-case,
portable-extraction, native/companion-stage and seismic-child elapsed-time
deadlines by carrying an unlimited `null` budget through the runner. It does not
change sequential execution, source/output space admission, parser row/cell/file
bounds, diagnostics, retained incremental outputs, the stop file, Ctrl+C/manual
cancellation, or the one-preflight-per-live-session identity checks. The session
receipt records the policy, and later cases remain valid while the owner process,
runtime, manifest and guarded entrypoints retain the same identity. Without the
flag or saved setting, all finite defaults in the table above remain unchanged.

One process at a time avoids turning shared-drive congestion into an apparent
decoder performance failure. Output remains in new attempt folders; failures
retain partial data and logs. No automatic deletion of source or output data.
`STOP_AFTER_CURRENT.txt` in the testing-plan folder stops new cases between jobs.
Only targeted source checks run before cases; no drive-wide rediscovery occurs.
Interrupted jobs need an explicit retry, and a surviving prior child prevents
overlapping execution.

Logs include scenario, selection evidence, source/build identity, version and
types, status, error details, timings, hashing and sampled resource counters.
Metadata-only completion and full conversion results appear as separate case IDs
and scenarios. A compact campaign archive is refreshed after each case and at
session exit; raw logs remain available if packaging fails. Existing diagnostics
privacy and evidence limits apply: names/paths remain in the local campaign ZIP,
with no automatic upload or project/converted payloads included.

## Next optimization experiments and acceptance gates

These are planned work, not delivered capabilities of the adapter:

1. **Narrow companion handling in the engine.** The current extractor still reads
   the selected project's parent lane for non-seismic preservation/integrity. The
   testing adapter intentionally exposes that cost. Introduce explicit native-only
   and referenced-dependency modes with receipts before changing production
   semantics. Avoid arbitrary ZIPs, unrelated descendants and adjacent projects.
2. **Checkpoint duplicate verification in the crawler.** Separate listing complete
   from duplicate verification complete; persist each decision, file identity and
   reusable hash. Do not make the project list wait for broad companion hashing.
3. **Paired benchmarks.** Run the same frozen case/build/storage with full hashes
   on/off or a scoped companion implementation, preserving exact numerical-QC
   equivalence. Record warm/cold caches; never attribute data/drive differences
   to a code speedup. The initial baseline keeps hashing on.
4. **Modern-version coverage.** Prioritize recorded 2023/2024 and newer layouts,
   then compare the same demo project saved in 2010 and 2018. Track type/profile
   outcomes separately; new parser profiles require independent reference exports.
5. **Additional waves.** Explicitly select missing types/versions, projects over
   20 GiB, alternate copies or external-link scenarios. Review recurring signatures
   before spending another full wave on the same unsupported profile.
6. **Controlled robustness fixtures.** Missing native stores, malformed metadata,
   ambiguous IDs, unknown units, long/Unicode paths, process timeout/interruption,
   unavailable output and low space. Simulate destructive/outage conditions on
   fixtures, not by disconnecting a user's live drives.
7. **Storage and resume.** Future work: hard output limits, stage/dataset resume,
   content-identity caches and storage-aware concurrency. Current retry restarts
   one case; it does not resume halfway through a dataset.
8. **Archived projects.** Inventory archive members first, then separately plan
   safe unpacking and space checks. The current crawler/test runner does not find
   or extract projects inside ZIP/RAR/7z files.

Success requires usable open outputs, numerical validation, source preservation,
truthful gaps and enough diagnostic evidence to reproduce failures. Fast completion
or a green process exit alone is not extraction coverage or cross-version support.
# Targeted retries

After fixing a diagnosed failure, use `geoviewer_test_lab.py extract --plan <saved-plan> --case <case-id> --retry-failed --max-projects 1` with the package's embedded Python. The explicit case ID must already exist in that scenario. Other queued cases remain untouched, old attempts are retained, and the retry uses a new numbered output folder. Runtime checks still run once per live runner session. Keep the existing source hash, space and time limits unless an intentional test change is recorded.
