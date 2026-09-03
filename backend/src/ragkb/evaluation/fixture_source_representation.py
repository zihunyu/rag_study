"""Independent source representations for local fixture verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ragkb.evaluation.local_ocr_representation import ocr
from ragkb.evaluation.uat_render_proof import RenderProofError, independent_render_proof


def representation(category: str, source: Path, locator: dict[str, object]) -> dict[str, object]:
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if category == "pdf_scanned_or_image" and source.suffix.casefold() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".gif",
    }:
        discovered = shutil.which("tesseract")
        executable = Path(discovered) if discovered else Path("__tesseract_unavailable__")
        configured_tessdata = os.environ.get("TESSDATA_PREFIX")
        tessdata = (
            Path(configured_tessdata) if configured_tessdata else executable.parent / "tessdata"
        )
        result = ocr(source, executable, tessdata)
        return {
            "status": result["status"],
            "source_sha256": source_sha,
            "locator_sha256": hashlib.sha256(
                json.dumps(locator, sort_keys=True).encode()
            ).hexdigest(),
            "representation_sha256": result["representation_sha256"],
            "content": result["content"],
        }
    try:
        proof = independent_render_proof(
            category=category, source_path=source, source_version_sha256=source_sha, locator=locator
        )
        return {
            "status": "AVAILABLE",
            "source_sha256": source_sha,
            "locator_sha256": proof["locator_sha256"],
            "representation_sha256": proof["representation_sha256"],
            "content": proof["rendered_text"],
        }
    except RenderProofError:
        return {
            "status": "UNAVAILABLE_FAIL_CLOSED",
            "source_sha256": source_sha,
            "locator_sha256": hashlib.sha256(
                json.dumps(locator, sort_keys=True).encode()
            ).hexdigest(),
            "representation_sha256": None,
            "content": None,
        }
