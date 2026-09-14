# Convert a Petrel ZGY binary cube to SEG-Y

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Version 0.3.0 includes **convert_zgy_to_segy.bat**. It reads the ZGY directly without opening Petrel, including supported integer-scaled and ZFP-compressed data. Keep the complete standalone release together. Dependencies install/repair from the bundled offline cache.

1. Extract the complete release ZIP into a new folder.
2. Drag a `.zgy` file onto `convert_zgy_to_segy.bat`, or double-click it and paste the file path. A ZGY inside a `.ptd` folder is acceptable; it is read-only.
3. If source metadata is missing, enter independently verified time and horizontal units. Do not guess. CRS may remain explicitly `unknown`.
4. Choose an output root outside the source folder and all `.ptd` stores. Default: `%USERPROFILE%\Petrel_Conversions`.
5. Open the printed result directory. It contains `volume.segy`, metadata JSON files and `RUN_RESULT.json` with source hashes, QC and elapsed time.

Noninteractive example for independently confirmed metadata:

```bat
convert_zgy_to_segy.bat "E:\Project.ptd\cube.zgy" "E:\Open Exports" -Domain time -VerticalUnit ms -HorizontalUnit m -Crs "unknown" -NoPause
```

Read metadata without converting or hashing the seismic payload:

```bat
convert_zgy_to_segy.bat "E:\Project.ptd\cube.zgy" -Inspect -NoPause
```

Use `-Capabilities -NoPause` to display the exact supported profile. The main extraction BAT also routes a dragged `.zgy` to this converter. A `.pet` still starts project extraction; automatic project-wide ZGY conversion is not yet integrated.

## Beta profile and limits

- Regular 3D **time-domain** cube, at least two inlines and two crosslines. Positive integer line increments. Float32 SEG-Y output; source integer data is decoded with its scale.
- Source time units: seconds, milliseconds or microseconds. Output interval must be exactly representable as integer microseconds (1–65,535); origin must be integer milliseconds (-32,768–32,767). At most 32,767 samples per trace. No silent resampling or axis rounding.
- Horizontal coordinates in metres or feet; rotated affine geometry is supported. Output scalar is selected for 0.001 or 0.01 source-unit precision and recorded in metadata. CRS is user-declared/unknown, not independently verified or reprojected.
- Depth-domain, non-finite amplitudes, degenerate geometry, conflicting units and unsupported sampling stop with an explanation. This is not a universal native-store decoder.
- Output represents decoded cube amplitudes. Earlier lossy compression/quantization is not reversed; original acquisition headers cannot be recovered when absent from ZGY.

The writer uses bounded array blocks (up to 64 MiB, excluding library/cache overhead). It estimates uncompressed output space, checks every amplitude and key trace header against the ZGY, then verifies source preservation. Conversion, verification and hashing have separate progress phases with elapsed time. Hashing reads the selected source before and after conversion; no other project files are copied or hashed.

Failures/cancellation after a run directory exists retain `RUN_RESULT.json`; `.partial.segy` files are not accepted results. Exit code zero requires completed conversion/QC and final hashes. Unknown-metadata preflight failures create no data output. Receiving-software import and geological acceptance remain separate from the supplied synthetic/raw-byte tests.
