# GeoViewer_data_extractor 0.8.0

![FieldViewer FV](assets/fv-mark.svg)

Website: https://saherlabs.dev/  
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor

The name changes in this version. The repository and source format remain Petrel.
FV / FieldViewer is a visual family affiliation only, without application-code
integration or a claim of registered trademark status.

## Start and find the results

Extract the complete ZIP to a short directory. Its top level has
`GeoViewer_data_extractor.bat` beside the `GeoViewer` support directory.
Double-click the BAT, paste the exact `.pet` or `.zgy` path, and select an output
root outside the source project. Keep the matching `.ptd` directory beside `.pet`.
No installed Petrel, Ocean, Python or internet connection is required.

Report and full inventory are always included. Conversion defaults to Yes;
full seismic SHA-256 defaults to No and is independent of conversion and previews.
Managed dependencies are verified and repaired from the bundled offline cache.

The output root contains:

- `<run>_REPORT.html`: open this first. Native inventory and converted-data index
  are separate. Filter the converted index and click the required format.
- `<run>_EXPORTS/FILE_INDEX.csv`: accepted exported files, object UUID/name,
  category, units, numerical QC and integrity scope. Category directories hold
  grids, logs, polygons, points, trajectories, seismic and readable workflows.
- `<run>_LOG.txt` and `<run>_EVENTS.jsonl`: process details and structured events.
- `<run>_data/`: preserved evidence, receipts, figures and package QC.

Keep these siblings together when moving the result. Large exports use filesystem
hard links: a shallow user path and the original validated package path address
one physical file. This preserves existing receipt verification without copying
large seismic/grid data. Filesystems without hard-link support retain direct
links to the validated original and report that shallow publication was unavailable.
Copying the entire result with ordinary copy tools may duplicate hard-linked bytes.

## Modern native evidence

The primary validation fixture records **Petrel 2024.5.0**, build Oct 17 2024.
Its `.pet`, Model.ptd and SQLite Data.ptd are read without Petrel. The reader uses
observed object/container profiles; recognizing the version is not a promise that
every object in every 2024 project can be exported.

This release addresses mapped-drive/UNC handling in PowerShell and SQLite,
length-framed typed Points3 data, the observed surface `is_known_consistent`
field and float-encoded integral categorical log codes. Surface geometry, masks,
array lengths, UUID associations and numeric readback checks remain enforced.
Unknown layouts remain visible. Legacy distributed stores receive inventory and
an explicit numeric-decoder limitation rather than a missing-Data.ptd crash.

Native version, save time, filesystem modification time, build, saved-by evidence,
history and project license evidence are distinguished in the report. License
fields do not establish current entitlement or licensed module availability.

## Unknown-axis seismic export

Readable regular affine ZGY cubes can export with unknown domain or units.
Amplitude values, trace order and XY numbers are preserved and checked. No unit,
CRS, depth/time transformation or resampling is invented.

Known, representable time axes use standard interval/origin headers. For an
unresolved physical axis, interval/origin headers are **zero (unspecified)**;
exact native origin, increment, count and domain/unit status are retained in the
SEG-Y textual header and `conversion_metadata.json`. Measurement/coordinate
unit codes also remain unspecified where unknown. **The receiving application
may substitute a default time interval; set the native axis explicitly using
the companion metadata.** This profile does not claim automatic axis recognition
in every SEG-Y application. Invalid geometry or samples still prevent acceptance.

The zero-as-unspecified convention is described in the
[SEG-Y standard](https://seg.org/wp-content/uploads/2025/11/seg_y_rev2_1-oct2023.pdf).
This is a numerical exchange file; original acquisition headers are not recovered.

## Workflow and model boundaries

Saved Commands definitions can be recovered to readable JSON, with serialized
order and references retained. The report lists their entries. This is not an
executable workflow or a validated native re-import format. Other workflow
storage families remain unsupported.

Full 3D RESCUE export remains a separate [validation plan](RESCUE_EXPORT_PLAN.md).
Individual recovered surfaces and properties must not be described as a complete
reservoir model. Numeric validation does not establish geological correctness.
