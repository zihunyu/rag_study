"""Complete figure/table identifiers shared by retrieval and exclusion closure."""

from __future__ import annotations

import re
import unicodedata

_REFERENCE = re.compile(
    r"(?<![A-Za-z])(?P<kind>Figure|Table|图|表)\s*"
    r"(?P<number>\d+(?:\s*[.．\-–]\s*\d+)*)(?![\dA-Za-z]|[.\-–]\s*\d)",
    re.I,
)


def references(text: str) -> list[tuple[str, str]]:
    result = []
    for m in _REFERENCE.finditer(unicodedata.normalize("NFKC", text)):
        kind = "figure" if m["kind"].lower() in {"图", "figure"} else "table"
        number = re.sub(r"\s", "", m["number"]).replace("–", "-")
        result.append((kind + ":" + number, m.group()))
    return result


def mentions_section(text: str, section: str) -> bool:
    # Numeric suffixes are identities: 产品1 is not 产品10; Latin names need word boundaries.
    left = r"(?<![A-Za-z0-9_])" if section[:1].isascii() else ""
    right = r"(?![A-Za-z0-9_])" if section[-1:].isascii() else ""
    if section[-1:].isdigit():
        right = r"(?![A-Za-z0-9_]|[.\-–]\s*\d)"
    return bool(re.search(left + re.escape(section) + right, text, re.I))
