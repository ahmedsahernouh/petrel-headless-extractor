# v0.6.2 — Recover native polygon segments correctly

Fixes polygon coordinates and segments lost by the old byte-marker decoder. The v0.6.1 report-ordering fix could not restore geometry already lost during extraction.

- Decode length-framed, typed native polygon arrays; preserve original segments, vertex order, missing positions and closure flags.
- Export explicit segment keys and native closure metadata in the polygon CSV.
- Select a named polygon object in the report to inspect its separate outlines. Spread preview sampling across segments instead of dropping later objects.
- Keep attached polygon properties, unsupported profiles and unresolved CRS/units explicitly separate from decoded geometry.

Validated against an independent Microsoft binary XML reader on 150 supplied-project objects, plus regression and standalone BAT checks. Private project data is excluded from the release. See [the polygon profile and CSV contract](NATIVE_POLYGONS.md).

Download `PetrelExtractor-0.6.2-win64.zip`, extract it, and run the main `run_portable_petrel_extract.bat`. Previous polygon CSVs need native re-extraction; simply reopening an old report cannot recover missing segments. The report remains mandatory, conversion defaults on, and full seismic hashing defaults off.
