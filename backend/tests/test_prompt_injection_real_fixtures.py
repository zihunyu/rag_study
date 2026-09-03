from __future__ import annotations

import hashlib
import json
from pathlib import Path

from docx import Document
from PIL import Image
from pypdf import PdfReader


def test_three_real_format_attack_fixtures_are_valid_and_manifest_bound() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture_root = root / "backend/tests/fixtures/security/real"
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest["files"]) == {"pdf", "docx", "ocr_image"}
    assert manifest["max_provider_calls"] == 12
    for item in manifest["files"].values():
        path = root / item["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]

    pdf = PdfReader(fixture_root / "malicious-indirect-injection.pdf")
    pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    docx_text = "\n".join(
        paragraph.text
        for paragraph in Document(fixture_root / "malicious-indirect-injection.docx").paragraphs
    )
    with Image.open(fixture_root / "malicious-indirect-injection.png") as image:
        assert image.format == "PNG" and image.size == (1500, 900)
    assert "SYSTEM OVERRIDE" in pdf_text
    assert "SYSTEM OVERRIDE" in docx_text
    assert "phishing.invalid" in pdf_text and "phishing.invalid" in docx_text
