# Project purpose: Petrel binary data to open, usable outputs

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Re-evaluated 2026-09-14 following the clarified requirement: recover data stored in Petrel binary files into formats other software can read, without opening Petrel. **This is a binary-data extractor, not a general format-conversion utility.**

The core work recovers native binary payloads. Companion conversions such as CSV-to-LAS, LAS-to-CSV, Excel-to-CSV and SEG-Y-to-CSV support preservation and inspection, while ASCII grid reformatting is lower priority.

## What success means

Start from the exact `.pet` project and its matching `.ptd` store. Identify native objects and linked binary arrays, resolve their relationships and metadata, decode actual sample/geometry/property values, and write usable open outputs. Report each object's state separately:

- `decoded`: actual payload values recovered under a validated layout.
- `metadata_only`: identity/attributes found but payload values not recovered.
- `preserved_only`: original bytes copied without decoded values.
- `unsupported_layout` / `missing_metadata` / `missing_dependency`: specific unresolved reason.
- `conversion_failed`: attempted conversion failed its checks.

A copied `.ptd`, a hash, a detected object name or a successfully completed workflow does not mean its data was converted. Count recovered native objects and samples, with denominators based on the objects actually discovered. Do not use LAS companion counts as native log recovery or count an unrelated surface file as a decoded native grid object.

## Evidence-based coverage and priorities

The read-only audit of an existing demo `Data.ptd` SQLite store found 185 `FloatWellLog` and 56 `IntWellLog` objects, 46 `RegValGrid2`, 46 `FaultInterpretation`, 347 `FloatProperty`, 32 `IntProperty`, and 7 `PillarGrid2` objects. These are discovery counts, not decoded results or representative counts for every Petrel project. [Sanitized audit](native_binary_audit.json).

| Native source/object | Required open output | Current evidence and next step |
|---|---|---|
| ZGY binary seismic | SEG-Y + geometry/domain/CRS metadata | The working 1.0.0 engine integrates exact-file and project-linked conversion in the main launcher. Known time axes use standard headers; unknown/depth axes retain unspecified header fields and the exact native axis in text/JSON. Numerical and trace-header QC remain mandatory. Receiving-application validation is separate. [Profile and limits](ZGY_TO_SEGY.md). |
| `FloatWellLog` / `IntWellLog` in native stores | LAS for scalar curves; CSV + metadata; an array format for image logs | The current beta decodes observed native sample/MD/null encodings with exact UUID/well linkage. LAS for resolved continuous curves; categorical boundary records stay CSV. Unresolved units and image logs remain gaps. [Profile/evidence](NATIVE_LOGS_SURFACES.md). |
| Native surface arrays `.zhz` + `.zhz_msk`; `RegValGrid2` / `ValGrid2` | XYZ/CSV, ZMAP for regular grids | Validated RegValGrid2 profiles and explicit ValGrid2 arrays are supported. Regular grids export XYZ/CSV/ZMAP; explicit XYZ meshes export XYZ/CSV. Complete masks and report maps are retained; unknown units remain labelled. Other geometry and `.zhz` tile layouts remain unsupported. [Profile/evidence](NATIVE_LOGS_SURFACES.md). |
| Native trajectory providers and legacy `WellTrace3` | Stored trajectory ASCII/CSV with explicit units/datum status | Selected provider layouts and two validated legacy array profiles decode. [Legacy evidence and limits](NATIVE_LEGACY_TRAJECTORIES.md): 242 retained objects/887 stations; raw unknown-unit angles and native flags preserved; no head inference. Extend only with fixtures. |
| Native well heads, `Points3`, `Polygons3` | CSV/ASCII; geometry formats where appropriate | Selected layouts already decoded. Retain IDs, polygon parts, ordering, nulls and unresolved CRS status. |
| Native well tops | Well/top/MD/TVD/XYZ table | Existing decoding requires independent ASCII calibration for labels. Not a generic native-top recovery solution. |
| Checkshots | Identified time/depth ASCII table with units and convention | Sampled native CheckShotSubject objects link to recoverable Points3 geometry, but attribute-vector roles and time/depth conventions remain unvalidated. Generic XYZ output is not a recovered checkshot curve. |
| `FaultInterpretation`, horizon/fault geometry | XYZ plus connectivity; VTK/TSurf where appropriate | Sampled fault payloads parse as event histories; active-event replay, alignment and coordinate semantics remain unvalidated. One sampled empty horizon parent has an exact child Interpretation3Local payload referencing preserved `.zhz`/`.zhz_msk` files and lattice metadata. The child may contain geometry despite empty parent bytes; tile decoding remains unvalidated. Model subjects and storage records are separate counts. General geometry export is unavailable. |
| `PillarGrid2`, other native grid geometry | RESQML/VTK/GRDECL as appropriate | Complete 3D model/RESCUE export is deferred beyond the basic-data phase. Recover and verify corner/pillar geometry, IJK ordering, inactive cells and faults before exposing properties. |
| `FloatProperty`, `IntProperty`, simulation/property arrays | Geometry-linked arrays; CSV only as an explicit derivative | Native parser not validated. Values must remain attached to cells/nodes, units, categories and timesteps. |

The 2026-08-30 spatial run recorded 72 decoded objects and one rejected layout, with 6,903 polygon vertices, 303 point vertices, 55,122 trajectory rows and 84 independently calibrated top rows.

The 26 September archived horizon investigation identified a sparse tile directory
in one exact linked `.zhz` / `.zhz_msk` pair. The experimental contiguous-tile reader
skipped that directory, shifted tiles after an absent slot and interpreted directory
bytes as scalar samples. Independent descriptor/scalar checks reproduce zero
data/mask mismatches with directory-based placement. The child's 375 by 301 lattice
contains 42,733 finite values; treating header endpoint words as 374 by 300 counts
would retain only 42,628. Both footprints agree perfectly, so this agreement cannot
prove dimension semantics or georeferencing. This is diagnostic evidence, not a new
supported export profile: exact-object coordinate/numeric reference validation and
mask-bit semantics remain open. Do not reuse the old donor-specific half-cell shift
or SEG-Y transform as a generic horizon geometry rule.

## Current acceptance sequence (26 September 2026)

1. Confirm the combined basic-data engine through exact-project trials: native logs, 2D grids, polygons, well heads, point sets, trajectories and supported seismic. Match actual exports and numerical QC to the separate inventory.
2. Verify final report/index links, partial-category survival, shared-drive behavior, integrity scope and compact causal diagnostics. Selected-object tests and a zero exit code do not replace complete-project evidence.
3. Keep top/checkshot/fault/horizon limitations explicit while auditing their actual native layouts and reference evidence. Do not infer topology or attribute meaning merely because arrays are readable.
4. Reconcile capabilities and limitations against [the current public release scope](RELEASE_NOTES.md). Complete 3D model/RESCUE remains future work and is not a substitute for validated basic-data exports.

The report must show **what became usable outside Petrel**, what only has metadata, and what still remains binary. Maintain the original files and source/artifact hashes as supporting provenance. Scientific/CRS correctness, exact-value QC and source-preservation checks remain separate.

## Scope boundaries

Preserve read-only source handling, offline Windows operation and no Petrel/Ocean dependency. Require an observed binary layout and survey-specific geometry; keep units unresolved when their source is unknown. Missing units/domain/CRS alone need not block validated native numeric preservation: retain unknown metadata and exact axes explicitly under the supported profile. Missing identity, ambiguous geometry/topology, conflicting known metadata or failed numerical checks still block the affected export. Scientific interpretation and receiving-application imports require separate checks.

## Report-first product workflow

The full visual report and complete foldable data inventory are the mandatory baseline. Conversion is optional and enabled by default: SEG-Y for supported seismic; documented ASCII/text for other data. Report-only retains previews and inventory metadata, with no retained converted datasets. The main BAT handles supported ZGY in the selected store and explicit project references. Seismic SHA-256 is optional and on by default in 1.0.0; source references, metadata, previews and numerical QC are retained without a false checksum claim when the user disables hashing.


Current release scope is in [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
