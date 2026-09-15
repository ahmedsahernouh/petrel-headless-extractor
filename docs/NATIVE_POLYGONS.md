# Native polygon segments

[Website](https://saherlabs.dev/) · [Project](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Version 0.6.2 replaces polygon byte-marker scanning with the bounded BXML/NBFX reader. A declared frame can interrupt a coordinate's bytes. Removing guessed marker sequences or repairing apparent coordinate jumps can corrupt vertices and lose segments. The reader now assembles declared frames, validates each typed item, and exports its original order.

## CSV and map behavior

`05_spatial/polygons/native_polygons_vertices.csv` contains one row per valid vertex. Group rows by `object_id` and `segment_id`, then order by `vertex_index`. Do not sort by X/Y or join separate groups.

- `segment_id` and `part_index` are the original zero-based native collection ordinal. Empty segments never renumber later ones. This is a stable grouping key for the decoded object, not a claim to recover a separately authored Petrel segment label.
- `native_serialization_id` is retained separately; it is not a geological segment ID.
- `vertex_index` is the original slot in the native XYZ array. Missing slots are omitted from CSV but leave index gaps. Full counts and missing-slot counts remain in `native_spatial_decode_report.json`.
- `is_closed_native` preserves the native Boolean. The report closes a complete segment when this flag is true. It never closes or joins across a missing vertex. `is_closed_by_repeated_xyz` separately records whether the array repeats its first point.
- The map offers a **Polygon object** selector and fits the chosen object's segments. Names come from the native project inventory when available. Layer toggles separate polygons, point sets and wells.
- Preview sampling retains segment endpoints and original order, with a shared 12,000-vertex budget and at most 6,000 displayed line parts. The complete CSV is unaffected. A segment needs at least two consecutive valid vertices to form a line. Empty and singleton segments remain in the receipt/inventory.

## Validated scope and limits

The observed outer profile is `Polygons3` version `[1,2,0,1,1]`; each inner `Polygon3` is version `[0,1,2,0,1,1]`, in the Petrel 2011/03 serialization namespace. Counts, field names, numeric types, closure flags and declared framing must agree. Unknown item versions, object references, inner attributes/object IDs, malformed arrays and invalid coordinates fail closed per object. Native missing-float markers retain missing slots. Attached outer polygon properties are explicitly **not exported**; their geometry is decoded independently. CRS and units are not inferred from coordinates.

A supplied local project was validated read-only: 150 polygon objects, 347 native segments, 115,746 valid vertices and 409 missing slots. Microsoft `XmlDictionaryReader` independently parsed all 150 original binary XML bodies. Segment order, vertex slot order, missing positions and closure flags agreed; coordinates agreed within two floating-point ULPs allowed for the independent reader's XML text formatting. The corrected CSV was checked against that independent output. Project identifiers, coordinates and source binaries are not distributed in the public release.

Regression fixtures cover frames splitting inside doubles, marker-like float bytes, empty segments, missing positions, explicit closure, count/version rejection, CSV ordering and source preservation. These checks validate the observed profile, not all Petrel versions or receiving-software/geological acceptance.

Old CSV files produced by the marker-based decoder cannot be repaired by sorting their rows. Re-extract the native polygons with 0.6.2 or later and regenerate the report. The main BAT includes this change; no separate polygon launcher is needed.
