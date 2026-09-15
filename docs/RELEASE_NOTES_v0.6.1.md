# v0.6.1 — Multi-block native metadata and polygon segments

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Fixes `LZ4 envelope size mismatch` when a supported `Model.ptd` stores its metadata in multiple compressed blocks. The previous reader expected a single block. The new reader follows every declared block length, resets match history between blocks and checks the complete BXML stream. Malformed input still produces an explicit failure for the affected data.

In a local project test, this recovered 1,008 native well-log CSV files containing 14,561,521 sample/boundary records; two logs were empty. CSV values passed exact read-back checks, and source hashes remained unchanged. The report displayed 64 log plots and all 5,594 inventory nodes, with no broken links or browser script errors. These results describe this test project only.

The project's native unit semantics remain unverified: CSV and plot labels explicitly retain unresolved units, and LAS is not produced. Its 161 surface records still use an unsupported object version. The fix does not claim support for every Petrel version, field-unit LAS exports or additional surface geometry.

Polygon previews also preserve object/part/segment identity and vertex order, splitting at gaps or invalid vertices before display sampling.

Use the new standalone ZIP in a fresh folder and run its single main BAT. Keep dataset conversion selected to retain the CSVs. Seismic hashing remains optional and off by default. The full report and foldable inventory are always included, and the report remains directly beside the run's data folder. Existing outputs are not overwritten.

Validation includes an independent C LZ4 decoder comparison for both real metadata blocks, 107 focused regression tests, and relocated actual-BAT acceptance. The release's `VALIDATION.json` records the exact archive hash and completed acceptance checks. No private project files or data values are included in the download.
