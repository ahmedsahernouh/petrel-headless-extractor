# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded readers for observed Petrel LZ4-v1/BXML-v1 containers and NBFX data.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
NBFX record/array definitions: Microsoft [MC-NBFX], sections 2.1.1 and 2.3.3.
Petrel's outer framing is independently checked against local native samples.
This module reads data only; serialized Type/Ref attributes never execute code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import struct
import uuid
import tempfile
import numpy as np

MAX_CONTAINER = 512 * 1024 * 1024
MAX_DOCUMENT = 128 * 1024 * 1024
MAX_NAMES = 100_000
MAX_NODES = 2_000_000
MAX_LZ4_BLOCKS = 4096


def sqlite_readonly_uri(path):
    """SQLite rejects UNC URI authorities; encode the host as an absolute path."""
    uri = Path(path).resolve(strict=True).as_uri()
    if uri.startswith('file://') and not uri.startswith('file:///'):
        uri = 'file:////' + uri[len('file://'):]
    return uri + '?mode=ro'


class NativeError(ValueError):
    pass


class Cursor:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0

    def take(self, count):
        if count < 0 or count > len(self.data)-self.pos:
            raise NativeError(f'Truncated binary data at {self.pos}: requested {count} bytes')
        result = self.data[self.pos:self.pos+count]
        self.pos += count
        return result

    def byte(self):
        return self.take(1)[0]

    def number(self, code):
        return struct.unpack('<'+code, self.take(struct.calcsize('<'+code)))[0]

    def varint(self):
        result = 0
        for shift in range(0, 35, 7):
            b = self.byte()
            if shift == 28 and b > 7:
                raise NativeError('MultiByteInt31 overflow')
            result |= (b & 127) << shift
            if b < 128:
                return result
        raise NativeError('Invalid MultiByteInt31')

    def string(self):
        count = self.varint()
        if count > 1024*1024:
            raise NativeError('Name/string exceeds limit')
        return bytes(self.take(count)).decode('utf-8', 'strict')


def read_bounded(path, limit=MAX_CONTAINER):
    with Path(path).open('rb') as stream:
        data = stream.read(limit+1)
    if len(data) > limit:
        raise NativeError('Input exceeds configured native-file bound')
    return data


def decompress(blob, limit=MAX_CONTAINER):
    """Read the observed LZ4-v1 stream: one magic, then length-framed blocks.

    Each block has its own eight-byte header and independent match dictionary.
    Large Model.ptd samples split the BXML stream across these blocks. Declared
    lengths must consume the entire input; output limits apply to their sum.
    The second header word remains opaque, not a claimed checksum validation.
    """
    cur = Cursor(blob)
    if bytes(cur.take(4)) != b'LZ4\x01':
        raise NativeError('Unsupported native compression envelope')
    output = bytearray()
    blocks = 0
    while cur.pos < len(blob):
        blocks += 1
        if blocks > MAX_LZ4_BLOCKS:
            raise NativeError('LZ4 block count exceeds bound')
        offset = cur.pos
        if len(blob)-offset < 8:
            raise NativeError(f'Truncated LZ4 block header at {offset}')
        size = cur.number('I')
        cur.take(4)  # Opaque per-block metadata, retained in the source file.
        if not size or size > len(blob)-cur.pos:
            raise NativeError(f'LZ4 envelope size mismatch at block {blocks}, offset {offset}')
        try:
            block = _decompress_lz4_block(cur.take(size), limit-len(output))
        except NativeError as exc:
            raise NativeError(f'LZ4 block {blocks} at offset {offset}: {exc}') from exc
        output.extend(block)
    if not blocks:
        raise NativeError('LZ4 envelope contains no blocks')
    return bytes(output)


def _decompress_lz4_block(blob, limit):
    """Bounded raw-block decoder; previous blocks cannot satisfy backreferences."""
    cur = Cursor(blob)
    output = bytearray()
    def extended(value):
        if value == 15:
            while True:
                extra = cur.byte(); value += extra
                if value > limit: raise NativeError('LZ4 length exceeds output bound')
                if extra != 255: break
        return value
    while cur.pos < len(blob):
        token = cur.byte(); length = extended(token >> 4)
        if len(output)+length > limit: raise NativeError('LZ4 output exceeds bound')
        output.extend(cur.take(length))
        if cur.pos == len(blob): break
        distance = cur.number('H')
        if not 0 < distance <= len(output): raise NativeError('Invalid LZ4 match offset')
        length = extended(token & 15)+4
        if len(output)+length > limit: raise NativeError('LZ4 output exceeds bound')
        # Repeating the available suffix implements overlapping LZ4 matches.
        pattern = bytes(output[-distance:])
        output.extend((pattern*((length+distance-1)//distance))[:length])
    return bytes(output)


def project_payload(raw):
    cur = Cursor(raw)
    if bytes(cur.take(4)) != b'\xff\xff\x01\x00':
        raise NativeError('Unsupported project serializer header')
    count = cur.number('H')
    if bytes(cur.take(count)) != b'ProjectSerializer':
        raise NativeError('Not the observed ProjectSerializer container')
    cur.take(9)  # Versioned outer fields, retained by source hashing.
    if cur.pos != 32:
        raise NativeError('Unsupported project serializer envelope length')
    return decompress(bytes(cur.take(len(raw)-cur.pos)))


def documents(payload):
    """Yield complete NBFX documents and their dictionary, using declared lengths.

    A1 introduces UTF-8 dictionary entries; A0 precedes a length-delimited data
    block; A2 ends a document. Data bytes are never scanned for frame markers.
    An object blob may end after its final data block without an A2 terminator.
    """
    cur = Cursor(payload)
    if bytes(cur.take(5)) != b'BXML\x01': raise NativeError('Unsupported BXML version')
    names = []; unique = set(); blocks = []; size = 0
    while cur.pos < len(payload):
        token = cur.byte()
        if token == 0xa1:
            name = cur.string()
            if not name or name in unique or len(names) >= MAX_NAMES:
                raise NativeError('Invalid/duplicate BXML dictionary entry')
            names.append(name); unique.add(name)
        elif token == 0xa0:
            count = cur.varint(); size += count
            if count == 0 or size > MAX_DOCUMENT: raise NativeError('BXML document size outside bound')
            blocks.append(cur.take(count))
        elif token == 0xa2:
            if not blocks: raise NativeError('Empty BXML document boundary')
            yield names, b''.join(blocks)
            blocks = []; size = 0
        else:
            raise NativeError(f'Unsupported BXML framing token {token:#x}')
    if blocks: yield names, b''.join(blocks)


@dataclass(slots=True)
class Array:
    name: str
    values: np.ndarray


@dataclass(slots=True)
class Node:
    name: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    content: list = field(default_factory=list)

    def child(self, name, required=True):
        found = [n for n in self.children if n.name == name]
        if len(found) > 1 or (not found and required):
            raise NativeError(f'{self.name}: expected one {name}, found {len(found)}')
        return found[0] if found else None

    def scalar(self):
        if self.children: raise NativeError(f'{self.name}: scalar has child records')
        if not self.content: return ''
        if len(self.content) == 1: return self.content[0]
        if all(isinstance(v, bytes) for v in self.content): return b''.join(self.content)
        if all(isinstance(v, str) for v in self.content): return ''.join(self.content)
        raise NativeError(f'{self.name}: ambiguous mixed scalar content')

    def get(self, name, default=''):
        node = self.child(name, required=False)
        return node.scalar() if node is not None else default

    def array(self, name):
        found = [c for c in self.children if c.name == name]
        if len(found) == 1 and isinstance(found[0], Array): return found[0].values
        if found and all(isinstance(c, Node) for c in found): return np.asarray([c.scalar() for c in found])
        raise NativeError(f'{self.name}: missing/ambiguous {name} array')


class NBFX(Cursor):
    """Typed, bounded NBFX subset. Unknown tokens fail; namespaces stay explicit."""
    def __init__(self, body, names):
        super().__init__(body); self.names = names; self.node_count = 0

    def count_node(self):
        self.node_count += 1
        if self.node_count > MAX_NODES: raise NativeError('NBFX node count exceeds bound')

    def opened(self, node, stack):
        return node

    def closed(self, node, stack):
        pass

    def record_text(self, node, start, end):
        pass

    def validate_array(self, name, kind, count, stack):
        pass

    def dictionary(self):
        value = self.varint()
        if value & 1 or value//2 >= len(self.names): raise NativeError('Unresolved NBFX dictionary key')
        return self.names[value//2]

    def text(self, token):
        code = token & 0xfe
        if code in (0x80,0x82,0x84,0x86): return {0x80:0,0x82:1,0x84:False,0x86:True}[code]
        if code in (0x88,0x8a,0x8c,0x8e,0x90,0x92,0xb2):
            return self.number({0x88:'b',0x8a:'h',0x8c:'i',0x8e:'q',0x90:'f',0x92:'d',0xb2:'Q'}[code])
        if code in (0x94,0x96,0xae):
            # Non-geoscience metadata preserved as typed raw bytes, never reinterpreted.
            return {'nbfx_type':code,'raw_hex':bytes(self.take(16 if code==0x94 else 8)).hex()}
        if code in (0x98,0x9a,0x9c,0x9e,0xa0,0xa2,0xb6,0xb8,0xba):
            width = {0x98:1,0x9a:2,0x9c:4,0x9e:1,0xa0:2,0xa2:4,0xb6:1,0xb8:2,0xba:4}[code]
            length = int.from_bytes(self.take(width),'little')
            raw = bytes(self.take(length))
            if code in (0x9e,0xa0,0xa2): return raw
            return raw.decode('utf-16-le' if code>=0xb6 else 'utf-8','strict')
        if code == 0xa4:
            values = []
            while True:
                next_token = self.byte()
                if next_token == 0xa6: return values
                if next_token & 1 or next_token == 0xa4: raise NativeError('Invalid NBFX list item')
                values.append(self.text(next_token))
        if code == 0xa8: return ''
        if code == 0xaa: return self.dictionary()
        if code in (0xac,0xb0):
            return ('urn:uuid:' if code==0xac else '')+str(uuid.UUID(bytes_le=bytes(self.take(16))))
        if code == 0xb4:
            value=self.byte()
            if value not in (0,1): raise NativeError('Invalid NBFX boolean')
            return bool(value)
        if code == 0xbc:
            prefix=self.byte()
            if prefix>25: raise NativeError('Invalid QName prefix')
            return chr(97+prefix)+':'+self.dictionary()
        raise NativeError(f'Unsupported NBFX text token {token:#x} at {self.pos-1}')

    def start(self, token):
        if token == 0x40: name=self.string()
        elif token == 0x42: name=self.dictionary()
        else: raise NativeError(f'Unsupported namespaced/extended NBFX element {token:#x}')
        self.count_node()
        node=Node(name)
        while self.pos<len(self.data) and 4<=self.data[self.pos]<=0x3f:
            attr=self.byte()
            if attr in (4,6):
                key=self.string() if attr==4 else self.dictionary()
                value_token=self.byte()
                if value_token & 1: raise NativeError('Attribute value closes an element')
                value=self.text(value_token)
            elif attr in (8,10):
                key='xmlns'; value=self.string() if attr==8 else self.dictionary()
            else: raise NativeError(f'Unsupported NBFX attribute {attr:#x}')
            if key in node.attrs: raise NativeError('Duplicate NBFX attribute')
            node.attrs[key]=value
        return node

    def parse(self):
        stack=[];root=None
        while self.pos<len(self.data):
            token=self.byte()
            if token in (0x40,0x42):
                node=self.opened(self.start(token), stack)
                if stack: stack[-1].children.append(node)
                elif root is not None: raise NativeError('Multiple NBFX document roots')
                else: root=node
                stack.append(node)
                if len(stack)>128: raise NativeError('NBFX nesting exceeds bound')
            elif token==1:
                if not stack: raise NativeError('Unbalanced NBFX closing element')
                self.closed(stack.pop(), stack)
            elif token==3:
                if not stack: raise NativeError('Array outside root element')
                node=self.start(self.byte())
                if node.attrs:
                    raise NativeError('Array-template attributes are outside the supported subset')
                if self.byte()!=1: raise NativeError('NBFX array template is not empty')
                kind=self.byte();count=self.varint()
                self.validate_array(node.name, kind, count, stack)
                types={0x8b:'<i2',0x8d:'<i4',0x8f:'<i8',0x91:'<f4',0x93:'<f8',0xb5:'u1',
                       0xb1:'V16',0x95:'V16',0x97:'V8',0xaf:'V8'}
                if kind not in types or count==0: raise NativeError('Unsupported NBFX array type/count')
                dtype=np.dtype(types[kind]);raw=self.take(count*dtype.itemsize)
                values=np.frombuffer(raw,dtype=dtype)
                if kind==0xb5 and np.any(values>1): raise NativeError('Invalid boolean array value')
                if kind==0xb1: values=np.asarray([str(uuid.UUID(bytes_le=bytes(v))) for v in values])
                stack[-1].children.append(Array(node.name,values))
            elif token==2:
                self.string()  # Comment: data only.
            elif token>=0x80:
                if not stack: raise NativeError('Text outside NBFX root')
                start = self.pos - 1
                stack[-1].content.append(self.text(token))
                self.record_text(stack[-1], start, self.pos)
                if token & 1: self.closed(stack.pop(), stack)
            else:
                raise NativeError(f'Unsupported NBFX token {token:#x} at {self.pos-1}')
        if stack or root is None: raise NativeError('Incomplete NBFX document')
        return root


@dataclass(slots=True)
class IdentityScalar(Node):
    raw_token_hex: str = ''


@dataclass(slots=True)
class IdentitySlots(Node):
    """Validated scalar identity values in their original point-slot order."""
    slot_count: int = 0
    items: list = field(default_factory=list)
    versions: set = field(default_factory=set)
    parsed_nodes: int = 0
    max_item_nodes: int = 0
    max_item_bytes: int = 0
    inactive_slot_count: int = 0


class Points3NBFX(NBFX):
    """Reduce only data/object_ids/item after complete bounded parsing.

    The ordinary tree retains MAX_NODES. Identity work has separate bounds:
    at most two million slots, 128 nodes and 64 KiB per slot, and the inherited
    128-level depth and document limits. No tokens or opaque values are skipped.
    Only one identity subtree is live at a time; geometry remains typed arrays.
    """
    MAX_ID_ITEMS = 2_000_000
    MAX_ID_ITEM_NODES = 128
    MAX_ID_ITEM_BYTES = 64 * 1024
    ID_FIELD_ORDER = ('is_active', 'identifier_first', 'identifier_second',
                      'data_key', 'last_update', 'os_lmd_database',
                      'os_lmd_petrel', 'suspended_key')
    ID_FIELDS = frozenset(ID_FIELD_ORDER)

    def __init__(self, body, names):
        super().__init__(body, names)
        self.identity_slots = None
        self.item_start = 0
        self.item_nodes = 0

    def take(self, count):
        # Check before text/array allocation, including attributes and comments.
        if (self.identity_slots is not None
                and self.pos - self.item_start + count > self.MAX_ID_ITEM_BYTES):
            raise NativeError('Points3 identity-slot byte/string work exceeds bound')
        return super().take(count)

    def count_node(self):
        if self.identity_slots is None:
            return super().count_node()
        self.item_nodes += 1
        self.identity_slots.parsed_nodes += 1
        if self.item_nodes > self.MAX_ID_ITEM_NODES:
            raise NativeError('Points3 identity-slot node work exceeds bound')

    def opened(self, node, stack):
        if len(stack) == 1 and stack[0].name == 'data' and node.name == 'object_ids':
            size = node.attrs.get('Size')
            if type(size) is not int or not 0 <= size <= self.MAX_ID_ITEMS:
                raise NativeError('Points3 identity-slot declared count exceeds bound')
            node = IdentitySlots(node.name, node.attrs)
            self.identity_slots = node
            self.item_start = self.pos
            self.item_nodes = 0
        elif self.identity_slots is not None and stack[-1] is self.identity_slots:
            if node.name != 'item':
                raise NativeError('Points3 object identity-slot profile is not validated')
            if self.identity_slots.slot_count >= self.identity_slots.attrs['Size']:
                raise NativeError('Points3 object identity-slot declared count mismatch')
        elif (self.identity_slots is not None and len(stack) >= 2
              and stack[-2] is self.identity_slots):
            node = IdentityScalar(node.name, node.attrs)
        return node

    def record_text(self, node, start, end):
        if isinstance(node, IdentityScalar):
            node.raw_token_hex += bytes(self.data[start:end]).hex()

    def closed(self, node, stack):
        slots = self.identity_slots
        if slots is None:
            return
        if node is slots:
            if slots.content or slots.children or slots.slot_count != slots.attrs['Size']:
                raise NativeError('Points3 object identity-slot declared count mismatch')
            self.identity_slots = None
        elif stack and stack[-1] is slots:
            version = node.attrs.get('Version')
            names = {child.name for child in node.children}
            # The validated compact inactive shape occurs at Version 2;
            # no identifier values are stored in that shape. Preserve absence.
            inactive = (version in (2, 3) and len(node.children) == 1
                        and names == {'is_active'})
            full = len(node.children) == len(self.ID_FIELDS) and names == self.ID_FIELDS
            if (type(version) is not int or version not in (2, 3) or node.content
                    or set(node.attrs) != {'Version'}
                    or not (full or inactive)):
                raise NativeError('Points3 object identity-slot profile is not validated')
            fields = {}
            raw_tokens = {}
            for child in node.children:
                if (not isinstance(child, IdentityScalar) or child.attrs or child.children
                        or len(child.content) != 1 or not child.raw_token_hex):
                    raise NativeError('Points3 object identity-slot scalar profile is not validated')
                value = child.content[0]
                if child.name == 'is_active':
                    if type(value) is not bool:
                        raise NativeError('Points3 identity is_active must be serialized boolean')
                elif type(value) not in (bool, int, float, str, bytes, dict):
                    raise NativeError('Points3 object identity-slot scalar type is not validated')
                elif type(value) is float and not math.isfinite(value):
                    raise NativeError('Points3 object identity-slot nonfinite scalar is not validated')
                elif type(value) is dict and (set(value) != {'nbfx_type', 'raw_hex'}
                                              or value['nbfx_type'] not in (0x94, 0x96, 0xae)):
                    raise NativeError('Points3 object identity-slot raw scalar is not validated')
                fields[child.name] = value
                raw_tokens[child.name] = child.raw_token_hex
            if inactive and fields['is_active'] is not False:
                raise NativeError('Points3 object identity-slot profile is not validated')
            slots.slot_count += 1
            slots.inactive_slot_count += int(fields['is_active'] is False)
            slots.items.append((version, 'inactive_compact' if inactive else 'full',
                                tuple(fields.get(name) for name in self.ID_FIELD_ORDER),
                                tuple(raw_tokens.get(name) for name in self.ID_FIELD_ORDER)))
            slots.versions.add(version)
            slots.max_item_nodes = max(slots.max_item_nodes, self.item_nodes)
            slots.max_item_bytes = max(slots.max_item_bytes, self.pos - self.item_start)
            slots.children.pop()  # Release the fully parsed and checked opaque item.
            self.item_start = self.pos
            self.item_nodes = 0


def read_points3_documents(payload):
    for names, body in documents(payload):
        yield Points3NBFX(body, names).parse()


def read_documents(payload):
    for names,body in documents(payload):
        yield NBFX(body,names).parse()


def object_document(blob):
    records=list(read_documents(decompress(blob,MAX_DOCUMENT)))
    if len(records)!=1: raise NativeError('Expected one native object document')
    return records[0]


VALGRID_EXPANDED_LIMIT = 256 * 1024 * 1024
VALGRID_MAX_NODES = 10_000_000
VALGRID_VERSION = [0, 0, 0, 0, 0, 2, 0, 1, 1]


class _PackedValGridNBFX(NBFX):
    """Exact packed-array profile, preventing expanded node/list allocations."""
    def count_node(self):
        super().count_node()
        if self.node_count > 256:
            raise NativeError('Packed ValGrid2 metadata node count exceeds bound')

    def text(self, token):
        if token & 0xfe != 0xa4:
            return super().text(token)
        values = []
        while True:
            t = self.byte()
            if t == 0xa6: return values
            if len(values) >= 32 or t & 1 or t == 0xa4:
                raise NativeError('Packed ValGrid2 metadata list exceeds profile')
            values.append(self.text(t))

    def validate_array(self, name, kind, count, stack):
        path = [n.name for n in stack]
        if path == ['data', 'node_size'] and name == 'int' and kind == 0x8d and count == 2:
            return
        if (path == ['data', 'grid'] and name == 'node' and kind == 0x93
                and 0 < count <= 3*VALGRID_MAX_NODES and count % 3 == 0):
            return
        raise NativeError('Unsupported packed ValGrid2 array path/type/count')


def packed_valgrid_document(blob, *, spool_directory=None):
    """Bounded LZ4-to-BXML spool for exact packed ValGrid2 objects.

    Compressed input and each independent LZ4 block retain their 128 MiB cap.
    Aggregate expanded framing/body have a separate explicit 256 MiB cap.
    Only one body is materialized for the zero-copy typed array reader, after
    decompression blocks have been discarded. Temporary output is always closed.
    """
    if len(blob) > MAX_DOCUMENT:
        raise NativeError('Compressed native object exceeds bound')
    stats = dict(profile='ValGrid2_spooled_v1', compressed_bytes=len(blob),
                 expanded_bytes=0, body_bytes=0, blocks=0, largest_expanded_block=0,
                 expanded_byte_limit=VALGRID_EXPANDED_LIMIT, block_byte_limit=MAX_DOCUMENT,
                 node_limit=VALGRID_MAX_NODES, spool_memory_limit=2*1024*1024)
    def chunks():
        cur = Cursor(blob)
        if bytes(cur.take(4)) != b'LZ4\x01': raise NativeError('Unsupported native compression envelope')
        while cur.pos < len(blob):
            stats['blocks'] += 1
            if stats['blocks'] > MAX_LZ4_BLOCKS: raise NativeError('LZ4 block count exceeds bound')
            size=cur.number('I');cur.take(4)
            if not size or size > len(blob)-cur.pos: raise NativeError('LZ4 envelope size mismatch')
            block=_decompress_lz4_block(cur.take(size), min(MAX_DOCUMENT, VALGRID_EXPANDED_LIMIT-stats['expanded_bytes']))
            if not block: raise NativeError('Empty LZ4 expansion outside packed ValGrid2 profile')
            stats['expanded_bytes'] += len(block)
            stats['largest_expanded_block'] = max(stats['largest_expanded_block'], len(block))
            yield memoryview(block)
        if not stats['blocks']: raise NativeError('LZ4 envelope contains no blocks')
    iterator=iter(chunks());pending=memoryview(b'')
    def read(size, *, optional=False):
        nonlocal pending
        result=bytearray()
        while len(result)<size:
            if not len(pending):
                pending=next(iterator, memoryview(b''))
                if not len(pending):
                    if optional and not result:return b''
                    raise NativeError('Truncated streamed BXML frame')
            take=min(size-len(result),len(pending))
            result.extend(pending[:take]);pending=pending[take:]
        return bytes(result)
    def varint():
        raw=bytearray()
        for _ in range(5):
            value=read(1)[0];raw.append(value)
            if value<128:return Cursor(raw).varint()
        raise NativeError('Invalid streamed BXML length')
    if read(5)!=b'BXML\x01':raise NativeError('Unsupported BXML version')
    names=[];unique=set();ended=False
    with tempfile.SpooledTemporaryFile(max_size=stats['spool_memory_limit'], mode='w+b',
                                      dir=spool_directory, prefix='.fv_grid_') as spool:
        while True:
            raw=read(1,optional=True)
            if not raw:break
            if ended:raise NativeError('Packed ValGrid2 requires exactly one document')
            token=raw[0]
            if token==0xa1:
                size=varint()
                if not 0<size<=1024*1024:raise NativeError('Name/string exceeds limit')
                name=read(size).decode('utf-8','strict')
                if name in unique or len(names)>=MAX_NAMES:raise NativeError('Invalid/duplicate BXML dictionary entry')
                names.append(name);unique.add(name)
            elif token==0xa0:
                count=varint()
                if not count or stats['body_bytes']+count>VALGRID_EXPANDED_LIMIT:
                    raise NativeError('Packed ValGrid2 body exceeds byte bound')
                stats['body_bytes']+=count
                while count:
                    size=min(count,64*1024);spool.write(read(size));count-=size
            elif token==0xa2:
                if not stats['body_bytes']:raise NativeError('Empty BXML document boundary')
                ended=True
            else:raise NativeError(f'Unsupported BXML framing token {token:#x}')
        if not stats['body_bytes']:raise NativeError('Expected one native object document')
        spool.seek(0);body=spool.read(stats['body_bytes']+1)
        if len(body)!=stats['body_bytes']:raise NativeError('Streamed native body size mismatch')
    probe=_PackedValGridNBFX(body,names);token=probe.byte()
    if token not in (0x40,0x42):raise NativeError('Unsupported ValGrid2 document root')
    root=probe.start(token)
    version=root.attrs.get('Version')
    if (root.name!='data' or root.attrs.get('Type')!='ValGrid2' or version!=VALGRID_VERSION
            or not isinstance(version,list) or any(type(v) is not int for v in version)
            or root.attrs.get('xmlns')!='http://www.slb.com/Petrel/2011/03/Serialization'):
        raise NativeError('Packed ValGrid2 type/version is outside the validated profile')
    large=stats['body_bytes']>MAX_DOCUMENT
    stats['body_profile']='packed_array_only' if large else 'existing_typed_profile'
    node=(_PackedValGridNBFX if large else NBFX)(body,names).parse()
    return node,stats
