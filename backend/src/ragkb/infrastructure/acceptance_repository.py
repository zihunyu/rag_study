"""Transactional acceptance state, portable between SQLite and MySQL."""

# ruff: noqa: S608 -- SQL suffixes are fixed FOR UPDATE; all values remain bound.

from __future__ import annotations

import json
import time
from typing import Any

from ragkb.application.acceptance_budget import AcceptancePaused
from ragkb.domain.acceptance import fingerprint
from ragkb.domain.ids import new_uuid7
from ragkb.domain.uploads import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ResourceNotFoundError,
)
from ragkb.infrastructure.workspace_db import WorkspaceDB


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def decoded(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: v for k, v in row.items() if k != "payload_json"},
        "payload": json.loads(row["payload_json"]),
    }


class AcceptanceRepository:
    def __init__(self, db: WorkspaceDB, tenant: str) -> None:
        self.db, self.tenant = db, tenant

    def lock(self) -> str:
        return " FOR UPDATE" if self.db.mysql else ""

    def cases(self, space: str) -> list[dict[str, Any]]:
        with self.db.connection() as c:
            return [
                decoded(r)
                for r in self.db.rows(
                    c,
                    "SELECT * FROM acceptance_cases WHERE tenant_id=? AND space_id=? "
                    "ORDER BY case_key LIMIT 1000",
                    (self.tenant, space),
                )
            ]

    def save_case(
        self,
        space: str,
        actor: str,
        payload: dict[str, Any],
        revision: int,
        identity: str = "",
    ) -> dict[str, Any]:
        identity = identity or fingerprint([self.tenant, space, payload["key"]])[:36]
        with self.db.transaction() as c:
            row = self.db.one(
                c, "SELECT * FROM acceptance_cases WHERE id=?" + self.lock(), (identity,)
            )
            if row and (row["tenant_id"] != self.tenant or row["space_id"] != space):
                raise ResourceNotFoundError(identity)
            if (row["revision"] if row else 0) != revision:
                raise OptimisticConcurrencyError(identity)
            if row and row["case_key"] != payload["key"]:
                raise IdempotencyConflictError("CASE_KEY_IMMUTABLE")
            now, value = time.time(), encode(payload)
            if row:
                self.db.execute(
                    c,
                    "UPDATE acceptance_cases SET revision=?,payload_json=?,updated_at=? WHERE id=?",
                    (revision + 1, value, now, identity),
                )
            else:
                self.db.execute(
                    c,
                    "INSERT INTO acceptance_cases "
                    "(id,tenant_id,space_id,case_key,revision,payload_json,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (identity, self.tenant, space, payload["key"], 1, value, now),
                )
            self.db.execute(
                c,
                "INSERT INTO acceptance_case_revisions "
                "(case_id,revision,payload_json,actor_id,created_at) VALUES(?,?,?,?,?)",
                (identity, revision + 1, value, actor, now),
            )
        return {"id": identity, "revision": revision + 1, "payload": payload}

    def revisions(self, space: str, identity: str) -> list[dict[str, Any]]:
        if identity not in {r["id"] for r in self.cases(space)}:
            raise ResourceNotFoundError(identity)
        with self.db.connection() as c:
            return [
                decoded(r)
                for r in self.db.rows(
                    c,
                    "SELECT * FROM acceptance_case_revisions WHERE case_id=? "
                    "ORDER BY revision DESC",
                    (identity,),
                )
            ]

    def create_run(
        self,
        space: str,
        actor: str,
        key: str,
        payload: dict[str, Any],
        limit: int,
    ) -> dict[str, Any]:
        identity = fingerprint([self.tenant, space, actor, key])[:36]
        now = time.time()
        with self.db.transaction() as c:
            row = self.db.one(
                c, "SELECT * FROM acceptance_runs WHERE id=?" + self.lock(), (identity,)
            )
            if row:
                if json.loads(row["payload_json"]) != payload or row["call_limit"] != limit:
                    raise IdempotencyConflictError("ACCEPTANCE_REQUEST_CHANGED")
                return decoded(row)
            self.db.execute(
                c,
                "INSERT INTO acceptance_runs "
                "(id,tenant_id,space_id,actor_id,request_key,payload_json,state,call_limit,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    self.tenant,
                    space,
                    actor,
                    key,
                    encode(payload),
                    "ready",
                    limit,
                    now,
                    now,
                ),
            )
        return self.run(space, identity)

    def recover(self) -> None:
        # Read candidates without locking active attempts. Writers always lock
        # the run before its attempts; recovery must use the same order. The old
        # attempts-first UPDATE/subquery deadlocked with ordinary status polling.
        with self.db.connection() as c:
            expired = self.db.rows(
                c,
                "SELECT id FROM acceptance_runs WHERE tenant_id=? AND state='running' "
                "AND lease_until<? ORDER BY id",
                (self.tenant, time.time()),
            )
        for candidate in expired:
            with self.db.transaction() as c:
                row = self.db.one(
                    c, "SELECT * FROM acceptance_runs WHERE id=?" + self.lock(), (candidate["id"],)
                )
                now = time.time()
                if not row or row["state"] != "running" or row["lease_until"] >= now:
                    continue  # The owner renewed the lease after the read.
                self.db.execute(
                    c,
                    "UPDATE acceptance_runs SET state='paused',reason='WORKER_INTERRUPTED',"
                    "execution_token='',updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                self.db.execute(
                    c,
                    "UPDATE acceptance_attempts SET state='incomplete',updated_at=? "
                    "WHERE run_id=? AND state='running'",
                    (now, row["id"]),
                )

    def run(self, space: str, identity: str) -> dict[str, Any]:
        self.recover()
        with self.db.connection() as c:
            row = self.db.one(
                c,
                "SELECT * FROM acceptance_runs WHERE id=? AND tenant_id=? AND space_id=?",
                (identity, self.tenant, space),
            )
        if not row:
            raise ResourceNotFoundError(identity)
        return decoded(row)

    def runs(self, space: str) -> list[dict[str, Any]]:
        self.recover()
        with self.db.connection() as c:
            return [
                decoded(r)
                for r in self.db.rows(
                    c,
                    "SELECT * FROM acceptance_runs WHERE tenant_id=? AND space_id=? "
                    "ORDER BY created_at DESC LIMIT 100",
                    (self.tenant, space),
                )
            ]

    def claim(self, space: str, identity: str, token: str) -> bool:
        self.recover()
        with self.db.transaction() as c:
            return bool(
                self.db.execute(
                    c,
                    "UPDATE acceptance_runs SET state='running',"
                    "execution_token=?,lease_until=?,pause_requested=0,reason='',updated_at=? "
                    "WHERE id=? AND tenant_id=? AND space_id=? AND state IN ('ready','paused')",
                    (token, time.time() + 45, time.time(), identity, self.tenant, space),
                ).rowcount
            )

    def heartbeat(self, identity: str, token: str) -> bool:
        with self.db.transaction() as c:
            return bool(
                self.db.execute(
                    c,
                    "UPDATE acceptance_runs SET lease_until=? "
                    "WHERE id=? AND execution_token=? AND state='running' AND pause_requested=0",
                    (time.time() + 45, identity, token),
                ).rowcount
            )

    def pause(self, space: str, identity: str) -> None:
        self.run(space, identity)
        with self.db.transaction() as c:
            self.db.execute(
                c,
                "UPDATE acceptance_runs SET pause_requested=1 WHERE id=? AND state='running'",
                (identity,),
            )

    def finish_run(self, identity: str, token: str, state: str, reason: str = "") -> None:
        with self.db.transaction() as c:
            self.db.execute(
                c,
                "UPDATE acceptance_runs SET state=?,reason=?,execution_token='',"
                "updated_at=? WHERE id=? AND execution_token=? AND state='running'",
                (state, reason, time.time(), identity, token),
            )

    def increase_budget(self, space: str, identity: str, limit: int) -> None:
        self.run(space, identity)
        with self.db.transaction() as c:
            self.db.execute(
                c,
                "UPDATE acceptance_runs SET call_limit=? WHERE id=? "
                "AND state IN ('ready','paused') AND call_limit<?",
                (limit, identity, limit),
            )

    def attempts(self, identity: str) -> list[dict[str, Any]]:
        with self.db.connection() as c:
            items = [
                decoded(r)
                for r in self.db.rows(
                    c,
                    "SELECT * FROM acceptance_attempts WHERE run_id=? ORDER BY created_at,id",
                    (identity,),
                )
            ]
            for item in items:
                item["reviews"] = self.db.rows(
                    c,
                    "SELECT * FROM acceptance_reviews WHERE attempt_id=? ORDER BY created_at,id",
                    (item["id"],),
                )
                for review in item["reviews"]:
                    detail = self.db.one(
                        c,
                        "SELECT payload_json FROM acceptance_review_details WHERE review_id=?",
                        (review["id"],),
                    )
                    if detail:
                        review.update(json.loads(detail["payload_json"]))
                item["verdict"] = (
                    item["reviews"][-1]["verdict"]
                    if item["reviews"]
                    else "failed"
                    if item["state"] == "failed"
                    or (
                        item["state"] == "completed"
                        and any(
                            p["status"] in {"missing", "incorrect"}
                            for p in item["payload"].get("point_results", [])
                        )
                    )
                    else "pending_review"
                    if item["state"] == "completed"
                    else item["state"]
                )
            return items

    def begin_attempt(self, identity: str, token: str, case: str, *, repetition: int = 1) -> str:
        with self.db.transaction() as c:
            self._active(c, identity, token)
            previous = self.db.one(
                c,
                "SELECT MAX(attempt) AS n FROM acceptance_attempts WHERE run_id=? AND case_id=?",
                (identity, case),
            )
            attempt_id = new_uuid7()
            self.db.execute(
                c,
                "INSERT INTO acceptance_attempts "
                "(id,run_id,case_id,attempt,state,payload_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    identity,
                    case,
                    (previous["n"] or 0) + 1 if previous else 1,
                    "running",
                    encode({"repetition": repetition}),
                    time.time(),
                    time.time(),
                ),
            )
        return attempt_id

    def _active(self, c: Any, identity: str, token: str) -> dict[str, Any]:
        row = self.db.one(c, "SELECT * FROM acceptance_runs WHERE id=?" + self.lock(), (identity,))
        if (
            not row
            or row["state"] != "running"
            or row["execution_token"] != token
            or (row["pause_requested"] or row["lease_until"] < time.time())
        ):
            raise AcceptancePaused("RUN_PAUSED")
        return row

    def reserve(self, identity: str, token: str) -> None:
        with self.db.transaction() as c:
            row = self._active(c, identity, token)
            if row["calls_reserved"] >= row["call_limit"]:
                raise AcceptancePaused("CALL_BUDGET_EXHAUSTED")
            self.db.execute(
                c,
                "UPDATE acceptance_runs SET calls_reserved=calls_reserved+1 WHERE id=?",
                (identity,),
            )

    def observe(self, identity: str, attempt: str, value: dict[str, Any]) -> None:
        with self.db.transaction() as c:
            self.db.execute(
                c,
                "INSERT INTO acceptance_calls "
                "(id,run_id,attempt_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (new_uuid7(), identity, attempt, encode(value), time.time()),
            )

    def calls(self, identity: str) -> list[dict[str, Any]]:
        with self.db.connection() as c:
            return [
                decoded(r)
                for r in self.db.rows(
                    c,
                    "SELECT * FROM acceptance_calls WHERE run_id=? ORDER BY created_at",
                    (identity,),
                )
            ]

    def finish_attempt(
        self,
        identity: str,
        token: str,
        attempt: str,
        state: str,
        payload: dict[str, Any],
    ) -> None:
        with self.db.transaction() as c:
            # A pause still permits the owner to record its partial evidence; an expired owner
            # cannot overwrite the recovered attempt or another executor's work.
            row = self.db.one(
                c, "SELECT * FROM acceptance_runs WHERE id=?" + self.lock(), (identity,)
            )
            if not row or row["execution_token"] != token or row["lease_until"] < time.time():
                return
            self.db.execute(
                c,
                "UPDATE acceptance_attempts SET state=?,payload_json=?,updated_at=? "
                "WHERE id=? AND run_id=? AND state='running'",
                (state, encode(payload), time.time(), attempt, identity),
            )

    def review(
        self,
        run: str,
        attempt: str,
        actor: str,
        verdict: str,
        note: str,
        point_reviews: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as c:
            row = self.db.one(
                c, "SELECT * FROM acceptance_attempts WHERE id=? AND run_id=?", (attempt, run)
            )
            if not row or row["state"] not in {"completed", "failed"}:
                raise ResourceNotFoundError(attempt)
            if verdict == "passed" and row["state"] == "failed":
                raise IdempotencyConflictError("FAILED_CHECKS_REQUIRE_RETEST")
            record = {
                "id": new_uuid7(),
                "attempt_id": attempt,
                "actor_id": actor,
                "verdict": verdict,
                "note": note,
                "created_at": time.time(),
            }
            self.db.execute(
                c,
                "INSERT INTO acceptance_reviews "
                "(id,attempt_id,actor_id,verdict,note,created_at) VALUES(?,?,?,?,?,?)",
                tuple(record.values()),
            )
            if point_reviews is not None:
                detail = {"point_reviews": point_reviews}
                self.db.execute(
                    c,
                    "INSERT INTO acceptance_review_details(review_id,payload_json) VALUES(?,?)",
                    (record["id"], encode(detail)),
                )
                record.update(detail)
        return record
