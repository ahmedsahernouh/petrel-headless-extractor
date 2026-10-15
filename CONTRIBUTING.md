# Contributing to Field Viewer

Thanks for helping improve Field Viewer. Bug reports, small fixes, documentation
improvements and examples of unsupported data are welcome.

## Share a useful report

Open an issue with the Field Viewer version, Windows version, Petrel save version
if known, the steps you followed and the result you expected. A short log excerpt
or a small anonymized example is usually enough to start. The public SLB demo is
useful for reproducible examples.

For a possible security issue, use the contact in [SECURITY.md](SECURITY.md).

## Send a change

1. Fork the repository and create a branch for one focused change.
2. Describe the problem and the resulting behavior in your pull request.
3. Run the checks relevant to the files you changed and include their results.
4. Keep source Petrel projects unchanged and preserve unresolved units and CRS.

The extraction code and its checks live in `scripts/`. Build and release details
are in [the build guide](docs/BUILD.md) and [the release guide](docs/RELEASING.md).

## License and credit

Contributions use the project's [Apache-2.0 license](LICENSE). By submitting a
contribution, you confirm that you have the right to share it under that license.
You retain copyright in your contribution; no copyright assignment is required.
Keep applicable third-party licenses and author notices with reused material.
