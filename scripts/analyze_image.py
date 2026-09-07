"""Analyze a single image using the independent OCR configuration and original-image audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.config import load_env
from ragkb.domain.visual_graph import to_mermaid
from ragkb.domain.visuals import VisualExtraction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    loaded = load_env()
    if loaded.settings is None or not loaded.settings.ocr_enabled:
        raise SystemExit("OCR_CONFIGURATION_REQUIRED")
    data = args.image.read_bytes()
    args.out.mkdir(parents=True, exist_ok=False)
    result = VisualAnalyzer(loaded.settings).analyze(data)
    result["image_sha256"] = hashlib.sha256(data).hexdigest()
    (args.out / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result["status"] == "verified":
        extraction = VisualExtraction.model_validate(result["extraction"])
        (args.out / "evidence.md").write_text(extraction.retrieval_text(), encoding="utf-8")
        for index, graph in enumerate(extraction.graphs, 1):
            (args.out / f"diagram-{index}.mmd").write_text(to_mermaid(graph), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "issues": result["issues"],
                "calls": len(result["audit"]),
                "output": str(args.out.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
