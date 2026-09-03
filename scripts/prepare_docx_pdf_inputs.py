"""Prepare and validate anonymous LibreOffice-derived PDFs for ten DOCX samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.format_samples import _resolve  # noqa: E402
from ragkb.evaluation.local_sample_validation import _anonymous_id  # noqa: E402
from ragkb.infrastructure.provider_inputs import (  # noqa: E402
    LibreOfficeDocxPdfDeriver,
    SubprocessOwnedProcessRunner,
)

LIBREOFFICE_CANDIDATES = (
    Path("C:/Program Files/LibreOffice/program/soffice.com"),
    Path("C:/Program Files (x86)/LibreOffice/program/soffice.com"),
)


def _launcher() -> Path:
    found = next((path for path in LIBREOFFICE_CANDIDATES if path.is_file()), None)
    if found is None:
        raise RuntimeError("LIBREOFFICE_CONSOLE_LAUNCHER_NOT_FOUND")
    return found.resolve()


def _version(launcher: Path) -> str:
    result = SubprocessOwnedProcessRunner().run(
        [str(launcher), "--version"], cwd=launcher.parent, timeout_seconds=15
    )
    if result.return_code != 0:
        raise RuntimeError("LIBREOFFICE_VERSION_CHECK_FAILED")
    match = re.search(rb"\b(\d+\.\d+\.\d+\.\d+)\b", result.stdout)
    if match is None:
        raise RuntimeError("LIBREOFFICE_VERSION_UNRECOGNIZED")
    return match.group(1).decode("ascii")


def _context():
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    item = next(value for value in plan["collection_plan"] if value["format"] == "docx")
    metadata = yaml.safe_load(
        _resolve(ROOT, str(item["metadata_path"])).read_text(encoding="utf-8")
    )
    directory = _resolve(ROOT, str(item["sample_directory"]))
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    temporary_root = settings.local_storage_temp_dir
    if not temporary_root.is_absolute():
        temporary_root = (ROOT / temporary_root).resolve()
    launcher = _launcher()
    version = _version(launcher)
    deriver = LibreOfficeDocxPdfDeriver(
        artifacts_root,
        temporary_root,
        launcher,
        version,
        timeout_seconds=120,
    )
    return directory, metadata["samples"], deriver, version


def _expected_max_page(sample: dict[str, object]) -> int:
    return max(
        (
            int(locator["page"])
            for locator in sample.get("expected_locators", [])
            if isinstance(locator, dict) and isinstance(locator.get("page"), int)
        ),
        default=1,
    )


def prepare_docx_pdf_inputs() -> dict[str, object]:
    directory, samples, deriver, version = _context()
    results = []
    for sample in samples:
        source = (directory / str(sample["file"])).resolve()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = deriver.derive(
            source,
            _anonymous_id("docx", sample),
            str(sample["sha256"]),
        )
        if hashlib.sha256(source.read_bytes()).hexdigest() != before:
            raise RuntimeError("DOCX_PDF_SOURCE_MUTATED")
        if int(result["page_count"]) < _expected_max_page(sample):
            raise RuntimeError("DOCX_PDF_EXPECTED_PAGE_NOT_COVERED")
        results.append(result)
    return _safe_summary(results, version)


def validate_prepared_docx_pdf_inputs() -> dict[str, object]:
    _, samples, deriver, version = _context()
    results = [
        deriver.load(_anonymous_id("docx", sample), str(sample["sha256"])) for sample in samples
    ]
    for sample, result in zip(samples, results, strict=True):
        if int(result["page_count"]) < _expected_max_page(sample):
            raise RuntimeError("DOCX_PDF_EXPECTED_PAGE_NOT_COVERED")
    return _safe_summary(results, version)


def _safe_summary(results: list[dict[str, object]], version: str) -> dict[str, object]:
    page_counts = [int(result["page_count"]) for result in results]
    return {
        "revision": "docx-pdf-inputs:v1",
        "converted_count": len(results),
        "page_count_min": min(page_counts, default=0),
        "page_count_max": max(page_counts, default=0),
        "page_count_total": sum(page_counts),
        "derived_hash_count": len({str(result["derived_sha256"]) for result in results}),
        "expected_pages_covered_count": len(results),
        "converter_revision": LibreOfficeDocxPdfDeriver.revision,
        "libreoffice_version": version,
        "source_snapshots_preserved": True,
        "anonymous_paths": True,
        "network_call_performed": False,
        "source_names_output": False,
        "content_output": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "validate"), default="validate", nargs="?")
    args = parser.parse_args()
    report = (
        prepare_docx_pdf_inputs() if args.mode == "prepare" else validate_prepared_docx_pdf_inputs()
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
