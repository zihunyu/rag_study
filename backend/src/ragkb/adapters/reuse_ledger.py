"""Shared API/Worker SQLite receipts; each completed batch survives later failure."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class SQLiteReuseLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reuse_tasks (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reuse_attempts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, metadata TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, state TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS reuse_task_attempts ON reuse_attempts(task_id);
                CREATE TABLE IF NOT EXISTS reuse_events (
                    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS reuse_attempt_events ON reuse_events(attempt_id);
                CREATE TABLE IF NOT EXISTS reuse_rollups (
                    task_id TEXT PRIMARY KEY, data TEXT NOT NULL, finished REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS reuse_finished ON reuse_attempts(finished);
                CREATE INDEX IF NOT EXISTS reuse_rollup_age ON reuse_rollups(finished);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=0.2)
        db.row_factory = sqlite3.Row
        return db

    def begin(self, task_id: str, attempt_id: str, metadata: dict[str, Any]) -> None:
        identity = {
            k: metadata.get(k, "")
            for k in (
                "kind",
                "tenant_id",
                "space_id",
                "document_id",
                "document_version_id",
                "user_id",
            )
        }
        encoded = json.dumps(identity, sort_keys=True)
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO reuse_tasks VALUES (?, ?)", (task_id, encoded))
            if (
                db.execute("SELECT metadata FROM reuse_tasks WHERE id=?", (task_id,)).fetchone()[0]
                != encoded
            ):
                raise ValueError("REUSE_TASK_IDENTITY_MISMATCH")
            db.execute(
                "INSERT INTO reuse_attempts VALUES (?, ?, ?, ?, NULL, 'running')",
                (attempt_id, task_id, json.dumps(metadata), time.time()),
            )

    def event(self, attempt_id: str, event_id: str, kind: str, data: dict[str, Any]) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO reuse_events SELECT ?, ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM reuse_attempts WHERE id=?) ON CONFLICT(id) "
                "DO UPDATE SET data=excluded.data "
                "WHERE reuse_events.attempt_id=excluded.attempt_id "
                "AND reuse_events.kind=excluded.kind",
                (event_id, attempt_id, kind, json.dumps(data), attempt_id),
            )

    def finish(self, attempt_id: str, state: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE reuse_attempts SET state=?, finished=? WHERE id=?",
                (state, time.time(), attempt_id),
            )

    def identity(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT metadata FROM reuse_tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def report(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as db:
            db.execute("BEGIN")  # One snapshot for attempts and their concurrent event writes.
            attempts = db.execute(
                "SELECT id, state, started, finished FROM reuse_attempts "
                "WHERE task_id=? ORDER BY started",
                (task_id,),
            ).fetchall()
            archived = db.execute(
                "SELECT data FROM reuse_rollups WHERE task_id=?", (task_id,)
            ).fetchone()
            events = db.execute(
                "SELECT e.kind, e.data FROM reuse_events e JOIN reuse_attempts a "
                "ON a.id=e.attempt_id WHERE a.task_id=?",
                (task_id,),
            ).fetchall()
        rollup = json.loads(archived[0]) if archived else {}
        if not attempts and not rollup:
            return {"available": False, "task_id": task_id}
        totals = event_totals(events)
        for key in totals:
            totals[key] += int(rollup.get(key, 0))
        unavailable = any(row["kind"] == "statistics_unavailable" for row in events) or rollup.get(
            "statistics_unavailable", False
        )
        return {
            "available": not unavailable,
            "complete": not unavailable
            and rollup.get("complete", True)
            and not totals["unknown_http_outcomes"]
            and all(row["finished"] is not None for row in attempts),
            "statistics_unavailable": unavailable,
            "revision": "task-reuse-v1",
            "task_id": task_id,
            **totals,
            "attempt_count": len(attempts) + rollup.get("attempt_count", 0),
            "archived_attempt_count": rollup.get("attempt_count", 0),
            "detail_retention": "finished_attempts_rolled_up",
            "attempts": [dict(row) for row in attempts],
        }

    def maintain(
        self,
        *,
        raw_days: int = 90,
        summary_days: int = 730,
        max_finished_attempts: int = 100000,
        now: float | None = None,
        batch_size: int = 200,
    ) -> dict[str, int]:
        now = time.time() if now is None else now
        archived = expired = 0
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM reuse_attempts WHERE finished IS NOT NULL"
            ).fetchone()[0]
            rows = db.execute(
                "SELECT id,task_id,finished FROM reuse_attempts "
                "WHERE finished IS NOT NULL ORDER BY finished LIMIT ?",
                (batch_size,),
            ).fetchall()
            for attempt in rows:
                if attempt["finished"] >= now - raw_days * 86400 and count <= max_finished_attempts:
                    break
                events = db.execute(
                    "SELECT kind,data FROM reuse_events WHERE attempt_id=?", (attempt["id"],)
                ).fetchall()
                totals = event_totals(events)
                old = db.execute(
                    "SELECT data,finished FROM reuse_rollups WHERE task_id=?", (attempt["task_id"],)
                ).fetchone()
                rollup = json.loads(old[0]) if old else {}
                for key, value in totals.items():
                    rollup[key] = int(rollup.get(key, 0)) + value
                rollup["attempt_count"] = rollup.get("attempt_count", 0) + 1
                rollup["statistics_unavailable"] = rollup.get(
                    "statistics_unavailable", False
                ) or any(r["kind"] == "statistics_unavailable" for r in events)
                rollup["complete"] = (
                    not rollup["statistics_unavailable"] and not rollup["unknown_http_outcomes"]
                )
                db.execute(
                    "INSERT OR REPLACE INTO reuse_rollups VALUES (?,?,?)",
                    (
                        attempt["task_id"],
                        json.dumps(rollup),
                        max(attempt["finished"], old[1] if old else 0),
                    ),
                )
                db.execute("DELETE FROM reuse_events WHERE attempt_id=?", (attempt["id"],))
                db.execute("DELETE FROM reuse_attempts WHERE id=?", (attempt["id"],))
                archived += 1
                count -= 1
            summaries = db.execute("SELECT COUNT(*) FROM reuse_rollups").fetchone()[0]
            rows = db.execute(
                "SELECT task_id,finished FROM reuse_rollups WHERE NOT EXISTS "
                "(SELECT 1 FROM reuse_attempts a WHERE a.task_id=reuse_rollups.task_id) "
                "ORDER BY finished LIMIT ?",
                (batch_size,),
            ).fetchall()
            for row in rows:
                if (
                    row["finished"] >= now - summary_days * 86400
                    and summaries <= max_finished_attempts
                ):
                    break
                db.execute("DELETE FROM reuse_rollups WHERE task_id=?", (row["task_id"],))
                db.execute("DELETE FROM reuse_tasks WHERE id=?", (row["task_id"],))
                expired += 1
                summaries -= 1
        return {"archived_attempts": archived, "expired_summaries": expired}


def event_totals(events: list[Any]) -> dict[str, int]:
    totals = dict.fromkeys(
        (
            "requested_vectors",
            "cache_reused_vectors",
            "duplicate_reused_vectors",
            "missing_vectors",
            "generated_vectors",
            "embedding_batches",
            "http_attempts",
            "embedding_http_attempts",
            "failed_http_attempts",
            "unknown_http_outcomes",
            "input_tokens",
            "output_tokens",
            "unknown_usage_attempts",
        ),
        0,
    )
    for row in events:
        data = json.loads(row["data"])
        if row["kind"] == "embedding":
            for key in totals.keys() & data.keys():
                totals[key] += int(data[key])
        elif row["kind"] == "http":
            # A process may die between dispatch and receipt. Do not label
            # an unresolved dispatch as a known paid/successful request.
            if data.get("pending"):
                totals["unknown_http_outcomes"] += 1
                continue
            totals["http_attempts"] += 1
            totals["embedding_http_attempts"] += int(data.get("embedding", False))
            totals["failed_http_attempts"] += int(not data.get("success", False))
            usage = data.get("usage", {})
            source = usage.get("prompt_tokens", usage.get("input_tokens"))
            output = usage.get("completion_tokens", usage.get("output_tokens"))
            if type(source) is int and source >= 0:
                totals["input_tokens"] += source
            if type(output) is int and output >= 0:
                totals["output_tokens"] += output
            if type(source) is not int or (not data.get("embedding") and type(output) is not int):
                totals["unknown_usage_attempts"] += 1
    return totals
