# GeoViewer_data_extractor 0.8.1

[Website](https://saherlabs.dev/) · [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

This maintenance update addresses isolated Windows output failures, process
diagnostics and dense spatial maps. Native decoding profiles remain those tested
for 0.8.0, including the observed Petrel 2024.5.0 project. This is not a claim of
complete support for every project from that release.

## Run and find the result

Extract the complete ZIP into a short writable directory. Run the top-level
`GeoViewer_data_extractor.bat` beside the `GeoViewer` support folder. The BAT
checks and repairs its bundled dependencies offline. Supply the exact `.pet`
path with its matching `.ptd` directory, or an exact `.zgy` file.

The report and complete inventory are always included. Conversion defaults to
Yes; full seismic SHA-256 defaults to No. These choices remain independent.

Open `<run>_REPORT.html` in the selected output root first. Its **Open the
extracted data** section links accepted files and the sibling `_EXPORTS` folder.
The native project inventory remains a separate tree.

For a problem, open the sibling **`<run>_DIAGNOSTICS.txt`**. It summarizes run and
object outcomes and includes the object, operation, Windows error, attempts and
paths when available. The report links this summary, the full `_LOG.txt`, and
structured `_EVENTS.jsonl`. `RUN_RESULT.json` is in the sibling `_data` folder.
If normal log storage fails, a notice identifies a local temporary diagnostics
directory. Bootstrap diagnostics remain in the application support folder.

Local logs can contain private project names, paths and error details. Nothing
is uploaded automatically. A reviewed, redacted support-bundle workflow is
deferred; inspect any material before sharing it.

## What happens when an object fails

- A Windows access or sharing denial during output finalization gets bounded
  retries of the same validated staging data: 0.2, 0.5, 1, 2 and 4 seconds.
  Retry waiting is capped at 30 seconds per worker process.
- A persistent isolated failure retains the pending data and a precise object
  outcome. Other objects and independent categories continue. Repeated I/O
  failures trigger a fresh output write/rename probe.
- A failed optional LAS, ZMAP or grid-preview file does not invalidate the
  independently verified CSV/ASCII data. Each format's result stays explicit.
- Pending files never become accepted converted exports. Existing dataset
  destinations are not silently replaced. Source projects remain read-only.
- Missing optional figures or report updates preserve prior useful report
  content and are reported as gaps. If an entire visual report fails, a basic
  metadata report and accepted-file links are still attempted.
- Shared storage failure, confirmed source-integrity failure, cancellation or
  inability to produce trustworthy receipts stops the affected run honestly.
  Successful earlier work is retained, without claiming that uncompleted
  checks passed.

Console, report and `RUN_RESULT.json` distinguish **completed** from
**completed with gaps**. Exit codes are `0` for completion, `10` for completed
with gaps, `1` for a fatal run failure, and `130` for cancellation handled by
the Python launcher. Bootstrap/argument validation may return their own nonzero
codes. Missing external seismic or unsupported requested native data can cause
code 10 even when the available data was converted correctly. Package checksum
QC passing does not mean every geological object was recovered.

## Spatial map

Well and point diameters stay constant in screen pixels while zooming, fitting,
panning or resizing. Separate diameter and opacity sliders, optional well
labels, a well selector, Fit wells, Fit selected well, Fit visible layers and
Fit all make dense maps easier to inspect. Clicking an overlap lists nearby
wells individually. Positions are never jittered or altered to separate them.
Grid labels and well labels retain readable screen sizes.

Polygon object/segment identities and original vertex order remain intact.
Point clouds and polygon figures remain bounded previews; exports preserve the
full accepted data. Native XY coordinates are not automatically a known CRS.

## Remaining work

Durable object journals and commit receipts are now written. Full interrupted-run
resume, cross-run checkpoint reconciliation, a manual support bundle and broader
logging performance/rotation acceptance remain follow-on work under NEXT-25.
There is no user-facing resume command in this release. Keep interrupted
outputs for diagnosis and start a new run when retrying.

Complete 3D RESCUE export, general fault topology and unobserved native profiles
remain separate development work. See [the RESCUE plan](RESCUE_EXPORT_PLAN.md).
