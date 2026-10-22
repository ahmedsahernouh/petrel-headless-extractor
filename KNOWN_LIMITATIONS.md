# Known limitations - Field Viewer 1.0.0-beta.3

- Supported native layouts only; unsupported objects and conversions are reported as gaps.
- A file-specific long path may leave that file/conversion unavailable; peers continue.
- CRS/datum output describes stored declarations, not coordinate correctness, datum selection or a transformation.
- Public beta3 has RESCUE grid metadata inventory only, with no static-model RESCUE export.
- Full-model, fault-contact/activity semantics and general version coverage are not established.
- Closing Desktop does not stop its backend; reopening observes its recorded session.
- Clean-VM/DPI/mail-client/reconnect and receiving-application coverage remain incomplete.
- Review final receipts, hashes and QC before using recovered data.
