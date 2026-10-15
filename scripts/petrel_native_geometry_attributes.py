# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Typed raw geometry collection attributes; no inferred labels or topology.

Only the observed Version 5 vectors and Version 1 defaults are admitted. A
value ordinal is kept as an ordinal, not asserted to identify a polygon part.
"""
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
import json
import math

import numpy as np
from petrel_native_binary import Array, NativeError, Node
from petrel_native_polygon_bindings import FIELDS as BINDING_FIELDS

MAX_ATTRIBUTE_VALUES = 2_000_000
FIELDS = ('object_id', 'data_pk', 'attribute_vector_index', 'native_type_code',
          'value_index', 'native_value_type', 'value_json', 'raw_ieee754_le',
          'value_state', 'native_default_hex', 'vector_version',
          'association_status', 'semantic_status', *BINDING_FIELDS)
POINT_FIELDS = FIELDS[:-len(BINDING_FIELDS)]
TYPES = {1: ('bools', 'bool', 'boolean'), 2: ('ints', 'int', 'int32'),
         4: ('doubles', 'double', 'binary64'), 5: ('strings', 'item', 'string')}


@dataclass
class AttributeVector:
    index: int
    native_type: int
    values: object
    default: bytes
    binding: dict = field(default_factory=dict)

    def descriptor(self, *, point_context=False):
        row = dict(attribute_vector_index=self.index, native_type_code=self.native_type,
                    native_value_type=TYPES[self.native_type][2], value_count=len(self.values),
                    vector_version=5, native_default_hex=self.default.hex(), default_version=1,
                    default_application='not_inferred',
                    association_status=('native_vector_ordinal_only; point_binding_unresolved' if point_context
                                        else 'native_vector_ordinal_only; part_binding_unresolved'),
                    semantic_status=('raw_typed_values; labels_units_point_roles_unresolved' if point_context
                                     else 'raw_typed_values; labels_units_ring_roles_unresolved'))
        return self._binding(row)

    def _binding(self, row):
        if self.binding:
            row.update(self.binding)
            row['association_status']=self.binding['attribute_binding_status']
        return row

    def row(self, index, *, point_context=False):
        raw = ''; value = self.values[index]
        state = 'stored_value'
        if self.native_type == 4:
            # Do not roundtrip through float to construct the raw bits (NaN payloads).
            raw = self.values[index:index+1].astype('<f8', copy=False).tobytes().hex()
            value = float(value)
            if not math.isfinite(value):
                state = 'nonfinite_raw_value'; value = None
            else:
                state = 'finite_raw_value; null_semantics_unresolved'
        elif self.native_type == 1:
            value = bool(value)
        elif self.native_type == 2:
            value = int(value)
        row = dict(attribute_vector_index=self.index, native_type_code=self.native_type,
                    value_index=index, native_value_type=TYPES[self.native_type][2],
                    value_json=json.dumps(value, ensure_ascii=False, allow_nan=False),
                    raw_ieee754_le=raw, value_state=state,
                    native_default_hex=self.default.hex(), vector_version=5,
                    association_status=('native_vector_ordinal_only; point_binding_unresolved' if point_context
                                        else 'native_vector_ordinal_only; part_binding_unresolved'),
                    semantic_status=('raw_typed_values; labels_units_point_roles_unresolved' if point_context
                                     else 'raw_typed_values; labels_units_ring_roles_unresolved'))
        return self._binding(row)


def decode_attribute_vectors(node, expected_slots, *, max_values=MAX_ATTRIBUTE_VALUES):
    """Return every vector or raise; never partially accept a collection.

    The count equality is a profile check, not proof of per-part semantics.
    Unknown local namespaces/fields/versions are not silently discarded.
    """
    if (not isinstance(node, Node) or node.name != 'attributes' or node.content
            or set(node.attrs) != {'Size'} or type(node.attrs['Size']) is not int
            or not 0 <= node.attrs['Size'] <= 4096
            or len(node.children) != node.attrs['Size']):
        raise NativeError('Unsupported geometry attribute collection')
    if type(expected_slots) is not int or not 0 <= expected_slots <= MAX_ATTRIBUTE_VALUES:
        raise NativeError('Invalid geometry attribute slot count')
    if type(max_values) is not int or not 0 <= max_values <= MAX_ATTRIBUTE_VALUES:
        raise NativeError('Invalid geometry attribute value budget')
    if node.attrs['Size'] * expected_slots > max_values:
        raise NativeError('Geometry attribute values exceed bound')
    vectors = []
    text_bytes = 0
    for index, vector in enumerate(node.children):
        if (not isinstance(vector, Node) or vector.name != 'type_vector'
                or vector.attrs != {'Version': 5} or vector.content):
            raise NativeError('Unsupported geometry type_vector profile')
        type_node = vector.child('type')
        if not isinstance(type_node, Node):
            raise NativeError('Unsupported geometry attribute type field')
        kind = type_node.scalar()
        if type_node.attrs or type(kind) is not int or kind not in TYPES:
            raise NativeError('Unsupported geometry attribute type')
        collection_name, item_name, _ = TYPES[kind]
        if sorted(c.name for c in vector.children) != sorted(('type', collection_name, 'default')):
            raise NativeError('Unexpected or duplicate geometry attribute fields')
        default = vector.child('default')
        if not isinstance(default, Node):
            raise NativeError('Unsupported geometry attribute default field')
        raw_default = default.scalar()
        if (default.attrs != {'Version': 1} or type(default.attrs.get('Version')) is not int
                or type(raw_default) is not bytes or len(raw_default) != 8):
            raise NativeError('Unsupported geometry attribute default')
        collection = vector.child(collection_name)
        if (not isinstance(collection, Node) or collection.attrs != {'Size': expected_slots}
                or type(collection.attrs.get('Size')) is not int or collection.content):
            raise NativeError('Geometry attribute declared count mismatch')
        if kind == 5:
            values = []
            for item in collection.children:
                if not isinstance(item, Node) or item.name != 'item' or item.attrs:
                    raise NativeError('Unsupported geometry attribute string item')
                value = item.scalar()
                if type(value) is not str:
                    raise NativeError('Non-string geometry attribute item')
                text_bytes += len(value.encode('utf-8'))
                if text_bytes > 64*1024*1024:
                    raise NativeError('Geometry attribute text exceeds bound')
                values.append(value)
            values = tuple(values)
        elif not expected_slots and not collection.children:
            values = np.asarray([], dtype={1:'u1', 2:'<i4', 4:'<f8'}[kind])
        else:
            # Restrict numeric admission to the actually observed typed arrays;
            # do not coerce scalar strings or an integer array to floating data.
            if (len(collection.children) != 1 or not isinstance(collection.children[0], Array)
                    or collection.children[0].name != item_name):
                raise NativeError('Unsupported geometry attribute typed array')
            values = collection.children[0].values
            dtype = {1:np.dtype('u1'), 2:np.dtype('<i4'), 4:np.dtype('<f8')}[kind]
            if values.ndim != 1 or values.dtype != dtype:
                raise NativeError('Geometry attribute array type mismatch')
            if kind == 1 and not bool(np.all((values == 0) | (values == 1))):
                raise NativeError('Invalid geometry attribute boolean encoding')
        if len(values) != expected_slots:
            raise NativeError('Geometry attribute value count mismatch')
        vectors.append(AttributeVector(index, kind, values, raw_default))
    return vectors


class AttributeRows(Sequence):
    """Keep compact vectors and create CSV dictionaries only while writing."""
    def __init__(self, *, point_context=False):
        self.blocks = []; self.ends = []; self.point_context = point_context

    def add(self, object_id, data_pk, vectors):
        count = sum(len(v.values) for v in vectors)
        if len(self) + count > MAX_ATTRIBUTE_VALUES:
            raise NativeError('Accumulated geometry attribute values exceed bound')
        for vector in vectors:
            if len(vector.values):
                self.blocks.append((object_id, data_pk, vector))
                self.ends.append(len(self) + len(vector.values))

    def __len__(self):
        return self.ends[-1] if self.ends else 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[n] for n in range(*index.indices(len(self)))]
        if index < 0: index += len(self)
        if not 0 <= index < len(self): raise IndexError(index)
        block = bisect_right(self.ends, index)
        oid, pk, vector = self.blocks[block]
        offset = index - (self.ends[block-1] if block else 0)
        return dict(object_id=oid, data_pk=pk, **vector.row(offset, point_context=self.point_context))
