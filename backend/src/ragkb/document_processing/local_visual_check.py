"""Independent PP-OCRv4 ONNX reading, measured text regions and exact critical facts."""

from __future__ import annotations

import re
import threading
from typing import Any

import numpy as np

from ragkb.application.cancellation import check_cancelled
from ragkb.config import EnvSettings
from ragkb.document_processing.image_views import TILE_SIZE, normalize_image, tile_offsets
from ragkb.domain.visuals import VisualExtraction

_engine: Any = None
_lock = threading.Lock()


def compact(text: str) -> str:
    # SI unit case is meaningful: mW and MW must never be treated as equal.
    return re.sub(r"\s+", "", text)


def critical_tokens(text: str) -> list[str]:
    return re.findall(
        r"(?<![\w])[-+±]?\d+(?:[.,]\d+)*(?:\s*(?:MW|kW|mW|W|kV|mV|V|mA|A|MHz|kHz|Hz|GB|MB|mm|cm|kg|mg|°C|℃|%))?",
        text,
    )


def mentions_region(text: str, region_text: str) -> bool:
    needle = compact(region_text)
    # Do not highlight 23 W when the cited paragraph actually says 323 W.
    return bool(
        needle and re.search(r"(?<![\d.,])" + re.escape(needle) + r"(?![\d.,])", compact(text))
    )


def read_regions(data: bytes, settings: EnvSettings) -> dict[str, Any]:
    global _engine
    from rapidocr_onnxruntime import RapidOCR

    image = normalize_image(
        data, max_bytes=settings.ocr_max_image_bytes, max_pixels=settings.ocr_max_image_pixels
    )
    width, height = image.size
    if len(tile_offsets(height)) * len(tile_offsets(width)) > 64:
        image.close()
        raise ValueError("OCR_IMAGE_TILING_LIMIT")
    regions: list[dict[str, Any]] = []
    with _lock:
        if _engine is None:
            # Bound CPU resources; independent OCR runs on each full-resolution tile.
            _engine = RapidOCR(
                intra_op_num_threads=2,
                inter_op_num_threads=1,
                det_limit_side_len=1600,
                print_verbose=False,
            )
        for top in tile_offsets(height):
            for left in tile_offsets(width):
                check_cancelled()
                crop = image.crop(
                    (left, top, min(width, left + TILE_SIZE), min(height, top + TILE_SIZE))
                )
                result, _elapsed = _engine(np.asarray(crop))
                for polygon, text, score in result or []:
                    xs = [float(p[0]) + left for p in polygon]
                    ys = [float(p[1]) + top for p in polygon]
                    box = [
                        max(0.0, min(xs) / width),
                        max(0.0, min(ys) / height),
                        min(1.0, max(xs) / width),
                        min(1.0, max(ys) / height),
                    ]
                    duplicate = any(
                        compact(r["text"]) == compact(text)
                        and abs(r["bbox"][0] - box[0]) < 0.012
                        and abs(r["bbox"][1] - box[1]) < 0.012
                        for r in regions
                    )
                    if not duplicate:
                        regions.append(
                            {
                                "id": f"r{len(regions) + 1}",
                                "text": str(text),
                                "bbox": box,
                                "score": float(score),
                                "basis": "ocr_text_box",
                            }
                        )
    image.close()
    return {
        "engine": "rapidocr-onnx:PP-OCRv4",
        "width": width,
        "height": height,
        "coordinate_space": "normalized_exif_white_background",
        "regions": regions,
    }


def check_extraction(extraction: VisualExtraction, reading: dict[str, Any]) -> dict[str, Any]:
    regions = reading["regions"]
    all_text = "\n".join(compact(r["text"]) for r in regions if r["score"] >= 0.9)
    visible = "\n".join(
        [
            extraction.transcription,
            extraction.description,
            extraction.body_text,
            *(cell.text for table in extraction.tables for cell in table.cells),
            *(note for table in extraction.tables for note in table.notes),
            *(node.label for graph in extraction.graphs for node in graph.nodes),
            *(edge.label for graph in extraction.graphs for edge in graph.edges),
        ]
    )
    # Token boundaries matter: 23 is not confirmed by an OCR reading of 323.
    missing = [
        token
        for token in critical_tokens(visible)
        if re.search(r"(?<![\d.,])" + re.escape(compact(token)) + r"(?![\d.,])", all_text) is None
    ]
    targets: list[dict[str, Any]] = []
    for ti, table in enumerate(extraction.tables):
        for cell in table.cells:
            matches = [
                r["id"]
                for r in regions
                if compact(r["text"]) == compact(cell.text) and r["score"] >= 0.9
            ]
            targets.append(
                {
                    "kind": "cell",
                    "table": ti,
                    "row": cell.row,
                    "column": cell.column,
                    "region_ids": matches if len(matches) == 1 else [],
                }
            )
    for gi, graph in enumerate(extraction.graphs):
        for node in graph.nodes:
            matches = [
                r["id"]
                for r in regions
                if compact(r["text"]) == compact(node.label) and r["score"] >= 0.9
            ]
            targets.append(
                {
                    "kind": "node",
                    "graph": gi,
                    "node_id": node.id,
                    "region_ids": matches if len(matches) == 1 else [],
                }
            )
    return {
        "engine": reading["engine"],
        "status": "disagreement" if missing else "consistent",
        "unmatched_critical_tokens": list(dict.fromkeys(missing))[:100],
        "targets": targets,
        "note": "文字坐标来自独立 OCR；重复文字无法唯一定位时不猜测位置。",
    }
