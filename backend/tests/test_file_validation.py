from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import openpyxl
import pytest
from ragkb.engineering_security.file_validation import FileValidationError, UploadFileValidator


def _inspect(validator: UploadFileValidator, path: Path, filename: str, mime: str):
    content = path.read_bytes()
    return validator.inspect(
        path,
        filename=filename,
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime=mime,
    )


def test_detects_valid_xlsx_package(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["a", "b"])
    workbook.save(path)

    detected = _inspect(
        UploadFileValidator(max_size_bytes=10_000_000),
        path,
        "book.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert detected.source_format == "xlsx"


def test_rejects_ooxml_extension_magic_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    workbook = openpyxl.Workbook()
    workbook.save(path)

    with pytest.raises(FileValidationError) as error:
        _inspect(
            UploadFileValidator(max_size_bytes=10_000_000),
            path,
            "renamed.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert error.value.code == "DOC_MAGIC_MISMATCH"


def test_rejects_archive_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("../escape", "bad")

    with pytest.raises(FileValidationError) as error:
        _inspect(
            UploadFileValidator(max_size_bytes=10_000_000),
            path,
            "bad.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert error.value.code == "DOC_ARCHIVE_PATH_TRAVERSAL"


def test_rejects_size_hash_and_mime_mismatches(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    content = b"%PDF-1.7\n"
    path.write_bytes(content)
    validator = UploadFileValidator(max_size_bytes=1024)

    with pytest.raises(FileValidationError) as hash_error:
        validator.inspect(
            path,
            filename="sample.pdf",
            expected_size=len(content),
            expected_sha256="0" * 64,
            declared_mime="application/pdf",
        )
    assert hash_error.value.code == "DOC_HASH_MISMATCH"
    with pytest.raises(FileValidationError) as mime_error:
        _inspect(validator, path, "sample.pdf", "text/plain")
    assert mime_error.value.code == "DOC_MIME_MISMATCH"


def _minimal_docx(path: Path, extra: dict[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", b"<document/>")
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


def _inspect_docx(path: Path, validator: UploadFileValidator) -> None:
    content = path.read_bytes()
    validator.inspect(
        path,
        filename="sample.docx",
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )


def test_archive_rejects_absolute_total_and_single_entry_expansion_limits(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.docx"
    _minimal_docx(path, {"word/large.bin": b"x" * 64})
    total_limited = UploadFileValidator(
        max_size_bytes=10_000,
        max_archive_uncompressed_bytes=50,
        max_archive_entry_uncompressed_bytes=50,
    )
    with pytest.raises(FileValidationError) as total_error:
        _inspect_docx(path, total_limited)
    assert total_error.value.code == "DOC_ARCHIVE_UNCOMPRESSED_LIMIT"

    entry_limited = UploadFileValidator(
        max_size_bytes=10_000,
        max_archive_uncompressed_bytes=1_000,
        max_archive_entry_uncompressed_bytes=32,
    )
    with pytest.raises(FileValidationError) as entry_error:
        _inspect_docx(path, entry_limited)
    assert entry_error.value.code == "DOC_ARCHIVE_ENTRY_UNCOMPRESSED_LIMIT"


def test_archive_rejects_nested_zip_beyond_depth_limit(tmp_path: Path) -> None:
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("payload.txt", "nested")
    path = tmp_path / "nested.docx"
    _minimal_docx(path, {"word/embeddings/nested.zip": nested.getvalue()})
    validator = UploadFileValidator(
        max_size_bytes=10_000,
        max_archive_uncompressed_bytes=10_000,
        max_archive_entry_uncompressed_bytes=5_000,
        max_archive_nesting_depth=0,
    )

    with pytest.raises(FileValidationError) as error:
        _inspect_docx(path, validator)

    assert error.value.code == "DOC_ARCHIVE_NESTING_LIMIT"


def test_archive_validation_wall_clock_timeout_terminates_child(tmp_path: Path) -> None:
    path = tmp_path / "timeout.docx"
    _minimal_docx(path)
    validator = UploadFileValidator(
        max_size_bytes=10_000,
        archive_validation_timeout_seconds=0.000_001,
    )

    with pytest.raises(FileValidationError) as error:
        _inspect_docx(path, validator)

    assert error.value.code == "DOC_ARCHIVE_TIMEOUT"
