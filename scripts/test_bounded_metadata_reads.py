# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Metadata reads must bound allocation as well as total accepted input bytes."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

import geoviewer_contract as contract
import validate_geoviewer_contract as validator
import geoviewer_legacy_contract as legacy
import geoviewer_receiving_profiles as receiving
from geoviewer_io import read_up_to


class BoundedMetadataReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'receipt.json'
        self.raw = b'\xef\xbb\xbf'+json.dumps({'label': 'é地', 'values': [0, None, -1]}, ensure_ascii=False).encode('utf-8')
        self.path.write_bytes(self.raw)

    def readers(self):
        return {'contract': contract._read, 'validator': validator._json_with_digest,
                'legacy': lambda p: legacy._bounded_json(p, legacy.MAX_RECEIPT_BYTES),
                'receiving': lambda p: receiving._bounded_json(p, receiving.MAX_DECISIONS_BYTES)}

    def test_tiny_receipt_does_not_allocate_maximum_file_allowance(self):
        peaks = {}
        for name, read in self.readers().items():
            tracemalloc.start()
            try:
                read(self.path)
                peaks[name] = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertTrue(all(p < 2*1024*1024 for p in peaks.values()), peaks)

    def test_metadata_io_uses_small_requests(self):
        original = Path.open
        sizes = []
        class Observed:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def read(self, size=-1):
                sizes.append(size)
                return self.stream.read(size)
            def __getattr__(self, name): return getattr(self.stream, name)
        def opened(path, *args, **kwargs):
            return Observed(original(path, *args, **kwargs))
        with patch.object(Path, 'open', opened):
            for read in self.readers().values(): read(self.path)
        self.assertTrue(sizes)
        self.assertTrue(all(0 <= n <= 1024*1024 for n in sizes), sizes)

    def test_exact_byte_bound_and_bom_digest_are_preserved(self):
        for module, read in ((contract, contract._read), (validator, validator._json_with_digest)):
            with self.subTest(reader=module.__name__), patch.object(module, 'MAX_TABLE_BYTES', len(self.raw)):
                value, digest = read(self.path)
                self.assertEqual(value['label'], 'é地')
                self.assertEqual(digest, hashlib.sha256(self.raw).hexdigest())
                self.path.write_bytes(self.raw+b' ')
                try:
                    with self.assertRaisesRegex(ValueError, 'byte bound'):
                        read(self.path)
                finally:
                    self.path.write_bytes(self.raw)

    def test_non_object_and_invalid_utf8_remain_rejected(self):
        for raw in (b'[]', b'{"label":"\xff"}'):
            self.path.write_bytes(raw)
            for name, read in self.readers().items():
                with self.subTest(reader=name, raw=raw), self.assertRaises((ValueError, UnicodeError)):
                    read(self.path)

    def test_missing_optional_and_required_receipts_stay_distinct(self):
        self.path.unlink()
        self.assertEqual(contract._read(self.path), ({}, None))
        with self.assertRaises(FileNotFoundError): validator._json_with_digest(self.path)

    def test_io_error_is_not_a_truncated_success(self):
        class Broken(io.BytesIO):
            def read(self, size=-1): raise OSError('injected read failure')
        for name, read in self.readers().items():
            with self.subTest(reader=name), patch.object(Path, 'open', return_value=Broken(self.raw)):
                with self.assertRaisesRegex(OSError, 'injected read failure'):
                    read(self.path)

    def test_short_reads_continue_across_utf8_boundaries(self):
        class ShortReads(io.BytesIO):
            def read(self, size=-1): return super().read(min(size, 2))
        for name, read in self.readers().items():
            with self.subTest(reader=name), patch.object(Path, 'open', return_value=ShortReads(self.raw)):
                result = read(self.path)
                value = result[0] if isinstance(result, tuple) else result
                self.assertEqual(value['label'], 'é地')
                if isinstance(result, tuple): self.assertEqual(result[1], hashlib.sha256(self.raw).hexdigest())

    def test_growth_past_limit_is_detected_during_read(self):
        raw = b'{"ok":true}'
        class Growing(io.BytesIO):
            def read(self, size=-1):
                block = super().read(min(size, 3))
                if self.tell() == 3:
                    position = self.tell(); self.seek(0, 2); self.write(b' ' * 50); self.seek(position)
                return block
        with patch.object(contract, 'MAX_TABLE_BYTES', len(raw)), patch.object(Path, 'open', return_value=Growing(raw)):
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                contract._read(self.path)

    def test_failure_after_partial_read_never_returns_prefix(self):
        class BrokenAfterPrefix(io.BytesIO):
            def read(self, size=-1):
                if self.tell(): raise OSError('injected late failure')
                return super().read(3)
        with patch.object(Path, 'open', return_value=BrokenAfterPrefix(self.raw)):
            with self.assertRaisesRegex(OSError, 'injected late failure'):
                validator._json_with_digest(self.path)

    def test_helper_returns_exact_prefix_and_leaves_next_byte_unread(self):
        with io.BytesIO(b'abcdef') as stream:
            self.assertEqual(read_up_to(stream, 3), b'abc')
            self.assertEqual(stream.read(), b'def')
        with io.BytesIO(b'x') as stream:
            self.assertEqual(read_up_to(stream, 0), b'')
            self.assertEqual(stream.tell(), 0)

    def test_helper_rejects_invalid_allowance_before_reading(self):
        for limit in (-1, True, 1.5, None):
            with self.subTest(limit=limit), io.BytesIO(self.raw) as stream:
                with self.assertRaises(ValueError): read_up_to(stream, limit)
                self.assertEqual(stream.tell(), 0)

    def test_nonblocking_no_data_is_not_end_of_file(self):
        class NotReady(io.BytesIO):
            def read(self, size=-1): return None
        with NotReady(self.raw) as stream:
            with self.assertRaises(BlockingIOError): read_up_to(stream, 10)


if __name__ == '__main__':
    unittest.main()
