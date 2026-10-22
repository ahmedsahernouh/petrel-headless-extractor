# Agent example: audit a drive for Petrel CRS and datum metadata

Field Viewer beta 3 includes `skills/petrel-crs-drive-audit/SKILL.md` and a
deterministic batch runner in both Desktop and Headless packages. This example
connects an agent to the same CRS-only backend used by the GUI.

Example request:

> Audit the Petrel projects on E:\ and create a CRS and datum comparison CSV in C:\FV_Audits\DriveE_001.

Extract the whole package. Give a local agent the packaged `SKILL.md`, or copy
the `petrel-crs-drive-audit` folder into its supported skill directory. The host
must provide local shell/file tools. A hosted chat without access to the drive
cannot perform this task merely by reading a skill. No model provider, API key
or local model installation is supplied or required by the package.

The skill helper `skills/petrel-crs-drive-audit/scripts/audit.ps1` finds the
package relative to itself. A relocated whole package works without edits. If
the skill is installed separately, supply `-PackageRoot` or set the optional
`FIELDVIEWER_HOME` environment variable to the package root. No drive-wide search
for an executable is performed. Use `-LocateOnly` with the helper to verify the
resolved location without scanning or starting the runtime.

Direct use without an agent is also available:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File 'C:\FieldViewer\FieldViewer_CRS_Audit.ps1' -Action discover -ScanRoot 'E:\' -CampaignDirectory 'C:\FV_Audits\DriveE_001'
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File 'C:\FieldViewer\FieldViewer_CRS_Audit.ps1' -Action run -CampaignDirectory 'C:\FV_Audits\DriveE_001'
```

Create the output parent first. Use a new campaign folder outside the source
project directories. The first command discovers projects; the second runs the
frozen selection. `-Action status` regenerates its summary. `-MaxProjects 1` on
`run` limits a first inspection; another `run` resumes remaining projects.

The launcher prepares/checks the bundled Python dependencies. The runner calls
`FieldViewer/scripts/fieldviewer_crs.py` directly, serially. Discovery lists
missing pairs, orphan stores and inaccessible paths; it does not traverse links,
junctions, native PTD contents, archives or external references. Every found PET
has a CSV row, including unresolved or failed outcomes. Repeated backup paths
remain separate. Discovery exclusions are retained in `DISCOVERY.json`.

Keep `RESULTS.csv`, `SUMMARY.json`, discovery/binding files and the `projects`
directory together. CSV cells containing collections use JSON so multiple CRS
or datum records remain distinguishable. Original project metadata is never
replaced. Source PET/Model files are hashed around each inspection, and source
hashes are checked when resuming. This is not a whole-project checksum audit.

A failed project stops for review, retains stdout/stderr and is not blindly
retried. Interrupted project folders, active locks, altered receipts or changed
sources/tool code require investigation. The model should summarize these
states rather than infer missing metadata. No CRS transformation, automatic
datum selection, physical coordinate validation or Petrel launch occurs.

This is a portable integration example. Synthetic/package tests establish the
runner's behavior; no particular cloud/local model or agent harness has been
qualified by those tests.
