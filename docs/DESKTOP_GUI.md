# Field Viewer Desktop — development candidate

The optional Desktop interface runs the same CLI engine used by applications and
AI agents. It adds no decoder, conversion, unit override, service or account.
RC1009 remains the current public download. The GUI has no assigned release
version and is not yet an independently accepted clean-Windows release.

## Open and run

Extract the **whole Desktop ZIP** and open `FieldViewer_Desktop.bat`. Keep the
`desktop` and `FieldViewer` folders beside it. The original
`FieldViewer_data_extractor.bat` stays available with its existing CLI arguments.

Select the exact `.pet` file with its matching same-stem `.ptd` directory, then an
existing output folder outside the source project. Prefer a short path such as
`C:\FV`. Long destinations are rejected before launch so temporary assessment
paths can fit the Windows profile. Choose Full extraction or Report only.
The GUI creates a compact `FV_<ID>` run folder under that destination;
the full destination is shown in the session. Sources remain read-only.

Desktop requires an interactive Windows session. For SSH, services and agent
automation, use the headless BAT or CLI. The PowerShell entry point and synchronous
`--smoke` check return failure codes on startup errors. The normal BAT launches
the window asynchronously; its return code only records dispatch. Absence of a
rendered window or report is never a successful GUI test.

The existing launcher performs its normal offline dependency checks and enforces
the engine's admission and integrity rules. Full hashing and run-to-completion
stay enabled. Report only retains the existing report/inventory/preview scope;
it is not a promise of zero temporary work.

The top rows open this run's available reports, QC, diagnostics, log and extracted
data folders. Missing/not-yet-generated destinations stay unavailable. Reports
and files remain usable independently of the GUI.

## Progress and lifecycle

Elapsed time and the latest recorded activity are separate indicators. A stage
percentage appears only for compatible measured counters. There is no invented
overall percentage or ETA; stage position and output growth do not measure native
recovery. Completed with gaps remains distinct from fully completed execution.
Elapsed time includes launcher/bootstrap time. Event polling reads at most
128 KiB per tick; oversized/malformed display records are skipped without
changing the engine's original logs. Polling can lag a large event backlog.

Size observations are bounded, asynchronous snapshots. Input means the exact
selected PET/PTD pair; external references remain outside that count. Output for
this run is separate from the selected folder's total. Logical path sums can
include hard-link aliases and are not allocated disk usage or reclaimable space.
Partial/unavailable scans remain labelled.

The backend runs independently with persistent logs. Closing/crashing the GUI
does not cancel extraction. Reopening observes the recorded session only after
checking process creation time and command line. It never resumes or restarts a
stale PID. There is no new Cancel, Pause or Resume action.
An ambiguous process or a supervisor failure after launch blocks new work in
that GUI session; inspect the preserved session and logs before manual recovery.
Receipts larger than the GUI's 32 MiB reader limit remain available on disk and
are explicitly directed to manual review.

## Diagnostic email

Email last log prepares a closed sharing copy from the exact run, with an optional
explanation. Prefer the successfully collected redacted log in the existing
support bundle. If that mapping is unavailable, the interface explicitly asks
before preparing an **unredacted** copy. Running snapshots are incomplete.
Review the recipient, attachment, paths and free text before sharing.

A compatible Windows mail client opens a composition dialog. Otherwise the GUI
offers an addressed draft and reveals the local attachment for manual addition.
`mailto:` does not attach files. No background sending, SMTP password, automatic
upload or project/data attachment is added. Mail-client acceptance does not prove
delivery. Email problems do not change extraction results.

## One engine, two editions

- **Desktop:** GUI plus the complete Headless tools.
- **Headless:** the same engine and CLI, without GUI files or a GUI dependency.

`EDITION.json` identifies the edition, GUI revision and pinned engine archive.
`SHARED_ENGINE_MANIFEST.json` binds the unchanged baseline; the outer
`PACKAGE_MANIFEST.json` covers each edition's members. Each ZIP has its own
checksum. Historical engine metadata can differ from the outer candidate label;
the About dialog exposes both instead of changing engine files for branding.

The build command takes the published baseline explicitly:

```powershell
python tools/build_desktop_editions.py --engine-zip PATH_TO_PUBLIC_RC1009_ZIP --expected-sha256 e17674c71c9a64bb93d3f883c2ab247d5d109e4d273837042159cfe9f24a0a23 --output FRESH_BUILD_DIRECTORY --gui-revision preview-1 --source-revision CURRENT_COMMIT
```

This builder verifies archive parity. It does not replace installation, UI,
mail-client compatibility, DPI or independent clean-Windows acceptance. GUI
candidate publication requires a separate release decision.
