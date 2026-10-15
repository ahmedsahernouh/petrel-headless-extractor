# Reading older Field Viewer deliveries

`scripts/geoviewer_legacy_contract.py` reads an existing pre-contract
`FILE_INDEX.json` and its existing extraction package. It reuses the normal
catalogue normalization and assessment rules in memory. It writes no files into
the run, does not follow recorded original-project paths, and does not convert
or copy bulk exports. Existing applications can continue reading FILE_INDEX.

```powershell
python scripts/geoviewer_legacy_contract.py "D:\run\project_data\extraction\package\p_id" "D:\run\project_EXPORTS" --output "D:\review\legacy-assessment.json"
```

The output must be a new file outside the source run. The package and EXPORTS
must retain their bounded common run layout; moving that complete tree is
supported. Escaping paths and linked ancestry are rejected. When a committed
`INTEROPERABILITY_CURRENT.json` exists, use the normal contract validator instead.

The Python entrypoint is:

```python
from geoviewer_legacy_contract import assess_legacy
report = assess_legacy(package, exports, profile="source", hash_artifacts=False)
```

The report schema is `geoviewer.legacy-assessment/1`. It preserves inventory,
native recovery, accepted/partial artifacts, relationships and historical QC as
separate evidence. Pruned artifacts remain in the original inventory with their
current unavailability recorded. It explicitly has `committed_generation=null`:
this new assessment is not an original publication receipt. Missing generation
evidence blocks source-ready/receiving qualification; that does not mean every
legacy file is unreadable. Neither this adapter nor the receiving profiles execute
an OpendTect/PaleoScan import.

The `catalogue_input_fingerprint` (also named `derived_local_fingerprint`) binds
exact index and known small receipt bytes, aggregate head-table bytes, consumed
seismic sidecars, their missing states, and the five scripts used to normalize
and assess them. Parsed index/receipt/head snapshots are reused; changes detected
during assessment reject the result. Fingerprints exclude bulk payload contents,
current availability, assessment options and actual receiving execution. Those
are separate report observations. Optional caller index/receipt hashes must
match the existing inputs; they cannot replace them or invent missing evidence.

Default artifact checks inspect size/state only and can miss same-size content
changes. `--hash-artifacts` opts into fresh hashes of currently available indexed
files. It cannot revalidate bulk that was deleted after retention. The adapter
reads at most 32 MiB per index/known receipt/head table and 1 MiB per consumed
sidecar, with at most one million index rows. Unsupported index schemas and
malformed receipt rows produce explicit errors. These are bounded compatibility
profiles, not acceptance of arbitrary historical packages.

Current verification is recorded under
The bounded validation checks cover input
changes, malformed records, escapes, relocation, stable artifact keys and
accepted/partial/empty delivery. A retained validation delivery exercises
1,290 index entries and 2,475 entities: 383 retained artifacts and 907 unavailable
artifacts, with no fabricated generation or receiving qualification. Retained
hashes verify 382 declared artifacts; one retained artifact has no declared
digest. Original projects and missing bulk were not reread. Broader legacy
populations, semantic calibration, current-engine full trials and consumer
acceptance remain separate requirements.
