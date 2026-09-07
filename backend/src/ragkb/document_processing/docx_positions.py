"""A shared paragraph coordinate system for DOCX text and embedded pictures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from docx.document import Document
from docx.text.paragraph import Paragraph


@dataclass(frozen=True)
class ParagraphPosition:
    element: Any
    number: int
    text: str
    section_path: str
    heading_level: int | None
    is_caption: bool


def _heading_level(paragraph: Paragraph) -> int | None:
    # Use Word's real outline properties, including custom/inherited heading styles.
    candidates = [paragraph._p]
    style = paragraph.style
    seen: set[str] = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        candidates.append(style.element)
        style = style.base_style
    for candidate in candidates:
        values = candidate.xpath("./w:pPr/w:outlineLvl/@w:val")
        if values:
            try:
                outline = int(values[0])
            except (ValueError, TypeError):
                return None
            return outline + 1 if 0 <= outline < 9 else None
    match = re.match(r"(?i)^heading\s+([1-9])$", str(getattr(paragraph.style, "name", "")))
    return int(match.group(1)) if match else None


def paragraph_positions(document: Document) -> list[ParagraphPosition]:
    result: list[ParagraphPosition] = []
    headings: list[tuple[int, str]] = []
    for number, element in enumerate(document.element.body.xpath(".//w:p"), 1):
        paragraph = Paragraph(element, document._body)
        text = paragraph.text.strip()
        style = str(getattr(paragraph.style, "name", ""))
        level = _heading_level(paragraph) if element.getparent() is document.element.body else None
        if level and text:
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, text))
        result.append(
            ParagraphPosition(
                element,
                number,
                text,
                " / ".join(title for _, title in headings) or "root",
                level,
                style.casefold() == "caption"
                or bool(re.match(r"(?i)^(图|表|figure|table)\s*\d", text)),
            )
        )
    return result
