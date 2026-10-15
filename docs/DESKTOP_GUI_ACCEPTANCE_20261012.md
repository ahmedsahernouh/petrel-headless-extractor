# Desktop GUI candidate: local acceptance, 12 October 2026

This records the preview-2 local checks. Subsequent VM testing found two GUI
defects and produced preview-5; see the [VM findings and current candidate](DESKTOP_GUI_VM_ACCEPTANCE_20261012.md).
The current public download remains RC1009; this document does not approve or
publish a GUI release.

## Boundary and build

The Desktop frontend and independent supervisor invoke the existing standalone
launcher. No engine script, decoder, runtime file, CLI contract or output schema
changed. The Headless edition has no GUI files or dependency on the frontend.

Both candidate editions use the published RC1009 archive, SHA-256
`e17674c71c9a64bb93d3f883c2ab247d5d109e4d273837042159cfe9f24a0a23`.
All **102 baseline archive members** match byte for byte. After each fresh local
offline installation, all **5,671 managed files** matched their engine manifest.

Candidate GUI revision: `preview-2`. Implementation source:
`7cf9d784a07948f1784d519a618f813a5aea9c88`. Product version remains 1.0.0;
no next-release version has been assigned. Later documentation-only commits do
not change those candidate archives.

| Candidate archive | Bytes | SHA-256 |
|---|---:|---|
| FieldViewer-Desktop-GUI-preview-win64.zip | 58,798,762 | `71e2787d751c73ab4abec1950e5478dae1441aab41437a4769c8909a9967245b` |
| FieldViewer-Headless-GUI-preview-win64.zip | 58,774,244 | `bd2ac7dbce145069be8d57b2f6f209904417872c3cb864965265e5104fbffb35` |

## Observed checks

- Seven archive-builder tests passed: baseline parity, manifests, edition
  separation, hash mismatch, path/case conflicts and overwrite protection.
- Windows PowerShell 5.1 core checks passed: exact source-pair scope, neighboring
  project exclusion from size scans, source/output separation, paths with spaces
  and Unicode, matching request/receipt links, JSON empty-key fallback, outcomes,
  process identity, measured progress and closed diagnostic-log copies.
- A synthetic backend retained stdout/stderr and exit 7. A separate lifecycle
  test proved that a backend continued after its launching parent exited;
  one-tick creation-time and command-line mismatches were rejected. Exit 0
  without an engine receipt was not classified as extraction success.
- Bounded event-reader tests passed for UTF-8 byte offsets, oversized lines,
  heartbeat/activity distinction and saved-session corruption/missing evidence.
  A bad saved session blocks a new GUI run.
- One tiny synthetic FloatWellLog fixture ran through CLI and the GUI adapter
  in both modes. Full extraction returned exit 10 / `completed_with_gaps` on
  both routes, including the same deliberately incomplete Model fixture gap.
  The entire export index and all three LAS/CSV/JSON files matched byte for byte.
  Report-only returned exit 0 / `completed`, no export index and no retained
  dataset exports on both routes. All three source-file hashes were unchanged.
- Both final ZIPs passed fresh offline BAT installation/checks in separate local
  folders with spaces. The Desktop BAT also rendered and exited successfully.
  Normal and minimum-width layouts were inspected. No extraction engine ran
  during these screenshot checks.
- The email interop code compiled and attachment preparation was tested. No
  mail client was opened, email sent, project attached or upload performed.

The fixture tests are not additional real-project trials. The unchanged engine's
earlier acceptance does not establish GUI, mail-client or DPI acceptance.

## Remaining acceptance

Before publishing Desktop, perform independent clean-Windows GUI testing:
interactive selection and running, close/reconnect, high DPI and keyboard use,
network-path behavior, and supported mail clients/fallback. Actual email delivery
is user-controlled and remains unverified. The local checks above preceded the
separate VM trial; they do not establish that trial's result.

The GUI reads at most 128 KiB of events per tick and may lag a large backlog.
Receipts above its 32 MiB display limit require manual review; original files are
preserved. Folder sizes are bounded logical-path observations, include aliases,
and can be partial. Unknown units and existing engine coverage gaps remain
explicit. See the [Desktop guide](DESKTOP_GUI.md).

Validation datasets and diagnostic archives are not distributed. Preview ZIPs
are development artifacts, not public release downloads. The checks above apply
only to the identified candidates.
