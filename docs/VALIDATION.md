# Field Viewer validation and tested profiles

This page describes the evidence for the first public beta. Download the release's [VALIDATION.json](https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/download/v1.0.0/VALIDATION.json) for the exact package checks. The exact final
distribution identity and package checks belong in the release's `VALIDATION.json`
and [release notes](RELEASE_NOTES.md). The results below come from recorded
tests on observed layouts; they do not establish universal Petrel support.

## Windows package and runtime

- A tested standalone candidate passed **170 packaged regression tests**, **41
  standalone acceptance checks** and **7 polygon/grid checks**. The actual main
  BAT ran from relocated folders with system Python unavailable and package
  indexes disabled. Checks covered offline bootstrap and repair, invalid or
  incomplete input, native recovery, report-only mode, hashing choices,
  interactive answers and tamper rejection.
- Tested main-BAT runs produced readable support ZIPs except an intentionally
  incomplete extraction lacking launch scripts. A diagnostic collection test
  packaged **19,030,325 bytes** from **19 files** into **1,103,371 bytes**.
  This reused existing evidence; it was not a new full-project extraction.
- Tests ran on one Windows host. No second-machine, physically disconnected
  network or universal receiving-application import is established.

The final public ZIP has a new identity; these earlier candidate checks must
be distinguished from verification of the exact published artifact. See its
release-sidecar `VALIDATION.json` for the final archive and manifest checks.

## Observed native projects

An actual main-BAT run on a project recording **Petrel 2018.2** exported
**233 log CSVs, 179 LAS files, 50 grids (46 ZMAP), 64 polygon objects, six point
sets, 27 trajectories, 67 well headers and two SEG-Y volumes**. All **1,028**
indexed files existed. Its report had **118 figures** and **3,275** checked local
links. The run retained explicit gaps: **117** ambiguous local-property records,
**eight** empty logs and **23** grids outside current geometry/size profiles.
These did not invalidate independent supported exports.

An observed project recording **Petrel 2024.5.0** recovered **306 logs, 69
surface grids, 66 polygon payloads, 26 point payloads, 19 trajectory providers,
18 well headers** and one readable workflow definition. An available ZGY was
converted with decoded amplitudes and trace geometry checked. Four external
SEG-Y references were absent and stayed explicit gaps. Independent Microsoft
`XmlDictionaryReader` comparison matched **329 arrays/masks across 95 payloads**,
including all 69 surfaces and 26 point XYZ arrays. This checks binary arrays,
not physical units, CRS or geological interpretation.

A Windows lock test on this modern profile isolated one persistent finalization
failure while the other **319 log/grid objects** continued. A separate transient
lock recovered on attempt five; **306 logs, 69 grids, 111 spatial objects and 18
well headers** were recovered and **799** native CSV/XYZ/ZMAP/LAS artifacts
matched the prior baseline byte for byte. These tests show bounded handling of
the observed output failure; they do not promise recovery from every storage
error.

Across **213 supplied-project grids**, independent `XmlDictionaryReader`
comparison agreed byte for byte with numeric arrays and definition masks.
Independent `zmapio` readback covered **18,461,876 node positions** across five
regular grids. Native profile details and limits are in
[logs and surfaces](NATIVE_LOGS_SURFACES.md).

## Boundaries

Source projects remained unchanged in the recorded runs. Private input data,
identifying reports and raw logs are not distributed. Complete 3D RESCUE,
general fault topology, legacy distributed-PTD numeric decoding, unknown
unit/CRS resolution and blanket support for Petrel year ranges remain
unestablished. Review each object's result and numerical QC before use. See
[current scope](GEOVIEWER_1_0.md) and [remaining work](RELEASE_BACKLOG.md).
