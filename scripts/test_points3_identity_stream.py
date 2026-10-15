# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded opaque Points3 identity reduction; no Petrel/source-project access."""
import sys
from pathlib import Path
import struct
import csv
import json
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import petrel_native_binary as binary
import export_petrel_native_spatial_zero_gui as spatial

def vint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    return bytes(data + bytes([value]))


def name(value):
    raw = value.encode('utf-8')
    return vint(len(raw)) + raw


def scalar(value):
    if type(value) is bool:
        return b'\x86' if value else b'\x84'
    if type(value) is int:
        return b'\x8c' + struct.pack('<i', value)
    if type(value) is bytes:
        return b'\xa0' + struct.pack('<H', len(value)) + value
    if type(value) is list:
        return b'\xa4' + b''.join(scalar(item) for item in value) + b'\xa6'
    raw = str(value).encode('utf-8')
    return b'\x9a' + struct.pack('<H', len(raw)) + raw


def element(label, value=None, *, children=(), attrs=None):
    data = b'\x40' + name(label)
    for key, item in (attrs or {}).items():
        data += b'\x08' + name(item) if key == 'xmlns' else b'\x04' + name(key) + scalar(item)
    data += b''.join(children)
    if value is not None:
        data += scalar(value)
    return data + b'\x01'


def array(label, values, dtype):
    if dtype != '<f8':
        raise ValueError(dtype)
    return (b'\x03' + element(label) + b'\x93' + vint(len(values))
            + struct.pack('<' + 'd' * len(values), *values))


def frame(*docs, split=False):
    data = b'BXML\x01'
    for doc in docs:
        blocks = [doc[:len(doc)//2], doc[len(doc)//2:]] if split else [doc]
        for block in blocks:
            data += b'\xa0' + vint(len(block)) + block
        data += b'\xa2'
    return data

FIELDS = ('is_active', 'identifier_first', 'identifier_second', 'data_key',
          'last_update', 'os_lmd_database', 'os_lmd_petrel', 'suspended_key')


def identity(version=3, fields=FIELDS, extra=b''):
    return element('item', children=[element(key, False if key == 'is_active' else 0)
                                   for key in fields] + [extra], attrs={'Version': version})


def valued_identity(version, values):
    """Encode field values without consulting the production NBFX writer."""
    return element('item', attrs={'Version': version},
                   children=[element(key, value) for key, value in values.items()])


def document(items=None, size=None, count=None, ids=True, attrs=None):
    items = [identity()] if items is None else items
    count = len(items) if count is None else count
    children = [element('user_data', attrs={'Size': 0}),
                element('vertices', children=[array('double', [1., 2., 3.] * count, '<f8')]
                        if count else [], attrs={'Size': count}),
                element('has_attr', False), element('has_object_ids', ids)]
    if ids:
        children.append(element('object_ids', children=items,
                                attrs={'Size': len(items) if size is None else size}))
    return element('data', children=children, attrs=attrs or {
        'Type': 'Points3', 'Version': [1, 2, 0, 1, 1],
        'xmlns': 'http://www.slb.com/Petrel/2011/03/Serialization'})


class IdentityStreamTests(unittest.TestCase):
    def test_exact_native_values_versions_duplicates_and_compact_inactive(self):
        first = dict(zip(FIELDS, (True, 27, -4, 'key-a', 123456,
                                  b'\x00\xff', 'urn:uuid:source', 0)))
        second = dict(first)
        third = dict(zip(FIELDS, (False, 27, -4, 'key-b', 123457,
                                  b'\x01', 'petrel-only', True)))
        payload = frame(document([valued_identity(2, first), valued_identity(3, second),
                                  valued_identity(3, {'is_active': False}),
                                  valued_identity(3, third)], count=4), split=True)
        points, metadata = spatial.decode_points3(payload)
        self.assertEqual(points, [(1., 2., 3.)] * 4)
        self.assertEqual(metadata['object_id_slot_versions'], [2, 3])
        self.assertEqual(metadata['object_id_validation']['inactive_identity_slots'], 2)
        identities = metadata['point_identities']
        self.assertEqual([item[:2] for item in identities],
                         [(2, 'full'), (3, 'full'), (3, 'inactive_compact'), (3, 'full')])
        self.assertEqual(identities[0][2], tuple(first[key] for key in FIELDS))
        self.assertEqual(identities[0][3][0], '86')
        self.assertEqual(identities[0][3][1], '8c1b000000')
        self.assertEqual(identities[0][3][5], 'a0020000ff')
        self.assertEqual(identities[1][2], identities[0][2])
        self.assertEqual(identities[2][2], (False,) + (None,) * 7)
        self.assertEqual(identities[2][3], ('84',) + (None,) * 7)
        self.assertEqual(identities[3][2], tuple(third[key] for key in FIELDS))
        compact_row = spatial.point_identity_export_rows('obj', 1, points, identities)[2]
        self.assertEqual(compact_row['native_is_active'], 'false')
        self.assertEqual(compact_row['native_identifier_first'], '')
        self.assertEqual(compact_row['native_identifier_first_nbfx_hex'], '')

    def test_export_csv_roundtrip_keeps_ordinals_and_missing_xyz_identity(self):
        values = dict(zip(FIELDS, (True, 99, 1, 'same', 44, b'\x00\xff', 5, 6)))
        payload = frame(document([valued_identity(2, values)] * 3, count=3))
        points, metadata = spatial.decode_points3(payload)
        points[1] = None  # Missing geometry does not erase its native identity slot.
        rows = spatial.point_identity_export_rows('native-object', 7, points,
                                                  metadata['point_identities'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'native_points_identity.csv'
            self.assertTrue(spatial.write_csv_if_rows(path, rows, tuple(rows[0])))
            with path.open(encoding='utf-8-sig', newline='') as handle:
                readback = list(csv.DictReader(handle))
        self.assertEqual([int(row['point_index']) for row in readback], [0, 1, 2])
        self.assertEqual([row['xyz_present'] for row in readback], ['True', 'False', 'True'])
        self.assertEqual([json.loads(row['native_identifier_first']) for row in readback],
                         [99, 99, 99])
        self.assertEqual(json.loads(readback[1]['native_os_lmd_database']),
                         {'value_type': 'bytes', 'raw_hex': '00ff'})
        self.assertEqual(readback[1]['native_os_lmd_database_nbfx_hex'], 'a0020000ff')
        self.assertEqual({row['object_id'] for row in readback}, {'native-object'})

    def test_equal_decoded_integer_retains_distinct_native_token_width(self):
        values = dict(zip(FIELDS, (True, 27, 2, 3, 4, 5, 6, 7)))
        narrow = valued_identity(2, values)
        wide_field = (b'\x40' + name('identifier_first') + b'\x8e'
                      + struct.pack('<q', 27) + b'\x01')
        wide = element('item', attrs={'Version': 2}, children=[
            element(key, values[key]) if key != 'identifier_first' else wide_field
            for key in FIELDS])
        _, metadata = spatial.decode_points3(frame(document([narrow, wide], count=2)))
        items = metadata['point_identities']
        self.assertEqual(items[0][2][1], items[1][2][1])
        self.assertEqual(items[0][3][1], '8c1b000000')
        self.assertEqual(items[1][3][1], '8e1b00000000000000')

    def test_opaque_native_scalar_bytes_are_retained_without_meaning(self):
        values = dict(zip(FIELDS, (True, 1, 2, 3, 4, 5, 6, 7)))
        raw = bytes(range(16))
        opaque_field = b'\x40' + name('data_key') + b'\x94' + raw + b'\x01'
        item = element('item', attrs={'Version': 3}, children=[
            opaque_field if key == 'data_key' else element(key, values[key]) for key in FIELDS])
        _, metadata = spatial.decode_points3(frame(document([item])))
        decoded = spatial.point_identity_values(metadata['point_identities'][0])
        self.assertEqual(json.loads(decoded['native_data_key']),
                         {'nbfx_type': 0x94, 'raw_hex': raw.hex()})
        self.assertEqual(decoded['native_data_key_nbfx_hex'], '94' + raw.hex())

    def test_versions_and_full_tree_have_identical_geometry(self):
        for version in (2, 3):
            payload = frame(document([identity(version)] * 4), split=True)
            ordinary = list(binary.read_documents(payload))[0]
            compact = list(binary.read_points3_documents(payload))[0]
            self.assertEqual(ordinary.child('vertices').array('double').tobytes(),
                             compact.child('vertices').array('double').tobytes())
            slots = compact.child('object_ids')
            self.assertEqual(slots.children, [])
            self.assertEqual(slots.slot_count, 4)
            self.assertEqual(slots.parsed_nodes, 36)
            self.assertEqual(slots.max_item_nodes, 9)
            points, metadata = spatial.decode_points3(payload)
            self.assertEqual(points, [(1., 2., 3.)] * 4)
            self.assertEqual(metadata['object_id_slot_versions'], [version])
            self.assertEqual(metadata['object_id_validation']['validated_slot_count'], 4)

    def test_regular_points_without_ids_and_empty_ids(self):
        for payload in (frame(document(count=2, ids=False)), frame(document([]))):
            spatial.decode_points3(payload)

    def test_ordinary_node_bound_remains_and_items_use_separate_budget(self):
        payload = frame(document([identity()] * 4))
        with patch.object(binary, 'MAX_NODES', 12):
            with self.assertRaisesRegex(binary.NativeError, 'node count exceeds'):
                list(binary.read_documents(payload))
            self.assertEqual(len(spatial.decode_points3(payload)[0]), 4)
        with patch.object(binary, 'MAX_NODES', 2):
            with self.assertRaisesRegex(spatial.DecodeError, 'node count exceeds'):
                spatial.decode_points3(payload)

    def test_unknown_version_fields_and_duplicate_fields_fail(self):
        for item in (identity(99), identity(True), identity(fields=FIELDS[:-1]),
                     identity(fields=FIELDS + ('unknown',)),
                     identity(fields=FIELDS[:-1] + (FIELDS[0],)),
                     identity(extra=element('unexpected', 1))):
            with self.subTest(item=item), self.assertRaisesRegex(spatial.DecodeError, 'profile'):
                spatial.decode_points3(frame(document([item])))

    def test_invalid_scalar_types_and_compact_version_fail_closed(self):
        valid = dict(zip(FIELDS, (True, 1, 2, 3, 4, 5, 6, 7)))
        bad_active = valued_identity(3, {**valid, 'is_active': 1})
        nested = element('item', attrs={'Version': 3}, children=[
            element('is_active', True), element('identifier_first', children=[element('x', 2)]),
            *[element(key, valid[key]) for key in FIELDS[2:]]])
        list_value = element('item', attrs={'Version': 3}, children=[
            element(key, [1, 2] if key == 'data_key' else valid[key]) for key in FIELDS])
        compact_unknown = valued_identity(1, {'is_active': False})
        for item in (bad_active, nested, list_value, compact_unknown):
            with self.subTest(item=item), self.assertRaises(spatial.DecodeError):
                spatial.decode_points3(frame(document([item])))

    def test_compact_v2_preserves_inactive_slots_and_absent_identity_fields(self):
        # Exact retained Version-2 shape uses FalseTextWithEndElement (0x85).
        field = b'\x40' + name('is_active') + b'\x85'
        item = element('item', attrs={'Version': 2}, children=[field])
        payload = frame(document([item] * 3, count=3), split=True)
        points, metadata = spatial.decode_points3(payload)
        self.assertEqual(points, [(1., 2., 3.)] * 3)
        self.assertEqual(metadata['object_id_slot_versions'], [2])
        self.assertEqual(metadata['object_id_validation']['inactive_identity_slots'], 3)
        identities = metadata['point_identities']
        self.assertEqual(identities, [(2, 'inactive_compact', (False,) + (None,) * 7,
                                       ('85',) + (None,) * 7)] * 3)
        points[1] = None
        rows = spatial.point_identity_export_rows('native-object', 7, points, identities)
        self.assertEqual([r['xyz_present'] for r in rows], [True, False, True])
        self.assertEqual([r['point_index'] for r in rows], [0, 1, 2])
        for row in rows:
            self.assertEqual(row['native_is_active'], 'false')
            for field in FIELDS[1:]:
                self.assertEqual(row['native_' + field], '')
                self.assertEqual(row['native_' + field + '_nbfx_hex'], '')

    def test_compact_v2_does_not_accept_active_or_coerced_false_or_extra_fields(self):
        for value in (True, 0, 'false', b'\x00', []):
            with self.subTest(value=value), self.assertRaises(spatial.DecodeError):
                spatial.decode_points3(frame(document([valued_identity(2, {'is_active': value})])))
        bad_items = [
            valued_identity(2, {'is_active': False, 'identifier_first': 0}),
            element('item', attrs={'Version': 2, 'unknown': 0}, children=[element('is_active', False)]),
            element('item', attrs={'Version': 2}, children=[element('is_active', False)] * 2),
            element('item', attrs={'Version': 2}, children=[element('is_active', attrs={'unknown': 0}, value=False)]),
        ]
        for item in bad_items:
            with self.subTest(item=item), self.assertRaises(spatial.DecodeError):
                spatial.decode_points3(frame(document([item])))

    def test_slot_counts_and_geometry_count_fail(self):
        for size, count in ((0, 1), (2, 1), (1, 2), (-1, 1), (2_000_001, 1), (True, 1)):
            with self.subTest(size=size, count=count), self.assertRaises(spatial.DecodeError):
                spatial.decode_points3(frame(document(size=size, count=count)))

    def test_unexpected_array_slot_and_scalar_collection_fail(self):
        for item in (array('item', [1., 2.], '<f8'), b'\x80'):
            with self.subTest(item=item), self.assertRaises(spatial.DecodeError):
                spatial.decode_points3(frame(document([item])))

    def test_truncated_body_frame_and_unknown_token_fail(self):
        body = document()
        damaged = [frame(body[:-cut]) for cut in (1, 2, 5, 15, 50)]
        damaged += [frame(body)[:-1][:-5], frame(document([identity(extra=b'\x7f')]))]
        for payload in damaged:
            with self.subTest(length=len(payload)), self.assertRaises(spatial.DecodeError):
                spatial.decode_points3(payload)

    def test_item_byte_limit_precedes_scalar_allocation(self):
        huge_declared_string = b'\x9c\xff\xff\xff\x7f'
        payload = frame(document([identity(extra=huge_declared_string)]))
        with self.assertRaisesRegex(spatial.DecodeError, 'byte/string work exceeds'):
            spatial.decode_points3(payload)

    def test_item_header_is_bounded_before_allocation(self):
        bad_item = b'\x40\x04item\x04\x04Huge\x9c\xff\xff\xff\x7f'
        with self.assertRaisesRegex(spatial.DecodeError, 'byte/string work exceeds'):
            spatial.decode_points3(frame(document([bad_item])))

    def test_node_and_depth_bounds(self):
        wide = identity(extra=element('unknown', children=[element('x', 0)] * 130))
        with self.assertRaisesRegex(spatial.DecodeError, 'node work exceeds'):
            spatial.decode_points3(frame(document([wide])))
        deep = element('x', 0)
        for _ in range(130):
            deep = element('x', children=[deep])
        with patch.object(binary.Points3NBFX, 'MAX_ID_ITEM_NODES', 200):
            with self.assertRaisesRegex(spatial.DecodeError, 'nesting exceeds'):
                spatial.decode_points3(frame(document([identity(extra=deep)])))

    def test_each_document_reduces_independently_but_decoder_requires_one(self):
        payload = frame(document(), document([identity(2)] * 2), split=True)
        nodes = list(binary.read_points3_documents(payload))
        self.assertEqual([n.child('object_ids').slot_count for n in nodes], [1, 2])
        with self.assertRaisesRegex(spatial.DecodeError, 'Expected one Points3'):
            spatial.decode_points3(payload)

    def test_root_profile_gates_remain(self):
        attrs = {'Type': 'Points3', 'Version': [1, 2, 0, 1, 1],
                 'xmlns': 'http://www.slb.com/Petrel/2011/03/Serialization'}
        for field, value in (('Type', 'Other'), ('Version', [9]), ('xmlns', 'other')):
            with self.subTest(field=field), self.assertRaisesRegex(spatial.DecodeError, 'profile'):
                spatial.decode_points3(frame(document(attrs={**attrs, field: value})))


if __name__ == '__main__':
    unittest.main()
