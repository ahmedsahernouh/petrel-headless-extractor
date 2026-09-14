#!/usr/bin/env python3
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Focused tests for the evidence-gated native spatial decoder."""

from __future__ import annotations

import importlib.util
import math
import struct
import sys
import unittest
import uuid
from pathlib import Path


SCRIPT = Path(__file__).with_name("export_petrel_native_spatial_zero_gui.py")
SPEC = importlib.util.spec_from_file_location("petrel_native_spatial", SCRIPT)
assert SPEC and SPEC.loader
decoder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = decoder
SPEC.loader.exec_module(decoder)


BASE_NAMES = [
    "Version",
    "Type",
    "Id",
    "Ref",
    "Size",
    "use",
    "expr",
    "value",
    "data",
    "http://www.slb.com/Petrel/2011/03/Serialization",
]


def dictionary(names: list[str]) -> bytes:
    result = bytearray(b"BXML\x01")
    for name in names:
        raw = name.encode("ascii")
        result += b"\xA1" + decoder.encode_uleb128(len(raw)) + raw
    result += b"\xA0"
    return bytes(result)


def literal_lz4_envelope(payload: bytes) -> bytes:
    length = len(payload)
    block = bytearray([min(length, 15) << 4])
    if length >= 15:
        remaining = length - 15
        while remaining >= 255:
            block.append(255)
            remaining -= 255
        block.append(remaining)
    block += payload
    return b"LZ4\x01" + struct.pack("<I", len(block)) + b"TEST" + bytes(block)


def points_payload(points: list[tuple[float, float, float]], checkpoint: tuple[int, bytes] | None = None) -> bytes:
    names = BASE_NAMES + ["Points3", "user_data", "vertices", "double"]
    raw = b"".join(struct.pack("<3d", *point) for point in points)
    if checkpoint:
        offset, frame = checkpoint
        raw = raw[:offset] + frame + raw[offset:]
    return (
        dictionary(names)
        + b"\x42\x18\x06\x08\x88"
        + bytes([len(points)])
        + b"\x03\x42\x1A\x01\x93"
        + decoder.encode_uleb128(len(points) * 3)
        + raw
    )


class NativeSpatialDecoderTests(unittest.TestCase):
    def test_lz4_literal_envelope(self) -> None:
        payload = b"BXML\x01 synthetic payload"
        decoded, evidence = decoder.decompress_lz4_block(literal_lz4_envelope(payload))
        self.assertEqual(decoded, payload)
        self.assertEqual(evidence["decompressed_size"], len(payload))

    def test_points3_checkpoint_inside_double(self) -> None:
        points = [(450_000.0 + i, 2_900_000.0 + i * 2, -1_000.0 - i) for i in range(20)]
        # Insert the observed A0-plus-two-byte checkpoint inside Y of point 12.
        payload = points_payload(points, checkpoint=(12 * 24 + 8 + 5, b"\xA0\x12\x34"))
        decoded, evidence = decoder.decode_points3(payload)
        self.assertEqual(decoded, points)
        self.assertEqual(evidence["declared_point_count"], 20)
        self.assertEqual(len(evidence["dictionary_frames_skipped"]), 1)

    def test_polygons3_declared_counts(self) -> None:
        names = BASE_NAMES + [
            "Polygons3",
            "user_data",
            "array",
            "item",
            "Polygon3",
            "vertices",
            "double",
            "has_attr",
            "has_object_ids",
            "is_closed",
        ]
        points = [(100.0, 200.0, -10.0), (110.0, 200.0, -11.0), (100.0, 200.0, -10.0)]
        raw = b"".join(struct.pack("<3d", *point) for point in points)
        payload = (
            dictionary(names)
            + b"\x42\x18\x06\x08\x82"
            + b"\x42\x1E\x06\x08\x88\x03\x03"
            + b"\x42\x20\x01\x93\x09"
            + raw
        )
        polygons, evidence = decoder.decode_polygons3(payload)
        self.assertEqual(polygons, [points])
        self.assertEqual(evidence["outer_item_count"], 1)

    def test_explicit_trajectory_checkpoint_inside_marker(self) -> None:
        names = BASE_NAMES + [
            "ExplicitTrajectoryProviderData",
            "records",
            "item",
            "x",
            "y",
            "z",
            "md",
            "inclination",
            "azimuth",
        ]
        item = decoder.field_marker(names, "item")
        fields = {name: decoder.field_marker(names, name) for name in decoder.TRAJECTORY_FIELDS["ExplicitTrajectoryProviderData"]}
        body = bytearray(decoder.field_marker(names, "records") + b"\x06\x08\x88\x02")
        expected = []
        for index in range(2):
            values = {
                "x": 450_000.0 + index,
                "y": 2_900_000.0 + index,
                "z": -100.0 * index,
                "md": 100.0 * index,
                "inclination": 0.1 * index,
                "azimuth": 1.0 + index,
            }
            expected.append(values)
            body += item
            for name, value in values.items():
                marker = fields[name]
                if index == 1 and name == "z":
                    marker = marker[:1] + b"\xA0\x80\x20" + marker[1:]
                body += marker + b"\x93" + struct.pack("<d", value)
            body += b"\x01"
        records, evidence = decoder.decode_trajectory(dictionary(names) + bytes(body), "ExplicitTrajectoryProviderData")
        self.assertEqual(len(records), 2)
        self.assertTrue(math.isclose(records[1]["md"], expected[1]["md"]))
        self.assertEqual(len(evidence["opaque_frames_resynchronized"]), 1)

    def test_model_well_heads_and_trajectory_xy_crosscheck(self) -> None:
        names = BASE_NAMES + [
            "WellTraceSubject",
            "name",
            "definite_survey_provider_tag_",
            "well_head_",
            "double",
        ]
        subject = decoder.field_marker(names, "WellTraceSubject")
        name = decoder.field_marker(names, "name")
        provider = decoder.field_marker(names, "definite_survey_provider_tag_")
        well_head = decoder.field_marker(names, "well_head_")
        double = decoder.field_marker(names, "double")
        provider_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

        def model_subject(well_name: str, x: float, y: float, linked: bool) -> bytes:
            raw_name = well_name.encode("utf-8")
            provider_value = b"\xB1" + provider_id.bytes_le if linked else b"\xA9"
            return (
                subject
                + name
                + b"\x99"
                + decoder.encode_uleb128(len(raw_name))
                + raw_name
                + provider
                + provider_value
                + well_head
                + b"\x03"
                + double
                + b"\x01\x93\x02"
                + struct.pack("<2d", x, y)
                + b"\x01"
            )

        payload = dictionary(names) + model_subject("Well A", 450_000.0, 2_900_000.0, True) + model_subject(
            "Well B", 451_000.0, 2_901_000.0, False
        )
        rows, evidence = decoder.decode_model_well_heads_payload(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(evidence["status"], "decoded")
        self.assertEqual(rows[0]["well_name"], "Well A")
        self.assertEqual(rows[0]["trajectory_provider_object_id"], str(provider_id))
        self.assertEqual((rows[0]["x"], rows[0]["y"]), (450_000.0, 2_900_000.0))
        self.assertEqual(rows[1]["trajectory_provider_object_id"], "")

        trajectory_rows = [
            {
                "object_id": str(provider_id),
                "provider_type": "ExplicitTrajectoryProviderData",
                "record_index": 0,
                "x": 450_000.001,
                "y": 2_900_000.001,
                "z": 75.0,
            }
        ]
        enriched, mappings, crosscheck = decoder.enrich_well_heads_with_trajectory(rows, trajectory_rows, 0.02)
        self.assertEqual(enriched[0]["z"], 75.0)
        self.assertEqual(
            enriched[0]["xy_trajectory_crosscheck_status"],
            "matched_first_native_trajectory_station_within_tolerance",
        )
        self.assertEqual(crosscheck["matched_XY_rows"], 1)
        self.assertEqual(mappings[str(provider_id)]["well_name"], "Well A")
        self.assertEqual(
            enriched[1]["xy_trajectory_crosscheck_status"],
            "no_definitive_trajectory_provider_reference_in_Model_ptd",
        )


if __name__ == "__main__":
    unittest.main()
