"""Incremental accounting and durable raw-call archives; lifetime totals never reset."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

FIELDS = (
    "call_count",
    "cache_hits",
    "input_tokens",
    "output_tokens",
    "known_cost_cny",
    "unpriced_calls",
    "total_elapsed_seconds",
)
AGGREGATES = """
    COALESCE(SUM(CASE WHEN cached=0 THEN 1 ELSE 0 END),0) call_count,
    COALESCE(SUM(cached),0) cache_hits,
    COALESCE(SUM(CASE WHEN cached=0 THEN COALESCE(json_extract(usage,'$.prompt_tokens'),0)
        ELSE 0 END),0) input_tokens,
    COALESCE(SUM(CASE WHEN cached=0 THEN COALESCE(json_extract(usage,'$.completion_tokens'),0)
        ELSE 0 END),0) output_tokens,
    COALESCE(SUM(COALESCE(cost,0)),0) known_cost_cny,
    COALESCE(SUM(CASE WHEN cached=0 AND cost IS NULL THEN 1 ELSE 0 END),0) unpriced_calls,
    COALESCE(SUM(elapsed),0) total_elapsed_seconds
"""


def rollup(db: sqlite3.Connection) -> int:
    last = int(db.execute("SELECT last_id FROM visual_usage_checkpoint WHERE id=1").fetchone()[0])
    maximum = int(db.execute("SELECT COALESCE(MAX(id),0) FROM visual_usage").fetchone()[0])
    if maximum <= last:
        return last
    window = " FROM visual_usage WHERE id>? AND id<=?"
    total = db.execute("SELECT " + AGGREGATES + window, (last, maximum)).fetchone()
    grouped = db.execute(
        "SELECT COALESCE(version_id,'') version_id,"
        + AGGREGATES
        + window
        + " GROUP BY COALESCE(version_id,'')",
        (last, maximum),
    ).fetchall()
    columns = ",".join(FIELDS)
    updates = ",".join(f"{f}=visual_usage_totals.{f}+excluded.{f}" for f in FIELDS)
    for scope, row in [("all", total), *(("version:" + r["version_id"], r) for r in grouped)]:
        db.execute(
            f"INSERT INTO visual_usage_totals(scope,{columns}) VALUES(?,?,?,?,?,?,?,?) "
            f"ON CONFLICT(scope) DO UPDATE SET {updates}",
            (scope, *(row[field] for field in FIELDS)),
        )
    db.execute("UPDATE visual_usage_checkpoint SET last_id=? WHERE id=1", (maximum,))
    return maximum


def totals(db: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM visual_usage_totals WHERE scope=?",
        ("version:" + version_id if version_id else "all",),
    ).fetchone()
    return {field: row[field] if row else 0 for field in FIELDS}


def archive(
    db: sqlite3.Connection, directory: Path, *, before: float, limit: int = 500
) -> dict[str, Any]:
    rollup(db)
    rows = db.execute(
        "SELECT * FROM visual_usage WHERE started<? ORDER BY id LIMIT ?",
        (before, min(500, max(1, limit))),
    ).fetchall()
    if not rows:
        return {"archived_count": 0}
    payload = (
        "\n".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in rows) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"calls-{rows[0]['id']}-{rows[-1]['id']}-{digest[:16]}.jsonl.gz"
    if target.exists():
        if gzip.decompress(target.read_bytes()) != payload:
            raise ValueError("VISUAL_USAGE_ARCHIVE_MISMATCH")
    else:
        with target.open("xb") as handle:
            handle.write(gzip.compress(payload, mtime=0))
            handle.flush()
            os.fsync(handle.fileno())
    # The archive is durably written before raw rows are removed in the caller's transaction.
    ids = [row["id"] for row in rows]
    db.executemany("DELETE FROM visual_usage WHERE id=?", [(identity,) for identity in ids])
    return {"archived_count": len(rows), "archive": target.name, "sha256": digest}
