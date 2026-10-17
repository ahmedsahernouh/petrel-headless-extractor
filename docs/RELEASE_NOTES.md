# Field Viewer Desktop and Headless - 1.0.0-beta.2

Prepared prerelease candidate. This version adds a Windows desktop interface
around the existing extraction engine.

## Downloads

- [Desktop package, including the CLI and Start Here](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.2/FieldViewer-Desktop-1.0.0-beta.2-win64.zip)
- [Headless package, including Start Here](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0-beta.2/FieldViewer-Headless-1.0.0-beta.2-win64.zip)

Extract the complete ZIP into a short writable folder. Both Start Here guides
are in its root. Desktop users open `FieldViewer_Desktop.bat`; command-line and
agent users retain `FieldViewer_data_extractor.bat`.

## What is new

- Browse for the Petrel project and a separate output folder.
- Select Full extraction or Report only and start the run with one button.
- Follow recorded activity, elapsed time and measured stage progress.
- Open available HTML reports, logs and output folders from the interface.
- Close and reopen the interface while the independent backend continues;
  reconnect only to a verified recorded session.

The extraction scripts, bundled runtime, numerical checks and output contracts
are unchanged from beta 1. This update adds no new decoding coverage.
Desktop and Headless share identical engine files.

## Scope

Supported profiles recover native logs, surface grids, points, polygons,
trajectories, well-head evidence and readable workflow definitions. Supported
local ZGY can be converted to SEG-Y. Unknown units, CRS and seismic axes remain
explicit; values are never silently rescaled. Complete 3D reservoir/RESCUE export
and universal Petrel-version coverage remain outside the current scope.

Exit 10 means completed with stated gaps. Review the HTML report and QC before
using exported files. Receiving-application acceptance remains separate.

## Validation

See [candidate validation](DESKTOP_BETA2_VALIDATION.md) for completed checks and
the remaining interactive Windows acceptance.

Field Viewer is the open-source extraction component of a larger, non-public
geoscience project. The wider project is not included in this distribution.
