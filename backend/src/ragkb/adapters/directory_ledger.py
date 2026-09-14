"""Local SQLite manifest hints; authoritative document state remains in the repository."""

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from filelock import FileLock


class SQLiteDirectorySyncLedger:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS sync_records "
                "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def get(self, key: str) -> dict[str, Any]:
        with closing(self._db()) as db:
            row = db.execute("SELECT payload FROM sync_records WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def put(self, key: str, value: dict[str, Any]) -> None:
        with closing(self._db()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO sync_records VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_many(self, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        with closing(self._db()) as db:
            for offset in range(0, len(keys), 900):
                batch = keys[offset : offset + 900]
                rows = db.execute(
                    "SELECT key,payload FROM sync_records WHERE key IN ("  # noqa: S608 -- placeholders only
                    + ",".join("?" for _ in batch)
                    + ")",
                    batch,
                ).fetchall()  # noqa: S608 -- placeholders only
                result.update((key, json.loads(payload)) for key, payload in rows)
        return result

    def put_many(self, records: dict[str, dict[str, Any]]) -> None:
        with closing(self._db()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO sync_records VALUES (?,?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in records.items()],
            )

    def entries(self, prefix: str) -> dict[str, dict[str, Any]]:
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT key,payload FROM sync_records WHERE substr(key,1,?)=?",
                (len(prefix), prefix),
            ).fetchall()
        return {key: json.loads(payload) for key, payload in rows}

    def snapshots(self, scope: str) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT payload FROM sync_records WHERE key LIKE ?", ("root:" + scope + ":%",)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def lock(self, scope: str) -> FileLock:
        return FileLock(str(self.path) + "." + scope + ".lock", timeout=30)
