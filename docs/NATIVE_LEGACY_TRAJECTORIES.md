# Legacy stored well trajectories

Website: https://saherlabs.dev/ · Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

The working 1.0.0 source now selects `WellTrace3` payloads in addition to the existing trajectory-provider layouts. This has not been packaged or released.

3 October follow-up: one retained `MdInclAzimTrajectoryProviderData` profile has
`settings Version=1`, raw Boolean interpolation request, azimuth-reference code 1,
nonnegative finite interpolation tolerance, and child versions 0/1. A separate
length-frame/Microsoft NBFX read verified all 147 stations and 441 stored values
from a retained trajectory payload. The current spatial stage writes raw inclination/azimuth
columns and leaves radians/derived degrees empty for this profile. It preserves
the exact Model provider link without assigning angle units, north convention,
MD units or datum. Unknown versions/settings, invalid station order, and typed
value disagreements remain rejected. See the scoped fix evidence.

## Validated scope

Two retained project cases saved with Petrel 2012.5 and 2014.4 contain observed `WellTrace3` versions `[25,2,0,1,1]` and `[26,2,0,1,1]`. The decoder reads the framed, typed `trace_points.vertices`, `md_array`, `incl_array`, and `azim_array` fields. It checks float64 arrays, declared lengths, common station count, finite values, nondecreasing stored MD, and one exact named `WellTraceSubject` UUID. Unknown versions, ambiguous identity and inconsistent arrays fail only the affected object.

Output is CSV in native station order: XYZ, MD, raw inclination/azimuth, identity and metadata warnings. Angle units remain unresolved; no degree/radian conversion, resampling, sign change, CRS transformation or well-head inference is applied. Stored trace stations are not automatically a certified directional survey. Native non-spatial/stub flags are retained in the CSV and export-index warning. In the supplied 2012.5 case, all 115 objects carry the old non-spatial flag; the significance of that historical flag remains unresolved.

## Verification

The 27 September targeted replay recovered 115 objects/633 stations and 127 objects/254 stations: 242 objects and 887 stations total. A separate scalar reader checked declared field/array framing and little-endian doubles; exported CSV values matched exactly. Well names matched the same UUID in the typed subject inventory. Fresh local copied fixtures stayed byte-identical during decoding. The standard partial-delivery path produced 242 per-object CSVs with hashes, two shallow file indexes and linked HTML indexes.

These tests cover retained payloads and delivery. Units/CRS, well heads and
broader Petrel version coverage remain unresolved. The old two-project reports
are preserved unchanged.

## Remaining well-head gap

The older `WellTraceSubject` layouts do not contain the validated `well_head_` field. A first trace station, bounding-box corner or MD zero value is not substituted for a head coordinate. The category remains partial and keeps the successfully recovered trajectories. Next validation requires an exact stored head/reference representation and independent coordinate evidence; it must distinguish reference elevations, trace starts and true well-head XY.

Acceptance evidence is retained outside this public repository; proprietary fixtures and project identifiers are omitted.

## Catalogue identity follow-up, 2 October 2026

The shared well/payload UUID previously caused catalogue entities to merge, making
a stored track appear owned by the well/head entity. New catalogue generation and
the read-only legacy adapter now distinguish the conceptual well and its track
with role-qualified keys while preserving the native UUID. Exact typed subject
identity establishes their relationship; names alone do not. Track representations
no longer stand in for missing head data. This changes catalogue attribution,
not decoded coordinates, station order or the original FILE_INDEX.

A retained validation check independently reparses PET/Model identities for
33 wells and 33 corresponding tracks. Its historical receipt records 739 stations;
the identity check does not reread their full payloads. All 3,597 indexed artifacts
remain represented, and 2,547 retained files keep their original size and mtime.
Primary input hashes match retention receipts. Reference units, head coordinates,
angle meaning and receiving qualification remain unresolved where previously
unresolved. The [catalogue contract](INTEROPERABILITY_CONTRACT.md) describes the identity rules; this retained check does not qualify reference units or receiving imports.
