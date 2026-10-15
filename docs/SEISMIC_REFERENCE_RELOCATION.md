# Explicit seismic relocation maps

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor

This page describes the version 1.0.0 source behavior; [Field Viewer 1.0.0 release notes](RELEASE_NOTES.md) define the current public distribution. Missing external references remain inventory entries.
The extractor never searches other drives or assigns a nearby file by its name.

If you independently identify a replacement, supply an exact JSON map:

```json
{
  "schema": "fieldviewer.seismic-reference-map/1",
  "project_file": "F:\\Projects\\Example.pet",
  "references": [
    {
      "original_reference": "S:\\Original\\cube.zgy",
      "replacement": "G:\\Seismic\\cube.zgy"
    }
  ]
}
```

Copy `original_reference` exactly from `project_seismic_inventory.json` for an
entry with `association: native_model_external_reference`. The project path must
bind the selected project; the replacement must be an absolute, existing file
in the same format family. ZGY and SEG-Y cannot substitute for each other. The
map is limited to 1 MiB and 1,000 entries. Duplicate references/targets, duplicate
JSON keys and conflicting independently associated files are rejected before
any inventory entry changes.

```bat
FieldViewer_data_extractor.bat "F:\Projects\Example.pet" "D:\FV\Exports" -SeismicReferenceMap "D:\FV\example-map.json" -NoPause
```

The source `.pet` and `.ptd` files stay unchanged. The stable inventory ID and
original reference remain in the result. The report displays the replacement,
map SHA-256 and `user_declared_reference_mapping` association separately from
proven project links. A declaration alone cannot verify original/replacement
data equivalence. Conversion still performs its normal geometry, sample and
integrity checks. Existing SEG-Y is identified as an already open format; it is
not counted as newly recovered seismic. Unknown units/CRS remain explicit.

These source tests include exact mapped synthetic ZGY conversion/readback,
source preservation, report provenance and invalid-map rejection. Real relocated
project acceptance and receiving-software import remain separate checks.
