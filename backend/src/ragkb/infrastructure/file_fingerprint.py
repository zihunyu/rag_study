"""Cheap change detection; unsupported filesystems always fall back to content hashing."""

import ctypes
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=32)
def _windows_volume_supported(drive: str) -> bool:
    if not drive or drive.startswith("\\\\"):
        return False
    buffer = ctypes.create_unicode_buffer(32)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ok = kernel.GetVolumeInformationW(
        ctypes.c_wchar_p(drive + "\\"), None, 0, None, None, None, buffer, len(buffer)
    )
    return bool(ok and buffer.value.upper() in {"NTFS", "REFS"})


def fingerprint(path: Path) -> list[int] | None:
    before = path.stat()
    change = before.st_ctime_ns
    if os.name == "nt":
        import msvcrt

        if not _windows_volume_supported(path.drive):
            return None

        class BasicInfo(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_int64) for name in ("created", "accessed", "written", "changed")
            ] + [("attributes", ctypes.c_uint32)]

        info = BasicInfo()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        with path.open("rb") as file:
            ok = kernel.GetFileInformationByHandleEx(
                ctypes.c_void_p(msvcrt.get_osfhandle(file.fileno())),
                0,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
        if not ok or info.changed <= 0:
            return None
        change = info.changed
    after = path.stat()

    def values(s: os.stat_result) -> tuple[int, int, int, int]:
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns

    if values(before) != values(after):
        return None
    return [*values(after), change]
