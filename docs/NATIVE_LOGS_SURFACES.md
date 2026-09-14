# Native well logs and surfaces — v0.4.0 beta

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

The normal standalone BAT now attempts native well-log and surface recovery in `convert` mode. It reads the preserved `.pet`, `Model.ptd` and SQLite `Data.ptd` inside the new extraction package. It requires no Petrel, Ocean, .NET binary reader, external installation or internet connection at runtime. NumPy and lasio are already bundled.

## Using the outputs

Run `run_portable_petrel_extract.bat`, enter the exact `.pet` path, and let extraction finish. In `PROJECT_REPORT.html`, check **Extraction coverage** and the **Native log and surface recovery** evidence link.

Under the extraction package, open:

```text
07_workflows_reports/native_recovery/native_recovery_report.json
native_data/
  <short_name_and_full_object_uuid>/
    metadata.json
    samples.csv + curve.las     (continuous log)
    samples.csv                 (categorical/boundary log)
    nodes.csv + surface.xyz + cells.csv  (surface)
```

Each continuous curve has its own LAS file. This preserves different original MD positions and avoids combining logs by interpolation. LAS `STEP=0` indicates irregular sampling. Some receiving applications require regular spacing; use the accompanying CSV when they cannot read irregular LAS.

Log CSV columns are `sample_index,md,raw_value,is_null`. Filter `is_null=1` when using values; the raw native sentinel is retained for traceability. LAS converts those sentinels to the declared LAS NULL value. Categorical logs retain integer codes and original boundary positions in CSV. Category labels and interval boundary direction are not decoded, so categorical records are not silently resampled into LAS.

Surface XYZ files contain `X Y VALUE`, with a comment header. The third column retains the native property's sign, unit and domain: it can be time, depth, velocity or another surface attribute. It is not always elevation. `nodes.csv` includes every node's original value, `i/j` indices and definition flag. `cells.csv` preserves cell definition flags separately. These files do not infer triangulation or fault connections.

Read `metadata.json` for units, measurement, parent object/well IDs, source sign convention and validation. CRS remains explicitly unresolved. Files in a directory ending `.partial` failed before completion and are not valid exports.

Artifact paths in the recovery report are relative to `native_data`. Short folder names retain all 128 bits of the object UUID. The exporter rejects paths that would exceed its Windows path budget; choose a short output root such as `C:\PetrelOut` if necessary.

## Supported profiles

- Exact observed Petrel LZ4-v1 and BXML-v1 containers; BXML data bodies use Microsoft's NBFX binary XML records. A bounded parser follows declared block lengths and rejects unsupported structures. Serialized `Type`/`Ref` values never execute code.
- `FloatWellLog` and character-encoded `IntWellLog`, object version `1 3 0 2 0 1`, with measured-depth arrays and zero `min_index`. Native float32 maximum and character value 255 are the validated missing-value encodings.
- LAS requires an increasing continuous float log and resolved units. The unit profile is the observed non-customized Metric project (`m`, `m`, `ms`) with predefined unit templates. Other unit systems are not guessed; recoverable log values can still be written to CSV with `missing_metadata` status.
- `RegValGrid2`, version `1 1 1 0 0 0 0 2 0 1 1`: explicit coordinate context, positive increments, zero rotation/dip/axis-flip, no connections or segments, matching dimensions/extents and node masks. Values use X/I-fastest ordering. An attribute without its own validated coordinate context is rejected.
- `ValGrid2`, version `0 0 0 0 0 2 0 1 1`: direct `SurfaceSubject` float64 XYZ triples. Attribute geometry inheritance is not implemented. Direct surface bounds must match the separate native model bounds.

Source files are hashed before and after recovery. Every completed CSV, LAS and XYZ is read back and checked against the decoded values. Outputs use 17 significant digits. Only successful, unit-resolved objects from an unchanged snapshot contribute to the report's decoded count. Empty objects, unresolved units, rejected layouts and failed conversions have separate statuses. A completed overall extraction can contain partial native recovery; inspect its coverage report.

## Validation and remaining work

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

Still unsupported: customized/field unit systems, additional object versions, nonzero log base encodings, categorical label/interval interpretation, inherited/rotated surface geometry, connection/segment topology, general `.zhz` tiles, image logs, faults, pillar grids and property arrays. No Petrel re-import, other receiving-application import, cross-version support or whole-terabyte project completion is claimed. ZMAP/GeoTIFF writers are not included in this update; XYZ and CSV are the open surface outputs.

## Implementation references

- [Microsoft NBFX record specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx/e17683ce-cb4c-4968-bd7a-ebfe5cc18a1e) and [ArrayRecord](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx/e0cd55a8-016d-4bb7-924e-a8b7add5d52b). Petrel's outer framing was established from local samples; it is not claimed to be an SLB-published format specification.
- [lasio writer documentation](https://lasio.readthedocs.io/en/latest/_modules/lasio/writer.html) for LAS serialization and explicit sampling headers.

For a previously preserved package, the source command is `python scripts/petrel_native_recovery.py --export-package <package>`. It creates a new recovery directory and refuses to overwrite an existing one. Use a fresh extraction package for a retry.
