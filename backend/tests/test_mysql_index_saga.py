from __future__ import annotations

from typing import Any

import pytest
from ragkb.adapters.mysql_index_saga import MySQLIndexSagaLedger


def _record(chunk_id: str, checksum_char: str) -> dict[str, object]:
    return {"chunk_id": chunk_id, "content_checksum": checksum_char * 64}


class _SagaDatabase:
    def __init__(self) -> None:
        self.job: dict[str, Any] | None = None
        self.batches: dict[int, dict[str, Any]] = {}
        self.commits = 0
        self.rollbacks = 0
        self.force_ready_rowcount_zero = False


class _Cursor:
    def __init__(self, database: _SagaDatabase) -> None:
        self.database = database
        self.one: Any = None
        self.all: list[Any] = []
        self.rowcount = 0

    def execute(self, statement: str, parameters=()) -> None:
        sql = " ".join(statement.split())
        self.one = None
        self.all = []
        self.rowcount = 0
        if sql.startswith("SELECT tenant_id") and "FROM index_jobs_v3" in sql:
            self.one = dict(self.database.job) if self.database.job is not None else None
        elif sql.startswith("INSERT INTO index_jobs_v3"):
            (
                index_job_id,
                tenant_id,
                space_id,
                document_id,
                version_id,
                generation_id,
                expected_count,
                expected_checksum,
                expected_manifest_json,
            ) = parameters
            self.database.job = {
                "index_job_id": index_job_id,
                "tenant_id": tenant_id,
                "space_id": space_id,
                "document_id": document_id,
                "document_version_id": version_id,
                "generation_id": generation_id,
                "expected_count": expected_count,
                "expected_checksum": expected_checksum,
                "expected_manifest_json": expected_manifest_json,
                "state": "BUILDING",
                "attempt_number": 1,
                "error_code": None,
            }
            self.rowcount = 1
        elif sql.startswith("DELETE FROM index_batches_v3"):
            self.database.batches.clear()
            self.rowcount = 1
        elif sql.startswith("UPDATE index_jobs_v3 SET expected_count"):
            assert self.database.job is not None
            expected_count, checksum, manifest, _, attempt = parameters
            if int(self.database.job["attempt_number"]) == int(attempt):
                self.database.job.update(
                    expected_count=expected_count,
                    expected_checksum=checksum,
                    expected_manifest_json=manifest,
                    state="BUILDING",
                    error_code=None,
                    attempt_number=int(attempt) + 1,
                )
                self.rowcount = 1
        elif sql.startswith("SELECT state, attempt_number FROM index_jobs_v3"):
            if self.database.job is not None:
                self.one = {
                    "state": self.database.job["state"],
                    "attempt_number": self.database.job["attempt_number"],
                }
        elif sql.startswith("SELECT chunk_manifest_json"):
            batch_number = int(parameters[1])
            batch = self.database.batches.get(batch_number)
            self.one = dict(batch) if batch is not None else None
        elif sql.startswith("INSERT INTO index_batches_v3"):
            _, batch_number, attempt, manifest, checksum, vector, control = parameters
            self.database.batches[int(batch_number)] = {
                "batch_number": int(batch_number),
                "attempt_number": int(attempt),
                "chunk_manifest_json": manifest,
                "batch_checksum": checksum,
                "vector_confirmed": bool(vector),
                "control_confirmed": bool(control),
            }
            self.rowcount = 1
        elif sql.startswith("UPDATE index_batches_v3"):
            vector, control, _, batch_number, attempt = parameters
            batch = self.database.batches[int(batch_number)]
            if int(batch["attempt_number"]) == int(attempt):
                batch.update(vector_confirmed=bool(vector), control_confirmed=bool(control))
                self.rowcount = 1
        elif sql.startswith("SELECT expected_count") and "FROM index_jobs_v3" in sql:
            self.one = dict(self.database.job) if self.database.job is not None else None
        elif sql.startswith("SELECT batch_number"):
            self.all = [dict(self.database.batches[key]) for key in sorted(self.database.batches)]
        elif sql.startswith("UPDATE index_jobs_v3 SET state='READY'"):
            assert self.database.job is not None
            _, attempt = parameters
            if (
                not self.database.force_ready_rowcount_zero
                and self.database.job["state"] == "BUILDING"
                and int(self.database.job["attempt_number"]) == int(attempt)
            ):
                self.database.job.update(state="READY", error_code=None)
                self.rowcount = 1
        elif sql.startswith("SELECT state FROM index_jobs_v3"):
            if self.database.job is not None:
                self.one = {"state": self.database.job["state"]}
        elif sql.startswith("UPDATE index_jobs_v3 SET state='FAILED'"):
            assert self.database.job is not None
            if self.database.job["state"] == "BUILDING":
                self.database.job.update(state="FAILED", error_code=parameters[0])
                self.rowcount = 1
        else:  # pragma: no cover - protects the fake from silently accepting new SQL
            raise AssertionError(sql)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all


class _Connection:
    def __init__(self, database: _SagaDatabase) -> None:
        self.database = database

    def cursor(self) -> _Cursor:
        return _Cursor(self.database)

    def commit(self) -> None:
        self.database.commits += 1

    def rollback(self) -> None:
        self.database.rollbacks += 1

    def close(self) -> None:
        pass


class _Control:
    def __init__(self) -> None:
        self.database = _SagaDatabase()

    def connect(self) -> _Connection:
        return _Connection(self.database)


def _begin(ledger: MySQLIndexSagaLedger, records) -> str:
    return ledger.begin(
        tenant_id="tenant",
        space_id="space",
        document_id="document",
        document_version_id="version",
        generation_id="generation",
        records=records,
    )


def test_failed_attempt_is_reset_and_old_batches_are_not_reused() -> None:
    control = _Control()
    ledger = MySQLIndexSagaLedger(control)  # type: ignore[arg-type]
    records = (_record("c1", "a"), _record("c2", "b"), _record("c3", "c"))
    job_id = _begin(ledger, records)
    ledger.confirm_batch(job_id, 1, records[:2], vector=True, control=False)
    ledger.fail(job_id, "VECTOR_FAILED")

    assert _begin(ledger, records) == job_id
    assert control.database.job is not None
    assert control.database.job["state"] == "BUILDING"
    assert control.database.job["attempt_number"] == 2
    assert control.database.batches == {}

    ledger.confirm_batch(job_id, 1, records[:2], vector=True, control=True)
    ledger.confirm_batch(job_id, 2, records[2:], vector=True, control=True)
    ledger.mark_ready(job_id)

    assert ledger.is_ready(job_id) is True


def test_duplicate_batch_with_different_manifest_fails_closed() -> None:
    control = _Control()
    ledger = MySQLIndexSagaLedger(control)  # type: ignore[arg-type]
    records = (_record("c1", "a"), _record("c2", "b"))
    job_id = _begin(ledger, records)
    ledger.confirm_batch(job_id, 1, records[:1], vector=True, control=False)

    with pytest.raises(RuntimeError, match="BATCH_MANIFEST_CONFLICT"):
        ledger.confirm_batch(job_id, 1, records[1:], vector=False, control=True)


def test_building_manifest_change_starts_new_attempt_and_ready_manifest_is_immutable() -> None:
    control = _Control()
    ledger = MySQLIndexSagaLedger(control)  # type: ignore[arg-type]
    first = (_record("c1", "a"),)
    changed = (_record("c1", "b"),)
    job_id = _begin(ledger, first)
    ledger.confirm_batch(job_id, 1, first, vector=True, control=False)

    _begin(ledger, changed)

    assert control.database.job is not None
    assert control.database.job["attempt_number"] == 2
    assert control.database.batches == {}
    ledger.confirm_batch(job_id, 1, changed, vector=True, control=True)
    ledger.mark_ready(job_id)
    with pytest.raises(RuntimeError, match="READY_MANIFEST_CONFLICT"):
        _begin(ledger, first)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda database: database.batches.__setitem__(
                2, {**database.batches.pop(1), "batch_number": 2}
            ),
            "SEQUENCE",
        ),
        (
            lambda database: database.batches.__setitem__(
                2, {**database.batches[1], "batch_number": 2}
            ),
            "DUPLICATE_CONFIRMED_CHUNK_ID",
        ),
        (
            lambda database: database.batches[1].__setitem__("batch_checksum", "0" * 64),
            "BATCH_RECONCILIATION_FAILED",
        ),
    ],
)
def test_mark_ready_rejects_gaps_duplicates_and_checksum_drift(mutate, error: str) -> None:
    control = _Control()
    ledger = MySQLIndexSagaLedger(control)  # type: ignore[arg-type]
    record = _record("c1", "a")
    job_id = _begin(ledger, (record,))
    ledger.confirm_batch(job_id, 1, (record,), vector=True, control=True)
    mutate(control.database)

    with pytest.raises(RuntimeError, match=error):
        ledger.mark_ready(job_id)


def test_mark_ready_requires_exact_transition_rowcount() -> None:
    control = _Control()
    ledger = MySQLIndexSagaLedger(control)  # type: ignore[arg-type]
    record = _record("c1", "a")
    job_id = _begin(ledger, (record,))
    ledger.confirm_batch(job_id, 1, (record,), vector=True, control=True)
    control.database.force_ready_rowcount_zero = True

    with pytest.raises(RuntimeError, match="READY_CONCURRENT_UPDATE"):
        ledger.mark_ready(job_id)
