---
name: petrel-crs-drive-audit
description: Audit stored CRS and datum metadata across Petrel projects on a drive or folder, using Field Viewer's bundled CRS-only tool and producing a comparison CSV without opening the GUI or Petrel.
---

# Petrel CRS and datum drive audit

Use this skill for requests such as: "Audit the Petrel projects on E: and give me a CRS and datum comparison table."

## Locate the package

Use the user's extracted Field Viewer beta 3 (or later) package. Require
`FieldViewer_CRS_Audit.ps1` and `FieldViewer/scripts/fieldviewer_crs_batch.py`.
Call the helper `scripts/audit.ps1` beside this skill. It searches its parent
directories for the complete package, so extracting or moving the package does
not require editing paths. When this skill is copied into a separate harness
skills folder, pass `-PackageRoot 'C:\FieldViewer'` or set `FIELDVIEWER_HOME` to
the actual extracted package directory. The helper validates the launcher,
backend and toolkit metadata, and fails explicitly if they are missing. It does
not search arbitrary drives or silently use a different installation.
The harness needs local process/file access; the skill itself grants none.

## Run the audit

Get the drive/folder and output location from the request. Use absolute paths;
`E:\` means a drive root, while `E:` is drive-relative. Choose a new campaign
directory outside all source project folders, preferably on another drive for a
whole-drive audit. Its parent must already exist. Do not request passwords or
change access permissions for inaccessible folders.

Run the supplied commands through a local shell tool, substituting the actual
paths. Replace `<skill-folder>` with this skill's real directory; the helper
locates the colocated package automatically. These PowerShell commands use the
bundled runtime and CRS-only backend:

```powershell
& '<skill-folder>\scripts\audit.ps1' -Action discover -ScanRoot 'E:\' -CampaignDirectory 'C:\FV_Audits\DriveE_001'
& '<skill-folder>\scripts\audit.ps1' -Action run -CampaignDirectory 'C:\FV_Audits\DriveE_001'
& '<skill-folder>\scripts\audit.ps1' -Action status -CampaignDirectory 'C:\FV_Audits\DriveE_001'
```

If executing via `powershell.exe`, use `-NoProfile -NonInteractive -ExecutionPolicy Bypass -File` before the launcher path. The launcher bootstraps/checks the bundled dependencies. No external Python, Petrel, cloud service, API key or GUI automation is needed. Use `-MaxProjects 1` on `run` for a bounded first inspection when appropriate; omit it for the complete selected audit.

To check package discovery without initializing the runtime or scanning data,
use the helper with `-Action status -CampaignDirectory 'C:\FV_Audits\DriveE_001'
-LocateOnly`. It returns the resolved package and launcher as JSON.

Discovery records same-directory, same-stem PET/PTD pairs, missing pairs, orphan
stores and inaccessible paths. It skips links/reparse points and the interiors
of native PTD stores; archives and linked external projects are outside this
search. It does not silently remove repeated backup paths.

The runner invokes `fieldviewer_crs.py` serially. Do not replace it with the
full extractor, click the GUI for each project, or ask the model to decode bytes.
The campaign records its discovered selection and exact tool hashes.

## Completion, interruption and evidence

- Exit 0: the selected audit has no recorded gaps. Exit 10: discovery/pending
  work, metadata gaps or search exclusions exist; inspect `SUMMARY.json`.
  Exit 2: required action; inspect the retained project stdout/stderr and receipts.
- Resume by issuing `run` for the same campaign. Registered results are verified
  and skipped. Failed projects are retained rather than automatically retried.
  Changed sources/tool code, altered receipts or incomplete prior project folders
  require investigation. Preserve that campaign and create a fresh one after
  resolving the cause. Never delete an active/stale lock without establishing
  that its owner and any child extraction have stopped.
- Keep `RESULTS.csv`, `SUMMARY.json`, `DISCOVERY.json`, `BINDING.json` and the
  `projects` directory together. Report project counts by actual status and list
  discovery exclusions. Do not present pending, failed or unreadable projects as
  having no CRS. Summarize the CSV; do not manually regenerate its metadata.
- Results are stored declarations, not independent coordinate correctness or a
  verified active vertical datum. Preserve multiple CRS/datum records and raw
  units/tags, including zero. Never guess EPSG, infer units, apply a transform or
  choose a datum from its name alone. Follow per-project JSON evidence for detail.
- Source PET/Model metadata is hashed before/after inspection; this is not a
  full-project integrity audit. No Data.ptd/seismic extraction, native writes,
  Petrel launch, network upload or publication is performed.

Finish with a short status summary and links to the CSV, discovery and detailed
receipts. Keep healthy progress quiet. This is an integration example; model
compatibility and unattended operation require testing in the chosen harness.
