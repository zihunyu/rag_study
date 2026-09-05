"""OS resource limits for the parser child, never applied to the Worker."""

from __future__ import annotations

import ctypes
import importlib
import os
from typing import Any

_job_handle: Any = None


def limit_native_process(cpu_seconds: float, memory_bytes: int = 1024**3) -> None:
    if os.name != "nt":
        resource: Any = importlib.import_module("resource")
        seconds = max(1, int(cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        return
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_ws", ctypes.c_size_t),
            ("max_ws", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    limits = Extended()
    limits.basic.flags = 0x100 | 0x8 | 0x2 | 0x2000
    limits.basic.active_processes = 1
    limits.basic.process_time = int(cpu_seconds * 10_000_000)
    limits.process_memory = memory_bytes
    global _job_handle
    _job_handle = kernel.CreateJobObjectW(None, None)
    if (
        not _job_handle
        or not kernel.SetInformationJobObject(
            _job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        or not kernel.AssignProcessToJobObject(_job_handle, kernel.GetCurrentProcess())
    ):
        raise OSError("NATIVE_PROCESS_RESOURCE_LIMIT_FAILED")
