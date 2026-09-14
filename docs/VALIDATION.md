# Release validation

The initial standalone package passed 12 acceptance checks on Windows x64, including extraction of two locally supplied projects, on 2026-09-12. Those projects are not distributed. The public release is rebuilt with generic path defaults and the project license, then validated separately before upload.

The acceptance harness covers full bundle verification, an actual BAT extraction with spaces and `&` in paths, missing stores, source/output overlap, unsupported native layout rejection, companion exclusion, native spatial controls, the portable doctor, tampering rejection, and source hashes. Release-specific results are in the attached `VALIDATION.json`.

Tests relocate the ZIP, remove system Python from PATH, set invalid Python environment overrides, disable pip indexes, and use unavailable HTTP proxies. They run on the same Windows host: no second physical machine or physically disconnected network is claimed. Successful checks establish execution and integrity, not geological interpretation, correct CRS, universal format coverage, or Petrel re-import.
