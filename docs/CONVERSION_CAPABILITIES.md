# Field Viewer conversion and recovery capabilities

Field Viewer Beta version (1.0.0) focuses on recovering supported Petrel native payloads into
open files. Its source projects remain read-only. The complete object inventory
distinguishes recovered, empty, missing, unsupported and rejected objects.
Availability depends on the observed binary layout of each object, not a
universal Petrel version range.

| Input | Available output | Scope and limits |
|---|---|---|
| Supported local ZGY | SEG-Y and exact native-axis metadata | Numerical amplitude and trace-geometry checks. Unknown domain, units or CRS stay explicit; unresolved SEG-Y interval/origin headers are unspecified. [Profile](ZGY_TO_SEGY.md). |
| Supported native scalar well logs | Full-sample CSV; LAS when units and sampling resolve | Categorical boundaries remain CSV; image logs and unresolved native encodings are gaps. [Profile](NATIVE_LOGS_SURFACES.md). |
| Regular native surface grids | XYZ/CSV and ZMAP | Accepted geometry and masks only. Unknown units remain unknown. [Profile](NATIVE_LOGS_SURFACES.md). |
| Explicit native surface meshes | XYZ/CSV | Preserves supported native XYZ geometry; other topology/layouts remain unsupported. [Profile](NATIVE_LOGS_SURFACES.md). |
| Supported native points, polygons, trajectories and well heads | ASCII/CSV with object identity and ordering evidence | Specific observed payload profiles only. [Purpose and coverage](BINARY_EXTRACTION_PURPOSE.md). |
| Readable saved workflow definitions | JSON | Reading does not execute a workflow or qualify it for native reimport. [Current guide](GEOVIEWER_1_0.md). |
| Recognized companion LAS, Excel, shapefile and Well Tops ASCII | Bounded companion derivatives | These are separate from native binary recovery. Excel uses cached values; shapefile geometry retains source coordinates and is not necessarily RFC 7946 GeoJSON. |

The report, full inventory, export index, integrity scope and diagnostic files
are included even when an object cannot be decoded. Full 3D reservoir/RESCUE
models, general fault topology, unobserved native layouts and blanket
receiving-application compatibility are outside this release.

The [dated conversion research](CONVERSION_REVIEW_2026-09-14.md) and its
[probe results](conversion_probe_results.json) record bounded synthetic tests
and upstream library possibilities. They are research evidence, not extra
commands in the standalone package. The [machine-readable research matrix](conversion_capabilities.json)
likewise preserves a dated assessment; use this page and the individual current
profiles for available behavior.
