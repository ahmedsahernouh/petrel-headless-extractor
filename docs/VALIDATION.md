# Release validation

The initial standalone package passed 12 acceptance checks on Windows x64, including extraction of two locally supplied projects, on 2026-09-12. Those projects are not distributed. The public release is rebuilt with generic path defaults and the project license, then validated separately before upload.

The acceptance harness covers full bundle verification, an actual BAT extraction with spaces and `&` in paths, missing stores, source/output overlap, unsupported native layout rejection, companion exclusion, native spatial controls, the portable doctor, tampering rejection, and source hashes. Release-specific results are in the attached `VALIDATION.json`.

Version 0.2.0 missed Explorer's extra ZIP-stem folder during Extract All. A real Windows extraction failed with `0x80010135: Path too long`, leaving 3,397 manifest files missing, including every launcher/helper script. Version 0.2.1 uses short ZIP/root names, checks the nested extraction path budget, tests an incomplete package with its PowerShell launcher missing, and exercises the interactive project/output prompts. The BAT reports incomplete extraction before attempting PowerShell and retains startup errors on screen unless `-NoPause` is requested. These are regression checks for the observed failure, not a claim that every possible extraction location is supported.

Tests relocate the ZIP, remove system Python from PATH, set invalid Python environment overrides, disable pip indexes, and use unavailable HTTP proxies. They run on the same Windows host: no second physical machine or physically disconnected network is claimed. Successful checks establish execution and integrity, not geological interpretation, correct CRS, universal format coverage, or Petrel re-import.
