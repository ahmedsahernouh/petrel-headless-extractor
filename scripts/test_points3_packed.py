# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Byte-bounded point recovery and lazy delivery, including rejection controls."""
import csv
import dataclasses
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import export_petrel_native_spatial_zero_gui as s
import petrel_native_binary as b
import test_points3_identity_stream as f


def payload(values, count=None, packed=True, attrs=False):
    count = len(values)//3 if count is None else count
    children = ([f.array('double', values, '<f8')] if packed else
                [f.element('double', str(v)) for v in values])
    return f.frame(f.element('data', attrs={'Type':'Points3','Version':[1,2,0,1,1],
        'xmlns':'http://www.slb.com/Petrel/2011/03/Serialization'}, children=[
        f.element('user_data', attrs={'Size':0}),
        f.element('vertices', attrs={'Size':count},children=children),
        f.element('has_attr',attrs),f.element('has_object_ids',False)] +
        ([f.element('attributes')] if attrs else [])))


class PackedPointTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(s,'POINTS3_COMPACT_THRESHOLD',2).start()
        patch.object(s,'POINTS3_LIST_LIMIT',2).start()
        patch.object(s,'_csv_write_event').start()

    def test_large_path_exact_values_bounds_and_missing_bits(self):
        values=[-0.,2.,3.,4.,5.,6.,s.NATIVE_FLOAT_MAX_SENTINEL,8.,float('nan'),10.,11.,12.]
        missing=bytearray(); points,meta=s.decode_points3(payload(values),missing_slot_bytes=missing)
        self.assertIsInstance(points,s.PackedPoints3)
        self.assertEqual(list(points),[(-0.,2.,3.),(4.,5.,6.),None,(10.,11.,12.)])
        self.assertEqual(struct.pack('<d',points[0][0]),struct.pack('<d',-0.))
        self.assertEqual(meta['missing_vertex_slots'],1)
        self.assertEqual(missing[:8],struct.pack('<II',0,2))
        self.assertEqual(missing[8:],struct.pack('<3d',*values[6:9]))
        self.assertEqual(s.bbox(points),dict(min_x=-0.,max_x=10.,min_y=2.,max_y=11.,min_z=3.,max_z=12.))

    def test_lazy_csv_batches_preserve_original_slot_and_multi_object_order(self):
        p,_=s.decode_points3(payload([1.,2.,3.,s.NATIVE_FLOAT_MAX_SENTINEL,0.,0.,7.,8.,9.]))
        obj=s.NativeObject(2,'','id','label',1,'Points3','')
        rows=s.PointVertexRows();rows.add(obj,'first',p)
        rows.add(dataclasses.replace(obj,object_id='other'),'second',[(10.,11.,12.)])
        self.assertEqual(len(rows),3)
        self.assertEqual([r['point_index'] for r in rows[:2]],[0,2])
        self.assertEqual(rows[-1]['object_id'],'other')
        with tempfile.TemporaryDirectory() as td, patch.object(s,'CSV_WRITE_BATCH_ROWS',1):
            path=Path(td)/'points.csv';self.assertTrue(s.write_csv_if_rows(path,rows,('object_id',)))
            with path.open(encoding='utf-8-sig',newline='') as stream: actual=list(csv.DictReader(stream))
        self.assertEqual([x['point_index'] for x in actual],['0','2','0'])
        self.assertEqual([x['x'] for x in actual],['1.0','7.0','10.0'])

    def test_invalid_finite_nonfinite_and_wrong_count_fail(self):
        for invalid in (float('nan'),float('inf'),-float('inf'),s.MAX_ABS_COORDINATE*2):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(s.DecodeError,'invalid XYZ'):
                s.decode_points3(payload([1.,2.,3.]*2+[invalid,2.,3.]))
        with self.assertRaisesRegex(s.DecodeError,'count mismatch'):
            s.decode_points3(payload([1.,2.,3.]*3,count=4))

    def test_large_scalar_or_attribute_layout_is_not_admitted(self):
        with self.assertRaisesRegex(s.DecodeError,'packed binary64'):
            s.decode_points3(payload([1.,2.,3.]*3,packed=False))
        with self.assertRaisesRegex(s.DecodeError,'non-packed layout'):
            s.decode_points3(payload([1.,2.,3.]*3,attrs=True))

    def test_byte_bound_checked_before_parsing_and_declared_overflow(self):
        with patch.object(b,'MAX_DOCUMENT',16), patch.object(b,'read_points3_documents',side_effect=AssertionError('must not parse')):
            with self.assertRaisesRegex(s.DecodeError,'payload exceeds'):
                s.decode_points3(b'x'*17)
        with self.assertRaisesRegex(s.DecodeError,'declared XYZ bytes'):
            s.decode_points3(payload([1.,2.,3.],count=b.MAX_DOCUMENT//24+1))

    def test_aggregate_budget_refusal_is_atomic(self):
        p,_=s.decode_points3(payload([1.,2.,3.]*3))
        obj=s.NativeObject(1,'','one','one',1,'Points3','');rows=s.PointVertexRows()
        charge=p.storage_bytes+len(p)*4
        with patch.object(s,'POINTS3_RETAINED_BYTE_BUDGET',charge):
            rows.add(obj,'one',p)
            with self.assertRaisesRegex(s.DecodeError,'retained geometry'):
                rows.add(obj,'two',p)
        self.assertEqual(len(rows),3);self.assertEqual(len(rows.blocks),1)

    def test_all_missing_and_empty_sequence(self):
        p,m=s.decode_points3(payload([s.NATIVE_FLOAT_MAX_SENTINEL,0.,0.]*3))
        self.assertIsNone(s.bbox(p));self.assertEqual(m['missing_vertex_slots'],3)
        rows=s.PointVertexRows();rows.add(s.NativeObject(1,'','id','',1,'Points3',''),'name',p)
        self.assertEqual(len(rows),0)
        with self.assertRaises(IndexError): _=p[3]
        with self.assertRaises(IndexError): _=rows[0]

    def test_zero_declared_count_cannot_hide_nonempty_array(self):
        with self.assertRaisesRegex(s.DecodeError,'Empty Points3'):
            s.decode_points3(payload([1.,2.,3.],count=0))


if __name__=='__main__':unittest.main()
