# Complete 3D reservoir-grid export: validation plan

Field Viewer Data Extractor · https://saherlabs.dev/

Complete 3D reservoir-grid and RESCUE export are future work. This plan records the research and validation gates. See [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md) for current public scope; complete 3D export is not included.

Current status: **not an available exporter**. A supplied project records
Petrel 2024.5.0 and contains PillarGrid2, KeyPillars, Commands, FloatProperty and
IntProperty payloads. Their presence is evidence of stored model components;
cell topology and complete RESCUE output remain unrecovered.

The target is a self-contained RESCUE exchange package, not a copy of native
`.ptd` blobs labelled RESCUE. Petrel's documented RESCUE exporter includes more
than grid corners: properties, selected faults/transmissibility information,
wells and the active local grid set can matter.

1. Inventory each grid UUID, dimensions, coordinate reference, units, pillar
   and property links. Identify active/inactive cells, split pillars, faults,
   pinchouts, local refinements and parent/child grid relationships explicitly.
2. Establish bounded typed profiles for PillarGrid2 and associated payloads.
   Preserve cell indexing, handedness, orientation, corner order, masks and
   original values. Do not replace missing topology with a regular lattice.
3. Assemble a format-neutral in-memory model. Refuse incomplete exports with a
   per-component reason; retain recovered evidence without calling it a model.
4. Evaluate the available RESCUE writer/library and its redistribution license
   before bundling it. Select and document a supported RESCUE revision; provide
   ASCII output where the revision and receiving software support it. Keep all
   files of one model together in `EXPORTS/models/<model>/`.
5. Compare against an independently Petrel-authored RESCUE export of small
   representative fixtures: orthogonal grid, faulted/split grid, inactive cells,
   pinchouts, properties with nulls and local grid refinement. A native-to-writer
   round trip alone cannot establish geometric correctness.
6. Re-import using an independent receiving application/reader. Compare cell
   counts, topology, coordinates, active masks, volumes, property values/nulls,
   fault connections and supported wells. Record tolerances and unsupported
   features. Publish a version/profile matrix, not blanket year compatibility.
7. Only accepted complete models enter the report's converted-data index.
   Inventory and preview information remain separate, including failed or
   unsupported models.

The current modern fixture enables investigation, but an independent reference
export and a validated RESCUE writer are still required before release support.

## Structural evidence added on 22 September 2026

A bounded read-only trial parsed the complete pillar-grid payload, its key-pillar
payload and six representative properties (smallest, median and largest by
compressed size for each property type). All eight passed the existing container
and typed-record parser. Exact project/model identities link all 42 property
registry entries to the model; only six property payloads were inspected.
Source-file SHA-256 values matched before and after the trial. The detailed validation record is retained outside this public repository.

The next obstacle is the interpretation of geometry and indexing, not an
unreadable 2024.5 container. The observed grid contains different pillar field
sets, both point and line geometry fields, and fault-related records with multiple
vertical sequences and per-corner indices. Assuming one sequence per pillar
would lose discontinuities. The model also records a J-index swap flag.

The six sampled properties use paged float32, byte-vector and ushort-vector
representations. Their full page counts agree with the declared value counts
and the population of the property definition mask. That mask covers fewer
locations than the logical cell lattice. These count checks do **not** establish
IJK order, category/null meaning, physical units or simulator ACTNUM semantics.
An additional internal check compared every stored layer value with candidate
logical indices: zero-based K varying slowest matched all values. Both bit orders
matched that test, so it does not resolve mask bit order or horizontal indexing.
In particular, the grid records `has_actnum_grid=false`; do not relabel a property
definition mask as an independently recovered active-cell grid.

The next bounded implementation sequence is:

1. Preserve every observed pillar variant, fault reference, sequence and corner
   selector in a typed intermediate representation. Reject unknown variants per
   object, without affecting already recovered logs or surfaces.
2. Validate packed-to-logical indexing, mask padding, axis swaps, handedness and
   per-corner sequence selection. Use stored layer and bulk-volume properties as
   internal consistency checks; they do not replace an independent reference.
3. Prove geometry-linked property alignment and null/category interpretation
   before writing model ASCII arrays. Preserve unresolved units explicitly.
4. Validate representative faulted and unfaulted cells against independent
   Petrel-authored reference exports, then implement and independently read back
   the selected RESCUE representation under the gates above.

The structural trial delivered no complete model, RESCUE file or production
decoding profile. Other Petrel versions and additional 2024.5 grid layouts
remain untested.
