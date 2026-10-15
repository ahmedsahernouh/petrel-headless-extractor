# Find the report and extracted files

Each report-generating extraction creates an `OPEN_RESULTS_<run-id>.html` page directly in the selected output folder. Open it for prominent **Open report** and **Open exported files** links. Keep the page, report, `_EXPORTS` and `_data` folders together when moving results; the page uses relative links. Desktop shortcuts point to the original locations; use the portable page after moving a run.

On Windows, the extractor also creates clearly named, per-run shortcuts on the current user's desktop. The report shortcut is created as soon as the initial report is available. The extracted-files shortcut is added once the run has actual published exports. Completed runs, runs with gaps and retained failure reports use the same access route and display their actual status.

Report-only runs and runs without published files do not advertise an extracted-files shortcut. If a desktop is unavailable or cannot be written, the extraction continues and prints a warning plus the local report/results location. No administrator access is required. Existing unrelated desktop files are preserved.

Read the report's object outcomes, integrity checks and unresolved units/CRS/datum. A failed or partial run's report remains labelled accordingly.
