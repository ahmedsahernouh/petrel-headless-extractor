# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded native explicit-grid framing and fail-closed resource controls."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import petrel_native_binary as b
import petrel_native_recovery as r
import test_petrel_native_recovery as f


class ValGridSpoolTests(unittest.TestCase):
    def blob(self, body=None, split=False):
        return f.envelope(f.frame(body or f.surface_doc(kind='ValGrid2'),split=split))

    def test_same_small_grid_exact_arrays_and_masks_across_frame_splits(self):
        blob=self.blob(split=True)
        original=b.object_document(blob)
        node,stats=b.packed_valgrid_document(blob)
        self.assertEqual(stats['body_profile'],'existing_typed_profile')
        before=r.decode_surface(original,'ValGrid2');after=r.decode_surface(node,'ValGrid2')
        for old,new in zip(before[:-1],after[:-1]):np.testing.assert_array_equal(old,new)
        self.assertEqual(before[-1],after[-1])

    def test_compressed_block_bound_unchanged(self):
        # One literal X followed by an overlapping match expanding to 300 Xs.
        compressed=b'\x1fX\x01\x00'+bytes([255,25])
        blob=b'LZ4\x01'+struct.pack('<II',len(compressed),0)+compressed
        with patch.object(b,'MAX_DOCUMENT',128):
            with self.assertRaisesRegex(b.NativeError,'LZ4.*bound'):
                b.packed_valgrid_document(blob)

    def test_expanded_and_compressed_caps_fail_closed(self):
        blob=self.blob()
        with patch.object(b,'MAX_DOCUMENT',len(blob)-1):
            with self.assertRaisesRegex(b.NativeError,'Compressed'):
                b.packed_valgrid_document(blob)
        with patch.object(b,'VALGRID_EXPANDED_LIMIT',32):
            with self.assertRaisesRegex(b.NativeError,'LZ4.*bound'):
                b.packed_valgrid_document(blob)

    def test_invalid_trailing_and_empty_blocks_are_not_ignored(self):
        for extra in (b'\x00',struct.pack('<II',1,0)+b'\x00',struct.pack('<II',2,0)+b'\x00'):
            with self.subTest(extra=extra),self.assertRaises(b.NativeError):
                b.packed_valgrid_document(self.blob()+extra)

    def test_multiple_documents_and_wrong_root_profile_fail(self):
        with self.assertRaisesRegex(b.NativeError,'exactly one'):
            b.packed_valgrid_document(f.envelope(f.frame(f.surface_doc(kind='ValGrid2'),f.surface_doc(kind='ValGrid2'))))
        with self.assertRaisesRegex(b.NativeError,'type/version'):
            b.packed_valgrid_document(self.blob(f.surface_doc()))

    def test_spool_rolls_to_output_and_closes_on_success_and_failure(self):
        real=tempfile.SpooledTemporaryFile
        def tiny(*args,**kw):kw['max_size']=16;return real(*args,**kw)
        with tempfile.TemporaryDirectory() as td,patch.object(b.tempfile,'SpooledTemporaryFile',tiny):
            b.packed_valgrid_document(self.blob(),spool_directory=td)
            self.assertEqual(list(Path(td).iterdir()),[])
            with self.assertRaises(b.NativeError):
                b.packed_valgrid_document(self.blob()+b'broken',spool_directory=td)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_packed_array_limits_and_foreign_array_types(self):
        parser=b._PackedValGridNBFX(b'',[])
        for name,kind,count,stack in [('node',0x93,3*(b.VALGRID_MAX_NODES+1),['data','grid']),
                                     ('node',0xb1,6,['data','grid']),
                                     ('int',0x8d,3,['data','node_size']),
                                     ('node',0x93,6,['data','other'])]:
            with self.subTest(kind=kind,count=count),self.assertRaisesRegex(b.NativeError,'array path/type/count'):
                parser.validate_array(name,kind,count,[b.Node(x) for x in stack])

    def test_large_profile_metadata_lists_and_nodes_are_bounded(self):
        with self.assertRaisesRegex(b.NativeError,'list exceeds'):
            b._PackedValGridNBFX(f.text([0]*33)[1:],[]).text(0xa4)
        parser=b._PackedValGridNBFX(b'',[])
        for _ in range(256):parser.count_node()
        with self.assertRaisesRegex(b.NativeError,'metadata node'):
            parser.count_node()

    def test_dimensions_and_masks_still_enforced_after_spooling(self):
        for doc in (f.surface_doc(kind='ValGrid2',mask=b'\xff\xff'),
                    f.surface_doc(kind='ValGrid2',connections=True)):
            node,_=b.packed_valgrid_document(self.blob(doc))
            with self.assertRaises(b.NativeError):r.decode_surface(node,'ValGrid2')

    def test_no_global_native_bound_change(self):
        self.assertEqual(b.MAX_DOCUMENT,128*1024*1024)
        self.assertEqual(b.VALGRID_MAX_NODES,r.MAX_SURFACE_NODES)
        self.assertEqual(b.VALGRID_EXPANDED_LIMIT,256*1024*1024)


if __name__=='__main__':unittest.main()
