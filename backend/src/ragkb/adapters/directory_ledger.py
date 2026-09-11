"""Local SQLite manifest hints; authoritative document state remains in the repository."""

import json
import sqlite3
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

    def snapshots(self, scope: str) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT payload FROM sync_records WHERE key LIKE ?", ("root:" + scope + ":%",)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def lock(self, scope: str) -> FileLock:
        return FileLock(str(self.path) + "." + scope + ".lock", timeout=30)
