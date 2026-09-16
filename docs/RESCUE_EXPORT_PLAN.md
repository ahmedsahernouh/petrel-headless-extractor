# Complete 3D reservoir-grid export: validation plan

GeoViewer_data_extractor · https://saherlabs.dev/

Status in 0.8.0: **planned, not an available exporter**. A supplied project records
Petrel 2024.5.0 and contains PillarGrid2, KeyPillars, Commands, FloatProperty and
IntProperty payloads. Their presence is evidence of stored model components;
it is not evidence of recovered cell topology or a complete RESCUE model.

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
