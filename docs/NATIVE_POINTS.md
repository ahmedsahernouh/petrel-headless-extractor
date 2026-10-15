# Native Points3 recovery and resource bounds

Current-source evidence, 2 October 2026. These changes postdate the active
UPDATE1002 frozen engine and do not change that running trial.

The retained Points3 object `8f482616-3a2c-41f4-aab0-ed730dd29f3b`, Data PK 344,
was rejected in two retained trial runs because its declared 2,114,390 vertices exceeded
the former two-million-point list limit. Its declaration agrees with the typed
array's 6,343,170 binary64 values. This was a resource-profile rejection, not
evidence of corrupt data.

Attribute-free, identity-free Points3 objects with one packed binary64 array now
use compact storage above 65,536 vertices. The root profile, exact array type and
length, missing-value rules and finite-coordinate guards remain enforced. The
existing 128 MiB native-document bound also bounds declared XYZ bytes. Larger
scalar-node, attribute-bearing and identity-bearing layouts are not admitted by
this extension; their existing two-million-point bound remains. The aggregate
retained point geometry has a 256 MiB accounting budget, including ordinal
indexes and conservative charges for small Python point lists. This budget is
not a limit on the entire process's working set.

CSV rows are generated in bounded batches instead of retaining one dictionary
per vertex. Missing slots preserve their original ordinals and raw components
in the existing auxiliary evidence table. No interpolation, CRS, units or
scientific meaning is inferred. An empty declaration cannot hide a nonempty
coordinate array.

The retained 19,905,069-byte original blob has SHA-256
`f209728edffb6ef79d7cacb27856f701c7b43934451039ae299cd7a7a140801f`.
An independent .NET `XmlDictionaryReader.ReadArray` agrees with every coordinate
in the final indexed CSV, including original row order. The replay recovered
2,114,390 points with no missing or nonfinite values. Peak measured process
working set was 309,661,696 bytes; this is one fixture measurement, not a general
speed or memory guarantee. The related suite ran 143 unique tests: 142 passed,
one Windows symlink-privilege skip, zero failures/errors.

The bounded checks include an independent .NET reference read and one retained point payload; detailed fixtures are not included in this public repository.
The SQLite carrier was reconstructed around retained original blob bytes; outer
LZ4/BXML framing is shared with the production reader. Separate identity-layout
cases remain unresolved. The replay covers one retained payload; complete-project
extraction and receiving import have not been tested.


## 3 October: exact compact Version 2 identity follow-up

Five retained Points3 payloads now recover 159 XYZ rows and 159 compact false-only identity rows, with 477 independent binary64 coordinate matches through final delivery. The 40 attribute vectors stay raw-only. 127 targeted tests pass, one Windows privilege skip. These checks cover retained payloads; no complete-project run includes this change.


## 4 October: raw Points3 attribute export follow-up

The same five retained objects now additionally export 40 typed vectors/1,272 stored values in five auxiliary CSVs. Microsoft NBFX readback independently matches all values and default bytes; all ten geometry/identity files remain byte-identical. 184 targeted tests pass, one Windows privilege test skips. Names, units, point associations and null/default semantics remain unresolved. Complete-project delivery and receiving import have not been tested for this change.
