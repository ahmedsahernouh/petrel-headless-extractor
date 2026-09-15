# Deferred release requests

## v0.7.0: native surface grids and report maps

The requested grid retrieval adds the observed older RegValGrid2 profile, packed node/cell definitions, exact numeric XYZ/CSV exports and ZMAP for regular grids. Reports show native grid cells with conservative gap masking, full-grid statistics, downloadable files and a separate count for numeric grids whose units remain unresolved. Report-only mode writes bounded previews without grid ASCII datasets. Explicit XYZ meshes are not forced into regular ZMAP geometry. General 3D reservoir grids and unrecognized layouts remain outside this profile; see [validation and limits](NATIVE_LOGS_SURFACES.md).

## v0.4.0 native recovery

Implemented native well logs to LAS/CSV and validated native surfaces to XYZ/CSV in the normal `convert` pipeline. The website/repository-only CLI banner is retained. See [profile and validation](NATIVE_LOGS_SURFACES.md). Remaining work is native unit/geometry inheritance, further version fixtures, categories and interval semantics, ZGY project linkage, then faults/grids/properties. Generic open-to-open conversions remain outside the core priority.

## Next update after v0.2.5: website and project repository CLI attribution

Registered on 2026-09-14. Status: implemented for v0.3.0 and checked through actual BAT startup output. v0.2.5 remains unchanged.

Original request: "remove my name (first line in the cli) keep only the website, not now, in the next update (register)".

Clarification on 2026-09-14: "also remove the third line, keep only the website (2nd line and the project repo 4th line)". This supersedes the earlier website-only interpretation.

In the BAT startup attribution block, remove line 1 (personal name/SaherLabs) and line 3 (personal GitHub profile). Keep line 2 (website) and line 4 (project repository), in this order:

```text
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
```

Keep the normal product/version heading, progress, timer and operational messages. This request concerns displayed CLI attribution only; documentation, source comments and license attribution are outside its scope.

Both launchers retain the website and project repository lines. The relocated acceptance test checks the original launcher's first two lines; the ZGY launcher is exercised separately. Version/progress/timer messages remain.

## Conversion expansion and capability accuracy â€” registered 2026-09-14

Status: priorities superseded by the user clarification: native/binary recovery only. See BINARY_EXTRACTION_PURPOSE.md. Historical broad research and bounded tests retained below; open-to-open additions are not planned. See the [28-route matrix](CONVERSION_CAPABILITIES.md), [applicability review](CONVERSION_REVIEW_2026-09-14.md) and [probe results](conversion_probe_results.json). These entries do not claim functionality in v0.2.5.

- P0: make feature availability explicit in CLI/report/docs from a shared registry. Distinguish conversion, preservation, inventory, unavailable dependency and unresolved metadata. Fix projected shapefile coordinates being emitted under a `.geojson` extension without WGS84 reprojection.
- P1: direct-file ZGY-to-SEG-Y with metadata preflight, bounded blocks, size estimate, progress/timer, failure receipts and receiving-application validation. Dependencies and synthetic float/int8/ZFP probes passed; real demo metadata remains unresolved. Do not silently infer time/depth or CRS.
- Superseded, out of scope: schema-mapped scalar CSV/Excel-to-LAS 2.0; synthetic value/unit/null/step test passed. Add SEG-Y header/navigation CSV and harden/promote the development ZMAP/XYZ converter.
- P2: DLIS/LIS scalar logs; separate multidimensional log-array export; IRAP/Surfer surfaces; GeoPackage; TSurf/VTK; typed ASCII and labelled array adapters. Validate pinned Windows packages and representative input variants.
- P3/P4: VDS/SGZ, reservoir models and Energistics object adapters; keep SEG-D and unknown native layouts unadvertised until specific evidence exists.
- Performance: offer conversion of one selected file without whole-project preservation; estimate uncompressed output size; separate raw file hashes from numerical QC; clearly label any future fast-inventory evidence as weaker than full SHA-256.

Acceptance requires source preservation, geometry/domain/units/null/precision checks, representative failure tests, bounded memory, relocated/offline runtime validation and accurate partial/unsupported states. A successful import or writer call alone is insufficient. The attribution request above is included in v0.3.0.

## Corrected binary-first update

CSV-to-LAS and new generic ASCII/SEG-Y table reformatting were removed from the implementation. v0.3.0 adds a beta ZGY-to-SEG-Y entry point and corrects native coverage counts: companion logs/tops, unlinked surfaces and legacy ZGY reports are excluded; spatial counts use successful object statuses and unique IDs. Native FloatWellLog/IntWellLog and surface payload decoders take priority next; faults/grids/properties follow with validated geometry and identity. See [purpose](BINARY_EXTRACTION_PURPOSE.md).

## Visual report before automatic project conversion â€” 2026-09-14

User priority: strong visual report with figures, maps, plots, statistics, lists, links and visualizations, including a full foldable data inventory tree. Implemented for v0.5.0: see [visual report](VISUAL_REPORT.md). Automatic project-linked ZGY conversion remains the subsequent integration step. Output contract: SEG-Y for seismic; documented ASCII/text for other data.

User clarification: full report/inventory is basic and always included. Dataset conversion is optional, selected by default. v0.5.0 adds Y/n selection and `-ReportOnly`; temporary preview datasets are discarded.

## Unified entry point, optional seismic hashing and report delivery — 2026-09-14

Implemented for v0.6.0: one root BAT beside the support folder; selected-project/store ZGY conversion in the same run; full seismic hashing optional and off by default. Report and full inventory remain mandatory, conversions on by default. Full `*_REPORT.html` is directly beside its matching `*_data` directory and updates after each seismic dataset. No raw seismic duplication. Unlinked companions and unresolved metadata are explicitly listed. Historical notes above describe earlier releases.

Also fixes a v0.5.0 whole-run abort when optional trajectory-name enrichment encounters an unsupported `Model.ptd` LZ4 envelope. The strict decoder remains unchanged; unavailable metadata is recorded in the report and independent spatial outputs are retained. Synthetic regression coverage verifies source preservation and visible, escaped report findings.

## v0.6.1: polygon segments and well-log visibility

Follow-up in v0.6.2: the supplied report showed that sorting alone did not fix the native geometry. Replaced polygon marker scanning with typed, length-framed decoding; preserved segment ordinals, vertex gaps and native closure; added a per-object map selector. See [validation and limits](NATIVE_POLYGONS.md).

Polygon previews group by object/part/explicit segment ID, order by vertex index, split at missing or invalid vertices, and clip before display decimation. Existing native `part_index` remains the segment grouping when no separate `segment_id` is provided. Never connect separate segments or infer topology from XY proximity.

The size-mismatch issue was traced to an observed multi-block `Model.ptd` stream. v0.6.1 supports its length framing with independent block dictionaries, bounded expansion and strict BXML validation. A local project now yields 1,008 numeric log CSVs and report tracks; units remain unresolved, so LAS stays blocked. Unsupported surface object versions remain visible. See [profile and evidence](NATIVE_LOGS_SURFACES.md). Further unit, object-version and geometry work still requires independent validation.
