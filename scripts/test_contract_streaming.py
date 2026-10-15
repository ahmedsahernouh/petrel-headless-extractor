# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Independent byte/boundary and interruption checks for catalogue publication."""
import hashlib
import json
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

import geoviewer_contract as contract
import validate_geoviewer_contract as validator
from test_geoviewer_contract import ContractTests


class ContractStreamingTests(TestCase):
    setUp = ContractTests.setUp
    add_row = ContractTests.add_row
    publish = ContractTests.publish

    def normalized(self):
        return contract.build_catalogue(self.package, self.exports, self.index)

    def assert_old_pointer(self, before):
        self.assertEqual((self.exports/contract.POINTER).read_bytes(), before)
        validator.validate_contract(self.exports/contract.POINTER)

    def test_oversized_row_does_not_commit_unreadable_generation(self):
        self.publish(); before=(self.exports/contract.POINTER).read_bytes()
        normalized=self.normalized()
        normalized['tables']['entities'][0]['name']='x'*(validator.MAX_ROW_BYTES+1)
        with patch.object(contract,'build_catalogue',return_value=normalized):
            with self.assertRaisesRegex(ValueError,'row.*byte|byte.*row'):
                self.publish()
        self.assert_old_pointer(before)

    def test_table_byte_limit_preserves_old_pointer(self):
        self.publish();before=(self.exports/contract.POINTER).read_bytes()
        normalized=self.normalized()
        with patch.object(contract,'MAX_TABLE_BYTES',100,create=True), patch.object(contract,'build_catalogue',return_value=normalized):
            with self.assertRaisesRegex(ValueError,'table.*byte|byte.*table'):
                self.publish()
        self.assert_old_pointer(before)

    def test_manifest_byte_limit_preserves_old_pointer(self):
        self.publish();before=(self.exports/contract.POINTER).read_bytes()
        normalized=self.normalized()
        normalized['tables']={name:[] for name in contract.TABLES}
        with patch.object(contract,'MAX_TABLE_BYTES',1024), patch.object(contract,'build_catalogue',return_value=normalized):
            with self.assertRaisesRegex(ValueError,'manifest.*byte'):
                self.publish()
        self.assert_old_pointer(before)

    def test_utf8_newline_and_exact_table_boundaries(self):
        row={'text':'é地'}
        expected=b'{"text":"\xc3\xa9\xe5\x9c\xb0"}\n'
        path=self.exports/'exact.jsonl'
        with patch.object(contract,'MAX_ROW_BYTES',len(expected)), patch.object(contract,'MAX_TABLE_BYTES',2*len(expected)), patch.object(contract,'MAX_TABLE_ROWS',2):
            result=contract._write_table(path,iter([row,row]))
            self.assertEqual(path.read_bytes(),expected*2)
            self.assertEqual(result['sha256'],hashlib.sha256(expected*2).hexdigest())
            self.assertEqual(result['rows'],2)
            with self.assertRaisesRegex(ValueError,'row count'):
                contract._write_table(self.exports/'one-too-many.jsonl',iter([row,row,row]))
        with patch.object(contract,'MAX_ROW_BYTES',len(expected)-1):
            with self.assertRaisesRegex(ValueError,'row byte'):
                contract._write_table(self.exports/'one-byte-too-big.jsonl',iter([row]))
        with patch.object(contract,'MAX_TABLE_BYTES',2*len(expected)-1):
            with self.assertRaisesRegex(ValueError,'table byte'):
                contract._write_table(self.exports/'total-too-big.jsonl',iter([row,row]))

    def test_empty_table_and_exclusive_output(self):
        path=self.exports/'empty.jsonl';result=contract._write_table(path,iter([]))
        self.assertEqual(path.read_bytes(),b'')
        self.assertEqual(result['rows'],0)
        self.assertEqual(result['sha256'],hashlib.sha256(b'').hexdigest())
        with self.assertRaises(FileExistsError):contract._write_table(path,iter([{}]))
        self.assertEqual(path.read_bytes(),b'')

    def test_table_row_count_limit_preserves_old_pointer(self):
        self.publish();before=(self.exports/contract.POINTER).read_bytes()
        with patch.object(contract,'MAX_TABLE_ROWS',1,create=True):
            with self.assertRaisesRegex(ValueError,'row.*count|count.*row'):
                self.publish()
        self.assert_old_pointer(before)

    def test_streamed_bytes_equal_independent_canonical_json(self):
        normalized=self.normalized()
        normalized['tables']['gaps']=[dict(entity_key='a',index=i,text='é 地',values=[1,1.25,None,True]) for i in range(71)]
        with patch.object(contract,'build_catalogue',return_value=normalized):
            pointer=self.publish()
        root=(self.exports/pointer['manifest']).parent
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        for name,rows in normalized['tables'].items():
            expected=(''.join(json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n' for r in rows)).encode('utf-8')
            self.assertEqual((root/f'{name}.jsonl').read_bytes(),expected)
            self.assertEqual(manifest['tables'][name]['sha256'],hashlib.sha256(expected).hexdigest())
            self.assertEqual(manifest['tables'][name]['rows'],len(rows))

    def test_mid_table_io_failure_does_not_replace_pointer(self):
        self.publish();before=(self.exports/contract.POINTER).read_bytes()
        original=Path.open
        class FailingWriter:
            def __init__(self,wrapped):self.wrapped=wrapped;self.calls=0
            def __enter__(self):self.wrapped.__enter__();return self
            def __exit__(self,*args):return self.wrapped.__exit__(*args)
            def write(self,data):
                self.calls+=1
                if self.calls==2:raise OSError('injected partial table write')
                return self.wrapped.write(data)
            def __getattr__(self,name):return getattr(self.wrapped,name)
        def opened(path,*args,**kwargs):
            handle=original(path,*args,**kwargs)
            if path.name=='entities.jsonl' and args and args[0] in ('wb','xb'):return FailingWriter(handle)
            return handle
        with patch.object(Path,'open',opened):
            with self.assertRaisesRegex(OSError,'injected partial table write'):
                self.publish()
        self.assert_old_pointer(before)

    def test_metadata_read_has_explicit_byte_bound(self):
        path=self.package/'oversized.json';path.write_bytes(b'{"x":"'+b'x'*100+b'"}')
        with patch.object(contract,'MAX_TABLE_BYTES',64,create=True):
            with self.assertRaisesRegex(ValueError,'metadata.*byte|byte.*bound'):
                contract._read(path)

    def test_manifest_digest_and_parse_use_one_snapshot(self):
        pointer=self.publish();manifest=(self.exports/pointer['manifest']);opened=[]
        original=Path.open
        def tracked(path,*args,**kwargs):
            if path==manifest:opened.append((args,kwargs))
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',tracked):
            validator.validate_contract(self.exports/contract.POINTER)
        self.assertEqual(len(opened),1,'Digest and JSON parse must use the same bounded byte snapshot')


if __name__=='__main__':main()
