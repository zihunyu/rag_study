"""Original-file standards, independent from parser-produced text."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REVISION = "original-parsing-checks-v2-markdown-rows"


class OriginalCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=2000, pattern=r".*\S.*")
    kind: Literal["text", "list_item", "table_row", "condition"] = "text"
    pages: list[int] = Field(min_length=1, max_length=20)
    quote: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")

    @model_validator(mode="after")
    def valid_pages(self) -> OriginalCheck:
        if any(p < 1 or p > 100000 for p in self.pages) or len(set(self.pages)) != len(self.pages):
            raise ValueError("ORIGINAL_PAGE_INVALID")
        return self


class ParsingStandard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    document_id: str = Field(min_length=1, max_length=191)
    reference_version_id: str = Field(min_length=1, max_length=191)
    checks: list[OriginalCheck] = Field(min_length=1, max_length=100)
    original_checked: bool = False
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique_checks(self) -> ParsingStandard:
        if len({c.id for c in self.checks}) != len(self.checks):
            raise ValueError("ORIGINAL_CHECK_ID_DUPLICATED")
        if len({page for check in self.checks for page in check.pages}) > 20:
            raise ValueError("SELECT_UP_TO_20_ORIGINAL_PAGES")
        if self.original_checked and not self.note.strip():
            raise ValueError("ORIGINAL_REVIEW_NOTE_REQUIRED")
        return self


class _TableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th", "tr", "p", "div", "br"}:
            self.parts.append(" ")


def plain(text: str) -> str:
    # Never normalize digits, units, negations, signs or comparisons.
    if re.search(r"<(?:table|tr|td|th)\b", text, re.I):
        parser = _TableText()
        parser.feed(text)
        text = "".join(parser.parts)
    # Markdown cell separators are layout, like HTML td boundaries. Only strip
    # them on complete table rows; mathematical/escaped pipes stay literal.
    text = "\n".join(
        re.sub(r"(?<!\\)\|", " ", line)
        if line.strip().startswith("|") and line.strip().endswith("|")
        else line
        for line in text.splitlines()
    )
    return re.sub(r"\s+", "", text)


def locations(row: dict[str, Any]) -> set[int]:
    locator = row.get("locator") or {}
    pages = {locator.get("page") or locator.get("slide")}
    for span in locator.get("source_spans", []):
        position = span.get("locator", span)
        pages.add(position.get("page") or position.get("slide"))
    result = {p for p in pages if isinstance(p, int)}
    # Text/Word fragments have paragraph positions rather than printed pages.
    return result or ({1} if locator else set())


def find_quote(quote: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = plain(quote)
    return [r for r in rows if expected and expected in plain(r.get("text") or "")]


def evaluate(checks: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for check in checks:
        stages = {}
        for stage in ("parsed", "chunks"):
            rows = snapshot.get(stage)
            selected = [r for r in rows or [] if locations(r) & set(check["pages"])]
            if check["kind"] == "table_row":
                selected = [
                    {**r, "text": part}
                    for r in selected
                    for part in (
                        re.findall(r"<tr\b[^>]*>.*?</tr\s*>", r["text"], re.I | re.S)
                        if re.search(r"<tr\b", r["text"], re.I)
                        else r["text"].splitlines()
                    )
                ]
            matches = find_quote(check["quote"], selected)
            stages[stage] = {
                "status": "unrecorded" if rows is None else "found" if matches else "not_found",
                "matches": [{"id": r["id"], "locator": r["locator"]} for r in matches[:8]],
            }
        result.append({**check, "stages": stages})
    return result
