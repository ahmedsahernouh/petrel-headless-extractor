# Desktop Beta 2 validation scope

Beta 2 adds the optional desktop interface and updated first-use guides to the
beta 1 extraction engine. Its supported decoding profiles are unchanged.
Complete 3D reservoir/RESCUE model export is not included.

## Completed checks

- ZIP CRC, package-member hashes and shared-engine parity between editions.
- 5,674 managed files, bundled dependency imports and a ZFP round trip.
- Local synthetic Full extraction and Report only through the GUI backend.
  Full extraction returned exit 10 with the fixture's intentional well-head
  metadata gap; Report only returned exit 0. QC passed on both.
- Independent sample/MD/null readback, unchanged source hashes, support ZIPs,
  and the 16 Full / 12 Report only available result targets.
- Process lifetime and identity, bounded event reading, path preflight,
  launcher failure propagation and three-width control layout checks.
- Offline HTML at desktop/mobile widths and visual review of the 14-page PDF.
- Isolated installation on an existing Windows 11 VM: all 117 outer package
  members and 5,674 managed engine files matched, the dependency check exited 0,
  and the shipped Desktop BAT opened the form. Project Browse opened the file
  picker and the selected synthetic project path appeared in the form.

The final release refreshes publication documentation and metadata. Extraction,
runtime, launcher and GUI code remain byte-identical to the prepared candidate.
The release assets and `RELEASE_MANIFEST.json` bind the final hashes; the release's
`VALIDATION.json` identifies the tested candidate and final comparison.

## Deferred checks

The visible GUI's output selection, Run workflow and result-opening controls,
close/reconnect, DPI/keyboard navigation and diagnostic mail-client behaviour
have not completed independent interactive acceptance. Network paths and
mail-client compatibility remain unqualified.

This is a scoped prerelease with those checks deferred. It is not a clean OS
image acceptance or a new full real-project extraction trial. Existing engine
evidence does not establish scientific interpretation, universal Petrel-version
coverage or receiving-application acceptance.

Start Here illustrates the preview 6 control layout and existing public SLB 2018
demo report/output examples. It does not claim a new beta 2 demo extraction.
