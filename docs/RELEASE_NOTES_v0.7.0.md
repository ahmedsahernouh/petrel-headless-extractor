# v0.7.0 — Native surface grids, ZMAP exports and report maps

Recover the validated older native grid profile through the main project BAT, then inspect the grids in the full report.

- Regular `RegValGrid2` surfaces: full-resolution XYZ, CSV and ZMAP.
- Direct `ValGrid2` XYZ meshes: XYZ/CSV, retaining native geometry without an invented regular-grid layout.
- Complete native node/cell masks, original grid indices and explicit null handling. Grid data is read back numerically after writing.
- Native grid-cell maps with gap masking, full-grid statistics/histograms, downloadable figures and links to the exported files. Up to 256 grid figures; every discovered object remains in the foldable inventory.
- Numeric grids with unresolved units are exported and counted separately; units, CRS and native signs are never guessed.
- Report-only mode creates bounded map previews without grid ASCII datasets. A per-grid free-space check reports insufficient space before writing that grid.
- The capabilities display includes native project recovery. Existing polygon segment handling, unified ZGY workflow and offline dependency repair remain included.

Independent checks used Microsoft's binary XML reader on all 213 supplied-project grid arrays/masks and a separate ZMAP reader on five exported grids, including two with 7.3 million nodes each. Release-specific full-run and actual standalone BAT results are attached in `VALIDATION.json`. No private project files or coordinates are distributed.

Download `PetrelExtractor-0.7.0-win64.zip`, extract it, and launch the root `run_portable_petrel_extract.bat`. Choose an output drive with enough space for the larger ASCII datasets. Open the top-level `*_REPORT.html` and select **Surfaces** in the figure filter. Conversion stays on by default; full seismic hashing stays off.

See [native grid profiles and ZMAP registration](NATIVE_LOGS_SURFACES.md). This supports validated 2D surface grids and explicit XYZ meshes; general 3D reservoir grids, rotated/inherited layouts and unrecognized native profiles remain unsupported.
