#!/usr/bin/env python3
# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

"""Read-only extraction of proven Petrel native spatial BXML layouts.

This converter is deliberately narrow and fail closed.  It reads the copied
``Data.ptd`` SQLite store in an export package, unwraps the per-object LZ4
blocks, and decodes only layouts that have been validated in the supplied
Petrel projects:

* Points3 XYZ vertices;
* Polygons3 XYZ vertex arrays;
* Explicit, MD/Inclination/Azimuth, and XYZ trajectory-provider records; and
* native well-head names and XY coordinates from ``Model.ptd``
  ``WellTraceSubject.well_head_`` fields, with optional trajectory-start Z only
  after an XY cross-check; and
* well-top rows only when a Petrel-authored ASCII export independently matches
  one Points3 object by row count, order, and XYZ coordinates.

The source database is opened with SQLite ``mode=ro``.  No Petrel process,
Ocean API, or native-store write is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import datetime as dt
import json
import math
import os
import sqlite3
import struct
import sys
import uuid
from collections import defaultdict
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import petrel_native_binary as native_binary
import petrel_native_geometry_attributes as geometry_attributes
import petrel_native_polygon_bindings as polygon_bindings
from petrel_native_well_evidence import enrich_heads, IDENTITY_STATUS
import time


SUPPORTED_TYPES = {
    "Points3",
    "Polygons3",
    "ExplicitTrajectoryProviderData",
    "MdInclAzimTrajectoryProviderData",
    "XYZTrajectoryProviderData",
    "WellTrace3",
}
TRAJECTORY_FIELDS = {
    "ExplicitTrajectoryProviderData": ("x", "y", "z", "md", "inclination", "azimuth"),
    "MdInclAzimTrajectoryProviderData": ("md", "inclination", "azimuth"),
    "XYZTrajectoryProviderData": ("x", "y", "z"),
}
NATIVE_FLOAT_MAX_SENTINEL = 3.4028234663852886e38
MAX_SCALAR_COUNT = 100_000_000
MAX_ABS_COORDINATE = 1.0e15
CSV_WRITE_BUFFER_BYTES = 1024 * 1024
CSV_WRITE_BATCH_ROWS = 2048
CSV_PROGRESS_INTERVAL_SECONDS = 5.0
# Large attribute-free Points3 arrays use packed binary64 storage, not Python
# tuples/dictionaries per vertex. Admission remains bounded by the existing
# native document byte limit; identity-bearing layouts keep their old cap.
POINTS3_LIST_LIMIT = 2_000_000
POINTS3_COMPACT_THRESHOLD = 65_536
POINTS3_RETAINED_BYTE_BUDGET = 256 * 1024 * 1024


class DecodeError(RuntimeError):
    """Raised when a proprietary layout does not satisfy the proven contract."""


@dataclass(frozen=True)
class NativeObject:
    data_pk: int
    droid: str
    object_id: str
    name: str
    version: Any
    blob_type: str
    timestamp: str


@dataclass(frozen=True)
class PrimitiveArray:
    marker_offset: int
    data_offset: int
    end_offset: int
    values: tuple[Any, ...]
    dictionary_frames_skipped: tuple[dict[str, Any], ...]


def is_native_missing_float(value: float) -> bool:
    return math.isfinite(value) and math.isclose(
        abs(value), NATIVE_FLOAT_MAX_SENTINEL, rel_tol=1.0e-12, abs_tol=0.0
    )


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clean_object_id(droid: str) -> str:
    value = (droid or "").strip()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value


def encode_uleb128(value: int) -> bytes:
    if value < 0:
        raise ValueError("ULEB128 values must be non-negative")
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            result.append(byte | 0x80)
        else:
            result.append(byte)
            return bytes(result)


def read_uleb128(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if offset >= len(data):
            raise DecodeError("Truncated ULEB128 value")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise DecodeError("ULEB128 value is too long")


def parse_initial_dictionary(payload: bytes) -> tuple[list[str], int]:
    if not payload.startswith(b"BXML\x01"):
        raise DecodeError("Decompressed payload does not start with BXML version 1")
    offset = 5
    names: list[str] = []
    while offset < len(payload) and payload[offset] == 0xA1:
        offset += 1
        size, offset = read_uleb128(payload, offset)
        if not 1 <= size <= 4096 or offset + size > len(payload):
            raise DecodeError("Invalid BXML dictionary entry length")
        try:
            name = payload[offset : offset + size].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecodeError("Invalid UTF-8 in BXML dictionary") from exc
        names.append(name)
        offset += size
    if not names or offset >= len(payload) or payload[offset] != 0xA0:
        raise DecodeError("BXML initial dictionary terminator was not found")
    return names, offset + 1


def field_marker(dictionary: Sequence[str], name: str) -> bytes:
    try:
        index = dictionary.index(name)
    except ValueError as exc:
        raise DecodeError(f"Required BXML dictionary field is absent: {name}") from exc
    # Trajectory record scopes in the validated Petrel 2010/2018 payloads use
    # twice the zero-based initial-dictionary index.
    return b"\x42" + encode_uleb128(2 * index)


def try_dictionary_extension(payload: bytes, offset: int) -> tuple[int, list[str]] | None:
    """Recognize a BXML dictionary frame embedded in primitive byte content.

    Petrel may split a primitive value and insert ``A1 <len> <name> ... A0``
    plus a two-byte opaque checkpoint between two bytes.  A bare 0xA1 is common inside IEEE-754 values, so a
    frame is skipped only if every name is printable identifier text and the
    complete observed terminator is present.
    """

    if offset >= len(payload) or payload[offset] != 0xA1:
        return None
    cursor = offset
    names: list[str] = []
    while cursor < len(payload) and payload[cursor] == 0xA1:
        cursor += 1
        try:
            size, cursor = read_uleb128(payload, cursor)
        except DecodeError:
            return None
        if not 1 <= size <= 128 or cursor + size > len(payload):
            return None
        raw = payload[cursor : cursor + size]
        if any(byte < 32 or byte > 126 for byte in raw):
            return None
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
        if not all(char.isalnum() or char in "_-. :/" for char in name):
            return None
        names.append(name)
        cursor += size
    if not names or cursor >= len(payload) or payload[cursor] != 0xA0:
        return None
    cursor += 1
    if cursor + 2 > len(payload):
        return None
    return cursor + 2, names


def read_primitive_bytes(payload: bytes, offset: int, byte_count: int) -> tuple[bytes, int, list[dict[str, Any]]]:
    if byte_count < 0 or byte_count > MAX_SCALAR_COUNT * 8:
        raise DecodeError(f"Primitive byte count is outside the safety bound: {byte_count}")
    result = bytearray()
    skipped: list[dict[str, Any]] = []
    while len(result) < byte_count:
        if offset >= len(payload):
            raise DecodeError("Primitive array is truncated")
        extension = try_dictionary_extension(payload, offset)
        if extension is not None:
            end_offset, names = extension
            skipped.append({"start_offset": offset, "end_offset": end_offset, "names": names})
            offset = end_offset
            continue
        result.append(payload[offset])
        offset += 1
    return bytes(result), offset, skipped


def strip_dictionary_extensions(payload: bytes, start: int) -> tuple[bytes, list[dict[str, Any]]]:
    """Remove only complete, structurally validated embedded dictionary frames."""

    result = bytearray(payload[:start])
    frames: list[dict[str, Any]] = []
    cursor = start
    while cursor < len(payload):
        extension = try_dictionary_extension(payload, cursor)
        if extension is None:
            result.append(payload[cursor])
            cursor += 1
            continue
        end_offset, names = extension
        frames.append({"start_offset": cursor, "end_offset": end_offset, "names": names})
        cursor = end_offset
    return bytes(result), frames


def decode_primitive_array(
    payload: bytes,
    marker_offset: int,
    marker: bytes,
    width: int,
    struct_code: str,
) -> PrimitiveArray:
    count, data_offset = read_uleb128(payload, marker_offset + len(marker))
    if count <= 0 or count > MAX_SCALAR_COUNT:
        raise DecodeError(f"Primitive array count is outside the safety bound: {count}")
    raw, end_offset, skipped = read_primitive_bytes(payload, data_offset, count * width)
    try:
        values = struct.unpack(f"<{count}{struct_code}", raw)
    except struct.error as exc:
        raise DecodeError("Primitive array unpack failed") from exc
    return PrimitiveArray(marker_offset, data_offset, end_offset, tuple(values), tuple(skipped))


def xyz_intrinsically_valid(point: tuple[float, float, float]) -> bool:
    return all(
        math.isfinite(value)
        and (abs(value) <= MAX_ABS_COORDINATE or is_native_missing_float(value))
        for value in point
    )


def xyz_consistent_with_history(
    point: tuple[float, float, float], history: Sequence[tuple[float, float, float]]
) -> bool:
    if len(history) < 10 or any(is_native_missing_float(value) for value in point):
        return True
    recent = history[-100:]
    outside = 0
    for component in range(3):
        values = [item[component] for item in recent]
        low, high = min(values), max(values)
        center = sorted(values)[len(values) // 2]
        margin = max((high - low) * 10.0, abs(center) * 0.5, 10_000.0 if component < 2 else 1_000.0)
        if point[component] < low - margin or point[component] > high + margin:
            outside += 1
    # A real discontinuity can move one component substantially.  Byte
    # misalignment in the supplied payloads moves at least two components out
    # of the established envelope simultaneously.
    return outside < 2


def decode_xyz_vector(
    payload: bytes,
    marker_offset: int,
    marker: bytes,
    declared_point_count: int,
) -> PrimitiveArray:
    scalar_count, cursor = read_uleb128(payload, marker_offset + len(marker))
    data_offset = cursor
    if scalar_count != declared_point_count * 3:
        raise DecodeError(
            f"scalar count {scalar_count} does not equal 3 * declared vertices {declared_point_count}"
        )
    points: list[tuple[float, float, float]] = []
    opaque_frames: list[dict[str, Any]] = []
    for point_index in range(declared_point_count):
        if cursor + 24 > len(payload):
            raise DecodeError("XYZ vector is truncated")
        point = struct.unpack_from("<3d", payload, cursor)
        plausible = xyz_intrinsically_valid(point) and xyz_consistent_with_history(point, points)
        if not plausible:
            candidates: list[tuple[int, int, list[tuple[float, float, float]]]] = []
            lookahead_count = min(5, declared_point_count - point_index)
            required_bytes = lookahead_count * 24
            for split in range(24):
                for skipped in range(1, 17):
                    if cursor + required_bytes + skipped > len(payload):
                        continue
                    repaired = (
                        payload[cursor : cursor + split]
                        + payload[cursor + split + skipped : cursor + required_bytes + skipped]
                    )
                    trial_history = list(points)
                    trial_points: list[tuple[float, float, float]] = []
                    valid = True
                    for lookahead in range(lookahead_count):
                        trial = struct.unpack_from("<3d", repaired, lookahead * 24)
                        if not xyz_intrinsically_valid(trial) or not xyz_consistent_with_history(trial, trial_history):
                            valid = False
                            break
                        trial_points.append(trial)
                        if not any(is_native_missing_float(value) for value in trial):
                            trial_history.append(trial)
                    if valid:
                        candidates.append((split, skipped, trial_points))
            if len(candidates) != 1:
                checkpoint_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate[1] == 3 and payload[cursor + candidate[0]] == 0xA0
                ]
                boundary_candidates = [candidate for candidate in candidates if candidate[0] == 0]
                if len(checkpoint_candidates) == 1:
                    candidates = checkpoint_candidates
                elif len(boundary_candidates) == 1:
                    candidates = boundary_candidates
            if len(candidates) != 1:
                raise DecodeError(
                    f"XYZ checkpoint recovery is {'ambiguous' if candidates else 'unavailable'} at point {point_index}"
                )
            split, skipped, trial_points = candidates[0]
            frame_start = cursor + split
            opaque_frames.append(
                {
                    "start_offset": frame_start,
                    "end_offset": frame_start + skipped,
                    "bytes_hex": payload[frame_start : frame_start + skipped].hex(),
                    "context": f"xyz_point_{point_index}:inside_or_before_point",
                    "point_byte_split": split,
                }
            )
            point = trial_points[0]
            # Consume the repaired point: 24 semantic bytes plus the inserted
            # framing bytes removed above.
            cursor += skipped
        points.append(point)
        cursor += 24
    flat_values = tuple(value for point in points for value in point)
    return PrimitiveArray(marker_offset, data_offset, cursor, flat_values, tuple(opaque_frames))


def decompress_lz4_block(blob: bytes) -> tuple[bytes, dict[str, Any]]:
    # Use the same bounded stream framing as the native log/model reader.
    # A stream has one magic followed by independent length-framed blocks.
    try:
        payload = native_binary.decompress(blob)
    except native_binary.NativeError as exc:
        raise DecodeError(str(exc)) from exc
    blocks = []; cursor = 4
    while cursor < len(blob):
        size = struct.unpack_from('<I', blob, cursor)[0]
        blocks.append(dict(header_offset=cursor, declared_compressed_size=size,
                           envelope_metadata_hex=blob[cursor+4:cursor+8].hex()))
        cursor += 8+size
    evidence = {
        "envelope": "LZ4_v1_raw_block" if len(blocks) == 1 else "LZ4_v1_block_stream",
        "declared_compressed_size": sum(block['declared_compressed_size'] for block in blocks),
        "decompressed_size": len(payload),
        "block_count": len(blocks),
        "blocks": blocks,
    }
    if len(blocks) == 1:
        evidence['envelope_metadata_hex'] = blocks[0]['envelope_metadata_hex']
    return payload, evidence


def open_read_only_sqlite(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Data.ptd was not found: {path}")
    connection = sqlite3.connect(native_binary.sqlite_readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    required = {"data", "blob_parts"}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.issubset(tables):
        connection.close()
        raise DecodeError(f"Data.ptd does not have the validated SQLite tables: {sorted(required)}")
    return connection


def version_key(value: Any) -> tuple[int, str]:
    try:
        return int(value), str(value)
    except (TypeError, ValueError):
        return -1, str(value)


def live_native_objects(connection: sqlite3.Connection) -> list[NativeObject]:
    placeholders = ",".join("?" for _ in SUPPORTED_TYPES)
    rows = connection.execute(
        f"SELECT data_pk,droid,name,version,blob_type,time_stamp FROM data WHERE blob_type IN ({placeholders})",
        sorted(SUPPORTED_TYPES),
    ).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (str(row["droid"]), str(row["blob_type"]))
        current = latest.get(key)
        if current is None or (version_key(row["version"]), int(row["data_pk"])) > (
            version_key(current["version"]),
            int(current["data_pk"]),
        ):
            latest[key] = row
    return [
        NativeObject(
            data_pk=int(row["data_pk"]),
            droid=str(row["droid"] or ""),
            object_id=clean_object_id(str(row["droid"] or "")),
            name=str(row["name"] or ""),
            version=row["version"],
            blob_type=str(row["blob_type"]),
            timestamp=str(row["time_stamp"] or ""),
        )
        for row in sorted(latest.values(), key=lambda item: (str(item["blob_type"]), str(item["droid"])))
    ]


def object_blob(connection: sqlite3.Connection, native_object: NativeObject) -> bytes:
    parts = connection.execute(
        "SELECT part,blob_data FROM blob_parts WHERE data_fk=? ORDER BY part", (native_object.data_pk,)
    ).fetchall()
    if not parts:
        raise DecodeError("Object has no blob_parts rows")
    expected_parts = list(range(int(parts[0]["part"]), int(parts[0]["part"]) + len(parts)))
    actual_parts = [int(row["part"]) for row in parts]
    if actual_parts != expected_parts:
        raise DecodeError(f"Object blob parts are not contiguous: {actual_parts}")
    return b"".join(bytes(row["blob_data"]) for row in parts)


def valid_numeric_payload(values: Sequence[float]) -> bool:
    """Accept coordinates plus Petrel's float-max missing-value sentinel."""

    return bool(values) and all(
        math.isfinite(value)
        and (abs(value) <= MAX_ABS_COORDINATE or is_native_missing_float(value))
        for value in values
    )


def find_double_arrays(payload: bytes, marker: bytes, start: int = 0, limit: int | None = None) -> list[PrimitiveArray]:
    arrays: list[PrimitiveArray] = []
    cursor = max(0, start)
    full_marker = marker + b"\x01\x93"
    while cursor < len(payload) and (limit is None or len(arrays) < limit):
        position = payload.find(full_marker, cursor)
        if position < 0:
            break
        try:
            array = decode_primitive_array(payload, position, full_marker, 8, "d")
        except (DecodeError, OverflowError):
            cursor = position + 1
            continue
        if valid_numeric_payload(array.values):
            arrays.append(array)
            cursor = array.end_offset
        else:
            cursor = position + 1
    return arrays


def find_collection_count(payload: bytes, marker: bytes, start: int) -> int | None:
    prefix = marker + b"\x06\x08"
    position = payload.find(prefix, start)
    if position < 0:
        return None
    cursor = position + len(prefix)
    try:
        count, _ = decode_count_scalar(payload, cursor)
    except DecodeError:
        return None
    return count if 0 <= count <= MAX_SCALAR_COUNT else None


def decode_count_scalar(payload: bytes, offset: int) -> tuple[int, int]:
    """Decode observed BXML unsigned collection-count encodings."""

    if offset >= len(payload):
        raise DecodeError("Truncated BXML collection count")
    encoding = payload[offset]
    offset += 1
    if encoding == 0x80:
        return 0, offset
    if encoding == 0x82:
        return 1, offset
    if encoding == 0x88:
        if offset >= len(payload):
            raise DecodeError("Truncated BXML uint8 collection count")
        return payload[offset], offset + 1
    if encoding == 0x8A:
        if offset + 2 > len(payload):
            raise DecodeError("Truncated BXML uint16 collection count")
        return struct.unpack_from("<H", payload, offset)[0], offset + 2
    if encoding == 0x8C:
        if offset + 4 > len(payload):
            raise DecodeError("Truncated BXML uint32 collection count")
        return struct.unpack_from("<I", payload, offset)[0], offset + 4
    raise DecodeError(f"Unsupported BXML collection-count code: 0x{encoding:02x}")


class PackedPoints3(Sequence):
    """Validated XYZ slots with bounded chunk validation and lazy tuples."""
    def __init__(self, values, payload_bytes, missing_slot_bytes=None):
        np = native_binary.np
        self.values = values.reshape((-1, 3))
        self.missing = np.zeros(len(self.values), dtype=np.bool_)
        self.storage_bytes = payload_bytes + self.missing.nbytes
        self.minimum = np.full(3, np.inf); self.maximum = np.full(3, -np.inf)
        for start in range(0, len(self), POINTS3_COMPACT_THRESHOLD):
            block = self.values[start:start+POINTS3_COMPACT_THRESHOLD]
            absolute = np.abs(block)
            sentinel = (np.isfinite(block) & (np.abs(absolute-NATIVE_FLOAT_MAX_SENTINEL)
                        <= 1e-12*np.maximum(absolute, NATIVE_FLOAT_MAX_SENTINEL))).any(axis=1)
            invalid = (~sentinel & (~np.isfinite(block) | (absolute > MAX_ABS_COORDINATE)).any(axis=1))
            if np.any(invalid):
                raise DecodeError(f'Points3 contains an invalid XYZ triple at point {start+int(np.flatnonzero(invalid)[0])}')
            self.missing[start:start+len(block)] = sentinel
            present = block[~sentinel]
            if len(present):
                self.minimum = np.minimum(self.minimum, present.min(axis=0))
                self.maximum = np.maximum(self.maximum, present.max(axis=0))
        self.missing_count = int(np.count_nonzero(self.missing))
        if missing_slot_bytes is not None:
            for index in np.flatnonzero(self.missing):
                missing_slot_bytes.extend(struct.pack('<II', 0, int(index)))
                missing_slot_bytes.extend(self.values[index].astype('<f8', copy=False).tobytes())

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[n] for n in range(*index.indices(len(self)))]
        if index < 0: index += len(self)
        if not 0 <= index < len(self): raise IndexError(index)
        return None if self.missing[index] else tuple(float(v) for v in self.values[index])

    def bounds(self):
        if self.missing_count == len(self): return None
        return {key+'_'+axis: float(values[i]) for key, values in
                (('min', self.minimum), ('max', self.maximum)) for i, axis in enumerate('xyz')}


class PointVertexRows(Sequence):
    """Retain bounded point blocks; construct only the requested CSV batch."""
    def __init__(self):
        self.blocks = []; self.ends = []; self.retained_bytes = 0

    def add(self, native_object, object_name, points):
        np = native_binary.np
        indexes = (np.flatnonzero(~points.missing).astype(np.uint32) if isinstance(points, PackedPoints3)
                   else np.asarray([i for i, p in enumerate(points) if p is not None], dtype=np.uint32))
        charge = (points.storage_bytes if isinstance(points, PackedPoints3) else len(points)*160) + indexes.nbytes
        if self.retained_bytes + charge > POINTS3_RETAINED_BYTE_BUDGET:
            raise DecodeError('Accumulated Points3 retained geometry exceeds byte budget')
        self.retained_bytes += charge
        if len(indexes):
            self.blocks.append((native_object, object_name, points, indexes))
            self.ends.append(len(self) + len(indexes))

    def __len__(self):
        return self.ends[-1] if self.ends else 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[n] for n in range(*index.indices(len(self)))]
        if index < 0: index += len(self)
        if not 0 <= index < len(self): raise IndexError(index)
        block = bisect_right(self.ends, index)
        obj, name, points, indexes = self.blocks[block]
        slot = int(indexes[index-(self.ends[block-1] if block else 0)])
        x, y, z = points[slot]
        return dict(object_id=obj.object_id, data_pk=obj.data_pk, object_name=name,
                    version=obj.version, point_index=slot, x=x, y=y, z=z,
                    crs_status='not_resolved_from_native_geometry_payload',
                    decode_status='native_Points3_LZ4_BXML_xyz_decoded')


def decode_points3(payload: bytes, *, missing_slot_bytes: bytearray | None = None,
                   attribute_vectors: list | None = None,
                   attribute_value_budget: int = geometry_attributes.MAX_ATTRIBUTE_VALUES) -> tuple[Sequence, dict[str, Any]]:
    if len(payload) > native_binary.MAX_DOCUMENT:
        raise DecodeError('Points3 payload exceeds native document byte bound')
    """Read framed typed XYZ arrays; never guess offsets from plausible coordinates."""
    try:
        nodes = list(native_binary.read_points3_documents(payload))
        if len(nodes) != 1:
            raise DecodeError('Expected one Points3 document')
        node = nodes[0]
        if (node.name != 'data' or node.attrs.get('Type') != 'Points3'
                or node.attrs.get('Version') != [1, 2, 0, 1, 1]
                or node.attrs.get('xmlns') != 'http://www.slb.com/Petrel/2011/03/Serialization'):
            raise DecodeError('Points3 type/version/namespace is outside the validated profile')
        expected = ['user_data', 'vertices', 'has_attr', 'has_object_ids']
        if node.get('has_attr') is True:
            expected.append('attributes')
        if node.get('has_object_ids') is True:
            expected.append('object_ids')
        if (node.content or sorted(c.name for c in node.children) != sorted(expected)
                or type(node.get('has_attr')) is not bool or type(node.get('has_object_ids')) is not bool):
            raise DecodeError('Unsupported Points3 fields or object IDs')
        user = node.child('user_data')
        if user.attrs.get('Size') != 0 or user.children or user.content:
            raise DecodeError('Points3 user-data layout is not validated')
        vertices = node.child('vertices'); point_count = vertices.attrs.get('Size')
        if type(point_count) is not int or point_count < 0 or vertices.content:
            raise DecodeError('Invalid Points3 declared count')
        compact = (point_count > POINTS3_COMPACT_THRESHOLD and not node.get('has_attr')
                   and not node.get('has_object_ids'))
        if point_count > POINTS3_LIST_LIMIT and not compact:
            raise DecodeError('Points3 non-packed layout exceeds declared point-count bound')
        if point_count*24 > native_binary.MAX_DOCUMENT:
            raise DecodeError('Points3 declared XYZ bytes exceed native document bound')
        if compact and (len(vertices.children) != 1 or not isinstance(vertices.children[0], native_binary.Array)):
            raise DecodeError('Large Points3 requires one packed binary64 array')
        if not point_count and vertices.children:
            raise DecodeError('Empty Points3 contains unexpected XYZ values')
        values = vertices.array('double') if point_count else []
        if (len(values) != 3*point_count or any(c.name != 'double' for c in vertices.children)
                or (point_count and (values.dtype.kind!='f' or values.dtype.itemsize!=8))):
            raise DecodeError('Points3 typed XYZ count mismatch')
        if node.get('has_object_ids'):
            ids=node.child('object_ids')
            if (not isinstance(ids, native_binary.IdentitySlots)
                    or ids.attrs.get('Size')!=point_count or ids.slot_count!=point_count
                    or len(ids.items)!=point_count or ids.children or ids.content):
                raise DecodeError('Points3 object identity slots do not match vertices')
    except native_binary.NativeError as exc:
        raise DecodeError(str(exc)) from exc
    points = PackedPoints3(values, len(payload), missing_slot_bytes) if compact else []
    for index in range(0, 0 if compact else len(values), 3):
        point = tuple(float(v) for v in values[index : index + 3])
        if any(is_native_missing_float(value) for value in point):
            points.append(None)
            if missing_slot_bytes is not None:
                missing_slot_bytes.extend(struct.pack('<II', 0, index // 3))
                missing_slot_bytes.extend(values[index:index+3].astype('<f8', copy=False).tobytes())
        elif all(math.isfinite(value) and abs(value) <= MAX_ABS_COORDINATE for value in point):
            points.append(point)
        else:
            raise DecodeError(f"Points3 contains an invalid XYZ triple at point {index // 3}")
    attribute_metadata = dict(attributes_status='not_present', attribute_value_count=0,
                              attribute_vectors=[])
    if node.get('has_attr'):
        try:
            vectors = geometry_attributes.decode_attribute_vectors(
                node.child('attributes'), point_count, max_values=attribute_value_budget)
            attribute_metadata.update(attributes_status='raw_typed_values_decoded',
                attribute_value_count=sum(len(v.values) for v in vectors),
                attribute_vectors=[v.descriptor(point_context=True) for v in vectors],
                reason='Raw typed attribute vectors preserved; point association, labels, defaults and units unresolved')
            if attribute_vectors is not None:
                attribute_vectors.extend(vectors)
        except native_binary.NativeError as exc:
            attribute_metadata.update(attributes_status='not_exported', attribute_failure=str(exc),
                reason='Geometry decoded; attached attributes unsupported: '+str(exc))
    return points, {
        "decoder": "length_framed_typed_NBFX",
        "declared_point_count": point_count,
        "vertices_scalar_count": len(values),
        "missing_vertex_slots": points.missing_count if compact else sum(point is None for point in points),
        "geometry_storage": "packed_binary64_bounded_chunks" if compact else "bounded_point_list",
        "geometry_byte_bound": native_binary.MAX_DOCUMENT,
        **attribute_metadata,
        "object_ids_status": "validated_serialized_values_available_for_export" if node.get('has_object_ids') else "not_present",
        "object_id_slot_versions": sorted(ids.versions) if node.get('has_object_ids') else [],
        # The caller removes this bounded table before writing the object report.
        # Point index, rather than a native value, is the stable row selector.
        "point_identities": ids.items if node.get('has_object_ids') else [],
        "object_id_validation": {
            "status": "validated_serialized_identity_values",
            "validated_slot_count": ids.slot_count,
            "inactive_identity_slots": ids.inactive_slot_count,
            "parsed_identity_nodes": ids.parsed_nodes,
            "max_item_nodes": ids.max_item_nodes,
            "max_item_bytes": ids.max_item_bytes,
            "values_status": "available_for_identity_table_export",
            "semantic_status": "native_serialization_values_only; not scientific or external well identifiers",
        } if node.get('has_object_ids') else {"status": "not_present"},
    }


def point_identity_values(item: tuple[Any, ...]) -> dict[str, Any]:
    """CSV-safe JSON scalars preserve native values and an absent compact field."""
    version, profile, values, raw_tokens = item
    fields = {"identity_version": version, "identity_profile": profile}
    for name, value, raw_token in zip(native_binary.Points3NBFX.ID_FIELD_ORDER,
                                      values, raw_tokens):
        fields[f"native_{name}_nbfx_hex"] = raw_token or ""
        if value is None:
            fields[f"native_{name}"] = ""
        elif type(value) is bytes:
            fields[f"native_{name}"] = json.dumps({"value_type": "bytes", "raw_hex": value.hex()},
                                                   separators=(",", ":"))
        else:
            fields[f"native_{name}"] = json.dumps(value, ensure_ascii=False,
                                                   separators=(",", ":"))
    return fields


def point_identity_export_rows(object_id: str, data_pk: int,
                               points: Sequence[tuple[float, float, float] | None],
                               items: Sequence[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Keep every native slot, including a missing XYZ and duplicate values."""
    if len(items) != len(points):
        raise DecodeError('Points3 identity export count does not match vertices')
    return [{"object_id": object_id, "data_pk": data_pk, "point_index": index,
             "xyz_present": point is not None, **point_identity_values(item)}
            for index, (point, item) in enumerate(zip(points, items))]


def decode_polygons3(payload: bytes, *, missing_slot_bytes: bytearray | None = None,
                     attribute_vectors: list | None = None,
                     attribute_value_budget: int = geometry_attributes.MAX_ATTRIBUTE_VALUES) -> tuple[list[list[tuple[float, float, float] | None]], dict[str, Any]]:
    """Decode complete typed Polygon3 items, preserving native collection order.

    BXML block boundaries may occur inside doubles. The bounded framing reader
    removes only declared frames before NBFX array parsing; no marker scanning,
    coordinate-based sorting or guessed checkpoint repair is used here.
    Collection indices are segment keys; serialization Id values are kept
    separately and are not advertised as user-authored polygon segment IDs.
    """
    try:
        nodes = list(native_binary.read_documents(payload))
        if len(nodes) != 1:
            raise DecodeError('Expected one Polygons3 document')
        node = nodes[0]
        if (node.name != 'data' or node.attrs.get('Type') != 'Polygons3'
                or node.attrs.get('Version') != [1, 2, 0, 1, 1]
                or node.attrs.get('xmlns') != 'http://www.slb.com/Petrel/2011/03/Serialization'):
            raise DecodeError('Polygons3 type/version/namespace is outside the validated profile')

        def fields(item, expected, allow_attributes=False):
            if allow_attributes and item.get('has_attr') is True:
                expected = [*expected, 'attributes']
            if item.content or sorted(c.name for c in item.children) != sorted(expected):
                raise DecodeError(f'{item.name}: unexpected or duplicate polygon fields')
            user = item.child('user_data')
            if user.attrs.get('Size') != 0 or user.children or user.content:
                raise DecodeError('Polygon user-data attributes are outside the validated profile')
            if item.get('has_attr') is not False and not (allow_attributes and item.get('has_attr') is True):
                raise DecodeError('Polygon property attributes are outside the validated profile')

        fields(node, ['user_data', 'array', 'has_attr'], allow_attributes=True)
        collection = node.child('array')
        outer_count = collection.attrs.get('Size')
        if (type(outer_count) is not int or not 0 <= outer_count <= 2_000_000
                or len(collection.children) != outer_count or collection.content):
            raise DecodeError('Polygons3 declared segment count disagrees with its items')
        polygons = []; segments = []; total_vertices = 0
        for part_index, item in enumerate(collection.children):
            if (item.name != 'item' or item.attrs.get('Type') != 'Polygon3'
                    or item.attrs.get('Version') != [0, 1, 2, 0, 1, 1] or 'Ref' in item.attrs):
                raise DecodeError(f'Polygon segment {part_index}: unsupported item type/version/reference')
            fields(item, ['user_data', 'vertices', 'has_attr', 'has_object_ids', 'is_closed'])
            if item.get('has_object_ids') is not False or type(item.get('is_closed')) is not bool:
                raise DecodeError(f'Polygon segment {part_index}: unsupported object IDs or closure flag')
            vertices = item.child('vertices'); count = vertices.attrs.get('Size')
            if type(count) is not int or not 0 <= count <= 2_000_000 or vertices.content:
                raise DecodeError(f'Polygon segment {part_index}: invalid vertex count')
            total_vertices += count
            if total_vertices > 2_000_000:
                raise DecodeError('Polygon object exceeds vertex bound')
            polygon = []
            if count:
                values = vertices.array('double')
                if (values.ndim != 1 or values.dtype.kind != 'f' or values.dtype.itemsize != 8
                        or len(values) != 3*count or any(c.name != 'double' for c in vertices.children)):
                    raise DecodeError(f'Polygon segment {part_index}: XYZ array shape/type/count mismatch')
                for vertex_index, row in enumerate(values.reshape((-1, 3))):
                    point = tuple(float(value) for value in row)
                    if any(is_native_missing_float(value) for value in point):
                        polygon.append(None)  # Retain the slot; never renumber or bridge a gap.
                        if missing_slot_bytes is not None:
                            missing_slot_bytes.extend(struct.pack('<II', part_index, vertex_index))
                            missing_slot_bytes.extend(row.astype('<f8', copy=False).tobytes())
                    elif not xyz_intrinsically_valid(point):
                        raise DecodeError(f'Polygon segment {part_index}: invalid coordinate')
                    else:
                        polygon.append(point)
            elif vertices.children:
                raise DecodeError(f'Polygon segment {part_index}: data inside an empty vertex array')
            polygons.append(polygon)
            segments.append(dict(part_index=part_index, segment_id=part_index,
                segment_id_source='native_collection_index_zero_based',
                native_serialization_id=item.attrs.get('Id'),
                is_closed_native=item.get('is_closed'), declared_vertex_count=count,
                missing_vertex_slots=sum(point is None for point in polygon),
                is_closed_by_repeated_xyz=(polygon[0] == polygon[-1] if len(polygon) > 1
                    and polygon[0] is not None and polygon[-1] is not None else None),
                closure_observation_scope='first_and_last_native_slots; no ring/hole or connectivity inference'))
        attribute_metadata = dict(attributes_status='not_present', attribute_value_count=0,
                                  attribute_vectors=[], reason='')
        if node.get('has_attr'):
            try:
                vectors = geometry_attributes.decode_attribute_vectors(node.child('attributes'), outer_count,
                    max_values=attribute_value_budget)
                attribute_metadata.update(attributes_status='raw_typed_values_decoded',
                    attribute_value_count=sum(len(v.values) for v in vectors),
                    attribute_vectors=[v.descriptor() for v in vectors],
                    reason='Raw typed attribute vectors preserved; names, part bindings, defaults, units and ring roles unresolved')
                if attribute_vectors is not None:
                    attribute_vectors.extend(vectors)
            except native_binary.NativeError as exc:
                # A property profile failure must not discard independent geometry.
                attribute_metadata.update(attributes_status='not_exported',
                    attribute_failure=str(exc), reason='Geometry decoded; attached attributes unsupported: '+str(exc))
        return polygons, dict(decoder='length_framed_typed_NBFX', outer_item_count=outer_count,
            decoded_nonempty_parts=sum(any(p is not None for p in part) for part in polygons),
            empty_or_undecoded_parts=sum(not any(p is not None for p in part) for part in polygons),
            vertices_scalar_count=3*total_vertices, segments=segments,
            missing_vertex_slots=sum(s['missing_vertex_slots'] for s in segments),
            **attribute_metadata,
            segment_order='native collection order', vertex_order='native vertex array order')
    except native_binary.NativeError as exc:
        raise DecodeError(str(exc)) from exc


class MissingVertexRows(Sequence):
    """Reiterable, sliceable CSV rows with 32 packed bytes per missing slot.

    Only append a buffer after the object's complete decoder succeeds. Raw IEEE
    bytes preserve signed zero and nonfinite payloads; these rows are not geometry.
    """
    def __init__(self):
        self.blocks = []
        self.ends = []

    def add(self, object_id, data_pk, packed):
        if len(packed) % 32:
            raise DecodeError('Invalid packed missing-vertex evidence length')
        if packed:
            self.blocks.append((object_id, data_pk, bytes(packed)))
            self.ends.append(len(self) + len(packed) // 32)

    def __len__(self):
        return self.ends[-1] if self.ends else 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[n] for n in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        block = bisect_right(self.ends, index)
        oid, pk, raw = self.blocks[block]
        offset = (index - (self.ends[block-1] if block else 0)) * 32
        part, vertex = struct.unpack_from('<II', raw, offset)
        row = dict(object_id=oid, data_pk=pk, part_index=part, vertex_index=vertex,
                   geometry_status='missing_native_slot_not_geometry')
        for number, axis in enumerate('xyz'):
            bits = raw[offset+8+8*number:offset+16+8*number]
            value = struct.unpack('<d', bits)[0]
            row[axis+'_raw'] = repr(value)
            row[axis+'_raw_ieee754_le'] = bits.hex()
            row[axis+'_state'] = ('native_missing_sentinel' if is_native_missing_float(value) else
                                  'nonfinite' if not math.isfinite(value) else
                                  'outside_coordinate_guard' if abs(value) > MAX_ABS_COORDINATE else 'finite')
        return row


def polygon_part_export_rows(object_id, data_pk, object_name, version, metadata):
    """One metadata row per original part, including parts without finite vertices."""
    for segment in metadata['segments']:
        repeated = segment.get('is_closed_by_repeated_xyz')
        yield dict(object_id=object_id, data_pk=data_pk, object_name=object_name, version=version,
            part_index=segment['part_index'], segment_id=segment['segment_id'],
            segment_id_source=segment['segment_id_source'], native_serialization_id=segment['native_serialization_id'],
            declared_vertex_count=segment['declared_vertex_count'], missing_vertex_slots=segment['missing_vertex_slots'],
            geometry_vertex_rows=segment['declared_vertex_count'] - segment['missing_vertex_slots'],
            is_closed_native='yes' if segment['is_closed_native'] else 'no',
            is_closed_by_repeated_xyz='unknown' if repeated is None else 'yes' if repeated else 'no',
            coordinate_components='native XYZ; units/CRS and physical roles unresolved',
            ring_role='unresolved_from_native_geometry_payload',
            attributes_status=metadata['attributes_status'], vertex_order=metadata['vertex_order'],
            gap_policy='Original vertex_index plus declared count preserves absent ordinals; never bridge gaps or infer closure')


def decode_scalar(payload: bytes, offset: int) -> tuple[float, int, str]:
    if offset >= len(payload):
        raise DecodeError("Truncated BXML scalar")
    code = payload[offset]
    offset += 1
    formats: dict[int, tuple[str, int, str]] = {
        0x89: ("b", 1, "int8"),
        0x8B: ("h", 2, "int16"),
        0x8D: ("i", 4, "int32"),
        0x8F: ("q", 8, "int64"),
        0x91: ("f", 4, "float32"),
        0x93: ("d", 8, "float64"),
    }
    if code == 0x81:
        return 0.0, offset, "compact_zero"
    if code not in formats:
        raise DecodeError(f"Unsupported BXML numeric scalar code: 0x{code:02x}")
    fmt, width, label = formats[code]
    if offset + width > len(payload):
        raise DecodeError("Truncated BXML numeric scalar")
    value = struct.unpack_from("<" + fmt, payload, offset)[0]
    return float(value), offset + width, label


def consume_expected_with_opaque_frame(
    payload: bytes,
    offset: int,
    expected: bytes,
    frames: list[dict[str, Any]],
    context: str,
    max_frame_bytes: int = 16,
) -> int:
    """Consume a fixed token, allowing one uniquely determined short insertion."""

    if payload[offset : offset + len(expected)] == expected:
        return offset + len(expected)
    options: list[tuple[int, int, int]] = []
    for split in range(len(expected) + 1):
        if payload[offset : offset + split] != expected[:split]:
            continue
        for skipped in range(1, max_frame_bytes + 1):
            suffix_start = offset + split + skipped
            if payload[suffix_start : suffix_start + len(expected) - split] == expected[split:]:
                options.append((split, skipped, suffix_start + len(expected) - split))
    if not options:
        raise DecodeError(f"Expected BXML token was not found: {context}")
    checkpoint_options = [
        option
        for option in options
        if option[1] == 3 and payload[offset + option[0]] == 0xA0
    ]
    if len(checkpoint_options) == 1:
        options = checkpoint_options
    # Prefer the option preserving the longest literal prefix, then the
    # shortest insertion.  Equal-ranked alternatives are ambiguous and fail.
    options.sort(key=lambda item: (-item[0], item[1]))
    best = options[0]
    if len(options) > 1 and (-options[1][0], options[1][1]) == (-best[0], best[1]):
        raise DecodeError(f"Ambiguous opaque-frame recovery: {context}")
    split, skipped, end_offset = best
    frame_start = offset + split
    frames.append(
        {
            "start_offset": frame_start,
            "end_offset": frame_start + skipped,
            "bytes_hex": payload[frame_start : frame_start + skipped].hex(),
            "context": context,
            "token_split_after_bytes": split,
        }
    )
    return end_offset


def decode_scalar_with_opaque_frame(
    payload: bytes,
    offset: int,
    frames: list[dict[str, Any]],
    context: str,
) -> tuple[float, int, str]:
    supported = {0x81, 0x89, 0x8B, 0x8D, 0x8F, 0x91, 0x93}
    if offset < len(payload) and payload[offset] in supported:
        return decode_scalar(payload, offset)
    # Three supplied 2018 trajectory objects place the same compact checkpoint
    # shape, ``A0 <two opaque bytes>``, immediately before the scalar type.
    # Accept it only at an already-proven scalar boundary.
    if offset + 3 < len(payload) and payload[offset] == 0xA0 and payload[offset + 3] in supported:
        frames.append(
            {
                "start_offset": offset,
                "end_offset": offset + 3,
                "bytes_hex": payload[offset : offset + 3].hex(),
                "context": context + ":before_scalar_code",
                "token_split_after_bytes": 0,
            }
        )
        return decode_scalar(payload, offset + 3)
    candidates = [skip for skip in range(1, 17) if offset + skip < len(payload) and payload[offset + skip] in supported]
    if len(candidates) != 1:
        code = payload[offset] if offset < len(payload) else -1
        raise DecodeError(f"Unsupported or ambiguous BXML numeric scalar code at {context}: 0x{code:02x}")
    skipped = candidates[0]
    frames.append(
        {
            "start_offset": offset,
            "end_offset": offset + skipped,
            "bytes_hex": payload[offset : offset + skipped].hex(),
            "context": context + ":before_scalar_code",
            "token_split_after_bytes": 0,
        }
    )
    return decode_scalar(payload, offset + skipped)


def signed_md_crosscheck(payload: bytes, blob_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain signed stored MD only for the independently read observed profile.

    This proves serialization agreement, not the meaning of a negative datum.
    No shift, unit conversion, or station reordering is applied.
    """
    try:
        documents = list(native_binary.read_documents(payload))
        if len(documents) != 1:
            raise DecodeError('Signed MD requires one complete typed document')
        document = documents[0]
        if (blob_type != 'ExplicitTrajectoryProviderData' or document.name != 'data'
                or document.attrs.get('Type') != blob_type
                or document.attrs.get('Version') != [1, 0, 1]
                or document.attrs.get('xmlns') != 'http://www.slb.com/Petrel/2011/03/Serialization'):
            raise DecodeError('Signed MD trajectory profile is not independently validated')
        collection = document.child('records')
        items = collection.children
        if collection.attrs.get('Size') != len(records) or len(items) != len(records):
            raise DecodeError('Signed MD typed record count disagrees')
        fields = TRAJECTORY_FIELDS[blob_type]
        for item, record in zip(items, records):
            if item.name != 'item' or {c.name for c in item.children} != set(fields):
                raise DecodeError('Signed MD typed trajectory fields disagree')
            if any(item.get(name) != record[name] for name in fields):
                raise DecodeError('Signed MD typed trajectory values disagree')
    except native_binary.NativeError as exc:
        raise DecodeError('Signed MD typed cross-check failed: '+str(exc)) from exc
    return {'negative_md_records': sum(r['md'] < -1e-9 for r in records),
            'md_validation': 'monotonic_signed_values; independent_typed_readback_exact',
            'md_reference_status': 'unresolved; signed native values preserved without offset',
            'md_min': min(r['md'] for r in records), 'md_max': max(r['md'] for r in records)}


def trajectory_station_evidence(records: Sequence[dict[str, Any]], *, legacy: bool = False,
                                xyz_only: bool = False) -> dict[str, Any]:
    """Describe decoded stored stations; do not repair, resample or infer datums."""
    if xyz_only:
        # XYZ providers store positions, not MD or survey angles. Their raw
        # first_md setting and the separately derived path length are not a
        # measured-depth array and must not acquire MD statistics.
        for index, record in enumerate(records):
            if (type(record.get('record_index')) is not int or record['record_index'] != index
                    or any(k in record for k in ('md', 'inclination', 'azimuth', 'inclination_raw', 'azimuth_raw'))
                    or any(type(record.get(k)) not in (int, float) or not math.isfinite(record[k])
                           or abs(record[k]) > MAX_ABS_COORDINATE for k in ('x', 'y', 'z'))):
                raise DecodeError('XYZ station metadata requires ordered finite XYZ without MD or angle fields')
        count = len(records)
        return dict(schema='geoviewer.native-trajectory-stations/1', station_count=count,
            station_order='native_record_index_ascending_preserved', md_source='not_stored_in_xyz_provider',
            md_min=None, md_max=None, negative_md_stations=None, adjacent_equal_md_pairs=None,
            adjacent_decreasing_md_pairs=None, md_strictly_increasing=None, md_nondecreasing=None,
            adjacent_pair_count=max(0, count-1), md_step_min=None, md_step_max=None,
            md_spacing_exactly_uniform=None, xyz_station_count=count, station_nulls=0,
            null_scope='Accepted XYZ passed finite/native-null guards; rejected payloads are not counted here.',
            gap_status='not_inferred_without_expected_station_spacing',
            native_angle_units='not_stored_in_xyz_provider', angle_derivation='none',
            md_reference='not_stored', vertical_reference='unresolved', azimuth_reference='not_stored',
            cumulative_3d_distance_role='separate_derived_geometry_measure_never_substituted_for_stored_MD',
            scope='Stored XYZ/order only; no MD or angle reconstruction, resampling, unit/datum calibration or receiving qualification')
    previous = None
    negative = equal = decreasing = xyz_count = 0
    minimum = maximum = min_step = max_step = first_step = None
    uniform = True
    for index, record in enumerate(records):
        if type(record.get('record_index')) is not int or record['record_index'] != index:
            raise DecodeError('Trajectory station order differs from native sequence')
        md = record.get('md')
        if type(md) not in (float, int) or not math.isfinite(md):
            raise DecodeError('Trajectory station metadata requires finite stored MD')
        negative += md < 0
        minimum = md if minimum is None else min(minimum, md)
        maximum = md if maximum is None else max(maximum, md)
        xyz_count += all(type(record.get(axis)) in (int, float) and math.isfinite(record[axis]) for axis in ('x', 'y', 'z'))
        if previous is not None:
            step = md - previous
            if not math.isfinite(step):
                raise DecodeError('Trajectory stored MD difference is not finite')
            equal += step == 0
            decreasing += step < 0
            min_step = step if min_step is None else min(min_step, step)
            max_step = step if max_step is None else max(max_step, step)
            if first_step is None: first_step = step
            else: uniform = uniform and step == first_step
        previous = md
    count = len(records)
    return dict(schema='geoviewer.native-trajectory-stations/1', station_count=count,
        station_order='native_record_index_ascending_preserved', md_source='stored_measured_depth',
        md_min=minimum, md_max=maximum, negative_md_stations=negative,
        adjacent_equal_md_pairs=equal, adjacent_decreasing_md_pairs=decreasing,
        md_strictly_increasing=(equal == decreasing == 0) if count > 1 else None,
        md_nondecreasing=(decreasing == 0) if count > 1 else None,
        adjacent_pair_count=max(0,count-1), md_step_min=min_step, md_step_max=max_step,
        md_spacing_exactly_uniform=uniform if count > 2 else None,
        xyz_station_count=xyz_count, station_nulls=0,
        null_scope='Accepted decoded MD and required fields passed finite/native-null guards; rejected payloads are not counted here.',
        gap_status='not_inferred_without_expected_station_spacing; unequal steps alone do not prove missing stations',
        native_angle_units='unresolved_raw_legacy_values' if legacy else 'radians_under_existing_supported_decoder_profile',
        angle_derivation='none' if legacy else 'separate_degree_columns_from_native_radians',
        md_reference='unresolved', vertical_reference='unresolved', azimuth_reference='unresolved',
        cumulative_3d_distance_role='separate_derived_geometry_measure_never_substituted_for_stored_MD',
        scope='Stored array/order statistics only; no resampling, unit/datum calibration, track reconstruction or receiving qualification')


def decode_legacy_welltrace(payload: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Preserve named legacy arrays; do not infer angle units or a well head.

    Validated against retained 2012.5/2014.4 WellTrace3 profiles. These are
    stored trace stations, not a reconstructed survey or datum interpretation.
    """
    try:
        documents = list(native_binary.read_documents(payload))
        if len(documents) != 1:
            raise DecodeError("WellTrace3 requires one framed document")
        node = documents[0]
        if (node.name != 'data' or node.attrs.get('Type') != 'WellTrace3' or
                node.attrs.get('Version') not in ([25, 2, 0, 1, 1], [26, 2, 0, 1, 1])):
            raise DecodeError("WellTrace3 version/type outside validated profiles")
        points = node.child('trace_points')
        if points.get('has_attr') is not False or points.get('has_object_ids') is not False:
            raise DecodeError("WellTrace3 point attributes/identities outside validated profile")
        vertices = points.child('vertices')
        count = vertices.attrs.get('Size')
        if type(count) is not int or not 0 <= count <= MAX_SCALAR_COUNT // 6:
            raise DecodeError("WellTrace3 vertex count outside bounds")

        def values(container, expected):
            if expected == 0:
                if container.children or container.content:
                    raise DecodeError("WellTrace3 empty array has unexpected data")
                return []
            if (container.content or len(container.children) != 1 or
                    not isinstance(container.children[0], native_binary.Array) or
                    container.children[0].name != 'double' or
                    container.children[0].values.dtype.str != '<f8'):
                raise DecodeError("WellTrace3 requires one float64 array")
            array = container.children[0].values
            if len(array) != expected:
                raise DecodeError("WellTrace3 declared/actual array lengths disagree")
            if not all(math.isfinite(float(v)) and abs(float(v)) <= MAX_ABS_COORDINATE
                       and not is_native_missing_float(float(v)) for v in array):
                raise DecodeError("WellTrace3 contains invalid numeric values")
            return array.tolist()

        xyz = values(vertices, 3 * count)
        arrays = {}
        for name in ('md_array', 'incl_array', 'azim_array'):
            container = node.child(name)
            if type(container.attrs.get('Size')) is not int or container.attrs['Size'] != count:
                raise DecodeError("WellTrace3 station and survey array sizes disagree")
            arrays[name] = values(container, count)
        md = arrays['md_array']
        if any(right < left for left, right in zip(md, md[1:])):
            raise DecodeError("WellTrace3 stored MD decreases")
        rows = [dict(record_index=i, x=xyz[3*i], y=xyz[3*i+1], z=xyz[3*i+2],
                     md=md[i], inclination_raw=arrays['incl_array'][i],
                     azimuth_raw=arrays['azim_array'][i]) for i in range(count)]
        return rows, dict(record_count=count, native_profile=node.attrs['Version'],
                         trajectory_station_evidence=trajectory_station_evidence(rows, legacy=True),
                         geometry_source='WellTrace3.trace_points.vertices; native order',
                         angle_units_status='unresolved; raw values preserved without conversion',
                         md_reference_status='native md_array; units and datum unresolved',
                         coordinate_units_status='unresolved; no axis or sign transform',
                         well_head_boundary='No well head inferred from a trace station',
                         native_flags={k:node.get(k) for k in ('has_points','old_non_spatial_well',
                                       'is_stub','may_have_legacy_unit_inconsistency','created_from_string')})
    except native_binary.NativeError as exc:
        raise DecodeError(str(exc)) from exc


def _trajectory_settings(document: Any, allowed_references: tuple[int, ...]) -> dict[str, Any]:
    """Read an exact observed settings shape without interpreting its codes."""
    settings = document.child('settings')
    fields = ('need_add_extra_points', 'azimuth_reference', 'interpolation_tolerance')
    if (set(settings.attrs) != {'Version'} or type(settings.attrs['Version']) is not int
            or settings.attrs['Version'] != 1 or settings.content
            or [c.name for c in settings.children] != list(fields)
            or any(c.attrs or c.children or len(c.content) != 1 for c in settings.children)):
        raise DecodeError('Trajectory settings profile is not independently validated')
    values = {name: settings.get(name) for name in fields}
    tolerance = values['interpolation_tolerance']
    if (type(values['need_add_extra_points']) is not bool
            or type(values['azimuth_reference']) is not int
            or values['azimuth_reference'] not in allowed_references
            or type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0):
        raise DecodeError('Trajectory settings values are outside the validated profile')
    return values


def _xyz_trajectory_settings(document: Any) -> dict[str, Any]:
    """Admit the retained XYZ settings shape, without applying its algorithm."""
    settings = document.child('settings')
    fields = ('calculation_algorithm', 'first_md', 'is_first_md_calculated')
    if (set(settings.attrs) != {'Version'} or type(settings.attrs['Version']) is not int
            or settings.attrs['Version'] != 1 or settings.content
            or [c.name for c in settings.children] != list(fields)
            or any(c.attrs or c.children or len(c.content) != 1 for c in settings.children)):
        raise DecodeError('XYZ trajectory settings profile is not independently validated')
    values = {name: settings.get(name) for name in fields}
    if (type(values['calculation_algorithm']) is not int or values['calculation_algorithm'] != 1
            or type(values['first_md']) not in (int, float) or values['first_md'] != 0
            or values['is_first_md_calculated'] is not True):
        raise DecodeError('XYZ trajectory settings values are outside the validated profile')
    return values


def decode_trajectory(payload: bytes, blob_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if blob_type == 'WellTrace3':
        return decode_legacy_welltrace(payload)
    # A dictionary token is not a declared type. Validate the entire typed
    # envelope for every MD sign before admitting the marker-based reading.
    native_settings = None
    try:
        documents = list(native_binary.read_documents(payload))
        if len(documents) != 1:
            raise DecodeError('Trajectory requires one complete typed document')
        document = documents[0]
        if (blob_type not in TRAJECTORY_FIELDS or document.name != 'data'
                or document.attrs.get('Type') != blob_type
                or document.attrs.get('xmlns') != 'http://www.slb.com/Petrel/2011/03/Serialization'):
            raise DecodeError('Trajectory profile is not independently validated')
        envelope_profile = 'attribute_version_1_0_1'
        if document.attrs.get('Version') == [1, 0, 1] and any(c.name == 'settings' for c in document.children):
            # Validated against 19 independently read Microsoft NBFX documents,
            # 296 stations. Attribute and child-Version shapes are distinct;
            # mixed envelopes remain rejected. Codes 0/1 are raw declarations.
            if (blob_type not in {'MdInclAzimTrajectoryProviderData', 'XYZTrajectoryProviderData'}
                    or set(document.attrs) != {'xmlns', 'Id', 'Type', 'Version'}
                    or type(document.attrs['Id']) is not int or document.attrs['Id'] != 1
                    or any(type(v) is not int for v in document.attrs['Version'])
                    or document.content
                    or [c.name for c in document.children] != ['settings', 'records']):
                raise DecodeError('Trajectory settings conflict with the validated attribute-version profile')
            if blob_type == 'XYZTrajectoryProviderData':
                # This profile has distinct XYZ settings, no stored MD/angles.
                native_settings = _xyz_trajectory_settings(document)
                envelope_profile = 'xyz_settings_v1_attribute_version_1_0_1'
            else:
                native_settings = _trajectory_settings(document, (0, 1))
                envelope_profile = 'md_incl_settings_v1_attribute_version_1_0_1'
        if document.attrs.get('Version') != [1, 0, 1]:
            # Validated explicit and MD/inclination payloads use
            # scalar Version siblings. Only the exact observed structures are
            # admitted; signed MD remains separately restricted below.
            settings_profile = blob_type == 'MdInclAzimTrajectoryProviderData'
            expected_children = (['settings', 'records', 'Version', 'Version'] if settings_profile
                                 else ['records', 'Version', 'Version'])
            versions = document.children[-2:]
            if (blob_type not in {'ExplicitTrajectoryProviderData', 'MdInclAzimTrajectoryProviderData'}
                    or set(document.attrs) != {'xmlns', 'Id', 'Type'}
                    or type(document.attrs.get('Id')) is not int or document.attrs['Id'] != 1
                    or document.content
                    or [c.name for c in document.children] != expected_children
                    or any(v.attrs or v.children or len(v.content) != 1
                           or type(v.content[0]) is not int or v.content[0] != expected
                           for v, expected in zip(versions, (0, 1)))):
                raise DecodeError('Trajectory profile is not independently validated')
            if settings_profile:
                # This profile was independently length-framed and read
                # by Microsoft NBFX, 147 stations/441 scalars. Settings are raw
                # declarations; code1 is not mapped to true/grid/magnetic north.
                native_settings = _trajectory_settings(document, (1,))
                envelope_profile = 'md_incl_settings_v1_child_versions_0_1'
            else:
                envelope_profile = 'explicit_child_versions_0_1'
        collections = [c for c in document.children if c.name == 'records']
        if len(collections) != 1:
            raise DecodeError('Trajectory requires exactly one records collection')
        typed_records = collections[0]
        if native_settings is not None and (set(typed_records.attrs) != {'Size'}
                or any(item.attrs or any(f.attrs or f.children or len(f.content) != 1
                                         or type(f.content[0]) not in (int, float)
                                         for f in item.children) for item in typed_records.children)):
            raise DecodeError('Trajectory settings-profile records contain unvalidated fields')
    except native_binary.NativeError as exc:
        raise DecodeError('Trajectory typed envelope failed: '+str(exc)) from exc
    dictionary, dictionary_end = parse_initial_dictionary(payload)
    if blob_type not in dictionary:
        raise DecodeError(f"Payload dictionary does not identify {blob_type}")
    fields = TRAJECTORY_FIELDS[blob_type]
    for required in ("records", "item", *fields):
        if required not in dictionary:
            raise DecodeError(f"Trajectory dictionary is missing: {required}")
    records_marker = field_marker(dictionary, "records")
    prefix = records_marker + b"\x06\x08"
    records_position = payload.find(prefix, dictionary_end)
    if records_position < 0:
        raise DecodeError("Trajectory records collection was not found")
    count, cursor = decode_count_scalar(payload, records_position + len(prefix))
    if count > MAX_SCALAR_COUNT:
        raise DecodeError(f"Trajectory record count exceeds the safety bound: {count}")
    item_marker = field_marker(dictionary, "item")
    field_markers = {name: field_marker(dictionary, name) for name in fields}
    records: list[dict[str, Any]] = []
    scalar_encodings: defaultdict[str, set[str]] = defaultdict(set)
    opaque_frames: list[dict[str, Any]] = []
    for record_index in range(count):
        cursor = consume_expected_with_opaque_frame(
            payload, cursor, item_marker, opaque_frames, f"record_{record_index}:item_marker"
        )
        record: dict[str, Any] = {"record_index": record_index}
        for name in fields:
            marker = field_markers[name]
            cursor = consume_expected_with_opaque_frame(
                payload, cursor, marker, opaque_frames, f"record_{record_index}:field_{name}"
            )
            value, cursor, encoding = decode_scalar_with_opaque_frame(
                payload, cursor, opaque_frames, f"record_{record_index}:field_{name}"
            )
            if not math.isfinite(value) or abs(value) > MAX_ABS_COORDINATE:
                raise DecodeError(f"Trajectory field is non-finite or implausibly large: record={record_index}, field={name}")
            record[name] = value
            scalar_encodings[name].add(encoding)
        cursor = consume_expected_with_opaque_frame(
            payload, cursor, b"\x01", opaque_frames, f"record_{record_index}:terminator"
        )
        records.append(record)
    if (type(typed_records.attrs.get('Size')) is not int
            or typed_records.attrs['Size'] != len(records)
            or len(typed_records.children) != len(records) or typed_records.content):
        raise DecodeError('Trajectory typed record count disagrees')
    for item, record in zip(typed_records.children, records):
        if (item.name != 'item' or len(item.children) != len(fields)
                or {c.name for c in item.children} != set(fields) or item.content):
            raise DecodeError('Trajectory typed fields disagree')
        if any(item.get(name) != record[name] for name in fields):
            raise DecodeError('Trajectory typed values disagree')
    md_evidence = {}
    if blob_type in {"ExplicitTrajectoryProviderData", "MdInclAzimTrajectoryProviderData"}:
        md_values = [record["md"] for record in records]
        if any(right + 1.0e-9 < left for left, right in zip(md_values, md_values[1:])):
            raise DecodeError("Stored trajectory MD decreases")
        if any(value < -1.0e-9 for value in md_values):
            md_evidence = signed_md_crosscheck(payload, blob_type, records)
    if blob_type == "XYZTrajectoryProviderData":
        cumulative = 0.0
        for index, record in enumerate(records):
            if index:
                previous = records[index - 1]
                cumulative += math.dist(
                    (previous["x"], previous["y"], previous["z"]),
                    (record["x"], record["y"], record["z"]),
                )
            record["derived_cumulative_3d_distance"] = cumulative
    settings_evidence = {}
    if native_settings is not None and blob_type == 'MdInclAzimTrajectoryProviderData':
        # Preserve stored angles without assigning physical units or applying
        # the interpolation request. This keeps legacy radians/degree columns
        # empty for the newly admitted raw-angle profile.
        for record in records:
            record['inclination_raw'] = record.pop('inclination')
            record['azimuth_raw'] = record.pop('azimuth')
        settings_evidence = {
            'native_settings': native_settings,
            'native_settings_semantics': 'unresolved; stored declarations only, no interpolation or azimuth conversion',
            'angle_units_status': 'unresolved; raw values preserved without conversion',
            'coordinate_units_status': 'unresolved; no coordinate derivation',
            'md_reference_status': 'native stored MD; units and datum unresolved',
        }
    if blob_type == 'XYZTrajectoryProviderData':
        settings_evidence.update(angle_units_status='not stored in XYZ provider',
            coordinate_units_status='unresolved; stored XYZ preserved without axis or sign transform',
            md_reference_status='not stored in XYZ provider; first_md setting and path distance are not a stored MD array')
        if native_settings is not None:
            settings_evidence.update(native_settings=native_settings,
                native_settings_semantics='unresolved; raw declarations only, no algorithm execution or MD reconstruction')
    station_evidence = trajectory_station_evidence(records, legacy=native_settings is not None,
                                                  xyz_only=blob_type == 'XYZTrajectoryProviderData')
    if native_settings is not None and blob_type == 'MdInclAzimTrajectoryProviderData':
        station_evidence['native_angle_units'] = 'unresolved_raw_settings_profile_values'
    return records, {
        **md_evidence,
        **settings_evidence,
        "trajectory_station_evidence": station_evidence,
        "typed_envelope_profile": envelope_profile,
        "dictionary_entries": len(dictionary),
        "record_count": count,
        "records_marker_offset": records_position,
        "scalar_encodings": {name: sorted(values) for name, values in scalar_encodings.items()},
        "opaque_frames_resynchronized": opaque_frames,
    }


def scan_same_count_arrays(
    payload: bytes,
    start: int,
    count: int,
    type_marker: bytes,
    width: int,
    struct_code: str,
) -> list[PrimitiveArray]:
    results: list[PrimitiveArray] = []
    cursor = start
    while cursor < len(payload):
        position = payload.find(type_marker, cursor)
        if position < 0:
            break
        try:
            candidate_count, _ = read_uleb128(payload, position + len(type_marker))
            if candidate_count != count:
                cursor = position + 1
                continue
            array = decode_primitive_array(payload, position, type_marker, width, struct_code)
        except (DecodeError, OverflowError):
            cursor = position + 1
            continue
        results.append(array)
        cursor = array.end_offset
    return results


def read_validation_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise DecodeError(f"Well-tops validation CSV has no rows: {path}")
    required = {"well_name", "surface", "x", "y"}
    if not required.issubset(rows[0]):
        raise DecodeError(f"Well-tops validation CSV lacks columns: {sorted(required - set(rows[0]))}")
    if "depth" not in rows[0] and "z" not in rows[0]:
        raise DecodeError("Well-tops validation CSV must contain depth or z")
    return rows


def number_or_none(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "-999":
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def normalize_native_float(value: float) -> float | None:
    return None if not math.isfinite(value) or is_native_missing_float(value) else value


def normalize_native_int(value: int) -> int | None:
    return None if value in {2_147_483_647, -2_147_483_648} else value


def numeric_array_match(
    values: Sequence[float | int], rows: Sequence[dict[str, str]], field: str, tolerance: float
) -> dict[str, Any] | None:
    compared = 0
    missing_agreement = 0
    errors: list[float] = []
    for native, row in zip(values, rows):
        expected = number_or_none(row.get(field))
        actual: float | None
        if isinstance(native, int):
            normalized = normalize_native_int(native)
            actual = float(normalized) if normalized is not None else None
        else:
            actual = normalize_native_float(float(native))
        if expected is None or actual is None:
            missing_agreement += int(expected is None and actual is None)
            if (expected is None) != (actual is None):
                return None
            continue
        compared += 1
        errors.append(abs(actual - expected))
    if not compared or max(errors, default=0.0) > tolerance:
        return None
    return {
        "field": field,
        "compared": compared,
        "missing_agreement": missing_agreement,
        "max_abs_error": max(errors, default=0.0),
        "mean_abs_error": sum(errors) / len(errors),
    }


def categorical_index_match(
    values: Sequence[int], rows: Sequence[dict[str, str]], field: str
) -> dict[str, Any] | None:
    value_to_label: dict[int, set[str]] = defaultdict(set)
    label_to_value: dict[str, set[int]] = defaultdict(set)
    for value, row in zip(values, rows):
        label = str(row.get(field, "")).strip()
        if not label:
            return None
        value_to_label[int(value)].add(label)
        label_to_value[label].add(int(value))
    if any(len(labels) != 1 for labels in value_to_label.values()) or any(
        len(indexes) != 1 for indexes in label_to_value.values()
    ):
        return None
    return {
        "field": field,
        "index_to_label": {str(value): next(iter(labels)) for value, labels in sorted(value_to_label.items())},
        "unique_indexes": len(value_to_label),
        "unique_labels": len(label_to_value),
    }


def calibrate_well_tops(
    point_objects: Sequence[dict[str, Any]],
    validation_rows: Sequence[dict[str, str]],
    tolerance: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_xyz = [
        (
            float(row["x"]),
            float(row["y"]),
            float(row.get("depth") or row.get("z") or "nan"),
        )
        for row in validation_rows
    ]
    matches: list[tuple[dict[str, Any], list[float]]] = []
    for candidate in point_objects:
        points = candidate["points"]
        if len(points) != len(expected_xyz):
            continue
        if any(point is None for point in points):
            continue
        distances = [math.dist(native, expected) for native, expected in zip(points, expected_xyz)]
        if distances and max(distances) <= tolerance:
            matches.append((candidate, distances))
    if len(matches) != 1:
        return [], {
            "status": "failed_closed",
            "reason": "no_unique_points3_xyz_match",
            "candidate_matches": len(matches),
            "validation_rows": len(validation_rows),
            "xyz_tolerance": tolerance,
        }
    candidate, distances = matches[0]
    payload = candidate["payload"]
    point_count = len(candidate["points"])
    geometry_end = int(candidate["geometry_metadata"]["vertices_marker_offset"])
    double_arrays = scan_same_count_arrays(payload, geometry_end + 1, point_count, b"\x42\x1a\x01\x93", 8, "d")
    # The first same-count double array after a 3*N vertex array is an
    # attribute array.  The vertex array itself has 3*N values and is excluded.
    int_arrays = scan_same_count_arrays(payload, geometry_end + 1, point_count, b"\x01\x8d", 4, "i")
    numeric_mapping: dict[str, PrimitiveArray] = {}
    numeric_evidence: dict[str, dict[str, Any]] = {}
    for field in ("measured_depth", "twt_picked", "twt_auto", "tvt", "tst", "dip_angle", "dip_azimuth"):
        matches_for_field: list[tuple[PrimitiveArray, dict[str, Any]]] = []
        for array in double_arrays:
            evidence = numeric_array_match(array.values, validation_rows, field, tolerance)
            if evidence is not None:
                matches_for_field.append((array, evidence))
        if len(matches_for_field) == 1:
            numeric_mapping[field], numeric_evidence[field] = matches_for_field[0]
    int_numeric_mapping: dict[str, PrimitiveArray] = {}
    for field in ("observation_number", "symbol", "zone_log"):
        matches_for_field = []
        for array in int_arrays:
            evidence = numeric_array_match(array.values, validation_rows, field, 0.0)
            if evidence is not None:
                matches_for_field.append((array, evidence))
        if len(matches_for_field) == 1:
            int_numeric_mapping[field], numeric_evidence[field] = matches_for_field[0]
    categorical_mapping: dict[str, tuple[PrimitiveArray, dict[str, Any]]] = {}
    for field in ("surface", "well_name"):
        matches_for_field = []
        for array in int_arrays:
            evidence = categorical_index_match([int(value) for value in array.values], validation_rows, field)
            if evidence is not None:
                matches_for_field.append((array, evidence))
        if len(matches_for_field) == 1:
            categorical_mapping[field] = matches_for_field[0]
    rows: list[dict[str, Any]] = []
    for index, point in enumerate(candidate["points"]):
        row: dict[str, Any] = {
            "record_class": "native_binary_well_top_pick_validated_against_petrel_ascii",
            "native_binary_confirmed": "yes",
            "object_id": candidate["object"].object_id,
            "data_pk": candidate["object"].data_pk,
            "point_index": index,
            "x": point[0],
            "y": point[1],
            "z": point[2],
            "coordinate_source": "native_Points3_LZ4_BXML",
            "label_source": "Petrel_ASCII_calibration_of_native_integer_indexes",
            "xyz_validation_distance": distances[index],
        }
        for field, array in numeric_mapping.items():
            row[field] = normalize_native_float(float(array.values[index]))
        for field, array in int_numeric_mapping.items():
            row[field] = normalize_native_int(int(array.values[index]))
        for field, (array, evidence) in categorical_mapping.items():
            native_index = int(array.values[index])
            row[field + "_index"] = native_index
            row[field] = evidence["index_to_label"][str(native_index)]
        rows.append(row)
    return rows, {
        "status": "validated",
        "object_id": candidate["object"].object_id,
        "data_pk": candidate["object"].data_pk,
        "row_count": point_count,
        "xyz_tolerance": tolerance,
        "max_xyz_distance": max(distances),
        "mean_xyz_distance": sum(distances) / len(distances),
        "native_double_attribute_arrays": len(double_arrays),
        "native_int_attribute_arrays": len(int_arrays),
        "numeric_attributes_mapped": numeric_evidence,
        "categorical_indexes_mapped": {field: evidence for field, (_, evidence) in categorical_mapping.items()},
        "dictionary_frames_skipped": [
            frame
            for array in (*double_arrays, *int_arrays)
            for frame in array.dictionary_frames_skipped
        ],
    }


def _csv_write_event(kind: str, **fields: Any) -> None:
    """Emit best-effort diagnostics without making telemetry an export dependency."""
    try:
        from geoviewer_diagnostics import event

        event(kind, **fields)
    except Exception:
        pass


def write_csv_if_rows(path: Path, rows: Sequence[dict[str, Any]], preferred_fields: Sequence[str]) -> bool:
    if not rows:
        # Prevent a prior run's generated derivative from masquerading as a
        # current result when the present decode or calibration yields no rows.
        if path.is_file():
            path.unlink()
        return False

    started = time.monotonic()
    total_rows = len(rows)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    rows_written = 0
    _csv_write_event(
        "csv_write_started",
        path=str(path),
        rows=0,
        total_rows=total_rows,
        bytes=0,
        elapsed_seconds=0.0,
        buffer_bytes=CSV_WRITE_BUFFER_BYTES,
    )
    try:
        fields = list(preferred_fields)
        for row in rows:
            for field in row:
                if field not in fields:
                    fields.append(field)
        path.parent.mkdir(parents=True, exist_ok=True)
        next_progress = started + CSV_PROGRESS_INTERVAL_SECONDS
        # The temporary file is beside the final destination so os.replace does
        # not cross volumes. On Windows, replacement can still fail when another
        # process holds the destination open; in that case the prior file remains.
        with open(
            temporary,
            "x",
            encoding="utf-8-sig",
            newline="",
            buffering=CSV_WRITE_BUFFER_BYTES,
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            while rows_written < total_rows:
                batch_end = min(rows_written + CSV_WRITE_BATCH_ROWS, total_rows)
                writer.writerows(rows[rows_written:batch_end])
                rows_written = batch_end
                now = time.monotonic()
                if now >= next_progress and rows_written < total_rows:
                    try:
                        bytes_written = temporary.stat().st_size
                    except OSError:
                        bytes_written = None
                    _csv_write_event(
                        "csv_write_progress",
                        path=str(path),
                        rows=rows_written,
                        total_rows=total_rows,
                        bytes=bytes_written,
                        elapsed_seconds=round(now - started, 3),
                    )
                    next_progress = now + CSV_PROGRESS_INTERVAL_SECONDS
        bytes_written = temporary.stat().st_size
        os.replace(temporary, path)
        elapsed = round(time.monotonic() - started, 3)
        _csv_write_event(
            "csv_write_completed",
            path=str(path),
            rows=rows_written,
            total_rows=total_rows,
            bytes=bytes_written,
            elapsed_seconds=elapsed,
        )
        return True
    except Exception as exc:
        try:
            bytes_written = temporary.stat().st_size
        except OSError:
            bytes_written = None
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        elapsed = round(time.monotonic() - started, 3)
        _csv_write_event(
            "csv_write_failed",
            path=str(path),
            rows=rows_written,
            total_rows=total_rows,
            bytes=bytes_written,
            elapsed_seconds=elapsed,
            error=f"{type(exc).__name__}: {exc}",
            severity="error",
        )
        raise


def bounded_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def discover_validation_csv(export_package: Path) -> Path | None:
    candidates = [
        export_package / "02_wells" / "well_tops" / "well_tops_from_petrel_ascii_export.csv",
        export_package / "02_wells" / "well_tops" / "well_tops_from_companion_ascii.csv",
    ]
    candidates.extend(sorted((export_package / "02_wells" / "well_tops").glob("*well*top*.csv")) if (export_package / "02_wells" / "well_tops").is_dir() else [])
    for candidate in candidates:
        if candidate.is_file() and not candidate.name.startswith("native_"):
            return candidate
    return None


def decode_bxml_string_field(payload: bytes, field_offset: int, marker: bytes) -> str:
    cursor = field_offset + len(marker)
    if cursor >= len(payload) or payload[cursor] != 0x99:
        raise DecodeError("BXML string field does not use the validated UTF-8 layout")
    size, cursor = read_uleb128(payload, cursor + 1)
    if not 1 <= size <= 4096 or cursor + size > len(payload):
        raise DecodeError("BXML string field length is outside the validated bounds")
    try:
        value = payload[cursor : cursor + size].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecodeError("BXML string field is not valid UTF-8") from exc
    if not value.strip():
        raise DecodeError("BXML string field is empty")
    return value


def decode_model_well_heads_payload(payload: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Decode exact well names and well-head XY values from Model.ptd BXML.

    The validated Petrel 2010 and 2018 models serialize one ``WellTraceSubject``
    scope per well.  Within that scope, the first ``name`` field is the native
    well name, ``well_head_`` is a two-double XY vector, and an optional
    ``definite_survey_provider_tag_`` is the exact link to a Data.ptd trajectory
    provider.  Unknown or malformed scopes fail closed individually.
    """

    dictionary, dictionary_end = parse_initial_dictionary(payload)
    required = (
        "WellTraceSubject",
        "name",
        "well_head_",
        "definite_survey_provider_tag_",
        "double",
    )
    missing = [name for name in required if name not in dictionary]
    if missing:
        raise DecodeError("Model BXML dictionary is missing well-head fields: " + ", ".join(missing))

    subject_marker = field_marker(dictionary, "WellTraceSubject")
    name_marker = field_marker(dictionary, "name")
    head_marker = field_marker(dictionary, "well_head_")
    provider_marker = field_marker(dictionary, "definite_survey_provider_tag_")
    double_marker = field_marker(dictionary, "double")

    subject_offsets: list[int] = []
    cursor = dictionary_end
    while True:
        cursor = payload.find(subject_marker, cursor)
        if cursor < 0:
            break
        subject_offsets.append(cursor)
        cursor += len(subject_marker)
    if not subject_offsets:
        return [], {
            "status": "not_available",
            "reason": "no_WellTraceSubject_scopes",
            "dictionary_entries": len(dictionary),
        }

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for subject_index, subject_offset in enumerate(subject_offsets):
        subject_end = subject_offsets[subject_index + 1] if subject_index + 1 < len(subject_offsets) else len(payload)
        try:
            head_offset = payload.find(head_marker, subject_offset + len(subject_marker), subject_end)
            if head_offset < 0:
                raise DecodeError("WellTraceSubject has no well_head_ field")
            if payload.find(head_marker, head_offset + len(head_marker), subject_end) >= 0:
                raise DecodeError("WellTraceSubject has multiple well_head_ fields")

            name_offset = payload.find(name_marker, subject_offset + len(subject_marker), head_offset)
            if name_offset < 0:
                raise DecodeError("WellTraceSubject has no name field before well_head_")
            well_name = decode_bxml_string_field(payload, name_offset, name_marker)

            vector_cursor = head_offset + len(head_marker)
            validated_prefix = b"\x03" + double_marker + b"\x01\x93"
            if payload[vector_cursor : vector_cursor + len(validated_prefix)] != validated_prefix:
                raise DecodeError("well_head_ does not use the validated two-double vector layout")
            primitive_marker_offset = vector_cursor + len(validated_prefix) - 1
            coordinates = decode_primitive_array(payload, primitive_marker_offset, b"\x93", 8, "d")
            if len(coordinates.values) != 2:
                raise DecodeError(f"well_head_ contains {len(coordinates.values)} values instead of XY")
            if coordinates.end_offset >= subject_end or payload[coordinates.end_offset] != 0x01:
                raise DecodeError("well_head_ vector terminator is absent")
            x, y = (float(value) for value in coordinates.values)
            if not all(
                math.isfinite(value)
                and not is_native_missing_float(value)
                and abs(value) <= MAX_ABS_COORDINATE
                for value in (x, y)
            ):
                raise DecodeError("well_head_ XY values are not finite native coordinates")

            provider_object_id = ""
            provider_uuid_offset: int | None = None
            provider_status = "native_null_no_definitive_survey_provider"
            provider_offset = payload.find(provider_marker, subject_offset + len(subject_marker), head_offset)
            if provider_offset >= 0:
                provider_value_offset = provider_offset + len(provider_marker)
                if payload[provider_value_offset : provider_value_offset + 1] == b"\xB1":
                    provider_uuid_offset = provider_value_offset + 1
                    if provider_uuid_offset + 16 > head_offset:
                        raise DecodeError("definite survey provider UUID is truncated")
                    provider_object_id = str(uuid.UUID(bytes_le=payload[provider_uuid_offset : provider_uuid_offset + 16]))
                    provider_status = "native_Model_ptd_definite_survey_provider_exact"
                elif payload[provider_value_offset : provider_value_offset + 1] != b"\xA9":
                    provider_status = "unrecognized_provider_value_failed_closed"

            rows.append(
                {
                    "record_class": "native_well_head",
                    "native_binary_confirmed": "true",
                    "well_name": well_name,
                    "well_name_source": "native_Model_ptd_WellTraceSubject_name",
                    "trajectory_provider_object_id": provider_object_id,
                    "trajectory_provider_link_status": provider_status,
                    "x": x,
                    "y": y,
                    "z": None,
                    "xy_coordinate_source": "native_Model_ptd_WellTraceSubject_well_head_",
                    "z_coordinate_source": "",
                    "z_coordinate_status": "not_available_no_validated_trajectory_start_link",
                    "crs_status": "not_resolved_from_native_Model_ptd_well_head_field",
                    "horizontal_units_status": "not_resolved_from_native_Model_ptd_well_head_field",
                    "vertical_reference_status": "not_resolved",
                    "model_subject_index": subject_index,
                    "model_subject_offset": subject_offset,
                    "model_well_name_offset": name_offset,
                    "model_well_head_offset": head_offset,
                    "provider_uuid_offset": provider_uuid_offset,
                    "decode_status": "native_Model_ptd_WellTraceSubject_name_and_well_head_xy_decoded",
                    "dictionary_frames_skipped": list(coordinates.dictionary_frames_skipped),
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "model_subject_index": subject_index,
                    "model_subject_offset": subject_offset,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    name_counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        name_counts[str(row["well_name"])] += 1
    for row in rows:
        occurrence_count = name_counts[str(row["well_name"])]
        row["native_name_occurrence_count"] = occurrence_count
        row["native_name_uniqueness"] = (
            "unique_native_name"
            if occurrence_count == 1
            else "duplicate_native_name_preserved_as_separate_WellTraceSubject"
        )
    rows.sort(key=lambda row: (str(row["well_name"]).casefold(), int(row["model_subject_index"])))
    return rows, {
        "status": "decoded" if rows and not failures else "decoded_with_failed_closed_subjects" if rows else "failed_closed",
        "dictionary_entries": len(dictionary),
        "WellTraceSubject_scopes": len(subject_offsets),
        "well_head_rows": len(rows),
        "provider_links": sum(bool(row["trajectory_provider_object_id"]) for row in rows),
        "duplicate_native_names": {
            name: count for name, count in sorted(name_counts.items()) if count > 1
        },
        "failed_closed_subjects": failures,
    }


def load_model_well_heads(export_package: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model_path = export_package / "08_native_project" / "ptd_store" / "Model.ptd"
    if not model_path.is_file():
        return [], {"status": "not_available", "reason": "copied_Model.ptd_missing", "model_path": str(model_path)}
    try:
        model_bytes = model_path.read_bytes()
        payload, envelope = decompress_lz4_block(model_bytes)
        rows, report = decode_model_well_heads_payload(payload)
        rows, well_evidence = enrich_heads(payload, rows)
        well_evidence['source_model_sha256'] = hashlib.sha256(model_bytes).hexdigest()
        report['native_well_metadata'] = well_evidence
        report.update(
            {
                "model_path": str(model_path),
                "source_open_mode": "binary_read_only_export_copy",
                "model_envelope": envelope,
                "coordinate_boundary": "native well_head_ XY preserved; CRS and units are not inferred",
            }
        )
        return rows, report
    except Exception as exc:
        return [], {
            "status": "failed_closed",
            "model_path": str(model_path),
            "error": f"{type(exc).__name__}: {exc}",
        }


def enrich_well_heads_with_trajectory(
    well_head_rows: Sequence[dict[str, Any]],
    trajectory_rows: Sequence[dict[str, Any]],
    xy_tolerance: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Cross-check Model.ptd well-head XY and attach trajectory-start Z safely."""

    first_trajectory_rows: dict[str, dict[str, Any]] = {}
    for row in sorted(trajectory_rows, key=lambda item: int(item["record_index"])):
        first_trajectory_rows.setdefault(str(row["object_id"]), row)

    enriched: list[dict[str, Any]] = []
    provider_name_mappings: dict[str, dict[str, Any]] = {}
    provider_subjects: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for source_row in well_head_rows:
        provider_id = str(source_row.get("trajectory_provider_object_id") or "")
        if provider_id:
            provider_subjects[provider_id].append(source_row)
    ambiguous_providers = {
        provider_id: [{"well_name": row.get("well_name"),
                       "model_subject_offset": row.get("model_subject_offset")}
                      for row in subjects]
        for provider_id, subjects in provider_subjects.items() if len(subjects) > 1
    }
    crosscheck_counts: defaultdict[str, int] = defaultdict(int)
    crosscheck_distances: list[float] = []
    for source_row in well_head_rows:
        row = dict(source_row)
        provider_id = str(row.get("trajectory_provider_object_id") or "")
        if provider_id and provider_id not in ambiguous_providers:
            provider_name_mappings[provider_id] = {
                "well_name": row["well_name"],
                "well_name_mapping_status": "native_Model_ptd_WellTraceSubject_definite_survey_provider_exact",
                "model_subject_offset": row["model_subject_offset"],
                "model_well_name_offset": row["model_well_name_offset"],
                "provider_uuid_offset": row["provider_uuid_offset"],
            }
            if row.get('native_well_identity_status') == IDENTITY_STATUS:
                provider_name_mappings[provider_id].update(
                    well_id=row['native_well_object_id'],well_identity_status=IDENTITY_STATUS,
                    well_metadata_locator=row['native_well_metadata_locator'])
        if provider_id in ambiguous_providers:
            row["trajectory_provider_link_status"] = "ambiguous_multiple_well_subjects"
            # Preserve native head fields, but never attach a station to an
            # ambiguously owned head merely because the XY happens to match.
            row["xy_trajectory_crosscheck_status"] = "not_assessed_ambiguous_provider_ownership"
            row["z_coordinate_status"] = "native_head_unchanged_ambiguous_provider_ownership"
            crosscheck_counts[row["xy_trajectory_crosscheck_status"]] += 1
            enriched.append(row)
            continue
        trajectory = first_trajectory_rows.get(provider_id) if provider_id else None
        if trajectory is None:
            status = (
                "no_decoded_Data_ptd_trajectory_for_native_provider"
                if provider_id
                else "no_definitive_trajectory_provider_reference_in_Model_ptd"
            )
        else:
            row["trajectory_provider_type"] = trajectory.get("provider_type")
            row["trajectory_first_record_index"] = trajectory.get("record_index")
            tx, ty = trajectory.get("x"), trajectory.get("y")
            if tx is None or ty is None:
                status = "trajectory_payload_has_no_xyz_MDInclAzim_only"
                row["z_coordinate_status"] = "not_available_trajectory_payload_has_no_xyz"
            else:
                distance = math.hypot(float(tx) - float(row["x"]), float(ty) - float(row["y"]))
                row["xy_trajectory_crosscheck_distance"] = distance
                crosscheck_distances.append(distance)
                if distance <= xy_tolerance:
                    status = "matched_first_native_trajectory_station_within_tolerance"
                    tz = trajectory.get("z")
                    if tz is not None and math.isfinite(float(tz)) and not is_native_missing_float(float(tz)):
                        row["z"] = float(tz)
                        row["z_coordinate_source"] = "native_Data_ptd_trajectory_first_station_after_XY_match"
                        row["z_coordinate_status"] = "observed_native_trajectory_start_z_not_resolved_as_datum_elevation"
                        row["vertical_reference_status"] = "trajectory_start_z_reference_not_resolved"
                else:
                    status = "trajectory_first_station_XY_mismatch_failed_closed_for_Z"
                    row["z_coordinate_status"] = "not_attached_due_to_XY_mismatch"
        row["xy_trajectory_crosscheck_status"] = status
        crosscheck_counts[status] += 1
        enriched.append(row)

    return enriched, provider_name_mappings, {
        "xy_tolerance": xy_tolerance,
        "ambiguous_provider_subjects": ambiguous_providers,
        "status_counts": dict(sorted(crosscheck_counts.items())),
        "matched_XY_rows": crosscheck_counts.get("matched_first_native_trajectory_station_within_tolerance", 0),
        "maximum_XY_distance": max(crosscheck_distances) if crosscheck_distances else None,
        "mean_XY_distance": sum(crosscheck_distances) / len(crosscheck_distances) if crosscheck_distances else None,
        "z_boundary": "trajectory first-station Z is attached only after XY match and is not asserted to be datum elevation",
    }


def trajectory_name_candidates(
    export_package: Path, object_ids: Sequence[str], max_distance: int = 128
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Link provider UUID bytes to a nearby registered borehole name in Model.ptd.

    This is kept as a named candidate rather than a confirmed well identity.
    In the supplied 2018 project, 20 provider records place the registered well
    name 53-56 bytes from the provider UUID.  Registry-only UUID occurrences
    elsewhere in Model.ptd have no nearby borehole name and are ignored.
    """

    model_path = export_package / "08_native_project" / "ptd_store" / "Model.ptd"
    borehole_path = export_package / "02_wells" / "well_headers" / "native_borehole_references.csv"
    if not object_ids:
        return {}, {"status": "not_needed", "reason": "no_unmapped_trajectory_providers"}
    if not model_path.is_file() or not borehole_path.is_file():
        return {}, {"status": "not_available", "reason": "Model.ptd_or_borehole_reference_csv_missing"}
    # Name enrichment is optional: an unvalidated Model.ptd envelope must not
    # discard independently decoded Data.ptd geometry or stop the full report.
    try:
        model_payload, envelope = decompress_lz4_block(model_path.read_bytes())
        with borehole_path.open("r", encoding="utf-8-sig", newline="") as handle:
            names = sorted({str(row.get("name", "")).strip() for row in csv.DictReader(handle) if str(row.get("name", "")).strip()})
    except (DecodeError, OSError, UnicodeError, csv.Error) as exc:
        return {}, {
            "status": "failed_closed", "model_path": str(model_path),
            "error": f"{type(exc).__name__}: {exc}",
            "provider_objects_requested": len(object_ids), "provider_objects_mapped": 0,
            "identity_boundary": "unresolved_names; independently_decoded_provider_geometry_retained",
        }
    name_positions: list[tuple[int, str]] = []
    for name in names:
        raw = name.encode("utf-8")
        cursor = 0
        while True:
            position = model_payload.find(raw, cursor)
            if position < 0:
                break
            name_positions.append((position, name))
            cursor = position + 1
    mappings: dict[str, dict[str, Any]] = {}
    for object_id in object_ids:
        try:
            raw_id = uuid.UUID(object_id).bytes_le
        except ValueError:
            continue
        id_positions: list[int] = []
        cursor = 0
        while True:
            position = model_payload.find(raw_id, cursor)
            if position < 0:
                break
            id_positions.append(position)
            cursor = position + 1
        candidates = [
            (abs(name_position - id_position), -len(name), name, id_position, name_position)
            for id_position in id_positions
            for name_position, name in name_positions
            if abs(name_position - id_position) <= max_distance
        ]
        if not candidates:
            continue
        candidates.sort()
        best = candidates[0]
        # Equal-distance, equal-specificity different names are ambiguous.
        if len(candidates) > 1 and candidates[1][:2] == best[:2] and candidates[1][2] != best[2]:
            continue
        mappings[object_id] = {
            "well_name_candidate": best[2],
            "well_name_mapping_status": "native_Model_ptd_uuid_bytes_le_near_registered_borehole_name_candidate",
            "model_byte_distance": best[0],
            "provider_uuid_offset": best[3],
            "well_name_offset": best[4],
        }
    return mappings, {
        "status": "candidate_linkage_completed",
        "model_path": str(model_path),
        "model_envelope": envelope,
        "registered_borehole_names": len(names),
        "provider_objects_requested": len(object_ids),
        "provider_objects_mapped": len(mappings),
        "maximum_byte_distance": max_distance,
        "identity_boundary": "candidate_not_confirmed_well_identity",
    }


def bbox(points: Iterable[tuple[float, float, float] | None]) -> dict[str, float] | None:
    if isinstance(points, PackedPoints3):
        return points.bounds()
    values = [point for point in points if point is not None]
    if not values:
        return None
    return {
        "min_x": min(point[0] for point in values),
        "max_x": max(point[0] for point in values),
        "min_y": min(point[1] for point in values),
        "max_y": max(point[1] for point in values),
        "min_z": min(point[2] for point in values),
        "max_z": max(point[2] for point in values),
    }


def run(args: argparse.Namespace) -> int:
    from geoviewer_diagnostics import event
    export_package = Path(args.export_package).resolve()
    data_file = Path(args.data_file).resolve() if args.data_file else export_package / "08_native_project" / "ptd_store" / "Data.ptd"
    if not export_package.is_dir():
        raise FileNotFoundError(f"Export package directory was not found: {export_package}")
    validation_path = (None if getattr(args, 'skip_well_top_calibration', False) else
                       Path(args.well_tops_validation_csv).resolve() if args.well_tops_validation_csv else discover_validation_csv(export_package))
    connection = open_read_only_sqlite(data_file)
    object_reports: list[dict[str, Any]] = []
    polygon_rows: list[dict[str, Any]] = []
    polygon_part_rows: list[dict[str, Any]] = []
    polygon_attribute_rows = geometry_attributes.AttributeRows()
    point_attribute_rows = geometry_attributes.AttributeRows(point_context=True)
    polygon_binding_source = None
    point_rows = PointVertexRows()
    trajectory_rows: list[dict[str, Any]] = []
    point_objects: list[dict[str, Any]] = []
    point_identity_rows: list[dict[str, Any]] = []
    point_missing_rows = MissingVertexRows()
    polygon_missing_rows = MissingVertexRows()
    context_path=export_package/'01_project_metadata/project_context.json'
    context=json.loads(context_path.read_text(encoding='utf-8-sig')) if context_path.is_file() else {}
    object_names={row['object_id']:row['name'] for row in context.get('objects',[])}
    try:
        objects = live_native_objects(connection)
        for native_object in objects:
            object_started=time.monotonic()
            event('object_started', object_id=native_object.object_id, blob_type=native_object.blob_type,
                  operation='spatial_read_blob', path=str(data_file))
            report: dict[str, Any] = {
                "object_id": native_object.object_id,
                "data_pk": native_object.data_pk,
                "name": native_object.name,
                "object_name": object_names.get(native_object.object_id,native_object.name),
                "version": native_object.version,
                "blob_type": native_object.blob_type,
                "timestamp": native_object.timestamp,
                "status": "failed_closed",
                "decoder_family": "native_spatial:" + native_object.blob_type,
            }
            checkpoint = 'spatial_read_blob'
            try:
                if native_object.blob_type == 'WellTrace3':
                    subjects = [item for item in context.get('objects', [])
                                if item.get('object_id') == native_object.object_id]
                    if (len(subjects) != 1 or subjects[0].get('category') != 'WellTraceSubject'
                            or not subjects[0].get('name')):
                        raise DecodeError('WellTrace3 requires one exact named WellTraceSubject UUID')
                blob = object_blob(connection, native_object)
                event('object_phase', object_id=native_object.object_id, blob_type=native_object.blob_type,
                      operation='spatial_decompress_decode', blob_bytes=len(blob))
                checkpoint = 'spatial_decompress'
                payload, envelope = decompress_lz4_block(blob)
                report["envelope"] = envelope
                # The typed polygon reader consumes declared frames itself. Removing
                # marker-like bytes beforehand can corrupt a float inside an array.
                if native_object.blob_type not in ("Polygons3", "Points3", "WellTrace3"):
                    checkpoint = 'spatial_dictionary_frames'
                    _, initial_dictionary_end = parse_initial_dictionary(payload)
                    payload, embedded_frames = strip_dictionary_extensions(payload, initial_dictionary_end)
                    report["embedded_dictionary_frames_removed"] = embedded_frames
                if native_object.blob_type == "Points3":
                    checkpoint = 'decode_points3'
                    report['attempted_decoder_profile'] = checkpoint
                    missing_slots = bytearray()
                    vectors = []
                    points, metadata = decode_points3(payload, missing_slot_bytes=missing_slots,
                        attribute_vectors=vectors,
                        attribute_value_budget=geometry_attributes.MAX_ATTRIBUTE_VALUES-len(polygon_attribute_rows)-len(point_attribute_rows))
                    point_rows.add(native_object, object_names.get(native_object.object_id,native_object.name), points)
                    point_attribute_rows.add(native_object.object_id, native_object.data_pk, vectors)
                    point_missing_rows.add(native_object.object_id, native_object.data_pk, missing_slots)
                    point_identities = metadata.pop("point_identities")
                    report.update(metadata)
                    report["point_count"] = len(points)
                    report["bbox"] = bbox(points)
                    report["status"] = "decoded" if any(point is not None for point in points) else "empty_supported_object"
                    if point_identities:
                        point_identity_rows.extend(point_identity_export_rows(
                            native_object.object_id, native_object.data_pk, points, point_identities))
                    if any(point is not None for point in points):
                        if validation_path:
                            point_objects.append(
                                {"object": native_object, "payload": payload, "points": points, "geometry_metadata": metadata}
                            )
                elif native_object.blob_type == "Polygons3":
                    checkpoint = 'decode_polygons3'
                    report['attempted_decoder_profile'] = checkpoint
                    missing_slots = bytearray()
                    vectors = []
                    polygons, metadata = decode_polygons3(payload, missing_slot_bytes=missing_slots,
                        attribute_vectors=vectors,
                        attribute_value_budget=geometry_attributes.MAX_ATTRIBUTE_VALUES-len(polygon_attribute_rows)-len(point_attribute_rows))
                    if vectors:
                        if polygon_binding_source is None:
                            polygon_binding_source = polygon_bindings.load_package(export_package, data_file)
                        binding = polygon_bindings.bind_vectors(native_object.object_id, vectors, polygon_binding_source)
                        metadata['attribute_binding_status'] = binding['status']
                        metadata['attribute_binding_details'] = {k:v for k,v in binding.items() if k!='bindings'}
                        metadata['source_blob_sha256'] = hashlib.sha256(blob).hexdigest()
                        if binding['status'] == polygon_bindings.STATUS:
                            metadata['reason'] = 'Raw typed attributes with exact native registry/Model names and IDs; part bindings, default application, units and ring roles unresolved'
                        for vector, identity in zip(vectors, binding['bindings']):
                            vector.binding = dict(identity,source_blob_sha256=metadata['source_blob_sha256'])
                        metadata['attribute_vectors'] = [v.descriptor() for v in vectors]
                    polygon_attribute_rows.add(native_object.object_id, native_object.data_pk, vectors)
                    polygon_missing_rows.add(native_object.object_id, native_object.data_pk, missing_slots)
                    report.update(metadata)
                    polygon_part_rows.extend(polygon_part_export_rows(native_object.object_id, native_object.data_pk,
                        object_names.get(native_object.object_id,native_object.name), native_object.version, metadata))
                    report["bbox"] = bbox(point for polygon in polygons for point in polygon if point is not None)
                    report["status"] = "decoded" if metadata['decoded_nonempty_parts'] else "empty_supported_object"
                    for part_index, polygon in enumerate(polygons):
                        segment = metadata['segments'][part_index]
                        is_closed = len(polygon) > 1 and polygon[0] is not None and polygon[0] == polygon[-1]
                        for vertex_index, point in enumerate(polygon):
                            if point is None:
                                continue
                            x, y, z = point
                            polygon_rows.append(
                                {
                                    "object_id": native_object.object_id,
                                    "data_pk": native_object.data_pk,
                                    "object_name": object_names.get(native_object.object_id,native_object.name),
                                    "version": native_object.version,
                                    "part_index": part_index,
                                    "segment_id": segment['segment_id'],
                                    "segment_id_source": segment['segment_id_source'],
                                    "native_serialization_id": segment['native_serialization_id'],
                                    "vertex_index": vertex_index,
                                    "part_vertex_count": len(polygon),
                                    "is_closed_native": "yes" if segment['is_closed_native'] else "no",
                                    "is_closed_by_repeated_xyz": "yes" if is_closed else "no",
                                    "x": x,
                                    "y": y,
                                    "z": z,
                                    "crs_status": "not_resolved_from_native_geometry_payload",
                                    "decode_status": "native_Polygons3_typed_NBFX_segments_decoded",
                                }
                            )
                else:
                    checkpoint = 'decode_trajectory'
                    report['attempted_decoder_profile'] = checkpoint + ':' + native_object.blob_type
                    records, metadata = decode_trajectory(payload, native_object.blob_type)
                    report.update(metadata)
                    report["status"] = "decoded" if records else "empty_supported_object"
                    for record in records:
                        row: dict[str, Any] = {
                            "object_id": native_object.object_id,
                            "data_pk": native_object.data_pk,
                            "object_name": object_names.get(native_object.object_id,native_object.name),
                            "version": native_object.version,
                            "provider_type": native_object.blob_type,
                            "record_index": record["record_index"],
                            "x": record.get("x"),
                            "y": record.get("y"),
                            "z": record.get("z"),
                            "md": record.get("md"),
                            "md_reference_status": metadata.get('md_reference_status', 'not_resolved_from_native_geometry_payload'),
                            "inclination_radians": record.get("inclination"),
                            "inclination_degrees_derived": math.degrees(record["inclination"]) if "inclination" in record else None,
                            "azimuth_radians": record.get("azimuth"),
                            "azimuth_degrees_derived": math.degrees(record["azimuth"]) if "azimuth" in record else None,
                            "inclination_raw": record.get("inclination_raw"),
                            "azimuth_raw": record.get("azimuth_raw"),
                            "angle_units_status": metadata.get('angle_units_status', 'radians'),
                            "coordinate_units_status": metadata.get('coordinate_units_status', 'unresolved'),
                            "legacy_non_spatial_flag": metadata.get('native_flags', {}).get('old_non_spatial_well'),
                            "legacy_stub_flag": metadata.get('native_flags', {}).get('is_stub'),
                            "derived_cumulative_3d_distance": record.get("derived_cumulative_3d_distance"),
                            "crs_status": "not_resolved_from_native_trajectory_payload",
                            "decode_status": ("native_WellTrace3_stored_arrays_decoded" if native_object.blob_type == 'WellTrace3'
                                              else "native_trajectory_LZ4_BXML_records_decoded"),
                        }
                        trajectory_rows.append(row)
            except Exception as exc:  # per-object fail-closed boundary
                report["error"] = f"{type(exc).__name__}: {exc}"
                report['failure_checkpoint'] = checkpoint
            else:
                if report['status'] in ('decoded', 'empty_supported_object'):
                    report['decoder_profile'] = report.get('attempted_decoder_profile')
            object_reports.append(report)
            report['elapsed_seconds']=round(time.monotonic()-object_started,3)
            print('OBJECT_RESULT '+json.dumps({key:report.get(key) for key in ('object_id','object_name','blob_type','status','error','elapsed_seconds')},ensure_ascii=True),flush=True)
    finally:
        connection.close()

    well_top_rows: list[dict[str, Any]] = []
    well_top_validation: dict[str, Any] = {"status": "not_attempted", "reason": "no_validation_csv"}
    if getattr(args, 'skip_well_top_calibration', False):
        well_top_validation['reason'] = 'Companion calibration stage was skipped or interrupted; independent native spatial decoding continues'
    if validation_path is not None:
        validation_rows = read_validation_rows(validation_path)
        well_top_rows, well_top_validation = calibrate_well_tops(
            point_objects, validation_rows, args.validation_tolerance
        )
        well_top_validation["validation_csv"] = str(validation_path)

    event('spatial_phase', operation='read_model_well_heads', path=str(export_package))
    well_head_rows, model_well_head_decode = load_model_well_heads(export_package)
    well_head_rows, direct_name_mappings, well_head_trajectory_crosscheck = enrich_well_heads_with_trajectory(
        well_head_rows, trajectory_rows, args.validation_tolerance
    )

    trajectory_ids = sorted({str(row["object_id"]) for row in trajectory_rows})
    # In legacy stores the payload UUID is the WellTraceSubject UUID itself.
    # Exact typed inventory identity takes precedence over proximity candidates.
    legacy_ids = {str(row['object_id']) for row in trajectory_rows if row['provider_type'] == 'WellTrace3'}
    for identity in legacy_ids:
        subjects = [item for item in context.get('objects', [])
                    if item.get('object_id') == identity and item.get('category') == 'WellTraceSubject']
        if len(subjects) == 1 and subjects[0].get('name'):
            direct_name_mappings[identity] = dict(well_name=subjects[0]['name'],
                well_name_mapping_status='native_WellTrace3_same_WellTraceSubject_UUID_exact')
    ambiguous_provider_ids = set(well_head_trajectory_crosscheck.get("ambiguous_provider_subjects", {}))
    # A nearby name cannot resolve competing explicit subject references.
    fallback_ids = [object_id for object_id in trajectory_ids
                    if object_id not in direct_name_mappings and object_id not in ambiguous_provider_ids
                    and object_id not in legacy_ids]
    fallback_name_mappings, fallback_name_linkage = trajectory_name_candidates(export_package, fallback_ids)
    for label, evidence in (("Native well-head metadata", model_well_head_decode),
                            ("Trajectory name candidates", fallback_name_linkage)):
        if evidence.get("status") == "failed_closed":
            print(f"WARNING: {label} unavailable: {evidence.get('error', 'unsupported Model.ptd layout')}; continuing with independent data", file=sys.stderr)
    name_mappings = {**fallback_name_mappings, **direct_name_mappings}
    trajectory_name_linkage = {
        "status": "native_exact_then_candidate_fallback",
        "provider_objects_requested": len(trajectory_ids),
        "native_exact_provider_objects_mapped": len(set(trajectory_ids) & set(direct_name_mappings)),
        "native_exact_provider_links_total": len(direct_name_mappings),
        "native_exact_provider_links_without_decoded_rows": sorted(set(direct_name_mappings) - set(trajectory_ids)),
        "candidate_fallback_provider_objects_requested": len(fallback_ids),
        "candidate_fallback_provider_objects_mapped": len(set(fallback_ids) & set(fallback_name_mappings)),
        "unmapped_provider_objects": sorted(set(trajectory_ids) - set(name_mappings)),
        "ambiguous_provider_subjects": well_head_trajectory_crosscheck.get("ambiguous_provider_subjects", {}),
        "identity_boundary": "WellTraceSubject provider links are exact; fallback proximity names remain candidates",
        "candidate_fallback_report": fallback_name_linkage,
    }
    for row in trajectory_rows:
        mapping = name_mappings.get(str(row["object_id"]))
        if mapping:
            row.update(mapping)
        elif str(row["object_id"]) in ambiguous_provider_ids:
            row["well_name_mapping_status"] = "ambiguous_multiple_well_subjects"
    for report in object_reports:
        mapping = name_mappings.get(str(report["object_id"]))
        if mapping:
            report["well_name_linkage"] = mapping
            if mapping.get('well_identity_status') == IDENTITY_STATUS:
                report['well_id'] = mapping['well_id']
                report['well_identity_status'] = IDENTITY_STATUS

    outputs: list[str] = []
    for relative, rows in (('polygons/native_polygon_missing_vertices.csv', polygon_missing_rows),
                           ('points/native_point_missing_vertices.csv', point_missing_rows)):
        missing_path = export_package / '05_spatial' / relative
        if write_csv_if_rows(missing_path, rows,
                ('object_id', 'data_pk', 'part_index', 'vertex_index', 'geometry_status',
                 *(field for axis in 'xyz' for field in (axis+'_raw', axis+'_raw_ieee754_le', axis+'_state')))):
            outputs.append(str(missing_path))
    attribute_path = export_package / '05_spatial/polygons/native_polygon_attributes.csv'
    if write_csv_if_rows(attribute_path, polygon_attribute_rows, geometry_attributes.FIELDS):
        outputs.append(str(attribute_path))
    point_attribute_path = export_package / '05_spatial/points/native_point_attributes.csv'
    if write_csv_if_rows(point_attribute_path, point_attribute_rows, geometry_attributes.POINT_FIELDS):
        outputs.append(str(point_attribute_path))
    polygon_path = export_package / "05_spatial" / "polygons" / "native_polygons_vertices.csv"
    if write_csv_if_rows(
        polygon_path,
        polygon_rows,
        ("object_id", "data_pk", "object_name", "version", "part_index", "segment_id", "segment_id_source", "native_serialization_id", "vertex_index", "part_vertex_count", "is_closed_native", "is_closed_by_repeated_xyz", "x", "y", "z", "crs_status", "decode_status"),
    ):
        outputs.append(str(polygon_path))
    point_path = export_package / "05_spatial" / "points" / "native_points_vertices.csv"
    polygon_part_path = export_package / "05_spatial" / "polygons" / "native_polygon_parts.csv"
    if write_csv_if_rows(polygon_part_path, polygon_part_rows,
        ('object_id', 'data_pk', 'object_name', 'version', 'part_index', 'segment_id', 'segment_id_source',
         'native_serialization_id', 'declared_vertex_count', 'missing_vertex_slots', 'geometry_vertex_rows',
         'is_closed_native', 'is_closed_by_repeated_xyz', 'coordinate_components', 'ring_role',
         'attributes_status', 'vertex_order', 'gap_policy')):
        outputs.append(str(polygon_part_path))
    if write_csv_if_rows(
        point_path,
        point_rows,
        ("object_id", "data_pk", "object_name", "version", "point_index", "x", "y", "z", "crs_status", "decode_status"),
    ):
        outputs.append(str(point_path))
    identity_path = export_package / "05_spatial" / "points" / "native_points_identity.csv"
    if write_csv_if_rows(
        identity_path,
        point_identity_rows,
        ("object_id", "data_pk", "point_index", "xyz_present", "identity_version", "identity_profile",
         *(field for name in native_binary.Points3NBFX.ID_FIELD_ORDER
           for field in (f"native_{name}", f"native_{name}_nbfx_hex"))),
    ):
        outputs.append(str(identity_path))
    trajectory_path = export_package / "02_wells" / "trajectories" / "native_well_trajectory_records.csv"
    if write_csv_if_rows(
        trajectory_path,
        trajectory_rows,
        ("object_id", "data_pk", "object_name", "well_name", "well_name_candidate", "well_name_mapping_status", "model_byte_distance", "version", "provider_type", "record_index", "x", "y", "z", "md", "md_reference_status", "inclination_radians", "inclination_degrees_derived", "azimuth_radians", "azimuth_degrees_derived", "inclination_raw", "azimuth_raw", "angle_units_status", "coordinate_units_status", "legacy_non_spatial_flag", "legacy_stub_flag", "derived_cumulative_3d_distance", "crs_status", "decode_status"),
    ):
        outputs.append(str(trajectory_path))
    well_head_path = export_package / "02_wells" / "well_headers" / "native_well_heads.csv"
    if write_csv_if_rows(
        well_head_path,
        well_head_rows,
        ("record_class", "native_binary_confirmed", "well_name", "well_name_source", "native_name_occurrence_count", "native_name_uniqueness", "trajectory_provider_object_id", "trajectory_provider_link_status", "trajectory_provider_type", "x", "y", "z", "xy_coordinate_source", "z_coordinate_source", "z_coordinate_status", "xy_trajectory_crosscheck_status", "xy_trajectory_crosscheck_distance", "crs_status", "horizontal_units_status", "vertical_reference_status", "model_subject_index", "model_subject_offset", "model_well_name_offset", "model_well_head_offset", "provider_uuid_offset", "decode_status"),
    ):
        outputs.append(str(well_head_path))
    well_top_path = export_package / "02_wells" / "well_tops" / "native_well_top_picks_validated.csv"
    if write_csv_if_rows(
        well_top_path,
        well_top_rows,
        ("record_class", "native_binary_confirmed", "object_id", "data_pk", "point_index", "well_name_index", "well_name", "surface_index", "surface", "x", "y", "z", "measured_depth", "twt_picked", "twt_auto", "tvt", "tst", "dip_angle", "dip_azimuth", "observation_number", "symbol", "zone_log", "coordinate_source", "label_source", "xyz_validation_distance"),
    ):
        outputs.append(str(well_top_path))

    status_counts: defaultdict[str, int] = defaultdict(int)
    type_counts: defaultdict[str, int] = defaultdict(int)
    for report in object_reports:
        status_counts[report["status"]] += 1
        type_counts[report["blob_type"]] += 1
    legacy_inventory = context.get('payload_type_counts', {}).get('WellTrace3')
    legacy_attempted = [r for r in object_reports if r['blob_type'] == 'WellTrace3']
    legacy_failed = sum(r['status'] not in ('decoded', 'empty_supported_object') for r in legacy_attempted)
    legacy_missing = max(0, legacy_inventory-len(legacy_attempted)) if type(legacy_inventory) is int else None
    coverage = dict(status='partial' if legacy_failed or legacy_missing else
                    'checked_selected_legacy_trajectories' if type(legacy_inventory) is int else 'unknown',
                    inventory_source='project_context.payload_type_counts',
                    inventory_complete=context.get('object_inventory_complete'),
                    legacy_trajectory_inventory=legacy_inventory,
                    legacy_trajectory_attempted=len(legacy_attempted),
                    legacy_trajectory_failed=legacy_failed,
                    legacy_trajectory_unselected=legacy_missing,
                    reason='Legacy WellTrace3 coverage; well-head recovery and units are separate outcomes')
    report_root = export_package / "07_workflows_reports" / "native_spatial_zero_gui"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "native_spatial_decode_report.json"
    summary = {
        "tool": "export_petrel_native_spatial_zero_gui.py",
        "tool_version": "1.0.0-typed-spatial",
        "coverage": coverage,
        "completed_at_utc": utc_now(),
        "source_data_file": str(data_file),
        "source_open_mode": "sqlite_uri_mode_ro",
        "runtime_gui_used": False,
        "petrel_process_launched": False,
        "ocean_api_used": False,
        "source_mutated": False,
        "layout_scope": "observed LZ4-v1/BXML object profiles, including the supplied Petrel 2024.5 fixture; not blanket release compatibility",
        "crs_boundary": "coordinates are preserved numerically; CRS is not inferred from geometry payloads",
        "supported_types": sorted(SUPPORTED_TYPES),
        "object_type_counts": dict(sorted(type_counts.items())),
        "object_status_counts": dict(sorted(status_counts.items())),
        "polygon_vertex_rows": len(polygon_rows),
        "polygon_part_rows": len(polygon_part_rows),
        "polygon_attribute_value_rows": len(polygon_attribute_rows),
        "point_attribute_value_rows": len(point_attribute_rows),
        "point_attribute_csv_sha256": bounded_file_sha256(point_attribute_path) if point_attribute_rows else None,
        "point_vertex_rows": len(point_rows),
        "point_identity_rows": len(point_identity_rows),
        "polygon_missing_vertex_rows": len(polygon_missing_rows),
        "point_missing_vertex_rows": len(point_missing_rows),
        "missing_vertex_export": {
            "row_key": ["object_id", "data_pk", "part_index", "vertex_index"],
            "point_part_index": 0,
            "value_encoding": "native IEEE-754 binary64 little-endian hex plus diagnostic text",
            "scope": "Only slots omitted from finite geometry; union by original indices preserves native XYZ slots without filling or joining gaps",
            "geometry_status": "Auxiliary missing-slot evidence; not complete coordinates or receiving-qualified geometry",
        },
        "point_identity_export": {
            "path": str(identity_path) if point_identity_rows else None,
            "row_key": ["object_id", "data_pk", "point_index"],
            "row_order": "native Points3 object_ids collection order; zero-based point_index",
            "value_encoding": "JSON scalar per native_* column; raw NBFX token hex per *_nbfx_hex column",
            "semantic_status": "native serialization fields only; not scientific or external well identifiers",
            "missing_xyz_policy": "identity row retained with xyz_present=false",
        },
        "point_attribute_export": {
            "path": str(point_attribute_path) if point_attribute_rows else None,
            "row_key": ["object_id", "data_pk", "attribute_vector_index", "value_index"],
            "row_order": "native Points3 attribute vector and value ordinals; no point binding inferred",
            "value_encoding": "typed JSON scalar with raw IEEE-754 little-endian bits for double values; opaque native default bytes",
            "semantic_status": "raw native values only; names, point binding, units, roles, null/default application unresolved",
        },
        "trajectory_rows": len(trajectory_rows),
        "native_well_head_rows": len(well_head_rows),
        "model_well_head_decode": model_well_head_decode,
        "well_head_trajectory_crosscheck": well_head_trajectory_crosscheck,
        "validated_native_well_top_rows": len(well_top_rows),
        "well_top_validation": well_top_validation,
        "trajectory_name_linkage": trajectory_name_linkage,
        "outputs": outputs,
        "objects": object_reports,
    }
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    outputs.append(str(report_path))
    print("Native spatial zero-GUI decode: completed")
    print(f"Export package: {export_package}")
    print(f"Objects inspected: {len(object_reports)}")
    print(f"Polygon vertex rows: {len(polygon_rows)}")
    print(f"Point vertex rows: {len(point_rows)}")
    print(f"Trajectory rows: {len(trajectory_rows)}")
    print(f"Native well-head rows with XY: {len(well_head_rows)}")
    print(f"Validated native well-top rows: {len(well_top_rows)}")
    print(f"Report: {report_path}")
    failed = status_counts.get("failed_closed", 0)
    if failed:
        print(f"WARNING: {failed} object(s) failed closed; see the report", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-package", required=True, help="Existing portable Petrel export package")
    parser.add_argument("--data-file", help="Override copied Data.ptd path")
    parser.add_argument("--well-tops-validation-csv", help="Optional Petrel-authored well-tops CSV for native pick validation")
    parser.add_argument('--skip-well-top-calibration', action='store_true', help='Ignore companion calibration after an incomplete companion stage')
    parser.add_argument("--validation-tolerance", type=float, default=0.02, help="Maximum XYZ/numeric calibration error (default: 0.02)")
    return parser


def record_top_level_failure(args: argparse.Namespace, exc: Exception) -> None:
    """Keep the causal failure even when no object receipt could be produced."""
    from geoviewer_diagnostics import event
    from geoviewer_io import atomic_json, contained, error_details

    details = error_details(exc, category="native_spatial", operation="run_native_spatial")
    # Print first: a failure in the diagnostics sink must not hide the cause.
    print(details["traceback"], file=sys.stderr, end="", flush=True)
    receipt_path = None
    try:
        package = Path(args.export_package).resolve()
        source = (Path(args.data_file).resolve() if args.data_file else
                  package / "08_native_project" / "ptd_store" / "Data.ptd")
        details["source_path"] = str(source)
        if not package.is_dir():
            raise FileNotFoundError(f"Cannot save failure receipt: export package does not exist: {package}")
        report_path = package / "07_workflows_reports" / "native_spatial_zero_gui" / "native_spatial_decode_report.json"
        contained(package, report_path)
        preserved_report = str(report_path) if report_path.exists() else None
        if preserved_report:
            report_path = report_path.with_name(f"native_spatial_decode_failure_{uuid.uuid4().hex}.json")
        receipt = {
            "tool": "export_petrel_native_spatial_zero_gui.py",
            "completed_at_utc": utc_now(), "status": "failed", "has_gaps": True,
            "category": "native_spatial", "operation": details["operation"],
            "reason": details["message"], "error": details,
            "source_data_file": str(source), "source_unchanged": None,
            "source_integrity_status": "unchecked", "outputs": [],
            "output_completeness": "unknown", "preserved_report_path": preserved_report,
        }
        atomic_json(report_path, receipt)
        receipt_path = str(report_path)
        print(f"Failure report: {report_path}", file=sys.stderr, flush=True)
    except Exception as logging_exc:
        print(f"Secondary failure saving spatial diagnostics: {type(logging_exc).__name__}: {logging_exc}",
              file=sys.stderr, flush=True)
    try:
        event("spatial_failed", severity="error", report_path=receipt_path, **details)
    except Exception as logging_exc:
        print(f"Secondary failure emitting spatial diagnostics: {type(logging_exc).__name__}: {logging_exc}",
              file=sys.stderr, flush=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not math.isfinite(args.validation_tolerance) or args.validation_tolerance <= 0:
        parser.error("--validation-tolerance must be a positive finite number")
    try:
        return run(args)
    except Exception as exc:
        record_top_level_failure(args, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
