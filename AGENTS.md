# Portable Petrel Extractor Agent Instructions

By [Ahmed Saher Nouh](https://github.com/ahmedsahernouh) · [SaherLabs](https://saherlabs.dev/) · [GitHub repository](https://github.com/ahmedsahernouh/petrel-headless-extractor)

This toolkit is read-only with respect to source Petrel projects.

Before release work, review [deferred release requests](docs/RELEASE_BACKLOG.md). Implement pending requests in their specified update; registration alone does not authorize an immediate release.

1. Run `scripts\doctor_portable_petrel_toolkit.ps1` before extraction.
2. Require an exact `.pet` path and matching `<stem>.ptd` directory.
3. Require the output root to be outside the source project.
4. Never launch Petrel, use Ocean, or mutate `.pet/.ptd` files.
5. When projects share one directory, exclude every neighboring `.pet` file and `.ptd` directory from companion ingestion and report the exclusions.
6. Preserve source companions and hashes before interpreting converted derivatives.
7. Treat converter success as structural execution evidence, not CRS, semantic, scientific, approval, or officiality evidence.
8. Report unsupported formats and cross-version gaps explicitly.
9. Do not describe this toolkit as a universal proprietary Petrel decoder.
