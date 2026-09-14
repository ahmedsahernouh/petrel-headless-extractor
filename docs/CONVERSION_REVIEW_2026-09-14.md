# Standalone geoscience conversion: applicability review

**Scope correction (2026-09-14):** this broad research is background, not the implementation roadmap. The project targets Petrel binary payload recovery. CSV-to-LAS and other open-format reformatting are excluded from new core features. See [the corrected purpose and priorities](BINARY_EXTRACTION_PURPOSE.md).

Date: 2026-09-14. [Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor).

This document preserves the v0.2.5 research assessment. The subsequent v0.3.0 implementation and exact beta limits are in [ZGY_TO_SEGY.md](ZGY_TO_SEGY.md).

## Decision

**Add a dedicated ZGY-to-SEG-Y exporter as the first new seismic conversion.** The required reader, writer and compression dependencies are already present in the standalone runtime. Controlled tests demonstrate numerical feasibility. It is not a feature of the released v0.2.5 BAT, and the tests do not establish compatibility with every ZGY or receiving application.

Expand the application by data family: seismic to SEG-Y; scalar well curves to LAS; surfaces to XYZ and GeoTIFF; spatial objects to GeoPackage; meshes to VTK; multidimensional arrays to NetCDF/Zarr. CSV should be a useful table derivative accompanied by metadata. It cannot preserve every geological object by itself.

The [28-route capability matrix](CONVERSION_CAPABILITIES.md) distinguishes shipped functionality, development-only tools, tested prototypes and upstream possibilities. The immediate work is a small number of dependable exporters, accurate feature discovery, and stronger metadata handling. Installing more libraries alone will not expose these conversions to users.

## What is actually available

The audited public release is [v0.2.5](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/tag/v0.2.5), published 2026-09-14 at commit `fb81df5beb728a345654eb992b5a703a30a7f4e2`. Local starting revision `942cc5dccf82d97857ac2b719de346f86e81e991` adds deferred banner documentation, not conversion functionality.

The release build uses an explicit script allowlist. Its companion conversion dispatch handles LAS to CSV, modern Excel sheets to CSV, shapefiles to tables/geometry JSON, and recognized Petrel Well Tops ASCII. Separate native readers expose supported metadata and validated spatial layouts. They do not decode every native object. Version-pinned evidence: [builder](https://github.com/ahmedsahernouh/petrel-headless-extractor/blob/v0.2.5/scripts/build_standalone_petrel_toolkit.py), [companion dispatch](https://github.com/ahmedsahernouh/petrel-headless-extractor/blob/v0.2.5/scripts/portable_petrel_companion_extract.py), [capability metadata](https://github.com/ahmedsahernouh/petrel-headless-extractor/blob/v0.2.5/portable_petrel_toolkit/toolkit.json).

ZGY and SEG-Y are specialist inventory formats in this extraction flow. The included geoscience helper can read ZGY for particular array operations; that is not a SEG-Y export command. A separate development MCP exporter produces NPY slices or a full array. A separate development grid tool handles ZMAP/XYZ. Neither tool is automatically available in the standalone BAT.

Three distinctions prevent misleading capability claims:

1. **Installed library:** an import succeeds in the runtime.
2. **Available feature:** the packaged command accepts the input and exposes the requested output.
3. **Validated conversion:** recorded tests establish the stated numerical, geometric and metadata limits.

The existing KB's universal-export workflow is a design draft. Its Petrel Workflow Editor SEG-Y examples require Petrel execution and cannot establish a no-Petrel exporter. Source copies and hashes establish preservation, not successful format conversion.

## ZGY to SEG-Y: practical applicability

Equinor's pyzgy exposes ZGY reading through OpenZGY and a segyio-like interface. segyio provides SEG-Y creation and header access. This supports a direct reader-to-writer implementation without launching Petrel or using Ocean. The inspected libraries are pyzgy 0.1.1 and segyio 1.9.14. [pyzgy](https://github.com/equinor/pyzgy), [segyio](https://segyio.readthedocs.io/en/stable/segyio.html).

The proposed first production profile is a regular 3D cube exported to IEEE float32 SEG-Y, with trace numbering, coordinates, sample origin and interval explicitly populated. Original acquisition headers cannot be reconstructed from a cube when they were never retained. The result is a new exchange file representing the available cube, not a recovery of the original acquisition tape.

### Tests executed

The [reproducible probe](../research/verify_conversion_candidates.py) ran with the release's Python 3.13.15 Windows x64 runtime. It created new synthetic files and opened two existing local demo files read-only. [Sanitized results](conversion_probe_results.json) record versions and scope.

| Case | Scope | Result |
|---|---|---|
| Uncompressed float32 ZGY | 72 traces, 4,680 samples | Decoded samples equal SEG-Y samples exactly |
| Integer-scaled int8 ZGY | 72 traces, 4,680 samples | Decoded, scaled amplitudes equal SEG-Y samples exactly |
| ZFP-compressed float32 ZGY | 4,096 traces, 262,144 samples | Decompressed samples equal SEG-Y samples exactly |
| CSV to LAS 2.0 | Three rows, depth plus two curves | Values, units, null and depth step preserved |
| Two local demo ZGY files | Six traces / 1,362 samples per file | Reading passed; SEG-Y export withheld pending metadata resolution |

For all three synthetic seismic cases, non-unit inline/crossline increments, a nonzero time origin and a rotated XY grid were checked. A second check parsed raw SEG-Y bytes using Python `struct` and NumPy, independently of the segyio reopening step. It checked size, sample format, key headers and every output sample. Input hashes were unchanged.

These tests compare **decoded ZGY amplitudes with output amplitudes**. The deliberately lossy ZFP fixture already differed from its pre-compression values; the integer fixture had storage quantization. Exporting either to float32 did not recover information lost before conversion. Do not advertise recovery of pre-compression precision.

The evidence is bounded: 4,240 synthetic traces and 271,504 samples. It is not a large-volume benchmark, SEG-Y standards certification, Petrel re-import test, or conversion of a user's supplied project. The research harness is not a production CLI and is not included in the release build allowlist.

### The main blocker is metadata, not compression

Both real demo ZGY files declared unknown horizontal and vertical unit dimensions and empty unit names. Their vertical origins and increments were fractional. Inferring milliseconds, metres or an EPSG code would be unjustified. Those files demonstrate why an exporter must expose unresolved fields rather than silently choose defaults.

For the initial exporter, require or resolve: domain (time/depth), vertical units and datum, horizontal units and CRS status, inline/crossline axes, XY geometry, sample origin/interval, output revision/profile and numerical precision. An explicitly labelled local/unknown CRS can support a research exchange where the receiver accepts it; it must never become an invented geographic CRS.

SEG-Y supports more than time data. Revision 2.1 specifies domain-dependent sample-interval units, depth-domain trace identification and extended fields. That does not mean a writer validated for a simple time-domain profile has implemented every revision 2.1 feature. Fractional origins, non-integral microsecond intervals, long traces and coordinate overflow need explicit handling. [SEG-Y revision 2.1, Tables 2 and 3](https://seg.org/wp-content/uploads/2025/11/seg_y_rev2_1-oct2023.pdf).

Recommended behavior: export only representable metadata under the selected profile; otherwise produce a clear unresolved/unsupported result. Never relabel depth as time to make a reader accept the file. Resampling or rounding beyond the stated tolerance should be a separate, recorded transformation.

### Disk, speed and hashing

A simple uncompressed float32 SEG-Y with one 240-byte header per trace and no extended headers is approximately:

`3,600 + number_of_traces × (240 + 4 × samples_per_trace)` bytes.

For 1,000 × 1,000 traces and 2,000 samples, this is 8,240,003,600 bytes, about 8.24 GB. This is a calculated example, not an estimate for a supplied project. Plan disk space from dimensions, not compressed ZGY size. Include output verification and any chosen preservation copy in the estimate.

Use bounded blocks aligned sensibly with ZGY storage, avoiding a full-volume `np.stack`. Expose trace/byte progress, elapsed time, throughput and separate conversion/verification phases. A small-inline implementation is useful for correctness but still requires performance profiling for compressed brick access.

Hashing answers whether file bytes changed or two copies match. It does not prove amplitudes or geometry were converted correctly. ZGY and SEG-Y should have different file hashes. Maintain separate source/artifact hashes and numerical/geometry QC. A future fast-inventory mode can use size/mtime as weaker evidence, explicitly labelled; it must not claim full checksum verification. Do not recompute full source hashes at every stage when an existing, valid preservation receipt can safely be reused. Hashing decoded blocks is not the same as hashing the raw ZGY file.

## Other conversion families

### Well logs and tables

**CSV/Excel to LAS 2.0 is a strong next addition.** lasio already writes LAS 1.2/2.0, and the bounded probe passed. Require a chosen index column, depth/time units, curve mnemonics and units, well identity, null representation and sampling policy. A CSV cannot supply absent well metadata merely through its extension. Duplicate depths, mixed units and irregular sampling should be reported. [lasio writer](https://lasio.readthedocs.io/en/latest/writing.html).

**DLIS and LIS are practical follow-on adapters.** dlisio documents scalar and multidimensional DLIS channels. Ordinary LAS or flat CSV is suitable for scalar curves; image, waveform and array channels need an array format plus metadata. LIS may contain multiple sampling groups, which should remain distinct unless an explicit resampling step is selected. [DLIS curves](https://dlisio.readthedocs.io/en/latest/dlis/curves.html), [LIS guide](https://dlisio.readthedocs.io/en/latest/lis/userguide.html).

Preserve frame/logical-file IDs and channel metadata alongside exports. Do not promise lossless DLIS-to-LAS conversion for every channel. LAS 3 introduces additional object sections, while the proposed first writer targets LAS 2.0. [USGS LAS overview](https://www.usgs.gov/programs/national-geological-and-geophysical-data-preservation-program/las-format).

Existing Excel conversion reads cached cell values through `data_only=True`; it does not recalculate formulas. Existing LAS-to-CSV writes eight significant digits. Preserve the original and disclose this precision choice rather than describe all table outputs as exact round trips.

### Surface grids, horizons and maps

Prioritize ZMAP+ to XYZ/CSV and IRAP or Surfer grids to XYZ/GeoTIFF. Retain null masks, row/column order, XY rotation, node-versus-cell convention, vertical meaning and precision. The development ZMAP converter needs hardening before promotion: its text writers round coordinates/values, and its inverse assumes a complete axis-aligned lattice.

GDAL documents ZMAP, Surfer ASCII and Surfer binary drivers and GeoTIFF output. These are useful building blocks for standalone adapters; the Windows runtime remains a separate packaging task. [ZMAP](https://gdal.org/en/stable/drivers/raster/zmap.html), [Surfer ASCII](https://gdal.org/en/stable/drivers/raster/gsag.html), [Surfer binary](https://gdal.org/en/stable/drivers/raster/gsbg.html), [GeoTIFF](https://gdal.org/en/stable/drivers/raster/gtiff.html).

XTGeo documents IRAP surface I/O and several reservoir-grid formats. Its current format table marks ZMAP as export-only and warns that certain outputs derotate surfaces. Older API material differs. Select and test a pinned implementation; avoid treating all names in documentation as bidirectional support. [XTGeo format table](https://xtgeo.readthedocs.io/en/latest/datamodels.html).

CSV XYZ is convenient for inspection and re-import but does not inherently describe a grid's topology. Include grid dimensions, index order, spacing, origin, rotation, null sentinel and CRS/domain in a sidecar. Raster export must not introduce a half-cell displacement or silently interpolate a rotated grid.

### Spatial vectors and fault meshes

**Correctness issue found:** current shapefile conversion writes source coordinates into a `.geojson` file without reprojection. When the shapefile is projected, this is not RFC 7946 GeoJSON, which uses WGS84 longitude/latitude degrees. Retaining a `.prj` does not make those coordinates compliant. Document this now; add verified transformation or an explicit source-coordinate format in the next implementation. [RFC 7946](https://www.rfc-editor.org/info/rfc7946/).

GeoPackage is a better proposed default for geometries in a known project CRS, with feature attributes and metadata retained. Conversion must preserve multipart objects, polygon holes, IDs and applicable Z/M values. [GDAL GeoPackage](https://gdal.org/en/stable/drivers/vector/gpkg.html).

GOCAD TSurf is a useful fault/horizon mesh candidate. GemGIS exposes vertices and faces; PyVista writes VTK/VTP and other mesh formats. Preserve triangle connectivity and properties. A vertices-only CSV is a point-cloud derivative, not the original surface. Parser variants and attribute retention need fixtures before release. [GemGIS reader](https://gemgis.readthedocs.io/en/stable/getting_started/reference/raster_api/gemgis.raster.read_ts.html), [PyVista writer](https://docs.pyvista.org/api/core/_autosummary/pyvista.polydata.save).

### Reservoir models and richer open containers

EGRID, GRDECL and ROFF adapters are feasible specialist work. Property CSV is useful for statistics, but cell-center tables do not preserve corner-point geometry, inactive cells, fault topology or categorical meaning. A VTK export would require an explicit topology adapter; it is not implied by CSV support. [XTGeo format table](https://xtgeo.readthedocs.io/en/latest/datamodels.html).

For RESQML, `.epc` metadata and referenced HDF5 arrays form a connected model. Exporting the EPC alone or flattening every object to CSV loses essential relationships. resqpy is a candidate for object-aware access, but supported schemas and objects must be tested individually. [resqpy Model API](https://resqpy.readthedocs.io/en/latest/_autosummary/resqpy.model.Model.html).

Energistics lists WITSML 2.1 and RESQML 2.2. Those standard versions do not establish support in a specific Python library. Start WITSML with explicit log/trajectory table mappings and preserved object metadata. Treat PRODML time-series extraction as a later schema adapter. [Energistics version information](https://energistics.org/witsml-developers-users).

NetCDF/Zarr are appropriate candidate targets for labelled multidimensional arrays. Xarray supplies serialization backends, but a generic HDF5 signature cannot identify seismic geometry or log-image dimensions. A domain adapter must define the schema first. [Xarray I/O](https://docs.xarray.dev/en/stable/user-guide/io.html).

### Additional seismic and point-cloud routes

OpenVDS supplies SEGYExport, but the inspected documentation requires SEG-Y headers already stored in the VDS. It is not an unconditional exporter for arbitrary VDS objects. seismic-zfp documents SGZ-to-SEG-Y; previously lossy compression remains lossy. A ZGY-to-SGZ-to-SEG-Y chain adds no benefit to the proposed direct route and may add loss. [OpenVDS exporter](https://osdu.pages.opengroup.org/platform/domain-data-mgmt-services/seismic/open-vds/tools/SEGYExport/README.html), [seismic-zfp](https://github.com/equinor/seismic-zfp/blob/master/README.md).

SU and supported SEG-2 records are possible secondary SEG-Y adapters. Missing file-level and acquisition metadata must be mapped. SEG-D is a separate, more complex acquisition format: this review does not establish a validated SEG-D adapter. [ObsPy SEG-Y/SU](https://docs.obspy.org/packages/obspy.io.segy.html), [SEG-2 reader scope](https://github.com/obspy/obspy/blob/master/obspy/io/seg2/seg2.py).

LiDAR LAS/LAZ is also readable with dedicated libraries, but it is unrelated to well-log LAS despite the shared suffix. Detect the binary `LASF` signature before routing; preserve point scale/offset, CRS and attributes. Its priority is lower for this Petrel-focused extractor. [laspy examples](https://laspy.readthedocs.io/en/latest/examples.html).

## Standalone Windows packaging

The current runtime already supports the tested ZGY/SEG-Y and LAS operations. Keep automatic installation based on a pinned, verified offline cache. Do not turn a BAT launch into an unbounded installation of latest packages from the internet.

Live PyPI distribution metadata was checked on 2026-09-14. dlisio 1.0.4, XTGeo 4.25.1 and OpenVDS 3.4.9 list CPython 3.13 Windows x64 wheels. resqpy 5.2.0, PyVista 0.49.0, seismic-zfp 0.4.4 and laspy 2.7.0 list pure-Python wheels; their compiled/transitive dependencies still need complete resolution and import tests. GDAL 3.13.3 lists no Windows or universal wheel in that release's PyPI files. A tested binary distribution or controlled build is needed; `pip install GDAL` is not a sufficient standalone plan. [dlisio files](https://pypi.org/project/dlisio/1.0.4/#files), [XTGeo files](https://pypi.org/project/xtgeo/4.25.1/#files), [OpenVDS files](https://pypi.org/project/openvds/3.4.9/#files), [GDAL files](https://pypi.org/project/GDAL/3.13.3/#files).

Wheel availability is a packaging signal, not execution evidence. All new optional families need pinned transitive dependencies, licenses/notices, runtime hashes, offline repair tests and relocated-folder execution. Prefer a small core and separately validated optional packs over increasing startup and repair cost for every user.

## Implementation order and acceptance

**P0 — truthful capability discovery and existing correctness.** Expose format, direction, version, availability and limitations from one capability registry shared by CLI, HTML report and documentation. Correct GeoJSON CRS handling. Report unsupported versus missing dependency versus unresolved metadata separately. No runtime capability command has been added by this review.

**P1 — usable conversion entry points.** Implement a direct-file ZGY exporter, separate from whole-project extraction, with metadata preflight, output size estimate, progress/cancellation and new output directories. Add scalar CSV-to-LAS, SEG-Y header/navigation tables and a hardened ZMAP/XYZ route. These should be individually selectable. A user converting one ZGY should not have to copy and hash a complete Petrel project.

**P2 — well and surface adapters.** Add DLIS/LIS, IRAP/Surfer, typed ASCII mapping and mesh/array routes when fixtures and Windows packages pass. Keep multidimensional logs out of an implicitly flattened LAS path.

**P3/P4 — specialist formats.** Add VDS, SGZ, reservoir models and Energistics adapters against actual versioned examples. Keep unknown proprietary stores and unverified SEG-D layouts explicitly unsupported.

Each production adapter should pass: signature detection; incomplete/corrupt input rejection; preserved source checks; output-overlap protection; units/domain/CRS handling; numerical tolerance and null checks; axes/geometry checks; receiving-reader tests; bounded-memory execution; large-file progress; cancellation/partial-output handling; and meaningful receipts. Failed or incomplete output must never be labelled conversion success.

For ZGY specifically, extend the fixtures to fractional/nonrepresentable axes, depth domain, int16, blank/dead traces, missing geometry, malformed files, large volumes and receiving-application import. Retain the existing compression and rotated-grid checks. For LAS, add duplicate/irregular indexes and unit/null ambiguity; for surfaces, add rotated grids and nodata; for meshes, add multipart and property fixtures.

Before the next binary release, implement the already registered CLI attribution change: retain only the website and project repository within the attribution block. Preserve the normal version, progress and timer messages. See [release backlog](RELEASE_BACKLOG.md).

## Evidence and limits

This review combines current source inspection, MCP/KB orientation, 25 primary documentation/source records, live package-distribution checks and six bounded probe cases. The [source inventory](conversion_sources.json) records versions and access limitations, including an older CWLS PDF available through search text but not direct retrieval. The [package snapshot](conversion_package_snapshot.json) records packaging evidence. Historical KB designs were treated as plans rather than execution proof.

Only synthetic conversion fixtures and read-only demo probes were executed. No client seismic volume was converted, no Petrel session was launched, no proprietary native store was changed, and no new release was published. The matrix is a dated research reference; it does not change the capabilities of downloaded v0.2.5 packages.
