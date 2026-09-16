#!/usr/bin/env python3
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
import datetime as dt
import json
import math
import os
import sqlite3
import struct
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import petrel_native_binary as native_binary
import time


SUPPORTED_TYPES = {
    "Points3",
    "Polygons3",
    "ExplicitTrajectoryProviderData",
    "MdInclAzimTrajectoryProviderData",
    "XYZTrajectoryProviderData",
}
TRAJECTORY_FIELDS = {
    "ExplicitTrajectoryProviderData": ("x", "y", "z", "md", "inclination", "azimuth"),
    "MdInclAzimTrajectoryProviderData": ("md", "inclination", "azimuth"),
    "XYZTrajectoryProviderData": ("x", "y", "z"),
}
NATIVE_FLOAT_MAX_SENTINEL = 3.4028234663852886e38
MAX_SCALAR_COUNT = 100_000_000
MAX_ABS_COORDINATE = 1.0e15


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


def decode_points3(payload: bytes) -> tuple[list[tuple[float, float, float] | None], dict[str, Any]]:
    """Read framed typed XYZ arrays; never guess offsets from plausible coordinates."""
    try:
        nodes = list(native_binary.read_documents(payload))
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
        if type(point_count) is not int or not 0 <= point_count <= 2_000_000 or vertices.content:
            raise DecodeError('Invalid Points3 declared count')
        values = vertices.array('double') if point_count else []
        if (len(values) != 3*point_count or any(c.name != 'double' for c in vertices.children)
                or (point_count and (values.dtype.kind!='f' or values.dtype.itemsize!=8))):
            raise DecodeError('Points3 typed XYZ count mismatch')
        if node.get('has_object_ids'):
            ids=node.child('object_ids')
            if (ids.attrs.get('Size')!=point_count or len(ids.children)!=point_count or ids.content
                    or any(c.name!='item' or c.attrs.get('Version')!=3 for c in ids.children)):
                raise DecodeError('Points3 object identity slots do not match vertices')
    except native_binary.NativeError as exc:
        raise DecodeError(str(exc)) from exc
    points: list[tuple[float, float, float] | None] = []
    for index in range(0, len(values), 3):
        point = tuple(float(v) for v in values[index : index + 3])
        if any(is_native_missing_float(value) for value in point):
            points.append(None)
        elif all(math.isfinite(value) and abs(value) <= MAX_ABS_COORDINATE for value in point):
            points.append(point)
        else:
            raise DecodeError(f"Points3 contains an invalid XYZ triple at point {index // 3}")
    return points, {
        "decoder": "length_framed_typed_NBFX",
        "declared_point_count": point_count,
        "vertices_scalar_count": len(values),
        "missing_vertex_slots": sum(point is None for point in points),
        "attributes_status": "retained_in_native_source_not_exported" if node.get('has_attr') else "not_present",
        "object_ids_status": "retained_in_native_source_not_exported" if node.get('has_object_ids') else "not_present",
    }


def decode_polygons3(payload: bytes) -> tuple[list[list[tuple[float, float, float] | None]], dict[str, Any]]:
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
                for row in values.reshape((-1, 3)):
                    point = tuple(float(value) for value in row)
                    if any(is_native_missing_float(value) for value in point):
                        polygon.append(None)  # Retain the slot; never renumber or bridge a gap.
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
                missing_vertex_slots=sum(point is None for point in polygon)))
        return polygons, dict(decoder='length_framed_typed_NBFX', outer_item_count=outer_count,
            decoded_nonempty_parts=sum(any(p is not None for p in part) for part in polygons),
            empty_or_undecoded_parts=sum(not any(p is not None for p in part) for part in polygons),
            vertices_scalar_count=3*total_vertices, segments=segments,
            attributes_status='not_exported' if node.get('has_attr') else 'not_present',
            reason='Polygon geometry decoded; attached properties remain in the native source' if node.get('has_attr') else '',
            segment_order='native collection order', vertex_order='native vertex array order')
    except native_binary.NativeError as exc:
        raise DecodeError(str(exc)) from exc


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


def decode_trajectory(payload: bytes, blob_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    if blob_type in {"ExplicitTrajectoryProviderData", "MdInclAzimTrajectoryProviderData"}:
        md_values = [record["md"] for record in records]
        if any(value < -1.0e-9 for value in md_values) or any(
            right + 1.0e-9 < left for left, right in zip(md_values, md_values[1:])
        ):
            raise DecodeError("Stored trajectory MD is negative or decreases")
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
    return records, {
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


def write_csv_if_rows(path: Path, rows: Sequence[dict[str, Any]], preferred_fields: Sequence[str]) -> bool:
    if not rows:
        # Prevent a prior run's generated derivative from masquerading as a
        # current result when the present decode or calibration yields no rows.
        if path.is_file():
            path.unlink()
        return False
    fields = list(preferred_fields)
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return True


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
        payload, envelope = decompress_lz4_block(model_path.read_bytes())
        rows, report = decode_model_well_heads_payload(payload)
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
    crosscheck_counts: defaultdict[str, int] = defaultdict(int)
    crosscheck_distances: list[float] = []
    for source_row in well_head_rows:
        row = dict(source_row)
        provider_id = str(row.get("trajectory_provider_object_id") or "")
        if provider_id:
            provider_name_mappings[provider_id] = {
                "well_name": row["well_name"],
                "well_name_mapping_status": "native_Model_ptd_WellTraceSubject_definite_survey_provider_exact",
                "model_subject_offset": row["model_subject_offset"],
                "model_well_name_offset": row["model_well_name_offset"],
                "provider_uuid_offset": row["provider_uuid_offset"],
            }
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
    export_package = Path(args.export_package).resolve()
    data_file = Path(args.data_file).resolve() if args.data_file else export_package / "08_native_project" / "ptd_store" / "Data.ptd"
    if not export_package.is_dir():
        raise FileNotFoundError(f"Export package directory was not found: {export_package}")
    validation_path = Path(args.well_tops_validation_csv).resolve() if args.well_tops_validation_csv else discover_validation_csv(export_package)
    connection = open_read_only_sqlite(data_file)
    object_reports: list[dict[str, Any]] = []
    polygon_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    trajectory_rows: list[dict[str, Any]] = []
    point_objects: list[dict[str, Any]] = []
    context_path=export_package/'01_project_metadata/project_context.json'
    context=json.loads(context_path.read_text(encoding='utf-8-sig')) if context_path.is_file() else {}
    object_names={row['object_id']:row['name'] for row in context.get('objects',[])}
    try:
        objects = live_native_objects(connection)
        for native_object in objects:
            object_started=time.monotonic()
            report: dict[str, Any] = {
                "object_id": native_object.object_id,
                "data_pk": native_object.data_pk,
                "name": native_object.name,
                "object_name": object_names.get(native_object.object_id,native_object.name),
                "version": native_object.version,
                "blob_type": native_object.blob_type,
                "timestamp": native_object.timestamp,
                "status": "failed_closed",
            }
            try:
                blob = object_blob(connection, native_object)
                payload, envelope = decompress_lz4_block(blob)
                report["envelope"] = envelope
                # The typed polygon reader consumes declared frames itself. Removing
                # marker-like bytes beforehand can corrupt a float inside an array.
                if native_object.blob_type not in ("Polygons3", "Points3"):
                    _, initial_dictionary_end = parse_initial_dictionary(payload)
                    payload, embedded_frames = strip_dictionary_extensions(payload, initial_dictionary_end)
                    report["embedded_dictionary_frames_removed"] = embedded_frames
                if native_object.blob_type == "Points3":
                    points, metadata = decode_points3(payload)
                    report.update(metadata)
                    report["point_count"] = len(points)
                    report["bbox"] = bbox(points)
                    report["status"] = "decoded" if any(point is not None for point in points) else "empty_supported_object"
                    if any(point is not None for point in points):
                        point_objects.append(
                            {"object": native_object, "payload": payload, "points": points, "geometry_metadata": metadata}
                        )
                        for index, point in enumerate(points):
                            if point is None:
                                continue
                            x, y, z = point
                            point_rows.append(
                                {
                                    "object_id": native_object.object_id,
                                    "data_pk": native_object.data_pk,
                                    "object_name": object_names.get(native_object.object_id,native_object.name),
                                    "version": native_object.version,
                                    "point_index": index,
                                    "x": x,
                                    "y": y,
                                    "z": z,
                                    "crs_status": "not_resolved_from_native_geometry_payload",
                                    "decode_status": "native_Points3_LZ4_BXML_xyz_decoded",
                                }
                            )
                elif native_object.blob_type == "Polygons3":
                    polygons, metadata = decode_polygons3(payload)
                    report.update(metadata)
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
                            "inclination_radians": record.get("inclination"),
                            "inclination_degrees_derived": math.degrees(record["inclination"]) if "inclination" in record else None,
                            "azimuth_radians": record.get("azimuth"),
                            "azimuth_degrees_derived": math.degrees(record["azimuth"]) if "azimuth" in record else None,
                            "derived_cumulative_3d_distance": record.get("derived_cumulative_3d_distance"),
                            "crs_status": "not_resolved_from_native_trajectory_payload",
                            "decode_status": "native_trajectory_LZ4_BXML_records_decoded",
                        }
                        trajectory_rows.append(row)
            except Exception as exc:  # per-object fail-closed boundary
                report["error"] = f"{type(exc).__name__}: {exc}"
            object_reports.append(report)
            report['elapsed_seconds']=round(time.monotonic()-object_started,3)
            print('OBJECT_RESULT '+json.dumps({key:report.get(key) for key in ('object_id','object_name','blob_type','status','error','elapsed_seconds')},ensure_ascii=True),flush=True)
    finally:
        connection.close()

    well_top_rows: list[dict[str, Any]] = []
    well_top_validation: dict[str, Any] = {"status": "not_attempted", "reason": "no_validation_csv"}
    if validation_path is not None:
        validation_rows = read_validation_rows(validation_path)
        well_top_rows, well_top_validation = calibrate_well_tops(
            point_objects, validation_rows, args.validation_tolerance
        )
        well_top_validation["validation_csv"] = str(validation_path)

    well_head_rows, model_well_head_decode = load_model_well_heads(export_package)
    well_head_rows, direct_name_mappings, well_head_trajectory_crosscheck = enrich_well_heads_with_trajectory(
        well_head_rows, trajectory_rows, args.validation_tolerance
    )

    trajectory_ids = sorted({str(row["object_id"]) for row in trajectory_rows})
    fallback_ids = [object_id for object_id in trajectory_ids if object_id not in direct_name_mappings]
    fallback_name_mappings, fallback_name_linkage = trajectory_name_candidates(export_package, fallback_ids)
    for label, evidence in (("Native well-head metadata", model_well_head_decode),
                            ("Trajectory name candidates", fallback_name_linkage)):
        if evidence.get("status") == "failed_closed":
            print(f"WARNING: {label} unavailable: {evidence.get('error', 'unsupported Model.ptd layout')}; continuing with independent data", file=sys.stderr)
    name_mappings = {**fallback_name_mappings, **direct_name_mappings}
    trajectory_name_linkage = {
        "status": "native_exact_then_candidate_fallback",
        "provider_objects_requested": len(trajectory_ids),
        "native_exact_provider_objects_mapped": len(direct_name_mappings),
        "candidate_fallback_provider_objects_requested": len(fallback_ids),
        "candidate_fallback_provider_objects_mapped": len(fallback_name_mappings),
        "unmapped_provider_objects": sorted(set(trajectory_ids) - set(name_mappings)),
        "identity_boundary": "WellTraceSubject provider links are exact; fallback proximity names remain candidates",
        "candidate_fallback_report": fallback_name_linkage,
    }
    for row in trajectory_rows:
        mapping = name_mappings.get(str(row["object_id"]))
        if mapping:
            row.update(mapping)
    for report in object_reports:
        mapping = name_mappings.get(str(report["object_id"]))
        if mapping:
            report["well_name_linkage"] = mapping

    outputs: list[str] = []
    polygon_path = export_package / "05_spatial" / "polygons" / "native_polygons_vertices.csv"
    if write_csv_if_rows(
        polygon_path,
        polygon_rows,
        ("object_id", "data_pk", "object_name", "version", "part_index", "segment_id", "segment_id_source", "native_serialization_id", "vertex_index", "part_vertex_count", "is_closed_native", "is_closed_by_repeated_xyz", "x", "y", "z", "crs_status", "decode_status"),
    ):
        outputs.append(str(polygon_path))
    point_path = export_package / "05_spatial" / "points" / "native_points_vertices.csv"
    if write_csv_if_rows(
        point_path,
        point_rows,
        ("object_id", "data_pk", "object_name", "version", "point_index", "x", "y", "z", "crs_status", "decode_status"),
    ):
        outputs.append(str(point_path))
    trajectory_path = export_package / "02_wells" / "trajectories" / "native_well_trajectory_records.csv"
    if write_csv_if_rows(
        trajectory_path,
        trajectory_rows,
        ("object_id", "data_pk", "object_name", "well_name", "well_name_candidate", "well_name_mapping_status", "model_byte_distance", "version", "provider_type", "record_index", "x", "y", "z", "md", "inclination_radians", "inclination_degrees_derived", "azimuth_radians", "azimuth_degrees_derived", "derived_cumulative_3d_distance", "crs_status", "decode_status"),
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
    report_root = export_package / "07_workflows_reports" / "native_spatial_zero_gui"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "native_spatial_decode_report.json"
    summary = {
        "tool": "export_petrel_native_spatial_zero_gui.py",
        "tool_version": "0.8.0-typed-spatial",
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
        "point_vertex_rows": len(point_rows),
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
    parser.add_argument("--validation-tolerance", type=float, default=0.02, help="Maximum XYZ/numeric calibration error (default: 0.02)")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not math.isfinite(args.validation_tolerance) or args.validation_tolerance <= 0:
        parser.error("--validation-tolerance must be a positive finite number")
    try:
        return run(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
