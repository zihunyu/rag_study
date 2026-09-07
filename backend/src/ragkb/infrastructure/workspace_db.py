"""Small parameterized SQL boundary for additive workspace data."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from ragkb.infrastructure.sqlite import SQLiteDatabase


class WorkspaceDB:
    def __init__(self, database: SQLiteDatabase, control: Any = None) -> None:
        self.database = database
        self.control = control
        self.mysql = control is not None

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        if self.mysql:
            connection = self.control.connect()
            try:
                connection.begin()
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        else:
            with self.database.transaction(immediate=True) as connection:
                yield connection

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.mysql:
            connection = self.control.connect()
            try:
                yield connection
            finally:
                connection.close()
        else:
            with self.database.connect() as connection:
                yield connection

    def execute(self, connection: Any, sql: str, values: Sequence[Any] = ()) -> Any:
        cursor = connection.cursor()
        cursor.execute(sql.replace("?", "%s") if self.mysql else sql, tuple(values))
        return cursor

    def rows(self, connection: Any, sql: str, values: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self.execute(connection, sql, values)
        names = [column[0] for column in cursor.description]
        return [
            dict(zip(names, row, strict=True)) if isinstance(row, tuple) else dict(row)
            for row in cursor.fetchall()
        ]

    def one(self, connection: Any, sql: str, values: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.rows(connection, sql, values)
        return rows[0] if rows else None
