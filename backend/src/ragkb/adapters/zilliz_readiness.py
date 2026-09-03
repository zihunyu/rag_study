"""Bounded collection loading and readiness confirmation."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ragkb.config import EnvSettings
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan


class ZillizCollectionNotReady(RuntimeError):
    pass


def wait_for_collection_ready(
    client: Any,
    settings: EnvSettings,
    *,
    total_timeout_seconds: float = 30,
    max_polls: int = 20,
    poll_interval_seconds: float = 1,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    if total_timeout_seconds <= 0 or max_polls < 1 or poll_interval_seconds < 0:
        raise ValueError("readiness timeout, polls and interval are invalid")
    expected_indexes = {
        str(index["index_name"])
        for index in build_zilliz_collection_plan(settings)["schema"]["indexes"]
    }
    deadline = monotonic() + total_timeout_seconds
    last_loaded = False
    last_index_count = 0
    for poll in range(1, max_polls + 1):
        load_state = client.get_load_state(
            collection_name=settings.zilliz_cloud_collection,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
        state = str(load_state.get("state", load_state.get("load_state", "")))
        last_loaded = state.casefold().endswith("loaded")
        indexes = set(
            map(
                str,
                client.list_indexes(collection_name=settings.zilliz_cloud_collection),
            )
        )
        last_index_count = len(indexes)
        if last_loaded and expected_indexes.issubset(indexes):
            return {
                "ready": True,
                "poll_count": poll,
                "loaded": True,
                "index_count": len(indexes),
                "expected_index_count": len(expected_indexes),
                "mutating_call_performed": False,
            }
        now = monotonic()
        if poll >= max_polls or now >= deadline:
            break
        sleep(min(poll_interval_seconds, max(0.0, deadline - now)))
    raise ZillizCollectionNotReady(
        f"ZILLIZ_COLLECTION_NOT_READY:loaded={last_loaded}:indexes={last_index_count}"
    )


def request_collection_load_if_needed(client: Any, settings: EnvSettings) -> str:
    load_state = client.get_load_state(
        collection_name=settings.zilliz_cloud_collection,
        timeout=settings.zilliz_cloud_timeout_seconds,
    )
    state = str(load_state.get("state", load_state.get("load_state", "")))
    if state.casefold().endswith("loaded"):
        return "already_loaded"
    try:
        client.load(
            collection_name=settings.zilliz_cloud_collection,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
    except AttributeError:
        return "load_return_attribute_error_requires_readiness_confirmation"
    return "load_requested"
