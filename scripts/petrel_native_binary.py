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
import struct
import uuid
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
        self.node_count += 1
        if self.node_count > MAX_NODES: raise NativeError('NBFX node count exceeds bound')
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
                node=self.start(token)
                if stack: stack[-1].children.append(node)
                elif root is not None: raise NativeError('Multiple NBFX document roots')
                else: root=node
                stack.append(node)
                if len(stack)>128: raise NativeError('NBFX nesting exceeds bound')
            elif token==1:
                if not stack: raise NativeError('Unbalanced NBFX closing element')
                stack.pop()
            elif token==3:
                if not stack: raise NativeError('Array outside root element')
                node=self.start(self.byte())
                if node.attrs:
                    raise NativeError('Array-template attributes are outside the supported subset')
                if self.byte()!=1: raise NativeError('NBFX array template is not empty')
                kind=self.byte();count=self.varint()
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
                stack[-1].content.append(self.text(token))
                if token & 1: stack.pop()
            else:
                raise NativeError(f'Unsupported NBFX token {token:#x} at {self.pos-1}')
        if stack or root is None: raise NativeError('Incomplete NBFX document')
        return root


def read_documents(payload):
    for names,body in documents(payload):
        yield NBFX(body,names).parse()


def object_document(blob):
    records=list(read_documents(decompress(blob,MAX_DOCUMENT)))
    if len(records)!=1: raise NativeError('Expected one native object document')
    return records[0]
