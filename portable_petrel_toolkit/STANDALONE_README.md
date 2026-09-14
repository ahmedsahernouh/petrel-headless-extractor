# Petrel Headless Extractor 0.2.3 — standalone Windows x64

1. Extract the **whole ZIP** into a normal writable folder, separate from your project data.
2. Drag your `.pet` file onto **run_portable_petrel_extract.bat**, or double-click the BAT and enter the project path.
3. On first launch, the BAT installs bundled Python and dependencies into its own `runtime` folder. Each later launch checks them and automatically repairs missing or damaged runtime files.
4. When it finishes, open the `PROJECT_REPORT.html` path printed in the window. The output also includes package QC, checksums, source-preservation receipts and a run log.

The release ZIP is `PetrelExtractor-0.2.3-win64.zip` and its inner folder is `PetrelExtractor`. Open that folder to find the BAT. If Windows shows `0x80010135: Path too long`, cancel, choose a shorter extraction destination, and extract again without skipping files. An incomplete extraction cannot run. The short outer ZIP contains a compressed dependency cache; the BAT expands it after extraction.

Keep the matching, complete `ProjectName.ptd` directory beside `ProjectName.pet`. Close the test project in Petrel before extraction so another program cannot change its files during the read. The extractor itself never opens Petrel or edits its stores.

The ZIP includes Python and its dependencies. No Python installation, pip setup, Petrel, Ocean, admin access or internet connection is required at run time. Windows 10/11 x64 with its built-in Windows PowerShell is the target. Other operating systems and ARM64 are not validated. The BAT is a launcher: **copying only the BAT is insufficient**.

Default output: `%USERPROFILE%\Petrel_Extracts`. Each run creates a new directory. Choose an output root outside the project directory. Do not put results inside `.ptd`.

From Command Prompt:

```bat
run_portable_petrel_extract.bat "E:\Test Data\Example.pet" "E:\Extracted Results" convert
```

Modes: `convert` (default) preserves native/companion files and attempts supported conversions and native spatial decoding; `copy` preserves companions without conversion; `inventory` inventories companions without copying them. All modes preserve the selected native `.pet/.ptd` files in the output. Neighboring Petrel projects are excluded from companion ingestion.

Check and automatically repair dependencies without reading a project:

```bat
run_portable_petrel_extract.bat --check -NoPause
```

For unattended runs, append `-NoPause`. Exit code 0 means extraction, receipt integrity and package QC execution passed. Read the QC findings separately; duplicates, missing depth/CRS information or unsupported formats are not automatically repaired. Nonzero exit means failure; retain the log and choose a new run after correcting the cause.

Extraction covers supported native metadata, well-head records, validated point/polygon/trajectory layouts, LAS tables, supported Excel sheets, shapefiles and Petrel Well Tops ASCII. Unsupported proprietary arrays remain preserved or explicitly unavailable. ZGY/SEG-Y and other specialist formats may be inventoried rather than converted by this extraction flow. This is not a universal native decoder, geological approval, or Petrel re-import test. Input Petrel release defaults to `unknown`.

`00_manifest/toolkit_files.json` records hashes for the installed application and runtime. Startup checks application integrity, restores missing or damaged runtime files from the SHA-256-verified `bootstrap/runtime.zip`, then verifies Python imports and a ZFP compression round-trip before extraction. The cache identity is in `00_manifest/dependency_repair.json`; repair results are in `build/dependencies/last_check.json`. No system Python, pip, internet download, administrator access, or registry change is used.

If the cache is missing or damaged and a dependency needs repair, extraction stops with instructions to re-extract the complete release. Missing or changed application scripts also require re-extraction. A runtime in use by another extraction is not repaired. Keep the entire release together; installing this release in a separate folder does not update a currently running older version.

ZFP support (`zfpy`) is included. Cloud SeismicStore (`sdglue`) is optional and not configured; it is not needed for local project files and requires its separate vendor client and authentication. The launcher reports this capability separately instead of displaying the known missing-`sdglue` warning. This does not extend the extractor's supported native layouts or enable cloud extraction.

 `00_manifest/runtime_provenance.json`, `00_manifest/dependency_inventory.json`, `requirements-standalone-lock.txt` and `THIRD_PARTY_NOTICES.md` record the bundled runtime, exact dependencies and licenses. No project data, local credentials, source corpus or proprietary Petrel binaries are included.

The runtime comes from the [official Python Windows release manifest](https://www.python.org/ftp/python/3.13.15/windows-3.13.15.json). This package uses Python 3.13.15 x64 and verifies the archive against the SHA-256 published there.

Large companion files: text detection reads at most 64 KiB, well-top header detection reads at most 256 KiB, and text profiling samples at most 1 MiB (with a one-byte truncation check). Prefix profiles are labelled and do not claim full-file line counts. The default companion copy/conversion limit is 2,000,000,000 bytes per file. Files above it stay in the inventory with `skipped_size_limit` and are never sent to a converter. SHA-256 still streams across source files, including oversized files, so folders containing very large seismic volumes can take considerable time. This release does not convert SEG-Y volumes.
