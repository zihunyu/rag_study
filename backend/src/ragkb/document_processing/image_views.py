"""Orientation-aware, alpha-safe image views with complete overlapping tile coverage."""

from __future__ import annotations

import base64
import io
import math
from typing import Any

from PIL import Image, ImageOps

from ragkb.application.cancellation import check_cancelled

TILE_SIZE = 1600
TILE_OVERLAP = 160
MAX_DETAIL_VIEWS = 64


def tile_offsets(length: int) -> list[int]:
    if length <= TILE_SIZE:
        return [0]
    count = math.ceil((length - TILE_SIZE) / (TILE_SIZE - TILE_OVERLAP)) + 1
    return [min(index * (TILE_SIZE - TILE_OVERLAP), length - TILE_SIZE) for index in range(count)]


def normalize_image(data: bytes, *, max_bytes: int, max_pixels: int) -> Image.Image:
    if not data or len(data) > max_bytes:
        raise ValueError("OCR_IMAGE_BYTES_LIMIT")
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > max_pixels:
            raise ValueError("OCR_IMAGE_PIXELS_LIMIT")
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("OCR_ANIMATED_IMAGE_REQUIRES_REVIEW")
        oriented = ImageOps.exif_transpose(source)
        rgba = oriented.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        rgb = Image.alpha_composite(background, rgba).convert("RGB")
    return rgb


def image_views(data: bytes, *, max_bytes: int, max_pixels: int) -> list[dict[str, Any]]:
    rgb = normalize_image(data, max_bytes=max_bytes, max_pixels=max_pixels)

    width, height = rgb.size
    xs, ys = tile_offsets(width), tile_offsets(height)
    detailed = width > TILE_SIZE or height > TILE_SIZE
    if detailed and len(xs) * len(ys) > MAX_DETAIL_VIEWS:
        raise ValueError("OCR_IMAGE_TILE_LIMIT")
    overview = rgb.copy()
    overview.thumbnail((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS)
    parts: list[dict[str, Any]] = []
    total_bytes = 0

    def append(view: Image.Image, label: str) -> None:
        nonlocal total_bytes
        check_cancelled()
        buffer = io.BytesIO()
        view.save(buffer, format="PNG")
        total_bytes += buffer.tell()
        if total_bytes > max_bytes:
            raise ValueError("OCR_NORMALIZED_IMAGE_BYTES_LIMIT")
        parts.extend(
            [
                {"type": "text", "text": label},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(buffer.getvalue()).decode(),
                        "detail": "high",
                    },
                },
            ]
        )

    append(overview, f"原图全景；已校正旋转；原图尺寸 {width} × {height}。局部视图使用此坐标系。")
    if detailed:
        for top in ys:
            for left in xs:
                box = (left, top, min(left + TILE_SIZE, width), min(top + TILE_SIZE, height))
                append(
                    rgb.crop(box),
                    f"同一原图的重叠局部，坐标 {box}；不是另一张图。"
                    "重叠处只计一次，结合全景连接跨区内容。",
                )
    return parts
