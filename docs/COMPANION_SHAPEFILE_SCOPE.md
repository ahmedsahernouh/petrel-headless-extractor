# Shapefile companion recovery scope

Shapefiles are open companion data, not decoded Petrel native objects. Current
source can preserve readable geometry when the exact DBF attribute sidecar is
absent. It does not synthesize attributes, infer CRS or claim a receiving import.

CSV and GeoJSON retain source SHP record order. CSV `source_shape_index` is
zero-based; `source_attribute_status` distinguishes read attributes, a missing DBF
and a DBF record marked deleted. Deleted slots keep geometry and empty attributes,
so later attribute rows cannot shift onto another shape. Unequal SHP/DBF record
counts, corrupt or unreadable DBF files still fail. Failure does not leave accepted
derivatives or overwrite existing destination files.

The summary records `attribute_status`, `attributes_complete` and deleted-record
counts. The companion inventory records `conversion_gap`, and affected sources
also appear in the manual/unsupported inventory. `converted_and_preserved` means
the declared structural conversion completed; it does not mean missing attributes
became available. The capability report counts converted sources with gaps.

CSV, GeoJSON, summary JSON and any source PRJ are registered and indexed in the
companion GIS lane after normal package QC. This does not broaden other lanes.
GeoJSON/WKT are XY representations; explicit point Z/M are additionally retained
in CSV. Other Z/M profiles, multipatch meaning, polygon topology, units and CRS
interpretation remain unqualified. PRJ text is a literal source declaration,
not independently validated geodesy.

## Scoped evidence

Seven independently constructed binary SHP/DBF fixtures and retained outputs were bound by hashes in the scoped validation. Comparison with the unchanged historical converter reproduces four
missing-DBF failures, one silent two-shape/one-attribute truncation and one
deleted-row attribute shift. A complete point/attribute control retains its
coordinate and attribute values. Current source preserves literal point,
multipart-line and null geometry, point Z/M CSV values and attribute row identity.

Anonymized validation records 262 tests: 259 passed, three Windows symlink
privilege skips, zero failures/errors. This includes the actual companion stage,
PowerShell package validator and final index on a synthetic geometry-only fixture,
plus count mismatch, corrupt/unreadable attributes, exclusive destination and
failed-publication cleanup checks. These are bounded synthetic acceptance tests.

Five historical shapefiles were not retained with their complete source
bundles and were not reread from the original disk. Their original DBF availability
and exact-case recovery remain unverified. The running frozen project wave does
not contain this repair. Related delivery and identity findings remain
provisional until the recorded evidence and later review are reconciled.


Current release scope is in [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
