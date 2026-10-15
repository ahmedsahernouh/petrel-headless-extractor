# Large packed regular grids

3 October follow-up: packed V0 arrays now have the separate resource profile
`packed_regular_v0_chunked/1`, using the same 32-million-node/65,536-axis bounds
and 100,000-node coordinate chunks as V1. It requires exact V0 version
`[0,1,1,0,0,0,0,2,0,1,1]`, packed float32 values and int32 dimensions; all
existing shape, mask, geometry and Model checks still apply. Catalogue receipts
recognize each version only with its matching resource-profile name.

A retained payload supplies the full independent numeric proof;
its unresolved true-depth-flag Model profile still blocks production acceptance.
Resource admission alone does not admit this object. The bounded diagnostic and export checks are summarized below.

## Original V1 profile and verification

Source extension dated 2 October 2026, with provisional grid and performance evidence.

A retained large-grid object
was rejected at the ten-million-node memory guard. Its 4,087 by 7,427 lattice has
30,354,149 raw float32 samples and 1,709,519 defined nodes. The compressed blob is
7,346,596 bytes. The grid's native definition mask, rather than its compressed
file size, determines which samples are usable.

A separate `packed_regular_chunked/1` resource profile now generates coordinates
in selections of at most 100,000 nodes. It keeps the packed float32 samples and
Boolean masks, instead of creating full float64 coordinate/index arrays. The
extension requires the existing current RegValGrid2 version
`[1,1,1,0,0,0,0,2,0,1,1]`, packed float32 values and int32 dimensions. Its maximum
is 32 million nodes, with each axis at most 65,536. The ordinary ten-million-node
bound, explicit-grid reader limits and 128 MiB general document limit remain
unchanged. Shape, masks, finite values, geometry, Model bounds, identity and disk
space still must pass their existing checks.

CSV and XYZ contain every usable node in original order, with original lattice
indices. Complete run-length node and cell masks retain the full lattice. Native
undefined/raw-null slots are not promoted into usable values. An unrotated grid
also gets a full node-registered ZMAP, including its explicit output nulls.
Coordinates, samples and signs are not rounded, resampled or converted to guessed
units. The preview examines underlying masks without allocating a full-grid
integer prefix sum; its statistics use bounded passes.

The independent .NET typed reader agrees with every raw float32 sample and mask
bit. The defined-node XYZ extrema equal the same UUID's copied Model cached
bounds exactly on all six coordinates. A reconstructed one-object SQLite replay
then passed recovery and final indexing. Parent verification compared all
1,709,519 CSV/XYZ rows, the complete node/cell masks and all 30,354,149 ZMAP
positions to that reference. There are six indexed files, including metadata and
masks. The independent reader shares the outer bounded LZ4/BXML framing, but uses
System.Xml for typed values and its own coordinate/mask calculations.

The focused integration suite passed 204 unique tests with no failures, errors
or skips. The runtime doctor reported ready. Recovery took 50.19 seconds; peak
measured process working set was 638,554,112 bytes (609 MiB), including recovery,
reference loading and verification. This is one measured workload, not a general
peak-memory guarantee or matched-project speedup.

Units and CRS remain unresolved, so the record correctly stays `missing_metadata`
with numeric recovery. This is not an actual receiving import, full-project
acceptance of current source, universal grid support or final issue closure.
Frozen engines and original projects were unchanged. The earlier full-project
rejection remains in its historical review; this later retained-object repair
does not rewrite that result.

The retained replay provides bounded numerical evidence. Its generated outputs and cleanup receipts are outside this public repository.


## Large-grid catalogue verification — 2 October 2026

Fixed catalogue rejection of the already verified 30,354,149-node packed regular grid. Receipt normalization recognizes only the exact current RegValGrid2 version and packed_regular_chunked/1 profile above the ordinary 10-million-node bound, up to the existing 32-million-node/65,536-axis limits. No parser/resource guard changed. 68 unique scoped tests: 67 passed, one Windows symlink privilege skip. Actual retained replay receipt now yields full 4,087x7,427 registration; six artifacts and project key unchanged. Two catalogue entities mean one recovered grid and its unresolved parent reference. Units/CRS/import remain unqualified.

The catalogue check uses a recorded full numerical readback; it does not reread bulk payloads or establish newest-source full-project acceptance.


Current release scope is in [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
