# Petrel Headless Extractor 0.2.1 — standalone Windows x64

1. Extract the **whole ZIP** into a normal writable folder, separate from your project data.
2. Drag your `.pet` file onto **run_portable_petrel_extract.bat**, or double-click the BAT and enter the project path.
3. When it finishes, open the `PROJECT_REPORT.html` path printed in the window. The output also includes package QC, checksums, source-preservation receipts and a run log.

The release ZIP is `PetrelExtractor-0.2.1-win64.zip` and its inner folder is `PetrelExtractor`. Open that folder to find the BAT. If Windows shows `0x80010135: Path too long`, cancel, choose a shorter extraction destination, and extract again without skipping files. An incomplete extraction cannot run. Version 0.2.1 replaces the long nested names used by 0.2.0 and reports missing launcher/runtime files before starting.

Keep the matching, complete `ProjectName.ptd` directory beside `ProjectName.pet`. Close the test project in Petrel before extraction so another program cannot change its files during the read. The extractor itself never opens Petrel or edits its stores.

The ZIP includes Python and its dependencies. No Python installation, pip setup, Petrel, Ocean, admin access or internet connection is required at run time. Windows 10/11 x64 with its built-in Windows PowerShell is the target. Other operating systems and ARM64 are not validated. The BAT is a launcher: **copying only the BAT is insufficient**.

Default output: `%USERPROFILE%\Petrel_Extracts`. Each run creates a new directory. Choose an output root outside the project directory. Do not put results inside `.ptd`.

From Command Prompt:

```bat
run_portable_petrel_extract.bat "E:\Test Data\Example.pet" "E:\Extracted Results" convert
```

Modes: `convert` (default) preserves native/companion files and attempts supported conversions and native spatial decoding; `copy` preserves companions without conversion; `inventory` inventories companions without copying them. All modes preserve the selected native `.pet/.ptd` files in the output. Neighboring Petrel projects are excluded from companion ingestion.

Check the complete bundle without reading a project:

```bat
run_portable_petrel_extract.bat --check -NoPause
```

For unattended runs, append `-NoPause`. Exit code 0 means extraction, receipt integrity and package QC execution passed. Read the QC findings separately; duplicates, missing depth/CRS information or unsupported formats are not automatically repaired. Nonzero exit means failure; retain the log and choose a new run after correcting the cause.

Extraction covers supported native metadata, well-head records, validated point/polygon/trajectory layouts, LAS tables, supported Excel sheets, shapefiles and Petrel Well Tops ASCII. Unsupported proprietary arrays remain preserved or explicitly unavailable. ZGY/SEG-Y and other specialist formats may be inventoried rather than converted by this extraction flow. This is not a universal native decoder, geological approval, or Petrel re-import test. Input Petrel release defaults to `unknown`.

`00_manifest/toolkit_files.json` records hashes for the entire delivered bundle. Startup verifies those hashes before extraction. `00_manifest/runtime_provenance.json`, `00_manifest/dependency_inventory.json`, `requirements-standalone-lock.txt` and `THIRD_PARTY_NOTICES.md` record the bundled runtime, exact dependencies and licenses. No project data, local credentials, source corpus or proprietary Petrel binaries are included.

The runtime comes from the [official Python Windows release manifest](https://www.python.org/ftp/python/3.13.15/windows-3.13.15.json). This package uses Python 3.13.15 x64 and verifies the archive against the SHA-256 published there.
