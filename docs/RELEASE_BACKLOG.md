# Public roadmap

The [Field Viewer 1.0.0 release notes](RELEASE_NOTES.md) define the current public scoped beta and its supported profiles. The roadmap below describes work still requiring implementation or validation; it does not expand the released capability matrix.

| Area | Current boundary | Next acceptance gate |
|---|---|---|
| Optional desktop interface | A GUI design is documented in [the GUI plan](NEXT_RELEASE_GUI_PLAN_20261012.md); no GUI edition is published. | Build Desktop and Headless editions from one verified engine, confirm matching engine hashes and package contents, then complete independent clean-Windows GUI and CLI checks. |
| Receiving interoperability | Contracts and preparation profiles exist; imports into Petrel, OpendTect and PaleoScan remain untested. | Validate representative outputs in the named receiving application and record object identity, values, geometry, CRS/units handling and unsupported cases. |
| Complete 3D model export | Native component inventory and bounded typed reads do not establish complete model topology or RESCUE output. | Recover topology, active cells, faults, properties and relationships; compare with independent reference exports and receiving readback under [the RESCUE plan](RESCUE_EXPORT_PLAN.md). |
| Petrel version coverage | Support is determined by verified container, storage and payload profiles, not by a blanket year range. | Extend the [compatibility matrix](PETREL_VERSION_COMPATIBILITY_PLAN.md) with representative saved-version fixtures, category-level numerical checks and explicit gaps. |
| Reliability and diagnostics | Isolated failures, partial results and local support logs need clear scope and honest status. | Exercise bounded recovery, interruption, output finalization and relocated package behavior against the [robustness plan](ROBUSTNESS_LOGGING_MAP_PLAN.md). |

The report must keep **full project inventory** separate from **decoded and converted data**. A readable container or listed object is not a validated export. Unknown units, CRS and seismic axes must be explicit; numerical validation and receiving-software interpretation remain separate checks. Unsupported or incomplete objects remain visible without being promoted to successful deliverables.
