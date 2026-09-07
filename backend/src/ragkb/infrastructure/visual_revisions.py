"""Conservative dependency closure for partial publication; never drop only a unit/header."""

from __future__ import annotations

from typing import Any

from ragkb.contracts.uploads import UploadRepositoryPort
from ragkb.domain.source_references import mentions_section, references


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
        figure_ids: set[str] = set()
        for asset in assets.values():
            section = asset.get("section_path", "root")
            if any(section == s or section.startswith(s + " / ") for s in excluded):
                figure_ids.update(key for key, _ in references(str(asset.get("caption", ""))))
        additions = {
            section
            for section, parts in texts.items()
            if section not in excluded
            and (
                any(mentions_section("\n".join(parts), term) for term in terms)
                or bool(figure_ids.intersection(key for key, _ in references("\n".join(parts))))
            )
        }
        if not additions:
            return sorted(excluded)
        excluded.update(additions)
