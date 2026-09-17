# Portable Petrel Extractor Agent Instructions

By [Ahmed Saher Nouh](https://github.com/ahmedsahernouh) · [SaherLabs](https://saherlabs.dev/) · [GitHub repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

This toolkit is read-only with respect to source Petrel projects.

The core purpose is Petrel binary/native payload recovery into open, usable formats. Read [the purpose and coverage contract](docs/BINARY_EXTRACTION_PURPOSE.md) before adding features. Do not prioritize conversions between already open formats (for example CSV-to-LAS), or count companion conversion, inventory or preserved bytes as native decoding.

Before release work, review [deferred release requests](docs/RELEASE_BACKLOG.md). Implement pending requests in their specified update; registration alone does not authorize an immediate release.

1. Run `scripts\doctor_portable_petrel_toolkit.ps1` before extraction.
2. For project extraction, require an exact `.pet` path and matching `<stem>.ptd` directory. For the direct ZGY converter, require the exact `.zgy` file ; unknown domain/units may export with explicitly unspecified headers and an exact native-axis sidecar; see [the supported profile](docs/ZGY_TO_SEGY.md).
3. Require the output root to be outside the source project.
4. Never launch Petrel, use Ocean, or mutate `.pet/.ptd` files.
5. When projects share one directory, exclude every neighboring `.pet` file and `.ptd` directory from companion ingestion and report the exclusions.
6. Preserve non-seismic companions and hashes before interpreting derivatives. Seismic may be referenced in place; full seismic hashing is optional and on by default (disable with -NoFullHash). Record the actual integrity scope, never imply a skipped SHA-256 passed.
7. Treat converter success as structural execution evidence, not CRS, semantic, scientific, approval, or officiality evidence.
8. Report unsupported formats and cross-version gaps explicitly.
9. Do not describe this toolkit as a universal proprietary Petrel decoder.
