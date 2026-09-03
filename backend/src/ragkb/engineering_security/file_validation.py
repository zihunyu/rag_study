"""Size, hash, extension, magic and safe-archive validation."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class FileValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DetectedFile:
    source_format: str
    mime_type: str
    extension: str


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
    revision = "file-validator:g1-v1"

    def __init__(
        self,
        *,
        max_size_bytes: int,
        max_archive_entries: int = 10_000,
        max_archive_ratio: int = 100,
    ) -> None:
        self.max_size_bytes = max_size_bytes
        self.max_archive_entries = max_archive_entries
        self.max_archive_ratio = max_archive_ratio

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
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > self.max_archive_entries:
                    raise FileValidationError(
                        "DOC_ARCHIVE_TOO_MANY_ENTRIES", "archive has too many entries"
                    )
                compressed = sum(max(item.compress_size, 1) for item in entries)
                uncompressed = sum(item.file_size for item in entries)
                if uncompressed > compressed * self.max_archive_ratio:
                    raise FileValidationError(
                        "DOC_ARCHIVE_RATIO_EXCEEDED", "archive expansion ratio is unsafe"
                    )
                names = {item.filename.replace("\\", "/") for item in entries}
                for name in names:
                    pure = PurePosixPath(name)
                    if pure.is_absolute() or ".." in pure.parts:
                        raise FileValidationError(
                            "DOC_ARCHIVE_PATH_TRAVERSAL", "archive entry escapes root"
                        )
        except zipfile.BadZipFile as error:
            raise FileValidationError(
                "DOC_INVALID_ARCHIVE", "OOXML file is not a valid ZIP"
            ) from error
        required = {
            ".docx": "word/document.xml",
            ".pptx": "ppt/presentation.xml",
            ".xlsx": "xl/workbook.xml",
        }[extension]
        if required not in names:
            raise FileValidationError(
                "DOC_MAGIC_MISMATCH", "OOXML package type does not match extension"
            )
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
        prefix = path.read_bytes()[:32]
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
                path.read_text(encoding="utf-8")
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
