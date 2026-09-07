"""Incremental SQLite ledger for visual progress, immutable plans, reuse and usage.

This sidecar accompanies the existing local artifact store in both runtime profiles;
document ownership and publication remain in the existing repository/control plane.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any


class VisualLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS visual_metadata (
                    namespace TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, updated REAL NOT NULL,
                    PRIMARY KEY(namespace, identity));
                CREATE TABLE IF NOT EXISTS visual_assets (
                    version_id TEXT NOT NULL, asset_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    payload TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(version_id, asset_id));
                CREATE INDEX IF NOT EXISTS visual_asset_order ON visual_assets(version_id, ordinal);
                CREATE TABLE IF NOT EXISTS visual_cache (
                    scope TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
                    expires REAL NOT NULL, PRIMARY KEY(scope, identity));
                CREATE TABLE IF NOT EXISTS visual_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT, asset_id TEXT,
                    role TEXT NOT NULL, model TEXT NOT NULL, started REAL NOT NULL,
                    elapsed REAL NOT NULL, outcome TEXT NOT NULL, usage TEXT NOT NULL,
                    cost REAL, cached INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS visual_usage_version ON visual_usage(version_id, id);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=20000")
            with db:
                yield db

    def get(self, namespace: str, identity: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload,revision FROM visual_metadata WHERE namespace=? AND identity=?",
                (namespace, identity),
            ).fetchone()
        return {**json.loads(row["payload"]), "row_version": row["revision"]} if row else {}

    def put(
        self,
        namespace: str,
        identity: str,
        payload: dict[str, Any],
        *,
        expected: int | None = None,
        immutable: bool = False,
    ) -> int:
        clean = {k: v for k, v in payload.items() if k != "row_version"}
        serialized = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload,revision FROM visual_metadata WHERE namespace=? AND identity=?",
                (namespace, identity),
            ).fetchone()
            revision = row["revision"] if row else 0
            if immutable and row:
                if row["payload"] != serialized:
                    raise ValueError("VISUAL_PLAN_CONFLICT")
                return int(revision)
            if expected is not None and expected != revision:
                raise ValueError("VISUAL_REVISION_CONFLICT")
            db.execute(
                "INSERT INTO visual_metadata VALUES(?,?,?,?,?) ON CONFLICT(namespace,identity) "
                "DO UPDATE SET payload=excluded.payload,revision=excluded.revision,"
                "updated=excluded.updated",
                (namespace, identity, serialized, revision + 1, time.time()),
            )
            return int(revision + 1)

    def assets(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM visual_assets WHERE version_id=? "
                    "ORDER BY ordinal,asset_id",
                    (version_id,),
                )
            ]

    def upsert_asset(self, version_id: str, asset: dict[str, Any], *, run: str = "") -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if run:
                current = db.execute(
                    "SELECT payload FROM visual_metadata WHERE namespace='version' AND identity=?",
                    (version_id,),
                ).fetchone()
                if not current or json.loads(current[0]).get("run") != run:
                    raise ValueError("VISUAL_EXECUTION_SUPERSEDED")
            db.execute(
                "INSERT INTO visual_assets VALUES(?,?,?,?,?) ON CONFLICT(version_id,asset_id) "
                "DO UPDATE SET ordinal=excluded.ordinal,payload=excluded.payload,"
                "updated=excluded.updated",
                (
                    version_id,
                    asset["id"],
                    asset.get("ordinal", 0),
                    json.dumps(asset, ensure_ascii=False),
                    time.time(),
                ),
            )

    def cache_get(self, scope: str, identity: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM visual_cache WHERE scope=? AND identity=? AND expires>?",
                (scope, identity, time.time()),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, scope: str, identity: str, payload: dict[str, Any], ttl: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM visual_cache WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT OR REPLACE INTO visual_cache VALUES(?,?,?,?)",
                (
                    scope,
                    identity,
                    json.dumps(payload, ensure_ascii=False),
                    time.time() + ttl,
                ),
            )

    def usage(
        self,
        version_id: str = "",
        asset_id: str = "",
        *,
        role: str,
        model: str,
        started: float,
        outcome: str,
        usage: dict[str, Any] | None = None,
        cost: float | None = None,
        cached: bool = False,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO visual_usage(version_id,asset_id,role,model,started,elapsed,"
                "outcome,usage,cost,cached) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    asset_id,
                    role,
                    model,
                    started,
                    max(0, time.time() - started),
                    outcome,
                    json.dumps(usage or {}),
                    cost,
                    int(cached),
                ),
            )

    def usage_report(self, version_id: str = "") -> dict[str, Any]:
        with self.connect() as db:
            totals = dict(
                db.execute(
                    """
                SELECT SUM(CASE WHEN cached=0 THEN 1 ELSE 0 END) call_count,
                    SUM(cached) cache_hits,
                    SUM(CASE WHEN cached=0 THEN COALESCE(json_extract(usage,'$.prompt_tokens'),0)
                        ELSE 0 END) input_tokens,
                    SUM(CASE WHEN cached=0 THEN
                        COALESCE(json_extract(usage,'$.completion_tokens'),0)
                        ELSE 0 END) output_tokens,
                    SUM(COALESCE(cost,0)) known_cost_cny,
                    SUM(CASE WHEN cached=0 AND cost IS NULL THEN 1 ELSE 0 END) unpriced_calls,
                    SUM(elapsed) total_elapsed_seconds
                FROM visual_usage WHERE (?='' OR version_id=?)
            """,
                    (version_id, version_id),
                ).fetchone()
            )
            rows = db.execute(
                "SELECT * FROM visual_usage WHERE (?='' OR version_id=?) "
                "ORDER BY id DESC LIMIT 2000",
                (version_id, version_id),
            ).fetchall()
        records = [{**dict(row), "usage": json.loads(row["usage"])} for row in rows]
        return {
            "records": records,
            "record_limit": 2000,
            **{key: value or 0 for key, value in totals.items()},
        }

    def purge_artifacts(self, keys: list[str]) -> None:
        """Remove derived content tied to deleted original-image lineage, plus reuse entries."""
        if not keys:
            return
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            versions: set[str] = set()
            for key in keys:
                versions.update(
                    row[0]
                    for row in db.execute(
                        "SELECT version_id FROM visual_assets WHERE "
                        "json_extract(payload,'$.storage_key')=?",
                        (key,),
                    )
                )
            for version in versions:
                db.execute("DELETE FROM visual_assets WHERE version_id=?", (version,))
                db.execute(
                    "DELETE FROM visual_metadata WHERE identity=? OR "
                    "json_extract(payload,'$.base_version_id')=?",
                    (version, version),
                )
                db.execute(
                    "DELETE FROM visual_cache WHERE json_extract(payload,'$._source_version_id')=?",
                    (version,),
                )
