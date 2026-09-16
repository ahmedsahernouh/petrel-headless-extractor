> Current release: [GeoViewer_data_extractor 0.8.0](GEOVIEWER_0_8.md) supersedes the historical time-only seismic restriction and adds observed 2024.5 profiles, project identity, diagnostics and a shallow export index.

# Convert a Petrel ZGY binary cube to SEG-Y

[Website](https://saherlabs.dev/) Â· [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Version 0.8.0 uses the same **GeoViewer_data_extractor.bat** for projects and exact ZGY files. It reads the ZGY directly without opening Petrel, including supported integer-scaled and ZFP-compressed data. Keep the complete standalone release together. Dependencies install/repair from the bundled offline cache.

1. Extract the complete release ZIP into a new folder.
2. Drag a `.zgy` file onto `GeoViewer_data_extractor.bat`, or double-click it and paste the file path. A ZGY inside a `.ptd` folder is acceptable; it is read-only.
3. If source metadata is missing, it remains unknown. The exact native axis is retained in text/JSON; set it explicitly in the receiving application. Verified overrides remain optional; never guess units.
4. Choose an output root outside the source folder and all `.ptd` stores. Default: `%USERPROFILE%\Petrel_Conversions`.
5. Open the top-level `*_REPORT.html`; its matching data folder contains `volume.segy`, metadata and conversion receipts. Full seismic hashing is off by default; add `-FullHash` to request it.

Noninteractive example for independently confirmed metadata:

```bat
GeoViewer_data_extractor.bat "E:\Project.ptd\cube.zgy" "E:\Open Exports" -Domain time -VerticalUnit ms -HorizontalUnit m -Crs "unknown" -NoPause
```

Read metadata without converting or hashing the seismic payload:

```bat
GeoViewer_data_extractor.bat "E:\Project.ptd\cube.zgy" -Inspect -NoPause
```

Use `-Capabilities -NoPause` to display the exact supported profile. The same main BAT converts supported ZGY discovered in the selected `.ptd` store or explicit XML/native-BXML project file references. Unlinked nearby files remain inventoried; select their exact paths to convert them.

## Beta profile and limits

- Regular affine 3D cube; known time or explicitly unresolved/depth axis as described in the 0.8.0 guide, at least two inlines and two crosslines. Positive integer line increments. Float32 SEG-Y output; source integer data is decoded with its scale.
- For known time axes (seconds, milliseconds or microseconds), the interval must be exactly representable as integer microseconds (1 to 65,535); origin must be integer milliseconds (-32,768 to 32,767). At most 32,767 samples per trace. No silent resampling or axis rounding.
- Unknown or depth axes use unspecified interval/origin fields and retain the exact native axis in SEG-Y text and `conversion_metadata.json`. Configure that axis explicitly in receiving software; this is not a time/depth conversion or a promise of automatic physical-axis recognition.
- Horizontal coordinates retain metres, feet or unknown units; rotated affine geometry is supported. Output scalar is selected for 0.001 or 0.01 source-unit precision and recorded in metadata. CRS is user-declared/unknown, not independently verified or reprojected.
- Non-finite amplitudes, degenerate geometry, conflicting known units and sampling outside the applicable header profile stop with an explanation. Missing interpretation metadata alone does not block readable data. This is not a universal native-store decoder.
- Output represents decoded cube amplitudes. Earlier lossy compression/quantization is not reversed; original acquisition headers cannot be recovered when absent from ZGY.

The writer uses bounded array blocks (up to 64 MiB, excluding library/cache overhead). It estimates uncompressed output space, checks every amplitude and key trace header against the ZGY, then verifies source preservation. Conversion, verification and hashing have separate progress phases with elapsed time. Full hashing is optional and off by default. When selected, it reads the source before and after conversion and hashes SEG-Y output. Otherwise file-state checks and complete numerical QC remain, with no SHA-256 identity claim. Raw seismic is not copied merely for preservation.

Failures/cancellation after a run directory exists retain `RUN_RESULT.json`; `.partial.segy` files are not accepted results. Exact-file conversion returns success only when the requested conversion/QC or report-only work completes. Unsupported conversion still retains its report and explanation; no accepted SEG-Y is fabricated. Receiving-software import and geological acceptance remain separate from the supplied synthetic/raw-byte tests.
