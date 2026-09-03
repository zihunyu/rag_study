from pathlib import Path

from PIL import Image
from ragkb.evaluation.local_ocr_representation import ocr


def test_missing_executable_fails_closed(tmp_path: Path):
    image = tmp_path / "x.png"
    Image.new("RGB", (8, 8), "white").save(image)
    assert ocr(image, tmp_path / "missing", tmp_path)["status"] == "UNAVAILABLE_FAIL_CLOSED"
