> For current public release scope, see [release notes](RELEASE_NOTES.md).
> Observed Petrel 2024.5 profile results are summarized in [validation](VALIDATION.md).

# Native well logs and 2D surfaces â€” 1.0.0 trial validation

[Website](https://saherlabs.dev/) Â· [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

The normal standalone BAT now attempts native well-log and surface recovery in `convert` mode. It reads the preserved `.pet`, `Model.ptd` and SQLite `Data.ptd` inside the new extraction package. It requires no Petrel, Ocean, .NET binary reader, external installation or internet connection at runtime. NumPy and lasio are already bundled.

## Using the outputs

Run `FieldViewer_data_extractor.bat`, enter the exact `.pet` path, and let extraction finish. In `PROJECT_REPORT.html`, check **Extraction coverage** and the **Native log and surface recovery** evidence link.

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

Log CSV columns are `sample_index,md,raw_value,is_null`. Filter `is_null=1` when using values; the raw native sentinel is retained for traceability. LAS converts those sentinels to the declared LAS NULL value. Categorical logs retain integer codes and original boundary positions in CSV. An exact supported template legend can supply native labels as separate metadata; code-to-label mapping and interval boundary direction remain unvalidated, so categorical records are not silently resampled into LAS.

Surface XYZ files contain `X Y VALUE`, with a comment header. The third column retains the native numbers and sign: it can represent time, depth, velocity or another surface attribute, not necessarily elevation. Native storage units are not inferred from project display units or template labels. Unresolved units remain null in metadata and explicitly labelled in figures.

For the older packed-mask profile, `nodes.csv` contains only usable defined nodes with their **original** `node_index,i,j` (no renumbering). Complete native masks are run-length ASCII tables with `start_index,length,defined`; expand each run to recover every original node/cell flag. `usable_node_definitions.csv` is also included when numeric nulls exclude a node marked defined in the native mask. The earlier bitmask profile retains its complete `nodes.csv` and `cells.csv` tables. Neither encoding infers triangulation or fault connections.

Regular grids also export `surface.zmap`. Header bounds are native **node coordinates**, data columns increase in X and rows decrease in Y. With GDAL, set `ZMAP_PIXEL_IS_POINT=TRUE` to avoid a half-cell registration shift. The ZMAP null represents undefined/unusable nodes; independent cell exclusions remain in the cell-definition CSV. Explicit `ValGrid2` XYZ meshes are not assumed regular and therefore have no ZMAP export. See the [GDAL ZMAP documentation](https://gdal.org/en/stable/drivers/raster/zmap.html) and its linked [ASCII layout reference](https://lists.osgeo.org/pipermail/gdal-dev/2011-June/029173.html).

Read `metadata.json` for units, measurement, parent object/well IDs, source sign convention and validation. CRS remains explicitly unresolved. Files in a directory ending `.partial` failed before completion and are not valid exports.

Artifact paths in the recovery report are relative to `native_data`. Short folder names retain all 128 bits of the object UUID. The exporter rejects paths that would exceed its Windows path budget; choose a short output root such as `C:\PetrelOut` if necessary.

In the current output layout, the full location is `<output root>/<run>_data/extraction/package/<package>/native_data/<curve folder>/`. The top-level report links to available curve files in the object catalogue. Figure images and their source links are under the **Well logs** figure filter, with a default budget of 64 log figures; every discovered log remains in the inventory. Report-only mode discards temporary LAS/CSV datasets after generating supported previews. If project/model metadata cannot be read, logs may remain `missing_metadata` with no exported samples or plot; changing the plot settings cannot resolve that decoding gap.

The `native_data` folder can be absent even when conversion was selected: if every native log/surface is blocked, no datasets are written and empty-folder cleanup removes it. Inspect `07_workflows_reports/native_recovery/native_recovery_report.json` for the actual per-object outcomes. Successful report/package QC does not mean native logs were exported.

## Supported profiles

- Exact observed Petrel LZ4-v1 and BXML-v1 containers; BXML data bodies use Microsoft's NBFX binary XML records. A bounded parser follows declared block lengths and rejects unsupported structures. Serialized `Type`/`Ref` values never execute code.
- The reader also handles the observed multi-block stream: one `LZ4\x01` magic, followed by independent compressed blocks with eight-byte length/opaque headers. BXML can cross block boundaries. Total output, block count, every length and each block's match offsets are bounded. Truncation, trailing garbage and concatenated complete envelopes remain errors. The opaque header word is not presented as a verified checksum.
- `FloatWellLog` and character-encoded `IntWellLog`, object version `1 3 0 2 0 1`, with measured-depth arrays and zero `min_index`. Native float32 maximum and character value 255 are the validated missing-value encodings.
- LAS requires an increasing continuous float log and resolved units. The unit profile is the observed non-customized Metric project (`m`, `m`, `ms`) with predefined unit templates. Other unit systems are not guessed; recoverable log values can still be written to CSV with `missing_metadata` status.
- `RegValGrid2`, version `1 1 1 0 0 0 0 2 0 1 1`: explicit coordinate context (or the independently validated own-geometry attribute profile below), positive increments, zero dip/axis-flip, validated XY rotation, no connections or segments, matching dimensions/extents and node masks. Values use X/I-fastest ordering. An attribute without its own validated coordinate context is rejected.
- The current profile includes `RegValGrid2` version `0 1 1 0 0 0 0 2 0 1 1`, which has no axis-flip field. The same positive-increment, zero-dip and independently validated rotation gates apply. A false coordinate-context flag is permitted only when independently stored model bounds match the reconstructed defined XYZ range exactly. Node/cell masks support the observed `Size/bools` layout. When the declared count is divisible by eight, its separate `ignore` field must contain exactly one byte; that byte is padding, not an additional definition bit. Observed padding includes both `00` and `ff`. Declared count, mask length, padding presence and field types remain checked. The maximum is 10 million node positions per grid; larger or unrecognized layouts remain explicit failures.
- `ValGrid2`, version `0 0 0 0 0 2 0 1 1`: float64 XYZ triples. Direct `SurfaceSubject` bounds are checked against separate native model bounds. The observed `SurfaceAttrSubject` model version `6 3 13 1 2 20 0 0 1` is also accepted when its explicit XYZ values match its **own** `cached_limit` exactly within the existing numerical tolerance. Missing, undefined, enclosing-only or mismatched attribute bounds remain blocked. This does not implement inherited coordinates or assert that an explicit mesh is a regular ZMAP grid.

For `SurfaceSubject` model version `9 2 13 1 0 1 18 0 0 1`, the stored `limit` can enclose a smaller defined grid. Exact equality, containment and an explicitly undefined cache have separate recorded outcomes. The observed older model version `8 2 12 1 0 1 17 0 0 1` also uses `limit`, but requires exact defined XYZ bounds; containment or an undefined cache is insufficient. The older `cached_limit` profile still requires exact bounds. Undefined caches do not count as independent geometry confirmation. Recovery metadata identifies `own_limit` versus `own_cached_limit`. Recoverable grids with unresolved units are exported with `missing_metadata`, plus explicit `dataset_exported=true` and verified artifact records. The report counts these numeric grid exports separately from unit-resolved decoded objects.

Source files are hashed before and after recovery. Every completed CSV, LAS, XYZ and ZMAP is read back and checked against the decoded values. Outputs preserve float64 values with 17 significant digits (ZMAP scientific notation retains at least that precision). Data writing and readback use bounded chunks; large ASCII exports can be much larger than native binaries. Only successful, unit-resolved objects from an unchanged snapshot contribute to the strict decoded count. Numeric grid exports with unresolved units have their own count. Empty objects, rejected layouts and failed conversions remain separate. A completed extraction can contain partial native recovery.

## Validation and remaining work

The local 1.0.0 trial on 22 September 2026 inspected 160 previously rejected
grids from two retained projects saved in 2018.2 and 2012.5. Narrow metadata and
padding-profile fixes recovered **63 additional grids**: nine explicit attribute
meshes, 53 grids using the older `limit` layout, and one grid with `ff` padding.
All 354 typed arrays/mask fields matched Microsoft's independent binary XML
reader byte for byte. The export trial wrote 63 XYZ files, 179 CSV tables, 47
ZMAP files and 63 preview grids, preserving 8,670,206 defined nodes. All 47 ZMAPs
also passed independent `zmapio` value, null and coordinate checks. Source hashes
were unchanged. Six objects had resolved units; 57 retained explicit unresolved
metadata while delivering verified numeric files. These are selected-object
results, not a new full-project/main-BAT acceptance run. The other 97 objects
were investigated separately on 23 September: 87 rotated regular grids and ten
regular attributes carrying their own geometry despite a false coordinate-context
flag. All 577 array/mask fields agreed with Microsoft's independent reader.
Reconstruction using the stored rotation angle, original minimum and increments
matched each object's own stored defined XYZ bounds; the opposite rotation sign
failed every rotated case. Eight representative actual ASCII recoveries passed
full numeric readback, unchanged-source checks and, where applicable, independent
ZMAP reading. Full-project export of all 97 is still pending in the ten-project
acceptance wave. Current release scope is in the [Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).

The added profile permits finite recorded XY rotation (within one revolution),
with zero dip and no axis flip, only when reconstructed defined XYZ matches the
object's own native bounds. It preserves original node order, masks and values.
Rotated grids export XYZ/CSV; they do not export axis-aligned ZMAP or silently
resample. The observed version-6 regular attribute profile likewise requires
exact agreement with its own cached bounds; inherited geometry remains unsupported.

For the supported grid profiles, Microsoft's independent `XmlDictionaryReader` agreed byte for byte with every numeric array and definition-mask byte from **213 supplied-project grids**: 188 regular grids and 25 explicit XYZ meshes. They contain 527,623,365 node positions, of which 253,830,417 are usable defined nodes. This validates binary array reading; it does not resolve native units. The independent `zmapio` reader also reproduced values, nulls and XY registration from five exported regular grids containing 18,461,876 node positions, including two 7,339,605-node grids. Full-run export/report totals and standalone acceptance are recorded in the release's `VALIDATION.json`; private grids and coordinates are not distributed.

The maps use selected original nodes and conservative native gap masks, with statistics from the complete decoded grid. They are previews; the ASCII files retain full resolution. A per-grid space check rejects an insufficient output drive before writing that grid's datasets. Source files remain unchanged and previous extraction packages are not overwritten.

Multi-block reading was checked on a local project whose Model container had two blocks expanding to 8,388,608 and 7,583,786 bytes. The separate C decoder in python-lz4 4.4.5 agreed byte for byte with both blocks and the combined production output; all 12,408 BXML documents parsed. The same bounded reader serves model, native object and spatial extraction.

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

Four separate course reference grids support the regular-grid ordering and georeferencing: correlation 0.9953â€“0.9993, RMS difference 2.63â€“11.15 ms after comparing observed opposite time signs. They are different grid realizations, not exact-value ground truth. Native source signs are preserved in the delivered outputs. Source-data values remain private; only aggregate evidence and synthetic tests are distributed.

Still unresolved or unsupported: native unit semantics outside the validated Metric profile (numeric ASCII may still be exported), additional object versions, nonzero log base encodings, categorical label/interval interpretation, inherited surface geometry, rotations without matching independent bounds, dip/axis flips, connection/segment topology, general `.zhz` tiles, image logs, faults, 3D pillar grids and property arrays. Petrel re-import and whole-terabyte project extraction have not been tested. GeoTIFF is not included. The surface/grid support here covers 2D grids and explicit XYZ meshes.

## Implementation references

### Retained-object corrections, 30 September 2026

These version 1.0.0 checks cover retained objects. Current release scope is in the [Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
An explicitly undefined `RegValGrid2` node may retain a stored NaN. Defined nodes
must remain finite; infinities and nonfinite explicit XYZ coordinates are rejected.
CSV/NPZ readback checks both finite values and the exact NaN mask. XYZ/ZMAP contain
only defined valid samples. No null classification is inferred from a plausible value.

The exact `SurfaceSubject` profile `[11,3,13,1,2,20,0,0,1]` can declare
`used_force_negative_depth=true`, cache positive Z bounds and store negative Z.
Only that flag/profile permits negating and swapping the cached Z limits for bounds
comparison. All six bounds must still agree exactly. Exported native coordinates
and samples are unchanged. Adjacent profiles, absent/false flags and XY mismatches
do not gain this exception. An entirely undefined cached bound is missing geometry
confirmation and does not become an accepted export.

Across 40 previously rejected retained grid payloads from saved 2015.5, 2017.4 and
2019.3 projects, 38 passed revised geometry/mask and numerical checks. Three of
those produced full ASCII/XYZ/ZMAP with exact readback; the other 35 were preview
checks only. Two grids retained undefined cached bounds and remain excluded.
This is bounded internal consistency evidence, not full-project or cross-version
acceptance. The detailed validation record is retained outside this public repository.

Categorical logs with nonintegral codes now report bounded offending-value counts
and examples. The sampled `-999.25` code has no independently validated native null
declaration; it is neither rounded nor silently discarded. Unknown depth/scalar
units and custom display templates remain unresolved where stored-unit evidence
is insufficient.

### Targeted native profile verification, 26 September 2026

The modern `RegValGrid2` payload profile with `has_coordinate_context=false`
now accepts the exact `SurfaceSubject` model profile `[11,3,13,1,2,20,0,0,1]`
only after the existing payload, mask and geometry checks and exact float64
equality of all six defined XYZ bounds with its own finite, nonsentinel
`cached_limit`. No parent geometry or CRS transform is inferred. An adjacent
profile, missing or malformed cache, even a one-ULP bounds mismatch, nonzero
dip and axis flips remain rejected; this surface-only gate does not accept QA
subject classes (the separate bounded QA gate is described below).
Three previously sampled archived April/DMS objects exported 37,947 defined
nodes with exact production readback and separately rechecked XYZ values and
artifact hashes. The sampled sentinel-cache object remained rejected; an empty
object produced no files or geometry-validation claim. Existing unit-validation
rules are unchanged and CRS remains unresolved; this geometry gate provides no
new unit or CRS evidence. The 93 focused synthetic tests passed. Detailed receipts, copied metadata and outputs are retained outside this public repository. Metadata hashes at
copy matched the prior diagnosis and archived source size/mtime stayed equal;
no original-project integrity check or full Data.ptd hash was performed.
These selected-object results are internal consistency evidence, not an
independent Petrel export, full-project retry or general version support.

### Stored native QA grids, 26 September 2026

Numeric preservation now accepts `QualityAssuranceSubject` model profile
`[1,11,3,13,1,2,20,0,0,1]` and `QualityAssuranceAttrSubject` profile
`[0,6,3,13,1,2,20,0,0,1]` only with current `RegValGrid2` payload profile
`[1,1,1,0,0,0,0,2,0,1,1]` and `has_coordinate_context=true`. Exact project/model
UUID and class linkage, namespace, payload, array, mask and geometry checks
remain mandatory. Every finite defined X/Y/scalar minimum and maximum must
equal that object's own finite, nonsentinel `cached_limit` exactly. No target
cache or `target_link_tag` geometry is inherited. Adjacent profiles, legacy
regular grids, `ValGrid2`, well logs, context-false QA, nonzero dip and axis
flips remain rejected. An empty grid creates no dataset and makes no geometry
validation claim.

The third XYZ column preserves the stored native **scalar value**; it is not
asserted to be depth or elevation. Zeros remain defined when the native mask
defines them. The original class, UUID and typed `is_recalculation_needed` and
`up_to_date` flags (or explicit absence) are retained. Malformed flag types
are rejected. `qa_state` preserves these flags and the ordinary report reason
displays them with “stored native QA values; computation validity and scalar
units unresolved”. Even a current-state flag or a recognized template cannot
establish computational validity. Declared template measurement/unit remain
separate metadata; resolved scalar `unit`, `measurement` and `depth_unit`
remain null, and CRS remains unresolved. Existing validated project XY units
may remain in `horizontal_unit`. Exported nonempty objects always retain
`status=missing_metadata`, `dataset_exported=true` and
`numeric_round_trip=exact`; they are not counted as unit-resolved decoded
objects or validated calculated properties.

The exact archived April/DMS cohort contains four QA and fourteen QAAttr
objects with 366,428 defined stored scalars, all zero. All fourteen QAAttr
objects request recalculation; seven are not up to date. This cohort provides
numeric preservation evidence only. Synthetic controls exercise arbitrary
nonzero values, partial masks, both classes, identity failures, all six
individual one-ULP bounds errors, missing/malformed/sentinel caches, unrelated
target caches, invalid state types, template conflicts and empty grids. It
does not provide independent Petrel comparison or validate the meaning of
computed QA properties. Private output verification and source integrity
scope are summarized in the bounded validation results above.

The legacy regular-surface model profile `[9,2,13,1,2,19,0,0,1]` now uses
its own `limit` field only when all six bounds exactly match decoded defined
nodes. It does not inherit a newer profile's enclosing-bounds allowance.
The initial archived 2014.4 object test recovered 873 defined nodes from a
630 by 676 grid, with exact XYZ readback and verified ZMAP serialization.
The subsequent isolated cohort retry recovered all 29 previously rejected
grids in that case: 4,380,702 defined nodes, with every XYZ value independently
checked against typed arrays and native geometry. Each produced XYZ and ZMAP;
auxiliary masks and metadata bring the file index to 174 entries.
Unknown units stay explicit. An adjacent model profile and conflicting XY
bounds are rejected. This is a tested layout extension, not general support
for a Petrel release or approval of the original coordinate reference system.

Spatial recovery also accepts the observed Points3 version-2 identity-slot
layout with exactly eight known fields and a slot count equal to the vertex
count. One archived 2012.5 object produced 1,122 exactly checked XYZ rows;
the opaque identity values remain preserved in the native source, not exported
as interpreted attributes. Three archived 2023.8 trajectories produced 289
stations after a second typed reader confirmed every coordinate, MD and angle.
Their negative starting MD values are retained unchanged with an unresolved
reference; decreasing MD still fails. These are targeted object tests, not
complete-project reruns or new original-source integrity checks.

The observed large Points3 identity collection is now parsed one slot at a
time, retaining the ordinary parser's node bound. Per-slot byte/node limits
and declared vertex/slot counts still apply; opaque identity values are not
interpreted. An archived 2022.2 DMS object produced 402,426 CSV point rows whose
complete XYZ hash matches Microsoft's independent binary-XML reader exactly.
The check covers that object and its observed profile. Other Petrel versions and
complete-project delivery remain untested.

### Legacy surface own-limit profile, 30 September 2026

The working 1.0.0 source provisionally accepts the observed `SurfaceSubject`
model profile `[10,2,13,1,2,20,0,0,1]` with its own `limit` and no
`cached_limit`, only for `RegValGrid2` payload `[0,1,1,0,0,0,0,2,0,1,1]`,
declared coordinate context and an explicitly Boolean false
`used_force_negative_depth`. All six finite, defined-node X/Y/scalar extrema
must equal the typed native bounds exactly. Undefined bounds, enclosing-only
bounds, sign-flag differences and adjacent unsupported profiles are rejected.
Existing cached-limit handling, identity checks and parser/node limits remain
unchanged. Native values are neither negated nor converted to inferred units.

One retained grid from the historically evidenced 2015.5 case produced
621,488 defined values from 1,211,088 node positions. The parent checked every
CSV/XYZ value and original index, complete node/cell masks, seven artifact
hashes and ZMAP values/ordering with the separate `zmapio` reader. The 127
loaded affected regression cases passed, including eight new profile tests.
This replay used retained Model UUID/namespace and a scoped metadata adapter;
it did not revalidate the original project SubjectStub/ancestry or source
integrity. Units and CRS remain unresolved, and Petrel-reference comparison,
receiving-application import and full-project delivery remain unverified.

Of 85 historically withheld grids, 84 have candidate metadata for this gate;
only the one retained payload has been replayed. The other 83 candidate
payloads require checks, and the remaining grid declares a true depth-sign
flag and stays excluded. Candidate counts are not recovered-export counts.
The repair remains provisional pending further validation.

- [Microsoft NBFX record specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx/e17683ce-cb4c-4968-bd7a-ebfe5cc18a1e) and [ArrayRecord](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx/e0cd55a8-016d-4bb7-924e-a8b7add5d52b). Petrel's outer framing was established from local samples; no SLB format specification was used.
- [lasio writer documentation](https://lasio.readthedocs.io/en/latest/_modules/lasio/writer.html) for LAS serialization and explicit sampling headers.

For a previously preserved package, the source command is `python scripts/petrel_native_recovery.py --export-package <package>`. It creates a new recovery directory and refuses to overwrite an existing one. Use a fresh extraction package for a retry.

### Native log failure evidence, 2 October 2026

For `IntWellLog`, the reader follows the resolved `DiscTemplateSubject` default
colour-table reference. The exact `DiscColorTableSubject` profile
`[0,2,1,2,20,0,0,1]`, `ColorTableStyle` profile `[7,0,1]` and item Version 3
can produce `native_category_legend`. It preserves labels, item ordinals, raw
minimum code, level and parent fields, the undefined display item, and exact
Model locators. The evidence identifies the template default, not a proven local
display override. Missing, ambiguous or malformed optional tables remain
unresolved without invalidating independently supported numeric recovery.

The reader does not infer a code mapping from ordinal plus minimum code, or a
log null policy from a legend's undefined display item. Sixteen exact retained
categorical payloads all link to one 23-label legend. Every payload still rejects
its fractional `-999.25` value. These are 23 unique recovered metadata labels,
not new numeric exports or 368 independent labels.

Rejected numeric records now retain metadata resolved before decoding, including
well/template identity, blob digest and source locator. Nonfinite diagnostics
separate array lengths, MD/value failures, NaNs, infinities and the first eight
affected positions. One retained FloatWellLog has 19,366 MD/value records,
finite MD and 7,919 NaNs in its values; it remains rejected. This does not classify
other logs or seismic payloads. All 19 retained files used by this investigation
freshly match their recorded hashes and remain unchanged. Null interpretation, interval meaning and later independent review remain open.
