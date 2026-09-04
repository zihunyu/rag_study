"""Size, hash, extension, magic and safe-archive validation."""

from __future__ import annotations

import codecs
import hashlib
import multiprocessing
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path, PurePosixPath
from typing import Any


class FileValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DetectedFile:
    source_format: str
    mime_type: str
    extension: str


@dataclass(frozen=True)
class _ArchiveLimits:
    max_entries: int
    max_ratio: int
    max_uncompressed_bytes: int
    max_entry_uncompressed_bytes: int
    max_nesting_depth: int
    timeout_seconds: float


@dataclass
class _ArchiveBudget:
    entries: int = 0
    uncompressed_bytes: int = 0


_NESTED_ARCHIVE_SUFFIXES = frozenset(
    {".zip", ".docx", ".xlsx", ".pptx", ".jar", ".apk", ".odt", ".ods", ".odp"}
)


def _check_archive_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise FileValidationError("DOC_ARCHIVE_TIMEOUT", "archive validation timed out")


def _scan_zip(
    source: Any,
    *,
    depth: int,
    limits: _ArchiveLimits,
    budget: _ArchiveBudget,
    deadline: float,
) -> set[str]:
    _check_archive_deadline(deadline)
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            budget.entries += len(entries)
            if budget.entries > limits.max_entries:
                raise FileValidationError(
                    "DOC_ARCHIVE_TOO_MANY_ENTRIES", "archive has too many entries"
                )
            compressed = sum(max(item.compress_size, 1) for item in entries)
            uncompressed = sum(item.file_size for item in entries)
            if uncompressed > compressed * limits.max_ratio:
                raise FileValidationError(
                    "DOC_ARCHIVE_RATIO_EXCEEDED", "archive expansion ratio is unsafe"
                )
            if uncompressed > limits.max_uncompressed_bytes - budget.uncompressed_bytes:
                raise FileValidationError(
                    "DOC_ARCHIVE_UNCOMPRESSED_LIMIT",
                    "archive total uncompressed size exceeds the absolute limit",
                )
            budget.uncompressed_bytes += uncompressed
            names: set[str] = set()
            for item in entries:
                _check_archive_deadline(deadline)
                name = item.filename.replace("\\", "/")
                names.add(name)
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise FileValidationError(
                        "DOC_ARCHIVE_PATH_TRAVERSAL", "archive entry escapes root"
                    )
                if item.flag_bits & 0x1:
                    raise FileValidationError(
                        "DOC_ARCHIVE_ENCRYPTED_ENTRY", "encrypted archive entries are forbidden"
                    )
                if item.file_size > limits.max_entry_uncompressed_bytes:
                    raise FileValidationError(
                        "DOC_ARCHIVE_ENTRY_UNCOMPRESSED_LIMIT",
                        "archive entry exceeds the absolute uncompressed limit",
                    )
                if item.is_dir() or item.file_size == 0:
                    continue
                with archive.open(item) as entry:
                    prefix = entry.read(4)
                    nested = prefix.startswith(b"PK\x03\x04") or pure.suffix.casefold() in (
                        _NESTED_ARCHIVE_SUFFIXES
                    )
                    if not nested:
                        continue
                    if depth >= limits.max_nesting_depth:
                        raise FileValidationError(
                            "DOC_ARCHIVE_NESTING_LIMIT", "archive nesting depth exceeded"
                        )
                    with tempfile.SpooledTemporaryFile(
                        max_size=1024 * 1024, mode="w+b"
                    ) as nested_file:
                        nested_file.write(prefix)
                        copied = len(prefix)
                        while chunk := entry.read(1024 * 1024):
                            _check_archive_deadline(deadline)
                            copied += len(chunk)
                            if copied > limits.max_entry_uncompressed_bytes:
                                raise FileValidationError(
                                    "DOC_ARCHIVE_ENTRY_UNCOMPRESSED_LIMIT",
                                    "nested archive entry exceeds the absolute limit",
                                )
                            nested_file.write(chunk)
                        nested_file.seek(0)
                        _scan_zip(
                            nested_file,
                            depth=depth + 1,
                            limits=limits,
                            budget=budget,
                            deadline=deadline,
                        )
            return names
    except zipfile.BadZipFile as error:
        raise FileValidationError("DOC_INVALID_ARCHIVE", "OOXML file is not a valid ZIP") from error


def _archive_validation_worker(
    path: str,
    extension: str,
    limits: _ArchiveLimits,
    sender: Connection,
) -> None:
    connection = sender
    try:
        try:
            import resource

            cpu_seconds = max(1, int(limits.timeout_seconds) + 1)
            setrlimit = getattr(resource, "setrlimit", None)
            rlimit_cpu = getattr(resource, "RLIMIT_CPU", None)
            if callable(setrlimit) and rlimit_cpu is not None:
                setrlimit(rlimit_cpu, (cpu_seconds, cpu_seconds))
        except (ImportError, AttributeError, OSError, ValueError):
            pass
        names = _scan_zip(
            path,
            depth=0,
            limits=limits,
            budget=_ArchiveBudget(),
            deadline=time.monotonic() + limits.timeout_seconds,
        )
        required = {
            ".docx": "word/document.xml",
            ".pptx": "ppt/presentation.xml",
            ".xlsx": "xl/workbook.xml",
        }[extension]
        if required not in names:
            raise FileValidationError(
                "DOC_MAGIC_MISMATCH", "OOXML package type does not match extension"
            )
        connection.send(("ok", ""))
    except FileValidationError as error:
        connection.send(("error", error.code, str(error)))
    except BaseException:
        connection.send(("error", "DOC_ARCHIVE_VALIDATION_FAILED", "archive validation failed"))
    finally:
        connection.close()


FORMAT_BY_EXTENSION = {
    ".pdf": ("pdf", "application/pdf"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".gif": ("image", "image/gif"),
    ".tif": ("image", "image/tiff"),
    ".tiff": ("image", "image/tiff"),
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".doc": ("doc", "application/msword"),
    ".pptx": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".ppt": ("ppt", "application/vnd.ms-powerpoint"),
    ".xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".xls": ("xls", "application/vnd.ms-excel"),
    ".md": ("markdown", "text/markdown"),
    ".txt": ("txt", "text/plain"),
    ".html": ("html", "text/html"),
    ".htm": ("html", "text/html"),
    ".csv": ("csv", "text/csv"),
    ".wav": ("audio", "audio/wav"),
    ".mp3": ("audio", "audio/mpeg"),
    ".m4a": ("audio", "audio/mp4"),
}


class UploadFileValidator:
    revision = "file-validator:archive-resource-limits:g1-v2"

    def __init__(
        self,
        *,
        max_size_bytes: int,
        max_archive_entries: int = 10_000,
        max_archive_ratio: int = 100,
        max_archive_uncompressed_bytes: int = 1024 * 1024 * 1024,
        max_archive_entry_uncompressed_bytes: int = 256 * 1024 * 1024,
        max_archive_nesting_depth: int = 1,
        archive_validation_timeout_seconds: float = 10.0,
    ) -> None:
        if (
            max_size_bytes < 1
            or max_archive_entries < 1
            or max_archive_ratio < 1
            or max_archive_uncompressed_bytes < 1
            or max_archive_entry_uncompressed_bytes < 1
            or max_archive_entry_uncompressed_bytes > max_archive_uncompressed_bytes
            or max_archive_nesting_depth < 0
            or archive_validation_timeout_seconds <= 0
        ):
            raise ValueError("archive validation limits are invalid")
        self.max_size_bytes = max_size_bytes
        self.max_archive_entries = max_archive_entries
        self.max_archive_ratio = max_archive_ratio
        self.max_archive_uncompressed_bytes = max_archive_uncompressed_bytes
        self.max_archive_entry_uncompressed_bytes = max_archive_entry_uncompressed_bytes
        self.max_archive_nesting_depth = max_archive_nesting_depth
        self.archive_validation_timeout_seconds = archive_validation_timeout_seconds

    @staticmethod
    def validate_filename(filename: str) -> str:
        if not filename or filename in {".", ".."}:
            raise FileValidationError("DOC_INVALID_FILENAME", "filename is required")
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise FileValidationError("DOC_PATH_TRAVERSAL", "filename must not contain a path")
        if not re.fullmatch(r"[^\x00-\x1f]+", filename):
            raise FileValidationError(
                "DOC_INVALID_FILENAME", "filename contains control characters"
            )
        return filename

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_archive(self, path: Path, extension: str) -> DetectedFile:
        limits = _ArchiveLimits(
            self.max_archive_entries,
            self.max_archive_ratio,
            self.max_archive_uncompressed_bytes,
            self.max_archive_entry_uncompressed_bytes,
            self.max_archive_nesting_depth,
            self.archive_validation_timeout_seconds,
        )
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_archive_validation_worker,
            args=(str(path), extension, limits, sender),
            daemon=True,
        )
        try:
            process.start()
            sender.close()
            process.join(self.archive_validation_timeout_seconds)
            if process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join(1)
                raise FileValidationError(
                    "DOC_ARCHIVE_TIMEOUT", "archive validation exceeded wall-clock limit"
                )
            if not receiver.poll():
                raise FileValidationError(
                    "DOC_ARCHIVE_VALIDATION_FAILED", "archive validator exited without a result"
                )
            result = receiver.recv()
            if not isinstance(result, tuple) or not result or result[0] != "ok":
                code = (
                    str(result[1])
                    if isinstance(result, tuple) and len(result) > 1
                    else "DOC_ARCHIVE_VALIDATION_FAILED"
                )
                message = (
                    str(result[2])
                    if isinstance(result, tuple) and len(result) > 2
                    else "archive validation failed"
                )
                raise FileValidationError(code, message)
        finally:
            sender.close()
            receiver.close()
            if process.pid is not None and not process.is_alive():
                process.close()
        source_format, mime = FORMAT_BY_EXTENSION[extension]
        return DetectedFile(source_format, mime, extension)

    def inspect(
        self,
        path: Path,
        *,
        filename: str,
        expected_size: int,
        expected_sha256: str,
        declared_mime: str,
    ) -> DetectedFile:
        safe_name = self.validate_filename(filename)
        extension = Path(safe_name).suffix.casefold()
        if extension not in FORMAT_BY_EXTENSION:
            raise FileValidationError(
                "DOC_FORMAT_UNSUPPORTED", "file extension is not in the G1 allowlist"
            )
        actual_size = path.stat().st_size
        if actual_size > self.max_size_bytes or expected_size > self.max_size_bytes:
            raise FileValidationError("DOC_SIZE_LIMIT", "file exceeds configured size limit")
        if actual_size != expected_size:
            raise FileValidationError(
                "DOC_SIZE_MISMATCH", "uploaded size does not match the session"
            )
        actual_hash = self.sha256(path)
        if (
            not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256)
            or actual_hash != expected_sha256.casefold()
        ):
            raise FileValidationError(
                "DOC_HASH_MISMATCH", "uploaded hash does not match the session"
            )
        with path.open("rb") as handle:
            prefix = handle.read(32)
        if extension == ".pdf" and not prefix.startswith(b"%PDF-"):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "PDF magic is missing")
        if extension in {".docx", ".pptx", ".xlsx"}:
            detected = self._validate_archive(path, extension)
        elif extension in {".xls", ".doc", ".ppt"}:
            if not prefix.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
                raise FileValidationError("DOC_MAGIC_MISMATCH", "OLE magic is missing")
            source_format, mime = FORMAT_BY_EXTENSION[extension]
            detected = DetectedFile(source_format, mime, extension)
        elif extension in {".png"} and not prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "PNG magic is missing")
        elif extension in {".jpg", ".jpeg"} and not prefix.startswith(b"\xff\xd8\xff"):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "JPEG magic is missing")
        elif extension == ".gif" and not prefix.startswith((b"GIF87a", b"GIF89a")):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "GIF magic is missing")
        elif extension in {".tif", ".tiff"} and not prefix.startswith((b"II*\x00", b"MM\x00*")):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "TIFF magic is missing")
        elif extension == ".wav" and not (prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE"):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "WAV magic is missing")
        elif extension == ".mp3" and not (
            prefix.startswith(b"ID3")
            or (len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0)
        ):
            raise FileValidationError("DOC_MAGIC_MISMATCH", "MP3 magic is missing")
        elif extension == ".m4a" and prefix[4:8] != b"ftyp":
            raise FileValidationError("DOC_MAGIC_MISMATCH", "M4A ftyp box is missing")
        elif extension in {".md", ".txt", ".html", ".htm", ".csv"}:
            try:
                decoder = codecs.getincrementaldecoder("utf-8")("strict")
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        decoder.decode(chunk)
                    decoder.decode(b"", final=True)
            except UnicodeDecodeError as error:
                raise FileValidationError(
                    "DOC_TEXT_ENCODING", "text input must be UTF-8"
                ) from error
            source_format, mime = FORMAT_BY_EXTENSION[extension]
            detected = DetectedFile(source_format, mime, extension)
        else:
            source_format, mime = FORMAT_BY_EXTENSION[extension]
            detected = DetectedFile(source_format, mime, extension)
        normalized_declared = declared_mime.split(";", 1)[0].strip().casefold()
        allowed_mimes = {detected.mime_type.casefold(), "application/octet-stream"}
        if detected.mime_type.startswith("text/"):
            allowed_mimes.add("text/plain")
        if normalized_declared not in allowed_mimes:
            raise FileValidationError(
                "DOC_MIME_MISMATCH", "declared MIME does not match detected type"
            )
        return detected
