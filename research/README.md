# Conversion research probes

[Website](https://saherlabs.dev/) · [Project repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

`verify_conversion_candidates.py` tests library feasibility. It is not a user conversion command and is not packaged by the standalone build allowlist. See the [applicability review](../docs/CONVERSION_REVIEW_2026-09-14.md).

This preserves the earlier broad research, including an out-of-scope CSV-to-LAS probe. It is not the feature roadmap. Current development follows [native binary recovery priorities](../docs/BINARY_EXTRACTION_PURPOSE.md); the released ZGY converter is documented [separately](../docs/ZGY_TO_SEGY.md).

From the repository root in PowerShell, use the Python runtime from a complete, initialized v0.2.5 release. Choose a new output directory:

```powershell
& 'C:\PetrelExtractor\runtime\python.exe' -B .\research\verify_conversion_candidates.py --output '.\build\conversion-probe-new'
```

The probe creates synthetic uncompressed, integer-scaled and ZFP-compressed ZGY files, exports synthetic SEG-Y, checks decoded samples and headers, and tests a schema-controlled CSV-to-LAS conversion. It writes `results.json` on success. It refuses to reuse an existing output directory. No network access or system Python installation is needed when using the complete release runtime.

Optionally append `--sample-zgy 'E:\Small Test Data\example.zgy'` for a read-only input probe. Use a small authorized test file: sample decoding reads up to six traces, but source-preservation SHA-256 checks read the entire file before and after. No SEG-Y is written from optional external inputs. The probe records unresolved domain/units and does not establish CRS or receiving-application compatibility.

Generated files remain under the selected output directory. Keep raw input data and identifying metadata out of public commits. [Published research results](../docs/conversion_probe_results.json) are sanitized summaries, not release acceptance evidence.
