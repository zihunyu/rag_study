"""Deterministic anonymous local derivation of provider-compatible inputs."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from ragkb.contracts.provider_execution import OwnedProcessResult, OwnedProcessRunnerPort


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload, usedforsecurity=False).hexdigest()


class SingleFrameTiffPngDeriver:
    revision = "single-frame-tiff-to-png:v1"
    _png_modes = frozenset({"1", "L", "P", "RGB", "RGBA", "I", "I;16"})

    def __init__(self, controlled_root: Path) -> None:
        self.root = (controlled_root / "provider-inputs" / "mineru-scan-v5").resolve()

    @staticmethod
    def _artifact_id(anonymous_id: str, source_sha256: str) -> str:
        if (
            not anonymous_id
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256.casefold())
        ):
            raise ValueError("DERIVED_INPUT_IDENTITY_INVALID")
        return hashlib.sha256(
            f"{anonymous_id}:{source_sha256.casefold()}:{SingleFrameTiffPngDeriver.revision}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:32]

    def _target(self, artifact_id: str) -> Path:
        if len(artifact_id) != 32 or any(
            character not in "0123456789abcdef" for character in artifact_id
        ):
            raise ValueError("DERIVED_INPUT_ARTIFACT_ID_INVALID")
        target = (self.root / artifact_id).resolve()
        if target.parent != self.root:
            raise ValueError("DERIVED_INPUT_PATH_INVALID")
        return target

    @staticmethod
    def _write(path: Path, payload: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)

    def _load(self, artifact_id: str, source_sha256: str) -> dict[str, object]:
        target = self._target(artifact_id)
        manifest_path = target / "manifest.json"
        png_path = target / "input.png"
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        png_payload = png_path.read_bytes()
        if (
            not isinstance(loaded, dict)
            or loaded.get("artifact_id") != artifact_id
            or loaded.get("source_sha256") != source_sha256.casefold()
            or loaded.get("converter_revision") != self.revision
            or loaded.get("derived_sha256") != _sha256(png_payload)
            or loaded.get("derived_bytes") != len(png_payload)
        ):
            raise ValueError("DERIVED_INPUT_INTEGRITY_INVALID")
        return {
            **loaded,
            "derived_path": png_path,
            "artifact_ref": f"provider-inputs/mineru-scan-v5/{artifact_id}",
        }

    def load(self, anonymous_id: str, source_sha256: str) -> dict[str, object]:
        artifact_id = self._artifact_id(anonymous_id, source_sha256)
        return self._load(artifact_id, source_sha256)

    def derive(
        self, source: Path, anonymous_id: str, expected_source_sha256: str
    ) -> dict[str, object]:
        source_before = source.read_bytes()
        source_sha256 = _sha256(source_before)
        if source_sha256 != expected_source_sha256.casefold():
            raise ValueError("DERIVED_INPUT_SOURCE_SNAPSHOT_MISMATCH")
        if source_before[:4] not in {b"II*\x00", b"MM\x00*"}:
            raise ValueError("DERIVED_INPUT_NOT_TIFF")
        artifact_id = self._artifact_id(anonymous_id, source_sha256)
        target = self._target(artifact_id)
        if target.is_dir():
            return self._load(artifact_id, source_sha256)
        self.root.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(source_before)) as image:
            if image.format != "TIFF":
                raise ValueError("DERIVED_INPUT_NOT_TIFF")
            frame_count = int(getattr(image, "n_frames", 1))
            if frame_count != 1:
                raise ValueError("DERIVED_INPUT_TIFF_MULTIFRAME_FORBIDDEN")
            if image.mode not in self._png_modes:
                raise ValueError("DERIVED_INPUT_TIFF_MODE_NOT_LOSSLESS_PNG")
            image.seek(0)
            image.load()
            width, height = image.size
            mode = image.mode
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=False, compress_level=9)
            png_payload = output.getvalue()
        if _sha256(source.read_bytes()) != source_sha256:
            raise ValueError("DERIVED_INPUT_SOURCE_MUTATED")
        derived_sha256 = _sha256(png_payload)
        temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=self.root)).resolve()
        try:
            manifest = {
                "revision": "provider-derived-input-manifest:v1",
                "artifact_id": artifact_id,
                "anonymous_sample_id": anonymous_id,
                "source_sha256": source_sha256,
                "derived_sha256": derived_sha256,
                "derived_bytes": len(png_payload),
                "converter_revision": self.revision,
                "width": width,
                "height": height,
                "mode": mode,
                "frame_count": frame_count,
            }
            manifest_payload = (
                json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            self._write(temporary / "input.png", png_payload)
            self._write(temporary / "manifest.json", manifest_payload)
            try:
                os.replace(temporary, target)
            except OSError:
                if not target.is_dir():
                    raise
            return self._load(artifact_id, source_sha256)
        finally:
            if temporary.is_dir():
                shutil.rmtree(temporary)


class SubprocessOwnedProcessRunner:
    """Runs and, on timeout, terminates only the process handle it created."""

    def run(
        self, command: Sequence[str], *, cwd: Path, timeout_seconds: float
    ) -> OwnedProcessResult:
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        process = subprocess.Popen(  # noqa: S603
            list(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise TimeoutError("LIBREOFFICE_CONVERSION_TIMEOUT") from error
        return OwnedProcessResult(process.returncode, stdout, stderr)


class LibreOfficeDocxPdfDeriver:
    revision = "libreoffice-docx-to-pdf:v1"

    def __init__(
        self,
        artifacts_root: Path,
        temporary_root: Path,
        launcher: Path,
        libreoffice_version: str,
        *,
        process_runner: OwnedProcessRunnerPort | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self.root = (artifacts_root / "provider-inputs" / "mineru-docx-pdf-v1").resolve()
        self.work_root = (temporary_root / "provider-inputs" / "mineru-docx-pdf-v1-work").resolve()
        self.launcher = launcher.resolve()
        if not self.launcher.is_file() or self.launcher.name.casefold() not in {
            "soffice.com",
            "soffice",
        }:
            raise ValueError("LIBREOFFICE_CONSOLE_LAUNCHER_INVALID")
        if not libreoffice_version.strip():
            raise ValueError("LIBREOFFICE_VERSION_REQUIRED")
        self.libreoffice_version = libreoffice_version.strip()
        self.process_runner = process_runner or SubprocessOwnedProcessRunner()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _artifact_id(anonymous_id: str, source_sha256: str) -> str:
        if (
            not anonymous_id
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256.casefold())
        ):
            raise ValueError("DOCX_PDF_DERIVED_IDENTITY_INVALID")
        return hashlib.sha256(
            f"{anonymous_id}:{source_sha256.casefold()}:{LibreOfficeDocxPdfDeriver.revision}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:32]

    def _target(self, artifact_id: str) -> Path:
        if len(artifact_id) != 32 or any(
            character not in "0123456789abcdef" for character in artifact_id
        ):
            raise ValueError("DOCX_PDF_ARTIFACT_ID_INVALID")
        target = (self.root / artifact_id).resolve()
        if target.parent != self.root:
            raise ValueError("DOCX_PDF_ARTIFACT_PATH_INVALID")
        return target

    @staticmethod
    def _write(path: Path, payload: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)

    @staticmethod
    def _validate_docx(payload: bytes) -> None:
        if payload[:4] != b"PK\x03\x04":
            raise ValueError("DOCX_PDF_SOURCE_MAGIC_INVALID")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as error:
            raise ValueError("DOCX_PDF_SOURCE_ZIP_INVALID") from error
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ValueError("DOCX_PDF_SOURCE_STRUCTURE_INVALID")

    @staticmethod
    def _validate_pdf(payload: bytes) -> int:
        if not payload.startswith(b"%PDF-"):
            raise ValueError("DOCX_PDF_OUTPUT_MAGIC_INVALID")
        try:
            page_count = len(PdfReader(io.BytesIO(payload)).pages)
        except Exception as error:
            raise ValueError("DOCX_PDF_OUTPUT_UNREADABLE") from error
        if page_count < 1:
            raise ValueError("DOCX_PDF_OUTPUT_HAS_NO_PAGES")
        return page_count

    def _load(self, artifact_id: str, source_sha256: str) -> dict[str, object]:
        target = self._target(artifact_id)
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        pdf_path = target / "input.pdf"
        pdf_payload = pdf_path.read_bytes()
        page_count = self._validate_pdf(pdf_payload)
        if (
            not isinstance(manifest, dict)
            or manifest.get("artifact_id") != artifact_id
            or manifest.get("source_sha256") != source_sha256.casefold()
            or manifest.get("derived_sha256") != _sha256(pdf_payload)
            or manifest.get("derived_bytes") != len(pdf_payload)
            or manifest.get("page_count") != page_count
            or manifest.get("converter_revision") != self.revision
            or manifest.get("libreoffice_version") != self.libreoffice_version
        ):
            raise ValueError("DOCX_PDF_DERIVED_INTEGRITY_INVALID")
        return {
            **manifest,
            "derived_path": pdf_path,
            "artifact_ref": f"provider-inputs/mineru-docx-pdf-v1/{artifact_id}",
        }

    def load(self, anonymous_id: str, source_sha256: str) -> dict[str, object]:
        return self._load(self._artifact_id(anonymous_id, source_sha256), source_sha256)

    def derive(
        self, source: Path, anonymous_id: str, expected_source_sha256: str
    ) -> dict[str, object]:
        if source.suffix.casefold() != ".docx":
            raise ValueError("DOCX_PDF_SOURCE_EXTENSION_INVALID")
        source_payload = source.read_bytes()
        source_sha256 = _sha256(source_payload)
        if source_sha256 != expected_source_sha256.casefold():
            raise ValueError("DOCX_PDF_SOURCE_SNAPSHOT_MISMATCH")
        self._validate_docx(source_payload)
        artifact_id = self._artifact_id(anonymous_id, source_sha256)
        target = self._target(artifact_id)
        if target.is_dir():
            return self._load(artifact_id, source_sha256)
        self.root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=self.work_root)).resolve()
        staged = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=self.root)).resolve()
        try:
            anonymous_docx = work / "input.docx"
            profile = work / "profile"
            output = work / "output"
            profile.mkdir()
            output.mkdir()
            self._write(anonymous_docx, source_payload)
            command = [
                str(self.launcher),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output),
                str(anonymous_docx),
            ]
            result = self.process_runner.run(
                command, cwd=work, timeout_seconds=self.timeout_seconds
            )
            if result.return_code != 0:
                raise ValueError("LIBREOFFICE_CONVERSION_FAILED")
            converted = output / "input.pdf"
            if not converted.is_file():
                raise ValueError("LIBREOFFICE_OUTPUT_MISSING")
            pdf_payload = converted.read_bytes()
            page_count = self._validate_pdf(pdf_payload)
            if _sha256(source.read_bytes()) != source_sha256:
                raise ValueError("DOCX_PDF_SOURCE_MUTATED")
            derived_sha256 = _sha256(pdf_payload)
            manifest = {
                "revision": "provider-derived-input-manifest:v1",
                "artifact_id": artifact_id,
                "anonymous_sample_id": anonymous_id,
                "source_sha256": source_sha256,
                "source_bytes": len(source_payload),
                "derived_sha256": derived_sha256,
                "derived_bytes": len(pdf_payload),
                "page_count": page_count,
                "converter_revision": self.revision,
                "libreoffice_version": self.libreoffice_version,
            }
            manifest_payload = (
                json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            self._write(staged / "input.pdf", pdf_payload)
            self._write(staged / "manifest.json", manifest_payload)
            try:
                os.replace(staged, target)
            except OSError:
                if not target.is_dir():
                    raise
            return self._load(artifact_id, source_sha256)
        finally:
            if work.is_dir():
                shutil.rmtree(work)
            if staged.is_dir():
                shutil.rmtree(staged)
