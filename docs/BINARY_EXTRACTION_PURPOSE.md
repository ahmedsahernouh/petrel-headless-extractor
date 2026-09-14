# Project purpose: Petrel binary data to open, usable outputs

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Re-evaluated 2026-09-14 following the clarified requirement: recover data stored in Petrel binary files into formats other software can read, without opening Petrel. **This is a binary-data extractor, not a general format-conversion utility.**

CSV-to-LAS, LAS-to-CSV, Excel-to-CSV and SEG-Y-to-CSV are not new core capabilities. Existing companion handling may remain useful for preservation and inspection, but it must not count as native-data recovery. ASCII grid reformatting is also outside the priority work. The earlier broad conversion research remains background material; its generic-format priorities are superseded by this document.

## What success means

Start from the exact `.pet` project and its matching `.ptd` store. Identify native objects and linked binary arrays, resolve their relationships and metadata, decode actual sample/geometry/property values, and write usable open outputs. Report each object's state separately:

- `decoded`: actual payload values recovered under a validated layout.
- `metadata_only`: identity/attributes found but payload values not recovered.
- `preserved_only`: original bytes copied; no decoding claim.
- `unsupported_layout` / `missing_metadata` / `missing_dependency`: specific unresolved reason.
- `conversion_failed`: attempted conversion failed its checks.

A copied `.ptd`, a hash, a detected object name or a successfully completed workflow does not mean its data was converted. Count recovered native objects and samples, with denominators based on the objects actually discovered. Do not use LAS companion counts as native log recovery or count an unrelated surface file as a decoded native grid object.

## Evidence-based coverage and priorities

The read-only audit of an existing demo `Data.ptd` SQLite store found 185 `FloatWellLog` and 56 `IntWellLog` objects, 46 `RegValGrid2`, 46 `FaultInterpretation`, 347 `FloatProperty`, 32 `IntProperty`, and 7 `PillarGrid2` objects. These are discovery counts, not decoded results or representative counts for every Petrel project. [Sanitized audit](native_binary_audit.json).

| Native source/object | Required open output | Current evidence and next step |
|---|---|---|
| ZGY binary seismic | SEG-Y + geometry/domain/CRS metadata | v0.3.0 adds a beta direct-file time-domain exporter. Next: project-linked batch export, broader sampling/depth profiles and receiving-application validation. |
| `FloatWellLog` / `IntWellLog` in native stores | LAS for scalar curves; CSV + metadata; an array format for image logs | v0.4.0 beta decodes observed native sample/MD/null encodings with exact UUID/well linkage. LAS for resolved continuous curves; categorical boundary records stay CSV. Unresolved units and image logs remain gaps. [Profile/evidence](NATIVE_LOGS_SURFACES.md). |
| Native surface arrays `.zhz` + `.zhz_msk`; `RegValGrid2` / `ValGrid2` | XYZ/CSV, later ZMAP or GeoTIFF + domain/CRS | v0.4.0 beta directly decodes validated RegValGrid2 and explicit ValGrid2 arrays to XYZ/CSV using their own native metadata. Other geometry, units and `.zhz` tile layouts remain unsupported; the old donor-based tile decoder is not packaged. [Profile/evidence](NATIVE_LOGS_SURFACES.md). |
| Native trajectory providers | Deviation-survey ASCII/CSV with units and datum | Selected layouts already decoded. Extend only with fixtures; preserve exact MD/inclination/azimuth or XYZ meaning. |
| Native well heads, `Points3`, `Polygons3` | CSV/ASCII; geometry formats where appropriate | Selected layouts already decoded. Retain IDs, polygon parts, ordering, nulls and unresolved CRS status. |
| Native well tops | Well/top/MD/TVD/XYZ table | Existing decoding requires independent ASCII calibration for labels. Not a generic native-top recovery solution. |
| `FaultInterpretation`, horizon/fault geometry | XYZ plus connectivity; VTK/TSurf where appropriate | Metadata exists; native payload parser not generally validated. Recover topology and object identity, not only nearby strings or points. |
| `PillarGrid2`, other native grid geometry | RESQML/VTK/GRDECL as appropriate | Native parser not validated. Recover corner/pillar geometry, IJK ordering, inactive cells and faults before exposing properties. |
| `FloatProperty`, `IntProperty`, simulation/property arrays | Geometry-linked arrays; CSV only as an explicit derivative | Native parser not validated. Values must remain attached to cells/nodes, units, categories and timesteps. |

Historical spatial evidence from 2026-08-30 records 72 decoded objects and one rejected layout, with 6,903 polygon vertices, 303 point vertices, 55,122 trajectory rows and 84 independently calibrated top rows. This is useful validation history, not a newly executed full-project run or cross-version proof.

## Corrected implementation sequence

1. Ship and validate ZGY-to-SEG-Y as the first binary seismic addition; keep metadata restrictions explicit. Apply the registered website/repository CLI banner change.
2. Prioritize native well-log and native surface decoding. Use bounded samples and independent reference exports to validate field layout, sample ordering, scale, units and nulls. Generalize existing surface evidence before offering it for arbitrary projects.
3. Extend fault, horizon, grid and property payload coverage with versioned fixtures and topology checks.
4. Integrate validated decoders into the project extraction pipeline, including object names/IDs and per-object failures. A direct ZGY exporter is a useful first component, not completion of whole-project recovery.

The report must show **what became usable outside Petrel**, what only has metadata, and what still remains binary. Maintain the original files and source/artifact hashes as supporting provenance. Scientific/CRS correctness, exact-value QC and source-preservation checks remain separate.

## Scope boundaries

Preserve read-only source handling, offline Windows operation and no Petrel/Ocean dependency. Never invent undocumented binary layouts, assume geometry from a different survey, infer units from plausible values, or claim every Petrel version is supported. Missing reference metadata is a conversion blocker for the affected object, not a reason to mislabel it as a successful export.

## Report-first product workflow

The full visual report and complete foldable data inventory are the mandatory baseline. Conversion is optional and enabled by default: SEG-Y for supported seismic; documented ASCII/text for other data. Report-only retains previews and inventory metadata, with no retained converted datasets. v0.5.0 implements this selection for the existing extraction pipeline; project-wide linked ZGY-to-SEG-Y automation remains subsequent work.
