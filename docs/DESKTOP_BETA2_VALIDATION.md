# Desktop Beta 2 candidate validation

The candidate is prepared for review and is not yet published. Its exact archive
hashes and check results are recorded in the accompanying validation asset.

## Candidate boundary

The engine is based on the verified public beta 1 archive. Only release
documentation and metadata are refreshed before the Desktop interface is added.
Extraction scripts, CLI launcher and runtime archives must remain byte-identical.
Desktop and Headless must contain matching shared-engine members.

The interface uses the locally checked preview 6 layout. Its native controls
select paths and mode, display recorded progress, and open available results.
Prior preview evidence covers synthetic payload parity, process lifetime and
identity, bounded event reads, output-path admission and a VM report-only run.
Those earlier checks are not an acceptance result for these new archives.

## Before public release

- Verify CRC, every package member, shared-engine parity and offline dependencies.
- Observe the exact Desktop package launch in an interactive Windows session.
- Exercise project/output selection, Full extraction and Report only on a small
  known fixture; verify source hashes, receipts, QC, report links and file index.
- Check close/reconnect, resize, high DPI and keyboard navigation.
- Check diagnostic draft/fallback behaviour without sending a message.
- Retain beta status and the supported-profile limitations.

New interactive acceptance and screenshots are pending. Existing screenshots illustrate the
preview 6 layout and the public SLB 2018 demo outputs; they do not represent a new
beta 2 extraction. Network paths and mail-client compatibility remain unqualified.
