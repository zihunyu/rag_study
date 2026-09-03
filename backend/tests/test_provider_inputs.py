from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from docx import Document
from PIL import Image, ImageChops
from pypdf import PdfWriter
from ragkb.contracts.provider_execution import OwnedProcessResult
from ragkb.infrastructure.provider_inputs import (
    LibreOfficeDocxPdfDeriver,
    SingleFrameTiffPngDeriver,
    SubprocessOwnedProcessRunner,
)


def _single_tiff(path: Path, *, mode: str = "L") -> None:
    image = Image.new(mode, (7, 5), color=37)
    image.save(path, format="TIFF", compression="raw")


class _FailingDeriver(SingleFrameTiffPngDeriver):
    def __init__(self, controlled_root: Path) -> None:
        super().__init__(controlled_root)
        self.write_count = 0

    def _write(self, path: Path, payload: bytes) -> None:
        self.write_count += 1
        if self.write_count == 2:
            raise OSError("injected manifest write failure")
        super()._write(path, payload)


def test_single_frame_tiff_derives_deterministic_lossless_anonymous_png(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private-original-name.tiff"
    _single_tiff(source)
    source_before = source.read_bytes()
    source_hash = hashlib.sha256(source_before).hexdigest()
    deriver = SingleFrameTiffPngDeriver(tmp_path / "controlled")

    first = deriver.derive(source, "anonymous-sample", source_hash)
    second = deriver.derive(source, "anonymous-sample", source_hash)

    assert first["derived_sha256"] == second["derived_sha256"]
    assert first["derived_path"] == second["derived_path"]
    assert first["converter_revision"] == "single-frame-tiff-to-png:v1"
    assert first["width"] == 7 and first["height"] == 5
    assert first["mode"] == "L" and first["frame_count"] == 1
    assert source.read_bytes() == source_before
    assert "private-original-name" not in str(first["derived_path"])
    assert str(first["derived_path"]).endswith("input.png")
    with Image.open(source) as original, Image.open(first["derived_path"]) as derived:
        assert original.mode == derived.mode == "L"
        assert original.size == derived.size
        assert ImageChops.difference(original, derived).getbbox() is None


def test_tiff_derivation_failure_is_atomic_and_rejects_multiframe_or_non_tiff(
    tmp_path: Path,
) -> None:
    source = tmp_path / "single.tif"
    _single_tiff(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    failing = _FailingDeriver(tmp_path / "atomic")
    with pytest.raises(OSError, match="manifest write failure"):
        failing.derive(source, "anonymous", source_hash)
    assert failing.root.is_dir()
    assert list(failing.root.iterdir()) == []

    multi = tmp_path / "multi.tiff"
    first = Image.new("L", (2, 2), color=1)
    second = Image.new("L", (2, 2), color=2)
    first.save(multi, format="TIFF", save_all=True, append_images=[second])
    with pytest.raises(ValueError, match="MULTIFRAME"):
        SingleFrameTiffPngDeriver(tmp_path / "multi-root").derive(
            multi, "anonymous", hashlib.sha256(multi.read_bytes()).hexdigest()
        )

    not_tiff = tmp_path / "not-tiff.bin"
    not_tiff.write_bytes(b"not a TIFF")
    with pytest.raises(ValueError, match="NOT_TIFF"):
        SingleFrameTiffPngDeriver(tmp_path / "bad-root").derive(
            not_tiff,
            "anonymous",
            hashlib.sha256(not_tiff.read_bytes()).hexdigest(),
        )


class _FakeLibreOfficeRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.commands: list[list[str]] = []

    def run(self, command, *, cwd: Path, timeout_seconds: float) -> OwnedProcessResult:
        del cwd, timeout_seconds
        self.commands.append(list(command))
        if self.fail:
            return OwnedProcessResult(1, b"safe stdout", b"safe stderr")
        output = Path(command[command.index("--outdir") + 1]) / "input.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        with output.open("wb") as handle:
            writer.write(handle)
        return OwnedProcessResult(0, b"safe stdout", b"")


class _AtomicFailingDocxDeriver(LibreOfficeDocxPdfDeriver):
    def _write(self, path: Path, payload: bytes) -> None:
        if path.name == "manifest.json":
            raise OSError("injected manifest failure")
        super()._write(path, payload)


def _docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("synthetic document")
    document.save(path)


def test_libreoffice_docx_pdf_is_anonymous_atomic_and_source_preserving(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "soffice.com"
    launcher.write_bytes(b"fake executable")
    source = tmp_path / "private-original-name.docx"
    _docx(source)
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    runner = _FakeLibreOfficeRunner()
    deriver = LibreOfficeDocxPdfDeriver(
        tmp_path / "artifacts",
        tmp_path / "temp",
        launcher,
        "26.8.0.3",
        process_runner=runner,
    )
    result = deriver.derive(source, "anonymous-docx", source_hash)

    assert result["page_count"] == 2
    assert result["converter_revision"] == "libreoffice-docx-to-pdf:v1"
    assert result["libreoffice_version"] == "26.8.0.3"
    assert (
        result["derived_sha256"] == hashlib.sha256(result["derived_path"].read_bytes()).hexdigest()
    )
    assert result["derived_path"].read_bytes().startswith(b"%PDF-")
    assert source.read_bytes() == source_bytes
    assert "private-original-name" not in str(result["derived_path"])
    command = runner.commands[0]
    assert command[-1].endswith("input.docx")
    assert "private-original-name" not in " ".join(command)
    assert any(value.startswith("-env:UserInstallation=file:") for value in command)


def test_libreoffice_failure_timeout_and_invalid_docx_leave_no_partial_artifact(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "soffice.com"
    launcher.write_bytes(b"fake executable")
    source = tmp_path / "source.docx"
    _docx(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    failing = LibreOfficeDocxPdfDeriver(
        tmp_path / "failed-artifacts",
        tmp_path / "failed-temp",
        launcher,
        "26.8.0.3",
        process_runner=_FakeLibreOfficeRunner(fail=True),
    )
    with pytest.raises(ValueError, match="CONVERSION_FAILED"):
        failing.derive(source, "anonymous", digest)
    assert failing.root.is_dir()
    assert list(failing.root.iterdir()) == []

    atomic = _AtomicFailingDocxDeriver(
        tmp_path / "atomic-artifacts",
        tmp_path / "atomic-temp",
        launcher,
        "26.8.0.3",
        process_runner=_FakeLibreOfficeRunner(),
    )
    with pytest.raises(OSError, match="manifest failure"):
        atomic.derive(source, "anonymous", digest)
    assert list(atomic.root.iterdir()) == []

    invalid = tmp_path / "invalid.docx"
    invalid.write_bytes(b"not a DOCX")
    with pytest.raises(ValueError, match="MAGIC_INVALID"):
        failing.derive(
            invalid, "anonymous-invalid", hashlib.sha256(invalid.read_bytes()).hexdigest()
        )

    with pytest.raises(TimeoutError, match="TIMEOUT"):
        SubprocessOwnedProcessRunner().run(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            cwd=tmp_path,
            timeout_seconds=0.05,
        )
