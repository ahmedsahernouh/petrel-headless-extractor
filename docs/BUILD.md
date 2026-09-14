# Build and test

Use Windows x64, Python 3.13 x64 with pip, and Windows PowerShell. Building downloads the pinned Python runtime and wheels. Running a built release does not download or install anything.

From the repository root:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\download_standalone_inputs.ps1 -PythonPath .venv\Scripts\python.exe
.venv\Scripts\python.exe scripts\build_standalone_petrel_toolkit.py --runtime-archive build\downloads\python-3.13.15-embed-amd64.zip --wheels build\downloads\wheels --output-root build\releases-v0.2.1
```

The builder verifies the official runtime SHA-256, installs only binary wheels matching `requirements-standalone-lock.txt` with `--require-hashes --no-index`, checks the embedded runtime, and writes `PetrelExtractor-0.2.1-win64.zip` plus a SHA-256 file. The ZIP contains a short `PetrelExtractor` root folder. A path-length gate models Explorer's additional ZIP-stem directory under Downloads. Use a fresh output directory for each build; existing package folders are not overwritten. Licenses and per-file inventories are included. Build timestamps and ZIP metadata vary; file hashes provide the exact identity of each release.

Run acceptance on the ZIP you built:

```powershell
.venv\Scripts\python.exe scripts\test_standalone_petrel_toolkit.py --zip "build\releases-v0.2.1\PetrelExtractor-0.2.1-win64.zip" --evidence-dir "build\acceptance-new"
```

The evidence directory must be new. The harness creates synthetic projects and runs the actual BAT from a separate temporary folder. It leaves logs and receipts for inspection. Optionally add repeated `--project "E:\Your Data\Project.pet"` arguments to test your own closed projects; their data and output remain local.

To run directly from source, install the core dependencies and use an explicit project path:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-core.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\doctor_portable_petrel_toolkit.ps1 -PythonPath .venv\Scripts\python.exe
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke_portable_petrel_extract.ps1 -ProjectFile "E:\Test Data\Example.pet" -OutputRoot "E:\Extracted Results" -CompanionMode convert -PythonPath .venv\Scripts\python.exe
```

The direct PowerShell entry point produces extraction validation and the HTML report. The standalone BAT additionally performs full-bundle integrity checks, operation-receipt checks, and automatic package QC.
