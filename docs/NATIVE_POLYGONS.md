# Native polygon segments

[Website](https://saherlabs.dev/) Â· [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

The current bounded BXML/NBFX reader reconstructs polygon segments without byte-marker scanning. A declared frame can interrupt a coordinate's bytes. Removing guessed marker sequences or repairing apparent coordinate jumps can corrupt vertices and lose segments. The reader assembles declared frames, validates each typed item, and exports its original order.

## CSV and map behavior

`05_spatial/polygons/native_polygons_vertices.csv` contains one row per valid vertex. Group rows by `object_id` and `segment_id`, then order by `vertex_index`. Do not sort by X/Y or join separate groups.

- `segment_id` and `part_index` are the original zero-based native collection ordinal. Empty segments never renumber later ones. They are stable grouping keys; separately authored Petrel segment labels are unavailable.
- `native_serialization_id` is retained separately; it is not a geological segment ID.
- `vertex_index` is the original slot in the native XYZ array. Missing slots are omitted from the finite-vertex CSV but leave index gaps. Full counts and missing-slot counts remain in `native_spatial_decode_report.json`; current source also delivers their raw components separately as described below.
- `is_closed_native` preserves the native Boolean. The report closes a complete segment when this flag is true. It never closes or joins across a missing vertex. `is_closed_by_repeated_xyz` separately records whether the array repeats its first point.
- The map offers a **Polygon object** selector and fits the chosen object's segments. Names come from the native project inventory when available. Layer toggles separate polygons, point sets and wells.
- Preview sampling retains segment endpoints and original order, with a shared 12,000-vertex budget and at most 6,000 displayed line parts. The complete CSV is unaffected. A segment needs at least two consecutive valid vertices to form a line. Empty and singleton segments remain in the receipt/inventory.

## Validated scope and limits

### Part evidence delivered to consumers, 2 October 2026

Current source adds `05_spatial/polygons/native_polygon_parts.csv`, with one
metadata row per native collection part, including empty parts and parts with
no finite vertices. The existing finite-vertex CSV and its counts remain
unchanged. The part table retains original collection/serialization identities,
declared and missing-slot counts, valid geometry-row count, native XYZ scope,
order, native closure flag and separately assessed repeated endpoints. Duplicate
serialization IDs do not merge distinct collection ordinals.

The new repeated-endpoint observation is null in the receipt (`unknown` in the
part CSV) when there are fewer than two vertices or an endpoint is missing. It
does not silently convert an open native path into a closed polygon. A consumer
must combine each part's declared vertex count with the original finite `vertex_index`
values to locate absent ordinals; it must never connect across those gaps.
Ring/hole roles remain unresolved, and attached property attributes remain
explicitly not exported where the native reader has not validated them.

Final and checkpoint delivery can index per-object `polygon_parts` CSVs. Their
catalogue role is `polygon_part_metadata`, which cannot satisfy a full geometry
requirement or upgrade an empty supported object to decoded geometry. Checkpoint
promotion verifies the recorded part-row count. Metadata-only files cannot hide
a missing geometry table in checkpoint findings.

The source catalogue normalizes the exact native `PolygonsSubject` category to
`culture_polygon` and separately preserves `native_subject_type`. A summary
`polygon_geometry_evidence` observation points to individual `polygon_part:<n>`
metadata records. Each part keeps its exact receipt locator, so large multipart
objects do not become one giant JSONL row. Old receipts stay unchanged and do not
gain newly computed closure flags. The synthetic 5,000-part test exercises that
bounded-record behavior. Processing speed was not benchmarked.

A retained two-run review reconciles 216 polygon objects and
798 recorded parts, including 409 recorded missing vertex slots and three
objects whose attributes were not exported. Fresh hashes and direct inspection
of two retained CSVs check six parts/64 finite vertices and their original
ordinals/closure observations. This is consolidation and validation of existing
evidence. The review checks reported parts and selected CSV hashes; native
payloads were not independently replayed in this check.

### Missing vertex components, 2 October 2026

When any XYZ component matches an already supported native missing sentinel,
current source preserves that slot's complete raw XYZ triple in
`05_spatial/polygons/native_polygon_missing_vertices.csv`. Points3 uses
`05_spatial/points/native_point_missing_vertices.csv`. These are auxiliary
evidence tables, not additional valid geometry. The finite-vertex CSVs and
their counts are unchanged; no coordinate is filled, interpolated or bridged.

Each row keeps `object_id`, `data_pk`, original `part_index` and `vertex_index`,
plus each component's textual value, classification and exact little-endian
binary64 hex (`x_raw_ieee754_le`, and equivalent Y/Z fields). The bit fields
preserve signed zero and masked nonfinite payloads that decimal text cannot
fully express. Points3 uses part index zero. Join by exact object/part/vertex
identity, never by row position or proximity. The union records observed native
slots without turning a missing slot into a complete coordinate.

The receipt records separate point/polygon missing-row counts. Only fully
decoded objects can contribute auxiliary rows; a later object rejection cannot
leak earlier partial rows. Delivery indexes these as `point_missing_vertices`
or `polygon_missing_vertices` with role `missing_vertex_evidence`. That role
cannot satisfy the full geometry requirement, including for all-missing objects.
Checkpoint delivery checks its recorded row count. The preparation validator
also blocks objects with recorded positive missing-slot counts, including older
polygon reports whose total must be derived from their per-part counts.

Eight targeted tests cover raw bits/ordinals, failed objects, all-missing points,
checkpoint count mismatch, compact 5,000-slot storage, legacy gap gates and stale
generated-table removal. Seventeen retained real payloads preserve the same
33,699 finite slots under frozen and current decoders, but contain zero missing
slots. Retained reports independently identify 409 missing slots in
eight polygons; compatibility assessment preserves those counts and blocks gaps.

A subsequent bounded replay found those eight objects' binary bodies and
dictionaries in retained diagnostic material. A fresh Microsoft
`XmlDictionaryReader.ReadElementContentAsDouble` read supplies an independent
bit-level reference. Current final CSV/index delivery preserves all 409 missing
slots and 61 finite vertices, exact original ordinals and closure flags, through
24 indexed files (eight geometry, eight part metadata, eight missing-slot tables).
The union of finite and missing rows covers every declared slot, while all eight
objects remain blocked for receiving preparation. Sixteen geometry/raw-slot file
hashes were freshly verified; archived inputs remain unchanged.

Anonymized validation records this positive real-body
evidence and its limits. The test reconstructs BXML/LZ4 framing and a small SQLite
carrier around the archived bodies. Those carriers are not original Petrel
stores. The old archive lacks original compressed-blob hashes, so matching UUIDs
and counts do not establish fresh source-store equivalence. Native ring/attribute
semantics, current full-project delivery and receiving acceptance remain open.

The observed outer profile is `Polygons3` version `[1,2,0,1,1]`; each inner `Polygon3` is version `[0,1,2,0,1,1]`, in the Petrel 2011/03 serialization namespace. Counts, field names, numeric types, closure flags and declared framing must agree. Unknown item versions, object references, inner attributes/object IDs, malformed arrays and invalid coordinates fail closed per object. Native missing-float markers retain missing slots. Outer polygon properties use the bounded raw-vector profile below when supported; otherwise they remain explicitly unexported while independently valid geometry is retained. CRS and units are not inferred from coordinates.

A supplied local project was validated read-only: 150 polygon objects, 347 native segments, 115,746 valid vertices and 409 missing slots. Microsoft `XmlDictionaryReader` independently parsed all 150 original binary XML bodies. Segment order, vertex slot order, missing positions and closure flags agreed; coordinates agreed within two floating-point ULPs allowed for the independent reader's XML text formatting. The corrected CSV was checked against that independent output. Project identifiers, coordinates and source binaries are not distributed in the public release.

Regression fixtures cover frames splitting inside doubles, marker-like float bytes, empty segments, missing positions, explicit closure, count/version rejection, CSV ordering and source preservation. These checks validate the observed profile, not all Petrel versions or receiving-software/geological acceptance.

Old CSV files produced by the marker-based decoder cannot be repaired by sorting their rows. Re-extract the native polygons with 0.6.2 or later and regenerate the report. The main BAT includes this change; no separate polygon launcher is needed.


### Raw native attribute vectors, 2 October 2026

Current source exports `05_spatial/polygons/native_polygon_attributes.csv` for
observed outer `type_vector` Version 5 arrays: native type 1 Boolean, 2 signed
int32, 4 binary64 and 5 string. Each vector retains its original index, every
value ordinal, typed JSON scalar, original eight-byte Version 1 default, and
binary64 little-endian bits. Empty strings stay empty strings; nonfinite values
have JSON null plus exact bits and an explicit nonfinite state. Native defaults
are preserved without applying them or inventing null semantics.

The vector collection, array counts/types, defaults and profiles must agree.
Unknown fields/versions, malformed arrays, duplicate fields and resource limits
reject all attached vectors for the affected object. Valid geometry survives
with an explicit attribute failure; no earlier vector is silently published.
At most 4,096 vectors per object, two million attribute values across the run
and 64 MiB of string content per object are accepted by this profile. Numeric
vectors stay compact until CSV writing. Zero-length vectors and defaults remain
in the primary receipt and individually located catalogue metadata records.

Delivery indexes the table as `polygon_attributes`, with the auxiliary
`native_attribute_values` role. It cannot satisfy full geometry, including
objects with empty parts. Checkpoint delivery checks recorded row counts.
Original array ordinals are not asserted to be authored part IDs. Native names
and IDs can now be bound under the exact metadata profile below; units,
ring/hole roles and default application remain unresolved. The catalogue
now distinguishes decoded raw values with unresolved bindings from unexported
attributes. Receiving preparation remains conservative.

A bounded replay checks three retained NBFX bodies through final indexed CSVs against a fresh independent Microsoft
`XmlDictionaryReader`: 22 vectors and 533 values (76 Booleans, 76 int32 values,
304 doubles and 77 strings), including exact defaults and binary64 bits.
The nine indexed files contain three geometry, three part-metadata and three
attribute tables; these are three native objects, not nine decoded objects.
Archived inputs and the reconstructed fixture database remain unchanged.
BXML/LZ4 carriers are reconstructed around the archived bodies; the old archive
lacks original compressed-blob provenance. Original source-store identity,
Model attribute-name binding, physical semantics, complete-project delivery and
receiving imports remain unverified.

### Native attribute names and IDs

The current source additionally joins an attribute vector to its native subject
only when the selected copied PET registry and Model records agree. The PET
SubjectStub parent chain must identify one polygon, one attribute folder and an
exact set of attribute subjects. Model `attr_index` and `attr_type` must match
the payload vector's ordinal and type. Native names must agree in both records;
duplicate IDs/indices, ambiguous folders, unknown profiles, conflicting types or
custom/unbound Data.ptd paths leave every vector for that object unbound. Raw
values and independently valid geometry survive with the reason recorded.

Each bound row carries native attribute UUID/name/template UUID, the folder UUID,
PET/Model locators and selected PET, Model and compressed-payload SHA-256 values.
This is a native declaration, not an external ID or inferred physical unit.
`Area_SqKm`, for example, remains a string when its native type is string.
`IsHoleInArea` does not by itself establish ring topology. The value-to-part
meaning, application of defaults/nulls, physical units and ring roles stay open.

The admitted PET serializer profile is `[153,1]`, ClassInfos/ClassInfo `[1,1]`
and SubjectStub `[0,1,1]`. Model profiles are PolygonsSubject
`[3,2,13,1,0,1,18,0,0,1]`, PolygonsAttrFolder `[0,0,1,18,0,0,1]` and
PolygonsAttrSubject `[0,1,0,1,18,0,0,1]`. Other profiles are explicitly unresolved.
Only bounded package copies are read; original project paths are not followed.

A bounded metadata comparison checks 175 selected native
registry/Model records against independent .NET binary-XML scalar reads and
checks all 22 vector identities through final indexed CSVs, preserving the
same 533 typed values and defaults. Outer framing uses the existing decoder;
the .NET metadata interpretation is independent. Retained copied metadata and
older archived geometry bodies are different evidence artifacts, so this replay
cannot link them to the same original save generation. Part semantics and
receiving imports remain unverified; no complete-project run includes this check.


Current release scope is in [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
