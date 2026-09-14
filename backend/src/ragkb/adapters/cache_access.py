"""Best-effort disk persistence with a bounded, process-local retry buffer."""

from __future__ import annotations

import sqlite3
import time
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from threading import RLock

from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache, cache_purpose
from ragkb.domain.errors import ProviderTimeout


class EmbeddingCacheAccess:
    def __init__(self, cache: SQLiteEmbeddingCache, max_bytes: int, ttl: float) -> None:
        self.cache, self.max_bytes, self.ttl = cache, max_bytes, ttl
        self.memory: OrderedDict[tuple[str, str, int], tuple[float, tuple[float, ...], str]] = (
            OrderedDict()
        )
        self.mutex = RLock()
        self.size = 0
        self.failures = 0
        self.flights = [RLock() for _ in range(64)]

    def _trim(self) -> None:
        now = time.monotonic()
        for key, (expires, value, _) in list(self.memory.items()):
            if expires <= now:
                self.memory.pop(key)
                self.size -= 256 + len(value) * 40
        while self.size > self.max_bytes and self.memory:
            _, (_, value, _) = self.memory.popitem(last=False)
            self.size -= 256 + len(value) * 40

    def get_many(
        self, namespace: str, keys: Sequence[str], dimension: int
    ) -> dict[str, list[float]]:
        with self.mutex:
            self._trim()
            found = {}
            purposes = {}
            for key in keys:
                identity = (namespace, key, dimension)
                if identity in self.memory:
                    found[key] = list(self.memory[identity][1])
                    purposes[key] = self.memory[identity][2]
                    self.memory.move_to_end(identity)
        missing = [key for key in keys if key not in found]
        if found:
            # Retry persistence of failed copies without buying another embedding.
            for purpose in set(purposes.values()):
                self.put(
                    namespace,
                    {k: v for k, v in found.items() if purposes[k] == purpose},
                    dimension,
                    purpose=purpose,
                )
        if missing:
            try:
                found.update(self.cache.get_many(namespace, missing, dimension))
            except (sqlite3.Error, OSError):
                self.failures += 1
        return found

    def put(
        self,
        namespace: str,
        values: dict[str, list[float]],
        dimension: int,
        *,
        purpose: str = "document",
    ) -> None:
        # Keep the paid result BEFORE attempting fallible persistence.
        with self.mutex:
            for key, vector in values.items():
                identity = (namespace, key, dimension)
                previous = self.memory.pop(identity, None)
                if previous:
                    self.size -= 256 + len(previous[1]) * 40
                self.memory[identity] = (time.monotonic() + self.ttl, tuple(vector), purpose)
                self.size += 256 + len(vector) * 40
            self._trim()
        token = cache_purpose.set(purpose)
        try:
            self.cache.put(namespace, values, dimension)
        except (sqlite3.Error, OSError):
            self.failures += 1
        else:
            # Normal reads still validate disk checksums. The buffer is only for
            # results whose persistence failed, not a bypass of corruption checks.
            with self.mutex:
                for key in values:
                    previous = self.memory.pop((namespace, key, dimension), None)
                    if previous:
                        self.size -= 256 + len(previous[1]) * 40
        finally:
            cache_purpose.reset(token)

    def access(self, namespace: str, keys: Sequence[str], purpose: str) -> None:
        try:
            self.cache.record_access(namespace, keys, purpose)
        except (sqlite3.Error, OSError):
            self.failures += 1

    @contextmanager
    def lock(self, namespace: str, keys: Sequence[str], timeout: float) -> Iterator[None]:
        # Keep a same-process single flight even when the lock directory is unavailable.
        with ExitStack() as stack:
            deadline = time.monotonic() + timeout
            for stripe in sorted(
                {int(self.cache.key(namespace + key)[:8], 16) % 64 for key in keys}
            ):
                flight = self.flights[stripe]
                if not flight.acquire(timeout=max(0, deadline - time.monotonic())):
                    raise ProviderTimeout("EMBEDDING_CACHE_WAIT_TIMEOUT")
                stack.callback(flight.release)
            manager = self.cache.lock(namespace, keys, max(0, deadline - time.monotonic()))
            try:
                manager.__enter__()
            except OSError:
                self.failures += 1
                yield
            else:
                try:
                    yield
                finally:
                    try:
                        manager.__exit__(None, None, None)
                    except OSError:
                        self.failures += 1
