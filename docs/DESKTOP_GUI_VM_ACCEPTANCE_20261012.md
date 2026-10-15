# Desktop GUI preview: VM findings, 12 October 2026

The VM trial found two GUI defects. Both have source corrections in preview-5;
interactive GUI acceptance remains pending. RC1009 is still the public download.
No GUI release is published by this test.

## Candidate and boundary

Preview-5 source revision: `1289a8eb5da9c5b32e1c733cdc81c5e37ac9170c`.
Product version remains 1.0.0. Both editions use the exact published RC1009 engine.
All 102 shared archive members remain byte-identical; no decoder, runtime,
agent entry point, output schema or parser budget changed.

| Archive | Bytes | SHA-256 |
|---|---:|---|
| Desktop preview-5 | 58,799,579 | `86261952d8c3c22cb53269a58c7dd101609a9445cd8802565131e585b3368c59` |
| Headless preview-5 | 58,774,242 | `f8cafc618461ce2849ab9f30d303fd7ff771917e3a68b78c3606faf2763dd5ab` |

The VM first installed preview-2 in an isolated directory with spaces using its
bundled Python and offline runtime. The doctor reported ready, 5,671 verified
files and a passed ZFP round trip. Its test wrapper lost the process exit code;
that wrapper failure is preserved, not called exit 0. Parent verification checked
the primary doctor JSON, terminal event, process absence and all managed hashes.

After the first fixture ended, five changed GUI/package files were updated from
the verified preview-4 ZIP. Their original versions were retained. All 111 package
members matched preview-4. The completed engine/runtime installation was reused;
this was not a second fresh installation or a change to a previous user install.

After accepting the completed demo run, preview-5 replaced only the BAT,
Desktop readme and two package-identity files. All prior versions were retained
and all 111 final package members matched. Its core, supervisor and engine are
identical to the successful preview-4 report run. No extraction was repeated for
this launcher correction.

## Findings and corrections

**FV-ISSUE-0049 — GUI startup failure returned success.** Under an SSH session,
WinForms rejected a modal window because the process was not interactive, yet
preview-2 returned exit 0 and created no screenshot. The entry point now treats
errors as terminating and rejects a noninteractive desktop before loading the
form. The corrected entry returned exit 1 with an explicit headless-launcher
message on the VM. A local interactive render returned exit 0 and was visually
checked; that local image is not a VM screenshot.

An additional packaged BAT check exposed a second part of this startup defect:
direct `cmd.exe /c` invocation lost the failure code from the parenthesized smoke
branch, whereas `call` and the PowerShell entry returned 1. Diagnostic child exits
7 and the original outputs are preserved. Preview-5 moves that synchronous path
to a label. Five real Windows checks cover direct/call success, child failure and
missing screenshot argument. The normal BAT still dispatches the window
asynchronously; its return code alone is not GUI acceptance.
The exact preview-5 packaged BAT subsequently returned exit 1 with the expected
noninteractive message and no screenshot on the VM.

**FV-ISSUE-0050 — nested GUI output exceeded the assessment path budget.** The
first VM fixture produced its three expected LAS/CSV/JSON exports, all matching
the local CLI hashes, but also recorded an unexpected interoperability report
failure: `No such file or directory` while creating a nested `.assessment-stage`
temporary file. It is not classified as complete CLI/GUI outcome parity.
The same frozen assessment writer reproduced the failure with the long layout
and succeeded under a short output root.

New GUI runs use shorter container names while keeping the full session ID in
their receipt. A conservative preflight reserves the longest flat/sharded
assessment paths and rejects an overly deep output directory before starting.
It asks the user to select a shorter path, such as `C:\FV`; it does not redirect
the output or change Windows settings. Existing sessions and failed results
remain intact. Seven focused path checks, the core suite and bounded-event suite
passed locally after the corrections.

## VM execution status

The packaged GUI core/supervisor completed its first corrected report-only test
on the existing 2018 demo project: **exit 0 / completed**. The engine receipt
records 1,612.353 seconds; the launcher finished support collection at
19:05:26 UTC (12:05 PDT). No converted datasets were produced in report-only mode.

Independent verification matched all three complete physical/logical profile
assessments, their publication bindings and human reports. All **686 input files**
(1,007,794,187 bytes) and **5,671 managed engine files** passed fresh hash checks.
Both the fixture and demo support ZIPs passed CRC and content-member checks, with
no unavailable diagnostic members. A background adapter test does not establish
that a user clicked Run or that the window rendered correctly on the VM.

All 12 available links from the completed GUI session resolved to existing
reports/folders. The application, demo report and results folder now have named
VM desktop shortcuts plus a short handoff note. The two finished test tasks and
unused frontend test task are disabled; owned extractor processes are absent.

The first fixture's three source files were unchanged and all 13 available GUI
link targets existed. Its extra report-path gap remains recorded. No completed
fixture extraction was repeated to repair its report.

## Additional background checks

Additional background checks were completed on
13 October at 04:59 UTC (12 October, 21:59 PDT), three existing synthetic suites
passed against byte-identical copies of the installed preview-5 core and
supervisor in a separate test directory:

- The child completed after its launching parent exited. Exact creation-time and
  command-line mismatches were rejected; exit 0 without an engine receipt did not
  become extraction success.
- UTF-8 event cursors and oversized lines remained bounded. Timer events did not
  replace actual progress, and missing/corrupt saved sessions blocked new work.
- All seven output-path checks passed, including rejection before run creation,
  preserved full session identity, collision refusal and unchanged synthetic input.

Installed modules matched their before/after hashes. The 29-member evidence ZIP
passed CRC, and independent checks verified its digest, test scripts and primary receipts.
All owned synthetic processes exited. No real project was accessed or extracted,
no window or mail client was opened, and no scheduled task was added. The evidence
supplement is retained as non-distributed validation evidence.

## Remaining acceptance

VM window rendering, path selection, buttons,
close/reconnect, high DPI, keyboard navigation, network paths and mail-client
behavior remain unverified. No email was sent or mail client configured.
This was an isolated installation on the existing Windows VM, not a fresh OS
image or complete clean-Windows GUI acceptance.

The trial does not establish receiving-application or scientific acceptance,
universal Petrel-version support, or new full-project recovery. Existing unknown
units and decoder coverage gaps remain explicit. Both issue states are
provisional, with no automatic closure.

The validation datasets and diagnostic archives are not distributed. Reported
checks apply to the identified preview candidate and do not establish wider
compatibility or interactive acceptance.
