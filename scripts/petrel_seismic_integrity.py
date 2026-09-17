# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Explicit seismic integrity levels; website: https://saherlabs.dev/.

Fast checks record filesystem identity and metadata, never a fabricated SHA-256.
On Windows the read handle also denies concurrent write/delete access.
"""
from contextlib import contextmanager
import os
from pathlib import Path

SEISMIC_SUFFIXES = {'.zgy', '.sgy', '.segy'}


def file_state(path):
    value = Path(path).stat()
    return dict(size_bytes=value.st_size, mtime_ns=value.st_mtime_ns,
                ctime_ns=value.st_ctime_ns, file_id=value.st_ino)


@contextmanager
def readonly_source(path):
    handle = None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(Path(path).resolve()), 0x80000000, 1, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), 'Cannot obtain a read-only source lease; close writers', str(path))
    try:
        yield
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
