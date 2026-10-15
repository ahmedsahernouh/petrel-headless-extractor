# Large explicit native grids

Scoped source extension dated 2 October 2026.
The running UPDATE1002 engine predates this change.

A retained ValGrid2 blob, UUID `0109b006-ffe8-423f-982e-0ee66fc43395`, Data PK
8780, contains a 1,692 × 3,594 explicit XYZ grid. Its 35,286,176 compressed bytes
expand to 146,705,651 bytes, exceeding the previous aggregate 128 MiB reader
limit. The existing ten-million-node limit was not exceeded: this grid has
6,081,048 nodes, of which its stored mask marks 3,263,692 defined.

The ValGrid2 reader now decompresses independent blocks into a bounded BXML
spool and materializes one typed body after discarding those blocks. Small
objects retain the existing typed-parser compatibility. Bodies exceeding
128 MiB require the exact packed ValGrid2 version `[0,0,0,0,0,2,0,1,1]`, packed
int32 dimensions and binary64 XYZ arrays. Scalar-node expansion, foreign array
types and excessive metadata nodes/lists are rejected in this extended profile.

Bounds remain explicit:

- Compressed native object and each expanded LZ4 block: 128 MiB each.
- Aggregate expanded framing and single body: 256 MiB each for this reader.
- Grid nodes: ten million, unchanged; dimensions, masks, finite coordinates,
  source identity and applicable Model bounds still must pass.
- In-memory spool threshold: 2 MiB; larger intermediate bodies use temporary
  files beside generated output, closed on success or failure.
- Other native object readers retain the original 128 MiB document limit.

The retained original blob and copied PET/Model were freshly hash-verified.
Independent .NET typed-array reads agree with all 18,243,144 source XYZ scalars
and the complete native node mask. A reconstructed one-object SQLite replay
passes unchanged Model-bounds checks and numerical readback, and its final
indexed files contain every defined XYZ node in original order and the complete
run-length node-definition mask. The compact output does not export
raw XYZ values of undefined nodes. Units and CRS remain unresolved.

The related suite ran 281 unique tests: 280 passed, one Windows privilege skip,
zero failures/errors. Peak measured working set for recovery, reference loading
and verification together was 652,730,368 bytes. That measurement includes
the verifier and is neither the spool's memory budget nor a universal bound.

The bounded checks include an independent .NET reference read and one retained grid; detailed fixtures are not included in this public repository.
This validates one retained grid, not all 50 rejected grids, a complete project,
scientific semantics or receiving import. The SQLite carrier was reconstructed;
the original projects and frozen trial engines were unchanged. Issue closure
and wider verification remain provisional.
