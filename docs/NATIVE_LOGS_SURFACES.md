> Current release: [GeoViewer_data_extractor 0.8.0](GEOVIEWER_0_8.md) supersedes the historical time-only seismic restriction and adds observed 2024.5 profiles, project identity, diagnostics and a shallow export index.

# Native well logs and surfaces — v0.7.0 beta

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

The normal standalone BAT now attempts native well-log and surface recovery in `convert` mode. It reads the preserved `.pet`, `Model.ptd` and SQLite `Data.ptd` inside the new extraction package. It requires no Petrel, Ocean, .NET binary reader, external installation or internet connection at runtime. NumPy and lasio are already bundled.

## Using the outputs

Run `GeoViewer_data_extractor.bat`, enter the exact `.pet` path, and let extraction finish. In `PROJECT_REPORT.html`, check **Extraction coverage** and the **Native log and surface recovery** evidence link.

Under the extraction package, open:

```text
07_workflows_reports/native_recovery/native_recovery_report.json
native_data/
  <short_name_and_full_object_uuid>/
    metadata.json
    samples.csv + curve.las     (continuous log)
    samples.csv                 (categorical/boundary log)
    nodes.csv + surface.xyz             (surface numeric nodes)
    surface.zmap                       (regular grid only)
    node_definitions.csv + cell_definitions.csv (packed-mask profile)
    cells.csv                          (earlier full-table profile)
    grid_preview.npz                   (bounded report grid)
```

Each continuous curve has its own LAS file. This preserves different original MD positions and avoids combining logs by interpolation. LAS `STEP=0` indicates irregular sampling. Some receiving applications require regular spacing; use the accompanying CSV when they cannot read irregular LAS.

Log CSV columns are `sample_index,md,raw_value,is_null`. Filter `is_null=1` when using values; the raw native sentinel is retained for traceability. LAS converts those sentinels to the declared LAS NULL value. Categorical logs retain integer codes and original boundary positions in CSV. Category labels and interval boundary direction are not decoded, so categorical records are not silently resampled into LAS.

Surface XYZ files contain `X Y VALUE`, with a comment header. The third column retains the native numbers and sign: it can represent time, depth, velocity or another surface attribute, not necessarily elevation. Native storage units are not inferred from project display units or template labels. Unresolved units remain null in metadata and explicitly labelled in figures.

For the older packed-mask profile, `nodes.csv` contains only usable defined nodes with their **original** `node_index,i,j` (no renumbering). Complete native masks are run-length ASCII tables with `start_index,length,defined`; expand each run to recover every original node/cell flag. `usable_node_definitions.csv` is also included when numeric nulls exclude a node marked defined in the native mask. The earlier bitmask profile retains its complete `nodes.csv` and `cells.csv` tables. Neither encoding infers triangulation or fault connections.

Regular grids also export `surface.zmap`. Header bounds are native **node coordinates**, data columns increase in X and rows decrease in Y. With GDAL, set `ZMAP_PIXEL_IS_POINT=TRUE` to avoid a half-cell registration shift. The ZMAP null represents undefined/unusable nodes; independent cell exclusions remain in the cell-definition CSV. Explicit `ValGrid2` XYZ meshes are not assumed regular and therefore have no ZMAP export. See the [GDAL ZMAP documentation](https://gdal.org/en/stable/drivers/raster/zmap.html) and its linked [ASCII layout reference](https://lists.osgeo.org/pipermail/gdal-dev/2011-June/029173.html).

Read `metadata.json` for units, measurement, parent object/well IDs, source sign convention and validation. CRS remains explicitly unresolved. Files in a directory ending `.partial` failed before completion and are not valid exports.

Artifact paths in the recovery report are relative to `native_data`. Short folder names retain all 128 bits of the object UUID. The exporter rejects paths that would exceed its Windows path budget; choose a short output root such as `C:\PetrelOut` if necessary.

In the v0.6.0 layout, the full location is `<output root>/<run>_data/extraction/package/<package>/native_data/<curve folder>/`. The top-level report links to available curve files in the object catalogue. Figure images and their source links are under the **Well logs** figure filter, with a default budget of 64 log figures; every discovered log remains in the inventory. Report-only mode discards temporary LAS/CSV datasets after generating supported previews. If project/model metadata cannot be read, logs may remain `missing_metadata` with no exported samples or plot; changing the plot settings cannot resolve that decoding gap.

The `native_data` folder can be absent even when conversion was selected: if every native log/surface is blocked, no datasets are written and empty-folder cleanup removes it. Inspect `07_workflows_reports/native_recovery/native_recovery_report.json` for the actual per-object outcomes. Successful report/package QC does not mean native logs were exported.

## Supported profiles

- Exact observed Petrel LZ4-v1 and BXML-v1 containers; BXML data bodies use Microsoft's NBFX binary XML records. A bounded parser follows declared block lengths and rejects unsupported structures. Serialized `Type`/`Ref` values never execute code.
- v0.6.1 also reads the observed multi-block stream: one `LZ4\x01` magic, followed by independent compressed blocks with eight-byte length/opaque headers. BXML can cross block boundaries. Total output, block count, every length and each block's match offsets are bounded. Truncation, trailing garbage and concatenated complete envelopes remain errors. The opaque header word is not presented as a verified checksum.
- `FloatWellLog` and character-encoded `IntWellLog`, object version `1 3 0 2 0 1`, with measured-depth arrays and zero `min_index`. Native float32 maximum and character value 255 are the validated missing-value encodings.
- LAS requires an increasing continuous float log and resolved units. The unit profile is the observed non-customized Metric project (`m`, `m`, `ms`) with predefined unit templates. Other unit systems are not guessed; recoverable log values can still be written to CSV with `missing_metadata` status.
- `RegValGrid2`, version `1 1 1 0 0 0 0 2 0 1 1`: explicit coordinate context, positive increments, zero rotation/dip/axis-flip, no connections or segments, matching dimensions/extents and node masks. Values use X/I-fastest ordering. An attribute without its own validated coordinate context is rejected.
- v0.7.0 adds `RegValGrid2` version `0 1 1 0 0 0 0 2 0 1 1`, which has no axis-flip field. The same positive-increment and zero-rotation/dip gates apply. A false coordinate-context flag is permitted only when independently stored model bounds match the reconstructed defined XYZ range exactly. Node/cell masks support the observed `Size/bools` layout and its checked padding field. The maximum is 10 million node positions per grid; larger or unrecognized layouts remain explicit failures.
- `ValGrid2`, version `0 0 0 0 0 2 0 1 1`: direct `SurfaceSubject` float64 XYZ triples. Attribute geometry inheritance is not implemented. Direct surface bounds must match the separate native model bounds.

For `SurfaceSubject` model version `9 2 13 1 0 1 18 0 0 1`, the stored `limit` can enclose a smaller defined grid. Exact equality, containment and an explicitly undefined cache have separate recorded outcomes. The older `cached_limit` profile still requires exact bounds. Undefined caches do not count as independent geometry confirmation. Recoverable grids with unresolved units are exported with `missing_metadata`, plus explicit `dataset_exported=true` and verified artifact records. The report counts these numeric grid exports separately from unit-resolved decoded objects.

Source files are hashed before and after recovery. Every completed CSV, LAS, XYZ and ZMAP is read back and checked against the decoded values. Outputs preserve float64 values with 17 significant digits (ZMAP scientific notation retains at least that precision). Data writing and readback use bounded chunks; large ASCII exports can be much larger than native binaries. Only successful, unit-resolved objects from an unchanged snapshot contribute to the strict decoded count. Numeric grid exports with unresolved units have their own count. Empty objects, rejected layouts and failed conversions remain separate. A completed extraction can contain partial native recovery.

## Validation and remaining work

For v0.7.0, Microsoft's independent `XmlDictionaryReader` agreed byte for byte with every numeric array and definition-mask byte from **213 supplied-project grids**: 188 regular grids and 25 explicit XYZ meshes. They contain 527,623,365 node positions, of which 253,830,417 are usable defined nodes. This validates binary array reading; it does not resolve native units. The independent `zmapio` reader also reproduced values, nulls and XY registration from five exported regular grids containing 18,461,876 node positions, including two 7,339,605-node grids. Full-run export/report totals and standalone acceptance are recorded in the release's `VALIDATION.json`; private grids and coordinates are not distributed.

The maps use selected original nodes and conservative native gap masks, with statistics from the complete decoded grid. They are previews; the ASCII files retain full resolution. A per-grid space check rejects an insufficient output drive before writing that grid's datasets. Source files remain unchanged and previous extraction packages are not overwritten.

The v0.6.1 fix was checked on a local project whose Model container had two blocks expanding to 8,388,608 and 7,583,786 bytes. The separate C decoder in python-lz4 4.4.5 agreed byte for byte with both blocks and the combined production output; all 12,408 BXML documents parsed. The same bounded reader serves model, native object and spatial extraction.

On that project, the fix recovered **1,008 native log CSV files with 14,561,521 sample/boundary records**; two logs were empty. All CSV values passed exact read-back checks and preserved project/model/database hashes were unchanged. The project uses Field-UTM display units, outside the validated native unit profile: the recovered numeric CSVs and up to 64 report tracks explicitly retain unknown units, and no LAS files are claimed. All 161 surface records still failed their object-version gate. These results validate this compression layout, not new unit semantics or all Petrel versions. Source values and project identities stay local.

A private demo saved in Petrel 2018.2, originating from a Petrel 2010 demo, contained 241 log and 59 surface records. The current profile recovered:

| Source | Result |
|---|---|
| 185 float logs | 179 LAS + CSV; 6 empty |
| 56 integer logs | 54 CSV; 2 empty. Of the 54, 39 have resolved units and 15 retain `missing_metadata`. |
| 46 regular surface records | 31 XYZ/CSV; 15 blocked by geometry or units |
| 13 explicit-grid records | 4 XYZ/CSV; 9 blocked by units or attribute geometry inheritance |

That is 201,956 log sample/boundary records and 781,990 defined surface nodes written. The strict decoded-object count is 253, plus 8 empty and 39 unresolved objects. These counts describe this test corpus only.

Independent checks used Microsoft's `XmlDictionaryReader` on all 300 object bodies and 1,564,260 numeric entries. Float32 values matched exactly; float64 comparisons allow the independent XML text formatter's two-ULP rounding. Separately, 161 curve-unit checks and 99 values at matching native MD positions agreed with 18 existing Petrel-authored LAS exports. All 233 non-empty native log min/max pairs agreed with Model metadata after excluding missing values. The LAS references have sparse/resampled rows; they do not validate every native sample.

Four separate course reference grids support the regular-grid ordering and georeferencing: correlation 0.9953–0.9993, RMS difference 2.63–11.15 ms after comparing observed opposite time signs. They are different grid realizations, not exact-value ground truth. Native source signs are preserved in the delivered outputs. Source-data values remain private; only aggregate evidence and synthetic tests are distributed.

Still unresolved or unsupported: native unit semantics outside the validated Metric profile (numeric ASCII may still be exported), additional object versions, nonzero log base encodings, categorical label/interval interpretation, inherited/rotated surface geometry, connection/segment topology, general `.zhz` tiles, image logs, faults, 3D pillar grids and property arrays. No Petrel re-import, cross-version support or whole-terabyte project completion is claimed. GeoTIFF is not included. The surface/grid support here means 2D grids and explicit XYZ meshes, not general reservoir models.

## Implementation references

- [Microsoft NBFX record specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx/e17683ce-cb4c-4968-bd7a-ebfe5cc18a1e) and [ArrayRecord](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx/e0cd55a8-016d-4bb7-924e-a8b7add5d52b). Petrel's outer framing was established from local samples; it is not claimed to be an SLB-published format specification.
- [lasio writer documentation](https://lasio.readthedocs.io/en/latest/_modules/lasio/writer.html) for LAS serialization and explicit sampling headers.

For a previously preserved package, the source command is `python scripts/petrel_native_recovery.py --export-package <package>`. It creates a new recovery directory and refuses to overwrite an existing one. Use a fresh extraction package for a retry.
