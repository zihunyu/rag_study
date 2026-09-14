"""Best-effort chapter copies, with bounded memory while durable storage is unavailable."""

import json
import logging
import sqlite3
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Protocol


class ChapterCacheStore(Protocol):
    def cache_get(self, scope: str, identity: str) -> dict[str, Any] | None: ...
    def cache_put(self, scope: str, identity: str, payload: dict[str, Any], ttl: int) -> None: ...


class ChapterCache:
    def __init__(self, store: ChapterCacheStore, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        self.store, self.max_bytes = store, max_bytes
        self.memory: OrderedDict[tuple[str, str], tuple[float, bytes]] = OrderedDict()
        self.lock = Lock()
        self.size = 0

    def _prune(self) -> None:
        now = time.monotonic()
        for key, (expires, data) in list(self.memory.items()):
            if expires <= now:
                self.size -= len(data)
                del self.memory[key]
        while self.size > self.max_bytes:
            _, (_, data) = self.memory.popitem(last=False)
            self.size -= len(data)

    def get(self, scope: str, identity: str) -> dict[str, Any] | None:
        with self.lock:
            self._prune()
            saved = self.memory.get((scope, identity))
            if saved:
                self.memory.move_to_end((scope, identity))
                return dict(json.loads(saved[1]))
        try:
            return self.store.cache_get(scope, identity)
        except (sqlite3.Error, OSError, ValueError, TypeError) as error:
            logging.getLogger(__name__).warning("chapter cache read: %s", type(error).__name__)
            return None

    def put(self, scope: str, identity: str, payload: dict[str, Any], ttl: int) -> None:
        try:
            self.store.cache_put(scope, identity, payload, ttl)
        except (sqlite3.Error, OSError) as error:
            logging.getLogger(__name__).warning("chapter cache write: %s", type(error).__name__)
            data = json.dumps(payload, ensure_ascii=False).encode()
            with self.lock:
                old = self.memory.pop((scope, identity), None)
                if old:
                    self.size -= len(old[1])
                if len(data) <= self.max_bytes:
                    self.memory[(scope, identity)] = (time.monotonic() + min(ttl, 600), data)
                    self.size += len(data)
                self._prune()
        else:
            with self.lock:
                old = self.memory.pop((scope, identity), None)
                if old:
                    self.size -= len(old[1])


def valid_chapter(brief: Any, sources: dict[str, str]) -> bool:
    """A stale or malformed disposable copy is a miss, never trusted source text."""
    return bool(
        isinstance(brief, dict)
        and isinstance(brief.get("quotes"), list)
        and isinstance(brief.get("gaps", []), list)
        and bool(brief["quotes"] or brief.get("gaps"))
        and all(isinstance(g, str) for g in brief.get("gaps", []))
        and all(
            isinstance(q, dict)
            and isinstance(q.get("chunk_id"), str)
            and isinstance(q.get("quote"), str)
            and bool(q["quote"].strip())
            and q["chunk_id"] in sources
            and q["quote"] in sources[q["chunk_id"]]
            for q in brief["quotes"]
        )
    )
