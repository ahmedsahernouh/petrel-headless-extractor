# Convert a Petrel ZGY binary cube to SEG-Y

[Website](https://saherlabs.dev/) Â· [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Version 0.6.0 uses the same **run_portable_petrel_extract.bat** for projects and exact ZGY files. It reads the ZGY directly without opening Petrel, including supported integer-scaled and ZFP-compressed data. Keep the complete standalone release together. Dependencies install/repair from the bundled offline cache.

1. Extract the complete release ZIP into a new folder.
2. Drag a `.zgy` file onto `run_portable_petrel_extract.bat`, or double-click it and paste the file path. A ZGY inside a `.ptd` folder is acceptable; it is read-only.
3. If source metadata is missing, enter independently verified time and horizontal units. Do not guess. CRS may remain explicitly `unknown`.
4. Choose an output root outside the source folder and all `.ptd` stores. Default: `%USERPROFILE%\Petrel_Conversions`.
5. Open the top-level `*_REPORT.html`; its matching data folder contains `volume.segy`, metadata and conversion receipts. Full seismic hashing is off by default; add `-FullHash` to request it.

Noninteractive example for independently confirmed metadata:

```bat
run_portable_petrel_extract.bat "E:\Project.ptd\cube.zgy" "E:\Open Exports" -Domain time -VerticalUnit ms -HorizontalUnit m -Crs "unknown" -NoPause
```

Read metadata without converting or hashing the seismic payload:

```bat
run_portable_petrel_extract.bat "E:\Project.ptd\cube.zgy" -Inspect -NoPause
```

Use `-Capabilities -NoPause` to display the exact supported profile. The same main BAT converts supported ZGY discovered in the selected `.ptd` store or explicit XML/native-BXML project file references. Unlinked nearby files remain inventoried; select their exact paths to convert them.

## Beta profile and limits

- Regular 3D **time-domain** cube, at least two inlines and two crosslines. Positive integer line increments. Float32 SEG-Y output; source integer data is decoded with its scale.
- Source time units: seconds, milliseconds or microseconds. Output interval must be exactly representable as integer microseconds (1â€“65,535); origin must be integer milliseconds (-32,768â€“32,767). At most 32,767 samples per trace. No silent resampling or axis rounding.
- Horizontal coordinates in metres or feet; rotated affine geometry is supported. Output scalar is selected for 0.001 or 0.01 source-unit precision and recorded in metadata. CRS is user-declared/unknown, not independently verified or reprojected.
- Depth-domain, non-finite amplitudes, degenerate geometry, conflicting units and unsupported sampling stop with an explanation. This is not a universal native-store decoder.
- Output represents decoded cube amplitudes. Earlier lossy compression/quantization is not reversed; original acquisition headers cannot be recovered when absent from ZGY.

The writer uses bounded array blocks (up to 64 MiB, excluding library/cache overhead). It estimates uncompressed output space, checks every amplitude and key trace header against the ZGY, then verifies source preservation. Conversion, verification and hashing have separate progress phases with elapsed time. Full hashing is optional and off by default. When selected, it reads the source before and after conversion and hashes SEG-Y output. Otherwise file-state checks and complete numerical QC remain, with no SHA-256 identity claim. Raw seismic is not copied merely for preservation.

Failures/cancellation after a run directory exists retain `RUN_RESULT.json`; `.partial.segy` files are not accepted results. Exact-file conversion returns success only when the requested conversion/QC or report-only work completes. Unsupported conversion still retains its report and explanation; no accepted SEG-Y is fabricated. Receiving-software import and geological acceptance remain separate from the supplied synthetic/raw-byte tests.
