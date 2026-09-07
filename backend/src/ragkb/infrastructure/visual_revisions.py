"""Conservative dependency closure for partial publication; never drop only a unit/header."""

from __future__ import annotations

import re
from typing import Any

from ragkb.contracts.uploads import UploadRepositoryPort


def exclusion_closure(
    repository: UploadRepositoryPort,
    version: str,
    assets: dict[str, dict[str, Any]],
    sections: list[str],
) -> list[str]:
    if not sections:
        return []
    texts: dict[str, list[str]] = {}
    offset = 0
    while True:
        rows = repository.list_chunks(version, limit=100, offset=offset, preview=True)
        for row in rows:
            section = str(row["locator"].get("section_path", "root"))
            texts.setdefault(section, []).append(str(row["text"]))
        if len(rows) < 100:
            break
        offset += len(rows)
    excluded = set(sections)
    while True:
        terms = {s for s in excluded if s != "root" and len(s) > 2}
        for asset in assets.values():
            if asset.get("section_path", "root") in excluded:
                terms.update(
                    re.findall(r"(?:图|表|Figure|Table)\s*\d+", str(asset.get("caption", "")), re.I)
                )
        additions = {
            section
            for section, parts in texts.items()
            if section not in excluded
            and any(term.casefold() in "\n".join(parts).casefold() for term in terms)
        }
        if not additions:
            return sorted(excluded)
        excluded.update(additions)
