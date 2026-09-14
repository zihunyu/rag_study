"""Durable vector cache; cache identities contain hashes, never source text."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

from filelock import FileLock, Timeout

from ragkb.domain.errors import ProviderTimeout

cache_purpose: ContextVar[str] = ContextVar("embedding_cache_purpose", default="document")


class SQLiteEmbeddingCache:
    """Disposable local cache shared by API and worker on a single-instance host."""

    def __init__(
        self,
        path: Path,
        *,
        document_days: int = 365,
        query_days: int = 30,
        document_bytes: int = 16 * 1024**3,
        query_bytes: int = 512 * 1024**2,
    ) -> None:
        self.path = path.resolve()
        self.limits = {
            "document": (document_days, document_bytes),
            "query": (query_days, query_bytes),
        }
        self.lock_dir = self.path.parent / (self.path.name + ".locks")
        self.initialized = False
        try:
            self._initialize()
        except (sqlite3.Error, OSError):
            # Auxiliary storage can recover on a later write; API/Worker startup
            # must not depend on a disposable copy of vectors being writable.
            pass

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS vectors (
                namespace TEXT NOT NULL, input_hash TEXT NOT NULL,
                dimension INTEGER NOT NULL, vector TEXT NOT NULL, checksum TEXT NOT NULL,
                PRIMARY KEY(namespace, input_hash))""")
            migrated = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_retention'"
            ).fetchone()
            db.execute("""CREATE TABLE IF NOT EXISTS vector_retention (
                namespace TEXT NOT NULL, input_hash TEXT NOT NULL, purpose TEXT NOT NULL,
                accessed REAL NOT NULL, bytes INTEGER NOT NULL,
                PRIMARY KEY(namespace, input_hash))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS vector_retention_age "
                "ON vector_retention(purpose, accessed)"
            )
            # Old vectors retain the document policy; upgrading never invalidates them.
            if not migrated:
                db.execute(
                    "INSERT OR IGNORE INTO vector_retention "
                    "SELECT namespace,input_hash,'document',?,length(vector)+256 FROM vectors",
                    (time.time(),),
                )
        self.initialized = True

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=0.2)

    def record_access(self, namespace: str, keys: Sequence[str], purpose: str) -> None:
        if purpose not in self.limits:
            raise ValueError("EMBEDDING_CACHE_PURPOSE_INVALID")
        now = time.time()
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT INTO vector_retention SELECT namespace,input_hash,?,?,length(vector)+256 "
                "FROM vectors WHERE namespace=? AND input_hash=? "
                "ON CONFLICT(namespace,input_hash) DO UPDATE SET "
                "accessed=excluded.accessed, purpose=CASE WHEN vector_retention.purpose='document' "
                "THEN 'document' ELSE excluded.purpose END "
                "WHERE vector_retention.accessed<? OR vector_retention.purpose!=excluded.purpose",
                [(purpose, now, namespace, key, now - 3600) for key in dict.fromkeys(keys)],
            )

    def maintain(self, *, now: float | None = None, batch_size: int = 2000) -> dict[str, int]:
        """Bounded maintenance; freed SQLite pages are reused, no corpus/index rows touched."""
        now = time.time() if now is None else now
        removed = {}
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for purpose, (days, capacity) in self.limits.items():
                total = db.execute(
                    "SELECT COALESCE(SUM(bytes),0) FROM vector_retention WHERE purpose=?",
                    (purpose,),
                ).fetchone()[0]
                rows = db.execute(
                    "SELECT namespace,input_hash,accessed,bytes FROM vector_retention "
                    "WHERE purpose=? ORDER BY accessed,namespace,input_hash LIMIT ?",
                    (purpose, batch_size),
                ).fetchall()
                victims = []
                for namespace, key, accessed, size in rows:
                    if accessed < now - days * 86400 or total > capacity:
                        victims.append((namespace, key))
                        total -= size
                db.executemany("DELETE FROM vectors WHERE namespace=? AND input_hash=?", victims)
                db.executemany(
                    "DELETE FROM vector_retention WHERE namespace=? AND input_hash=?", victims
                )
                removed[purpose] = len(victims)
        return removed

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
        return self.get_many(namespace, [key], dimension).get(key)

    def get_many(
        self, namespace: str, keys: Sequence[str], dimension: int
    ) -> dict[str, list[float]]:
        """Read warm entries without acquiring provider single-flight locks."""
        unique = list(dict.fromkeys(keys))
        result = {}
        with closing(self._connect()) as db:
            for start in range(0, len(unique), 900):
                batch = unique[start : start + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(
                    "SELECT input_hash, vector, checksum FROM vectors WHERE namespace=? "  # noqa: S608 -- only placeholder counts are interpolated
                    "AND dimension=? AND input_hash IN (" + placeholders + ")",
                    (namespace, dimension, *batch),
                ).fetchall()
                for key, encoded, checksum in rows:
                    value = self._decode(encoded, checksum, dimension)
                    if value is not None:
                        result[key] = value
        return result

    def _decode(self, encoded: str, checksum: str, dimension: int) -> list[float] | None:
        if not isinstance(encoded, str) or not isinstance(checksum, str):
            return None
        if self.key(encoded) != checksum:
            return None
        try:
            value = json.loads(encoded)
            if not isinstance(value, list) or len(value) != dimension:
                return None
            if any(type(x) not in (float, int) or not math.isfinite(x) for x in value):
                return None
            return [float(x) for x in value]
        except (ValueError, TypeError, OverflowError):
            return None

    def put(self, namespace: str, values: dict[str, list[float]], dimension: int) -> None:
        if cache_purpose.get() not in self.limits:
            raise ValueError("EMBEDDING_CACHE_PURPOSE_INVALID")
        if not self.initialized:
            self._initialize()
        records = []
        for key, vector in values.items():
            if len(vector) != dimension or not all(math.isfinite(x) for x in vector):
                raise ValueError("EMBEDDING_CACHE_VECTOR_INVALID")
            encoded = json.dumps(vector, separators=(",", ":"), allow_nan=False)
            records.append((namespace, key, dimension, encoded, self.key(encoded)))
        with closing(self._connect()) as db, db:
            db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?)", records)
            db.executemany(
                "INSERT INTO vector_retention VALUES (?,?,?,?,?) "
                "ON CONFLICT(namespace,input_hash) DO UPDATE SET accessed=excluded.accessed, "
                "bytes=excluded.bytes, purpose=CASE WHEN vector_retention.purpose='document' "
                "THEN 'document' ELSE excluded.purpose END",
                [
                    (namespace, key, cache_purpose.get(), time.time(), len(encoded) + 256)
                    for _, key, _, encoded, _ in records
                ],
            )
