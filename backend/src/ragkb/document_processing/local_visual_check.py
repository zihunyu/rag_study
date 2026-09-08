"""Independent PP-OCRv4 ONNX reading, measured text regions and exact critical facts."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np

from ragkb.application.cancellation import check_cancelled
from ragkb.config import EnvSettings
from ragkb.document_processing.image_views import TILE_SIZE, normalize_image, tile_offsets
from ragkb.document_processing.visual_graph_binding import bind_graph
from ragkb.document_processing.visual_table_binding import bind_table
from ragkb.domain.graph_facts import accepted_graph_projection
from ragkb.domain.visual_numbers import compact as compact
from ragkb.domain.visual_numbers import contains_number, critical_tokens
from ragkb.domain.visuals import VisualExtraction

LOCAL_CHECK_REVISION = "ocr-object-binding-v3"


def _make_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(
        intra_op_num_threads=2,
        inter_op_num_threads=1,
        det_limit_side_len=1600,
        print_verbose=False,
    )


class OCRPool:
    """Bounded independent ONNX sessions, each leased exclusively to one image."""

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.idle: list[Any] = []
        self.created = 0
        self.active = 0

    @contextmanager
    def borrow(self, limit: int) -> Iterator[Any]:
        engine = None
        with self.condition:
            while True:
                check_cancelled()
                if self.active < limit and (self.idle or self.created < limit):
                    self.active += 1
                    if self.idle:
                        engine = self.idle.pop()
                    else:
                        self.created += 1
                    break
                self.condition.wait(0.1)
        try:
            if engine is None:
                engine = _make_engine()
            yield engine
        finally:
            with self.condition:
                self.active -= 1
                if engine is None:
                    self.created -= 1
                else:
                    self.idle.append(engine)
                self.condition.notify_all()


_pool = OCRPool()


def mentions_region(text: str, region_text: str) -> bool:
    needle = compact(region_text)
    # Do not highlight 23 W when the cited paragraph actually says 323 W.
    return bool(
        needle and re.search(r"(?<![\d.,])" + re.escape(needle) + r"(?![\d.,])", compact(text))
    )


def read_regions(data: bytes, settings: EnvSettings) -> dict[str, Any]:
    image = normalize_image(
        data, max_bytes=settings.ocr_max_image_bytes, max_pixels=settings.ocr_max_image_pixels
    )
    width, height = image.size
    if len(tile_offsets(height)) * len(tile_offsets(width)) > 64:
        image.close()
        raise ValueError("OCR_IMAGE_TILING_LIMIT")
    regions: list[dict[str, Any]] = []
    try:
        with _pool.borrow(settings.ocr_local_max_concurrency) as engine:
            regions = _read_tiles(image, engine)
    finally:
        image.close()
    return {
        "engine": "rapidocr-onnx:PP-OCRv4",
        "width": width,
        "height": height,
        "coordinate_space": "normalized_exif_white_background",
        "regions": regions,
    }


def _read_tiles(image: Any, engine: Any) -> list[dict[str, Any]]:
    width, height = image.size
    regions: list[dict[str, Any]] = []
    for top in tile_offsets(height):
        for left in tile_offsets(width):
            check_cancelled()
            with image.crop(
                (left, top, min(width, left + TILE_SIZE), min(height, top + TILE_SIZE))
            ) as crop:
                result, _elapsed = engine(np.asarray(crop))
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
    return regions


def check_extraction(extraction: VisualExtraction, reading: dict[str, Any]) -> dict[str, Any]:
    regions = reading["regions"]
    graphs = [accepted_graph_projection(graph) for graph in extraction.graphs]
    # Preserve lexical/region boundaries until token extraction: A 220V must not become A220V.
    all_text = "\n".join(r["text"] for r in regions if r["score"] >= 0.9)
    visible = "\n".join(
        [
            extraction.transcription if not extraction.graphs else "",
            # Descriptions may count visible objects ("three nodes") rather than
            # transcribe printed numerals; the independent vision audit checks them.
            extraction.body_text,
            *(cell.text for table in extraction.tables for cell in table.cells),
            *(note for table in extraction.tables for note in table.notes),
            *(node.label for graph in graphs for node in graph.nodes),
            *(group.label for graph in graphs for group in graph.groups),
            *(edge.label for graph in graphs for edge in graph.edges),
        ]
    )
    # Token boundaries matter: 23 is not confirmed by an OCR reading of 323.
    missing = [token for token in critical_tokens(visible) if not contains_number(all_text, token)]
    targets: list[dict[str, Any]] = []
    bindings = []
    for ti, table in enumerate(extraction.tables):
        binding = bind_table(table, regions, ti)
        bindings.append(binding)
        if binding["status"] == "consistent":
            targets.extend(binding["targets"])
            continue
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
                    "region_ids": matches
                    if binding["status"] == "not_required" and len(matches) == 1
                    else [],
                }
            )
    graph_bindings = [bind_graph(graph, regions, gi) for gi, graph in enumerate(extraction.graphs)]
    for binding in graph_bindings:
        targets.extend(binding["targets"])
    all_bindings = bindings + graph_bindings
    issues = [issue for binding in all_bindings for issue in binding["issues"]]
    status = (
        "disagreement"
        if missing or any(b["status"] == "disagreement" for b in all_bindings)
        else ("inconclusive" if issues else "consistent")
    )
    return {
        "engine": reading["engine"],
        "revision": LOCAL_CHECK_REVISION,
        "status": status,
        "issues": issues,
        "table_checks": [{"table": i, "status": b["status"]} for i, b in enumerate(bindings)],
        "graph_checks": [{"graph": i, "status": b["status"]} for i, b in enumerate(graph_bindings)],
        "unmatched_critical_tokens": list(dict.fromkeys(missing))[:100],
        "targets": targets,
        "note": "文字坐标来自独立 OCR；重复文字无法唯一定位时不猜测位置。",
    }
