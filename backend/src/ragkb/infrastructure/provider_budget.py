"""SQLite persistence for the fail-closed real-provider budget ledger."""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from ragkb.application.provider_budget import (
    ProviderBudgetExceeded,
    ProviderBudgetLimits,
    ProviderBudgetUsage,
)


class SQLiteProviderBudgetLedger:
    revision = "sqlite-provider-budget-ledger:v1"

    def __init__(self, path: Path, limits: ProviderBudgetLimits) -> None:
        self.path = path.resolve()
        self.limits = limits
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    provider_role TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _usage(connection: sqlite3.Connection) -> ProviderBudgetUsage:
        row = connection.execute(
            """
            SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM provider_budget_reservations
            """
        ).fetchone()
        return ProviderBudgetUsage(
            calls=int(row["calls"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
        )

    def reserve(
        self, provider_role: str, operation: str, input_tokens: int, output_tokens: int
    ) -> str:
        if not provider_role or not operation or input_tokens < 0 or output_tokens < 0:
            raise ValueError("provider budget reservation is invalid")
        reservation_id = str(uuid.uuid4())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            usage = self._usage(connection)
            if (
                usage.calls + 1 > self.limits.max_calls
                or usage.input_tokens + input_tokens > self.limits.max_input_tokens
                or usage.output_tokens + output_tokens > self.limits.max_output_tokens
            ):
                raise ProviderBudgetExceeded("BUDGET_EXHAUSTED")
            now = time.time()
            connection.execute(
                """
                INSERT INTO provider_budget_reservations(
                    reservation_id, provider_role, operation, input_tokens,
                    output_tokens, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (
                    reservation_id,
                    provider_role,
                    operation,
                    input_tokens,
                    output_tokens,
                    now,
                    now,
                ),
            )
            connection.commit()
            return reservation_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete(
        self, reservation_id: str, *, actual_input_tokens: int, actual_output_tokens: int
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT input_tokens, output_tokens, state
                FROM provider_budget_reservations WHERE reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if row is None or str(row["state"]) != "RESERVED":
                raise KeyError("PROVIDER_BUDGET_RESERVATION_NOT_OPEN")
            if actual_input_tokens > int(row["input_tokens"]):
                raise ProviderBudgetExceeded("ACTUAL_INPUT_EXCEEDED_RESERVATION")
            if actual_output_tokens > int(row["output_tokens"]):
                raise ProviderBudgetExceeded("ACTUAL_OUTPUT_EXCEEDED_RESERVATION")
            connection.execute(
                """
                UPDATE provider_budget_reservations
                SET input_tokens = ?, output_tokens = ?, state = 'COMPLETED', updated_at = ?
                WHERE reservation_id = ?
                """,
                (actual_input_tokens, actual_output_tokens, time.time(), reservation_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail(self, reservation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE provider_budget_reservations SET state = 'FAILED', updated_at = ?
                WHERE reservation_id = ? AND state = 'RESERVED'
                """,
                (time.time(), reservation_id),
            )

    def usage(self) -> ProviderBudgetUsage:
        with self._connect() as connection:
            return self._usage(connection)

    def usage_by_role(self) -> dict[str, ProviderBudgetUsage]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider_role, COUNT(*) AS calls,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens
                FROM provider_budget_reservations GROUP BY provider_role
                """
            ).fetchall()
        return {
            str(row["provider_role"]): ProviderBudgetUsage(
                int(row["calls"]), int(row["input_tokens"]), int(row["output_tokens"])
            )
            for row in rows
        }

    def safe_report(self) -> dict[str, object]:
        usage = self.usage()
        return {
            "revision": self.revision,
            "limits": {
                "provider_calls": self.limits.max_calls,
                "input_tokens": self.limits.max_input_tokens,
                "output_tokens": self.limits.max_output_tokens,
            },
            "usage": {
                "provider_calls": usage.calls,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
            "usage_by_role": {
                role: {
                    "provider_calls": value.calls,
                    "input_tokens": value.input_tokens,
                    "output_tokens": value.output_tokens,
                }
                for role, value in self.usage_by_role().items()
            },
            "automatic_retries": 0,
            "secret_values_output": False,
        }
