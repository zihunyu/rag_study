"""Request-local reading scope; conversations persist these options with the turn."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["auto", "fact", "overview", "compare"] = "auto"
    document_ids: tuple[str, ...] = Field(default=(), max_length=20)


options: ContextVar[ReadingOptions] = ContextVar("reading_options", default=ReadingOptions())  # noqa: B039 -- frozen model with immutable fields
progress: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "reading_progress", default=None
)


def is_overview(question: str) -> bool:
    mode = options.get().mode
    if mode != "auto":
        return mode in {"overview", "compare"}
    return bool(
        re.search(
            r"整篇|整份|全文|全篇|所有章节|跨图|多张图|逐章|总结|综述|概述|"
            r"\b(summarize|summary|overview|whole document)\b",
            question,
            re.I,
        )
    )


@contextmanager
def reading_scope(
    value: ReadingOptions, callback: Callable[[dict[str, Any]], None] | None = None
) -> Iterator[None]:
    token, progress_token = options.set(value), progress.set(callback)
    try:
        yield
    finally:
        options.reset(token)
        progress.reset(progress_token)
