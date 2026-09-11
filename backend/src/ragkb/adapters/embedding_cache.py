"""Durable vector cache; cache identities contain hashes, never source text."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from ragkb.domain.errors import ProviderTimeout


class SQLiteEmbeddingCache:
    """Disposable local cache shared by API and worker on a single-instance host."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_dir = self.path.parent / (self.path.name + ".locks")
        self.lock_dir.mkdir(exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS vectors (
                namespace TEXT NOT NULL, input_hash TEXT NOT NULL,
                dimension INTEGER NOT NULL, vector TEXT NOT NULL, checksum TEXT NOT NULL,
                PRIMARY KEY(namespace, input_hash))""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @contextmanager
    def lock(self, namespace: str, keys: Sequence[str], timeout: float) -> Iterator[None]:
        # Globally ordered locks avoid deadlocks for overlapping batches. Different
        # texts can still be embedded concurrently; no SQLite transaction spans HTTP.
        deadline = time.monotonic() + timeout
        try:
            with ExitStack() as stack:
                # Bound lock files for large corpora; colliding stripes only wait,
                # while cache identities still use the complete SHA-256 digest.
                for name in sorted({self.key(namespace + ":" + key)[:3] for key in keys}):
                    stack.enter_context(
                        FileLock(
                            str(self.lock_dir / (name + ".lock")),
                            timeout=max(0, deadline - time.monotonic()),
                        )
                    )
                yield
        except Timeout as error:
            raise ProviderTimeout("EMBEDDING_CACHE_WAIT_TIMEOUT") from error

    def get(self, namespace: str, key: str, dimension: int) -> list[float] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT vector, checksum FROM vectors WHERE namespace=? AND input_hash=? "
                "AND dimension=?",
                (namespace, key, dimension),
            ).fetchone()
        if row is None or self.key(row[0]) != row[1]:
            return None
        try:
            value = json.loads(row[0])
            if not isinstance(value, list) or len(value) != dimension:
                return None
            if any(type(x) not in (float, int) or not math.isfinite(x) for x in value):
                return None
            return [float(x) for x in value]
        except (ValueError, TypeError):
            return None

    def put(self, namespace: str, values: dict[str, list[float]], dimension: int) -> None:
        records = []
        for key, vector in values.items():
            if len(vector) != dimension or not all(math.isfinite(x) for x in vector):
                raise ValueError("EMBEDDING_CACHE_VECTOR_INVALID")
            encoded = json.dumps(vector, separators=(",", ":"), allow_nan=False)
            records.append((namespace, key, dimension, encoded, self.key(encoded)))
        with closing(self._connect()) as db, db:
            db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?)", records)
