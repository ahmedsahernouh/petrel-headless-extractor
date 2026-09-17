# GeoViewer_data_extractor — Metadata failure isolation

[Website](https://saherlabs.dev/) · [Repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

This correction is included in the current **1.0.0** distribution. It fixes a project-wide compatibility failure triggered by
an individual model subject with ambiguous metadata. In the affected storage
profile, local-property subjects contain repeated `unique_tag` fields. The
generic metadata reader previously stopped at the first such subject and
disabled all native spatial, well-log and surface decoders.

The reader now records each unresolved subject and continues through later
project/version/seismic metadata. Complete binary-container readability and
complete semantic metadata are separate states. Numeric readers retain their
own identity, geometry, schema and value checks. The patch does not choose an
arbitrary duplicate ID or reinterpret unresolved 3D property relationships.
Malformed containers and unavailable databases remain explicit compatibility
failures. The report and process log display affected record types, candidate
identities and reasons, while accepted exports remain available.

Nested stores belonging to other projects are excluded from unlinked seismic
discovery. Unselected companion files and already-open SEG-Y files no longer
count as failed requested conversions.

Use the same root `GeoViewer_data_extractor.bat`. Extract the complete ZIP into
a new folder; do not mix scripts with an older distribution. The verified
offline runtime and Apache-2.0 LICENSE/NOTICE remain included. Run the selected
project again into a new output run to obtain its corrected report and exports.
Previously generated reports are not modified automatically.

This patch does not add a legacy distributed-PTD decoder, rotated/inherited grid
geometry profiles, unrestricted large-grid support, or complete 3D RESCUE export.
See [the 1.0 capability guide](GEOVIEWER_1_0.md) and
[validation](https://github.com/ahmedsahernouh/petrel-headless-extractor/blob/main/docs/VALIDATION.md).
