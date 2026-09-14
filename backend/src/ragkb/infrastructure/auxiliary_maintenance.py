"""Runtime maintenance for disposable vector copies and usage receipts."""

import logging
from typing import Any

from ragkb.adapters.reuse_ledger import SQLiteReuseLedger
from ragkb.config import EnvSettings


def maintain_auxiliary(
    settings: EnvSettings, embedding: Any, ledger: SQLiteReuseLedger | None
) -> None:
    cache = getattr(embedding, "cache", None)
    jobs = []
    if cache is not None:
        jobs.append(("embedding_cache", cache.maintain))
    if ledger is not None:
        jobs.append(
            (
                "reuse_ledger",
                lambda: ledger.maintain(
                    raw_days=settings.reuse_raw_retention_days,
                    summary_days=settings.reuse_summary_retention_days,
                    max_finished_attempts=settings.reuse_max_finished_attempts,
                ),
            )
        )
    for name, maintain in jobs:
        try:
            maintain()
        except Exception as error:
            logging.getLogger(__name__).warning(
                "%s maintenance failed: %s", name, type(error).__name__
            )
