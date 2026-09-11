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
                "INSERT INTO reuse_events VALUES (?, ?, ?, ?) ON CONFLICT(id) "
                "DO UPDATE SET data=excluded.data "
                "WHERE reuse_events.attempt_id=excluded.attempt_id "
                "AND reuse_events.kind=excluded.kind",
                (event_id, attempt_id, kind, json.dumps(data)),
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
            events = db.execute(
                "SELECT e.kind, e.data FROM reuse_events e JOIN reuse_attempts a "
                "ON a.id=e.attempt_id WHERE a.task_id=?",
                (task_id,),
            ).fetchall()
        if not attempts:
            return {"available": False, "task_id": task_id}
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
                if type(source) is not int or (
                    not data.get("embedding") and type(output) is not int
                ):
                    totals["unknown_usage_attempts"] += 1
        return {
            "available": not any(row["kind"] == "statistics_unavailable" for row in events),
            "complete": not any(row["kind"] == "statistics_unavailable" for row in events)
            and not totals["unknown_http_outcomes"]
            and all(row["finished"] is not None for row in attempts),
            "statistics_unavailable": any(
                row["kind"] == "statistics_unavailable" for row in events
            ),
            "revision": "task-reuse-v1",
            "task_id": task_id,
            **totals,
            "attempt_count": len(attempts),
            "attempts": [dict(row) for row in attempts],
        }
