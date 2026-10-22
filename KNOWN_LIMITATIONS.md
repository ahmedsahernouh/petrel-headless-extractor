## r13 report and well-top scope

Automatic native tops are schema/Model-bound, not automatically calibrated against a Petrel export. Unknown units/CRS/datum remain unknown; native Z is not TVD. Short/open polygon parts may be genuine native linework. XY previews omit Z and do not establish geological ring topology. Grid previews preserve axis direction but do not reproject rotated or curvilinear meshes. KB/TD are shown only when recovered. Local browser automation could not exercise file links because of its URL policy; file targets, generated scripts and isolated renderer behavior were checked offline.

# Known limitations - Field Viewer 1.0.0-beta.3

- Supported native layouts only; unsupported objects and conversions are reported as gaps.
- A file-specific long path may leave that file/conversion unavailable; peers continue.
- CRS/datum output describes stored declarations, not coordinate correctness, datum selection or a transformation.
- Public beta3 has RESCUE grid metadata inventory only, with no static-model RESCUE export.
- Full-model, fault-contact/activity semantics and general version coverage are not established.
- Closing Desktop does not stop its backend; reopening observes its recorded session.
- Clean-VM/DPI/mail-client/reconnect and receiving-application coverage remain incomplete.
- Review final receipts, hashes and QC before using recovered data.
