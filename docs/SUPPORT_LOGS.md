# Support diagnostics — 1.0.0

Website: https://saherlabs.dev/  
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor

Full seismic SHA-256 defaults to **Yes** for both `.pet` extraction and direct `.zgy` conversion. Enter accepts Yes. Choose `n` interactively or add `-NoFullHash` to the main BAT to skip those full-file reads. `-FullHash` remains supported. Hashing and complete numerical conversion QC are independent.

After a successful, partial, failed or normally cancelled run, send **one `*_SUPPORT.zip`** beside the report. The console prints its path, and the report links to it. Compression is offline; nothing is uploaded. The original readable/structured logs remain available locally.

The ZIP contains a README, environment summary, contents/redaction manifest and diagnostic files: startup/dependency checks; run options; application, Python, OS and dependency versions; disk capacity; stage timing; child commands, stdout/stderr and exit codes; object outcomes, retry evidence, OS error codes and tracebacks; extraction/QC/conversion receipts; rotated logs and fallback logs. Log files are streamed into ZIP compression. The collector does not hash or read the project payloads again.

Only allowlisted diagnostic filenames are collected. Native stores, converted datasets, sample arrays, report figures, screenshots and workflow definitions are excluded. Recognized names, paths, UUIDs and explicit credential fields are replaced in the sharing copy, consistently within that ZIP. No reverse mapping or complete environment-variable dump is included. **This is best-effort replacement, not guaranteed anonymity:** inspect the sharing copy before sending it; unfamiliar free text can contain sensitive details.

If a failure occurs before the report/run folder exists, the launcher prints the ZIP path in a temporary diagnostics folder. If managed Python cannot start, Windows/.NET creates a **startup-only, unredacted** ZIP and labels it accordingly. Review that archive before sharing. If output storage fails, ZIP creation attempts a temporary-folder fallback. Diagnostic failures do not turn accepted extraction output into a failure.

Closing the console forcibly, killing the process, power loss, or failure of both normal and temporary storage can prevent final packaging. The startup session folder and original logs are retained. A developer can rebuild a ZIP from a retained session using the bundled runtime:

```powershell
GeoViewer\runtime\python.exe -B GeoViewer\scripts\geoviewer_support.py --session "C:\path\to\GeoViewer_support_session"
```

Choose a session without an existing `SUPPORT.zip`, or preserve/rename the previous archive first; the builder does not overwrite it. A rebuilt bundle reflects the available partial evidence, not a resumed extraction.

Version remains **1.0.0** at the author's request. `build_revision` and package SHA-256 distinguish this updated distribution from earlier downloads bearing the same version. Download/extract the whole updated ZIP into a new folder.
