# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded native-recovery receipt intake for the source catalogue.

The receipt can contain decoder diagnostics that the catalogue does not use.
Parse every JSON value, retain the exact ordered objects and consumed fields,
and hash the complete original bytes without materializing the whole file.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import re
from pathlib import Path

MAX_NATIVE_RECEIPT_BYTES = 768 * 1024 * 1024
MAX_NATIVE_VALUE_BYTES = 32 * 1024 * 1024
MAX_NATIVE_NESTING = 128
MAX_NATIVE_OBJECTS = 1_000_000
MAX_NATIVE_PROJECTION_BYTES = 2 * 1024 * 1024 * 1024
MAX_INTERN_KEYS_BYTES = 64 * 1024 * 1024
_INTERN_FIELDS = frozenset(('native_unit_evidence', 'project_units', 'native_category_legend'))
_NONSPACE = re.compile(rb'\S')


def _space(source: mmap.mmap, position: int) -> int:
    match = _NONSPACE.search(source, position)
    return match.start() if match else len(source)


def _end(source: mmap.mmap, position: int) -> int:
    """Locate a JSON value boundary; json.loads subsequently checks grammar."""
    size = len(source)
    if position >= size:
        raise ValueError('Truncated native receipt')
    opening = source[position]
    if opening == 34:
        cursor = position + 1
        while True:
            quote = source.find(b'"', cursor)
            if quote < 0:
                raise ValueError('Unterminated JSON string in native receipt')
            if quote + 1 - position > MAX_NATIVE_VALUE_BYTES:
                raise ValueError('Native receipt JSON value exceeds explicit byte bound')
            backslashes = 0
            check = quote - 1
            while check >= position and source[check] == 92:
                backslashes += 1
                check -= 1
            if backslashes % 2 == 0:
                return quote + 1
            cursor = quote + 1
    if opening in (123, 91):
        stack = [125 if opening == 123 else 93]
        cursor = position + 1
        while stack:
            if cursor - position > MAX_NATIVE_VALUE_BYTES:
                raise ValueError('Native receipt JSON value exceeds explicit byte bound')
            next_token = source.find(b'"', cursor)
            next_structure = min((found for token in (b'{', b'}', b'[', b']')
                                  if (found := source.find(token, cursor)) >= 0), default=-1)
            if next_token >= 0 and (next_structure < 0 or next_token < next_structure):
                cursor = _end(source, next_token)
                continue
            if next_structure < 0:
                raise ValueError('Truncated JSON structure in native receipt')
            token = source[next_structure]
            if token in (123, 91):
                stack.append(125 if token == 123 else 93)
                if len(stack) > MAX_NATIVE_NESTING:
                    raise ValueError('Native receipt JSON nesting exceeds explicit bound')
            elif not stack or token != stack.pop():
                raise ValueError('Mismatched JSON structure in native receipt')
            cursor = next_structure + 1
        return cursor
    cursor = position
    while cursor < size and source[cursor] not in b',]} \t\r\n':
        cursor += 1
        if cursor - position > MAX_NATIVE_VALUE_BYTES:
            raise ValueError('Native receipt JSON value exceeds explicit byte bound')
    return cursor


def _value(source: mmap.mmap, start: int, end: int):
    if end - start > MAX_NATIVE_VALUE_BYTES:
        raise ValueError('Native receipt JSON value exceeds explicit byte bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate native receipt JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(source[start:end].decode('utf-8-sig'), object_pairs_hook=unique)


def read_native_receipt(path: Path, object_fields: frozenset[str]) -> tuple[dict, str, dict]:
    """Read native metadata with a bounded per-value heap and full-byte digest."""
    with Path(path).open('rb') as handle:
        size = handle.seek(0, 2)
        if size > MAX_NATIVE_RECEIPT_BYTES:
            raise ValueError('Native receipt exceeds explicit input byte bound')
        if size == 0:
            raise ValueError('Empty native receipt')
        handle.seek(0)
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as source:
            if len(source) > MAX_NATIVE_RECEIPT_BYTES:
                raise ValueError('Native receipt exceeds explicit input byte bound')
            receipt_hash = hashlib.sha256(source).hexdigest()
            pos = _space(source, 3 if source[:3] == b'\xef\xbb\xbf' else 0)
            if source[pos:pos + 1] != b'{':
                raise ValueError('Native receipt must be a JSON object')
            pos += 1
            result = {}
            skipped = {}
            projected = 0
            interned = {}
            intern_key_bytes = 0
            root_expect_key = True
            while True:
                pos = _space(source, pos)
                if source[pos:pos + 1] == b'}':
                    if root_expect_key and result:
                        raise ValueError('Trailing comma in native receipt object')
                    pos += 1
                    break
                key_end = _end(source, pos)
                key = _value(source, pos, key_end)
                if not isinstance(key, str):
                    raise ValueError('Invalid native receipt key')
                if key in result:
                    raise ValueError('Duplicate native receipt JSON key: ' + key)
                pos = _space(source, key_end)
                if source[pos:pos + 1] != b':':
                    raise ValueError('Invalid native receipt object separator')
                pos = _space(source, pos + 1)
                if key == 'objects':
                    if source[pos:pos + 1] != b'[':
                        raise ValueError('Native receipt objects must be an array')
                    pos += 1
                    objects = []
                    array_expect_value = True
                    while True:
                        pos = _space(source, pos)
                        if source[pos:pos + 1] == b']':
                            if array_expect_value and objects:
                                raise ValueError('Trailing comma in native receipt objects')
                            pos += 1
                            break
                        end = _end(source, pos)
                        item = _value(source, pos, end)
                        if isinstance(item, dict):
                            omitted = set(item) - object_fields
                            for name in omitted:
                                skipped[name] = skipped.get(name, 0) + 1
                            item = {name: value for name, value in item.items() if name in object_fields}
                            for name in _INTERN_FIELDS & item.keys():
                                serial = json.dumps(item[name], sort_keys=True, separators=(',', ':'),
                                                    ensure_ascii=False).encode('utf-8')
                                pool_key = (name, serial)
                                if pool_key in interned:
                                    item[name] = interned[pool_key]
                                elif intern_key_bytes + len(serial) <= MAX_INTERN_KEYS_BYTES:
                                    interned[pool_key] = item[name]
                                    intern_key_bytes += len(serial)
                        objects.append(item)
                        if len(objects) > MAX_NATIVE_OBJECTS:
                            raise ValueError('Native receipt object count exceeds explicit bound')
                        projected += len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
                        if projected > MAX_NATIVE_PROJECTION_BYTES:
                            raise ValueError('Native receipt consumed projection exceeds explicit byte bound')
                        pos = _space(source, end)
                        if source[pos:pos + 1] == b',':
                            pos += 1
                            array_expect_value = True
                        elif source[pos:pos + 1] != b']':
                            raise ValueError('Invalid native receipt array separator')
                        else:
                            array_expect_value = False
                    result[key] = objects
                else:
                    end = _end(source, pos)
                    value = _value(source, pos, end)
                    result[key] = value
                    projected += len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                    if projected > MAX_NATIVE_PROJECTION_BYTES:
                        raise ValueError('Native receipt consumed projection exceeds explicit byte bound')
                    pos = end
                pos = _space(source, pos)
                if source[pos:pos + 1] == b',':
                    pos += 1
                    root_expect_key = True
                elif source[pos:pos + 1] != b'}':
                    raise ValueError('Invalid native receipt object separator')
                else:
                    root_expect_key = False
            if _space(source, pos) != len(source):
                raise ValueError('Trailing bytes in native receipt')
            if hashlib.sha256(source).hexdigest() != receipt_hash:
                raise ValueError('Native receipt changed while being parsed')
            return result, receipt_hash, dict(bytes=size, objects=len(result.get('objects', [])),
                                              projected_bytes=projected, intern_key_bytes=intern_key_bytes,
                                              omitted_field_counts=skipped)
