# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Sample the launched extraction process tree; never inspect source payloads.

Website: https://saherlabs.dev/
Windows counter definitions: https://learn.microsoft.com/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo
Sampling can miss short-lived children and peaks. I/O counters are logical
process I/O, not measured physical disk or SMB throughput. No privileged access.
"""
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
import time


def windows_tree_sample(root_pid):
    if os.name != 'nt':
        return {'status': 'unavailable', 'reason': 'Windows process counters required'}

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                    ('heap', ctypes.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                    ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD), ('exe', w.WCHAR * 260)]

    class Memory(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('faults', w.DWORD)] + [(n, ctypes.c_size_t) for n in
                    ('peak_working_set', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged',
                     'nonpaged', 'pagefile', 'peak_pagefile', 'private_bytes')]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in
                    ('read_operations', 'write_operations', 'other_operations', 'read_bytes', 'write_bytes', 'other_bytes')]

    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
    k.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
    k.CloseHandle.argtypes = [w.HANDLE]
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    k.GetProcessIoCounters.argtypes = [w.HANDLE, ctypes.POINTER(IO)]
    k.K32GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Memory), w.DWORD]
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = ProcessEntry(); entry.size = ctypes.sizeof(entry)
        more = k.Process32FirstW(handle, ctypes.byref(entry))
        if not more:
            raise ctypes.WinError(ctypes.get_last_error())
        while more:
            parents[entry.pid] = entry.parent
            more = k.Process32NextW(handle, ctypes.byref(entry))
    finally:
        k.CloseHandle(handle)
    selected = {int(root_pid)}
    while True:
        expanded = selected | {pid for pid, parent in parents.items() if parent in selected}
        if expanded == selected:
            break
        selected = expanded
    rows, errors = [], []
    for pid in sorted(selected):
        handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION.
        if not handle:
            errors.append({'pid': pid, 'winerror': ctypes.get_last_error()})
            continue
        try:
            created, ended, kernel, user = (w.FILETIME() for _ in range(4))
            row = {'pid': pid, 'parent_pid': parents.get(pid)}
            ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            if k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)):
                row.update(created_ticks=ticks(created), cpu_seconds=(ticks(kernel) + ticks(user)) / 10_000_000)
            else:
                row['times_error'] = ctypes.get_last_error()
            mem = Memory(); mem.size = ctypes.sizeof(mem)
            if k.K32GetProcessMemoryInfo(handle, ctypes.byref(mem), mem.size):
                row.update(working_set_bytes=mem.working_set, private_bytes=mem.private_bytes,
                           peak_working_set_bytes=mem.peak_working_set, page_faults=mem.faults)
            else:
                row['memory_error'] = ctypes.get_last_error()
            io = IO()
            if k.GetProcessIoCounters(handle, ctypes.byref(io)):
                row.update({name: getattr(io, name) for name, _ in IO._fields_})
            else:
                row['io_error'] = ctypes.get_last_error()
            rows.append(row)
        finally:
            k.CloseHandle(handle)
    return {'status': 'sampled' if rows else 'unavailable', 'processes': rows, 'unavailable': errors}


class ProcessSampler:
    def __init__(self, path, output, interval=10):
        self.path = Path(path)
        self.output = Path(output)
        self.interval = max(1.0, float(interval))
        self.stop = threading.Event()
        self.errors = []
        self.thread = None
        self.samples = 0

    def start(self, pid):
        self.pid = pid
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._loop, daemon=True, name='GeoViewer metrics')
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            try:
                row = windows_tree_sample(self.pid)
                row.update(at_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic() - self.started)
                try:
                    row['output_free_bytes'] = shutil.disk_usage(self.output).free
                except OSError as exc:
                    row['output_disk_error'] = str(exc)
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row, allow_nan=False) + '\n')
                self.samples += 1
            except Exception as exc:
                if len(self.errors) < 20:
                    self.errors.append(type(exc).__name__ + ': ' + str(exc))
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        return {'sample_count': self.samples, 'errors': self.errors,
                'sampling_still_stopping': bool(self.thread and self.thread.is_alive()),
                'interval_seconds': self.interval,
                'scope': 'Sampled Windows extraction process tree; short children/peaks can be missed; logical I/O only'}
