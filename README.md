# Petrel Headless Extractor

By [Ahmed Saher Nouh](https://github.com/ahmedsahernouh) · [SaherLabs](https://saherlabs.dev/) · [GitHub repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

Extract supported data and metadata from a Petrel project into a browsable, checksummed package, without opening Petrel.

**[Download the standalone Windows x64 ZIP](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/latest)** · [Usage and supported formats](docs/USAGE.md) · [Build from source](docs/BUILD.md)

**Version 0.5.0:** adds an [offline visual report and complete foldable data inventory](docs/VISUAL_REPORT.md), with maps, log tracks, bounded seismic previews, statistics and data links.

**Purpose:** recover Petrel binary data into open formats. Version 0.4.0 adds [native well logs to LAS/CSV and supported surfaces to XYZ/CSV](docs/NATIVE_LOGS_SURFACES.md) to the normal project BAT. The [beta ZGY-to-SEG-Y BAT](docs/ZGY_TO_SEGY.md) remains available separately. See the [coverage and limits](docs/BINARY_EXTRACTION_PURPOSE.md).

The release ZIP includes Python and all pinned runtime dependencies. No Python installation, Petrel, Ocean SDK, administrator access, or internet connection is needed to run it. Windows 10/11 x64 with built-in Windows PowerShell is the target.

## Run on your project

1. Download the **standalone ZIP** from Releases and extract the whole folder. GitHub's automatic "Source code" archives do not include the runtime.
2. Open the inner `PetrelExtractor` folder containing the BAT, `scripts`, and `bootstrap`. Keep `Project.pet` and its complete matching `Project.ptd` directory together elsewhere. Close the project in Petrel during extraction.
3. Drag the `.pet` file onto `run_portable_petrel_extract.bat`, or double-click the BAT and enter its path.
4. The BAT installs Python and dependencies from its bundled cache on first launch. Later launches automatically repair missing or damaged runtime files before checking imports. This stays inside the extracted toolkit folder and works offline.
5. Open the printed `PROJECT_REPORT.html` path. Results default to `%USERPROFILE%\Petrel_Extracts`, in a new folder for each run.

Keep the whole extracted release together: the BAT alone is a launcher, not the application. Choose an output folder outside your source project.

**Windows "Path too long" during extraction:** cancel the incomplete extraction and use **v0.2.1 or newer**. These releases use short ZIP and folder names. Extract to a short destination and do not skip files. Version 0.2.0's long repeated folder names could interrupt Windows Explorer extraction and leave the BAT without its scripts.

```bat
run_portable_petrel_extract.bat "E:\Test Data\Example.pet" "E:\Extracted Results" convert
```

Append `-NoPause` for unattended runs. Check and repair dependencies without opening a project:

```bat
run_portable_petrel_extract.bat --check -NoPause
```

The BAT shows an overall stage bar and elapsed timer automatically. Large-file hashing also shows byte progress and an estimated time remaining for that hash pass. Stages take different amounts of time; the bar reaches completion only after extraction and QC pass. See [progress and timer details](docs/USAGE.md#progress-and-timer).

## What you get

- A separate beta BAT for **binary ZGY to SEG-Y**, with amplitude/geometry checks, progress and timing; see [supported profile](docs/ZGY_TO_SEGY.md).
- Native `.pet`/`.ptd` copies and source/artifact SHA-256 hashes.
- Supported native metadata, well heads, and structurally validated point, polygon, and trajectory layouts.
- Native continuous logs as LAS/CSV, categorical boundary records as CSV, and validated surface arrays as XYZ/CSV with node/cell masks. Units and geometry must match the supported profiles.
- Supported LAS, modern Excel, shapefile, and Petrel Well Tops ASCII conversions.
- An HTML project report, searchable file inventory, extraction manifest, QC findings, unsupported-format inventory, and run receipts.

Existing open-format companion handling is secondary. It does not count as decoding native well logs, faults, grids or property arrays. Those gaps and the next binary parsers are listed in the [native coverage roadmap](docs/BINARY_EXTRACTION_PURPOSE.md).

`convert` is the standalone default: preserve sources and attempt supported conversions. `copy` preserves companions without converting them. `inventory` inventories companions without copying them. **All three modes copy the selected native `.pet`/`.ptd` files.** Neighboring Petrel projects are excluded from companion ingestion.

## Scope and limits

The extractor reads source projects and writes new output folders. It never launches Petrel or modifies source stores. It does not universally decode proprietary Petrel formats. Unknown native layouts can stop conversion; ZGY/SEG-Y and other specialist formats may be inventoried instead of converted. CRS, units, geological validity, and Petrel re-import require independent checking. Input Petrel version defaults to `unknown`; cross-version compatibility is not established.

The release is checked with synthetic fixtures, actual BAT execution from a relocated folder, system Python unavailable on PATH, poisoned Python environment variables, checksum tampering, missing project stores, and source/output overlap. Local supplied-project checks are summarized without distributing their data in [validation](docs/VALIDATION.md).

## Source and development

This repository contains the extractor, its build scripts, tests, and documentation. The full agent/MCP tooling is a separate project: [petrel-agent-mcp](https://github.com/ahmedsahernouh/petrel-agent-mcp).

Source users need Python and dependencies; see [BUILD.md](docs/BUILD.md). The prebuilt release installs its bundled runtime automatically inside its own folder. The source checkout's legacy BAT defaults to `inventory`; the standalone release defaults to `convert`.

Licensed manuals, KB content, demonstration projects, client data, machine-local settings, and private development history are not distributed.

## License and attribution

MIT License, copyright 2026 Ahmed Saher Nouh. Bundled Python and dependencies retain their own license texts and notices. Releases include `THIRD_PARTY_NOTICES.md` and runtime/dependency provenance manifests.

Petrel and Ocean are trademarks of SLB. This is an independent interoperability project, not affiliated with or endorsed by SLB.
