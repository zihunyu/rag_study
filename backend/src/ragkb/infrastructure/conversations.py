"""Persistent, subject-scoped conversations with transactional single-turn execution."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.ids import new_uuid7
from ragkb.domain.pagination import PageKey, RepositoryPage
from ragkb.domain.uploads import IdempotencyConflictError, ResourceNotFoundError
from ragkb.infrastructure.workspace_db import WorkspaceDB

ACTIVE_STATES = ("queued", "resolving", "retrieving")


class ConversationBusy(ValueError):
    pass


class ConversationRepository:
    def __init__(self, db: WorkspaceDB) -> None:
        self.db = db

    def _owned(
        self, connection: Any, identity: str, subject: RequestPrincipal, *, lock: bool = False
    ) -> dict[str, Any]:
        row = self.db.one(
            connection,
            "SELECT * FROM conversations WHERE id=? AND tenant_id=? AND user_id=?"  # noqa: S608
            + (" FOR UPDATE" if lock and self.db.mysql else ""),
            (identity, subject.tenant_id, subject.user_id),
        )
        if not row:
            raise ResourceNotFoundError(identity)
        return row

    def get(self, identity: str, subject: RequestPrincipal) -> dict[str, Any]:
        self.recover()
        with self.db.connection() as connection:
            return self._owned(connection, identity, subject)

    def create(
        self, space_id: str, title: str, subject: RequestPrincipal, request_id: str
    ) -> dict[str, Any]:
        identity = hashlib.sha256(
            json.dumps([subject.tenant_id, subject.user_id, request_id]).encode()
        ).hexdigest()[:36]
        with self.db.transaction() as connection:
            row = self.db.one(connection, "SELECT * FROM conversations WHERE id=?", (identity,))
            if row:
                if row["space_id"] != space_id:
                    raise IdempotencyConflictError("CONVERSATION_SPACE_CHANGED")
                return row
            now = time.time()
            self.db.execute(
                connection,
                "INSERT INTO "
                "conversations(id,tenant_id,user_id,space_id,title,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?) "
                + (
                    "ON DUPLICATE KEY UPDATE id=id"
                    if self.db.mysql
                    else "ON CONFLICT(id) DO NOTHING"
                ),
                (identity, subject.tenant_id, subject.user_id, space_id, title, now, now),
            )
            created = self._owned(connection, identity, subject, lock=True)
            if created["space_id"] != space_id:
                raise IdempotencyConflictError("CONVERSATION_SPACE_CHANGED")
            return created

    def list_conversations(
        self,
        subject: RequestPrincipal,
        space: str = "",
        *,
        limit: int = 30,
        after: PageKey | None = None,
    ) -> RepositoryPage:
        filters = ["tenant_id=?", "user_id=?", "archived=0"]
        params: list[Any] = [subject.tenant_id, subject.user_id]
        if space:
            filters.append("space_id=?")
            params.append(space)
        # Fixed integer millisecond ordering makes cursors portable across both databases.
        stamp = "FLOOR(updated_at*1000)" if self.db.mysql else "CAST(updated_at*1000 AS INTEGER)"
        if after:
            filters.append(f"({stamp}, id) < (?, ?)")
            params.extend(after)
        with self.db.connection() as connection:
            rows = self.db.rows(
                connection,
                "SELECT * FROM conversations WHERE "  # noqa: S608 - closed SQL, bound values
                + " AND ".join(filters)
                + f" ORDER BY {stamp} DESC,id DESC LIMIT ?",
                (*params, limit + 1),
            )
        last = rows[limit - 1] if len(rows) > limit else None
        return RepositoryPage(
            rows[:limit], (int(last["updated_at"] * 1000), last["id"]) if last else None
        )

    def update(
        self,
        identity: str,
        subject: RequestPrincipal,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            row = self._owned(connection, identity, subject, lock=True)
            if archived and row["active_turn_id"]:
                raise ConversationBusy("CONVERSATION_BUSY")
            self.db.execute(
                connection,
                "UPDATE conversations SET title=?,archived=?,updated_at=? WHERE id=?",
                (
                    title if title is not None else row["title"],
                    int(archived) if archived is not None else row["archived"],
                    time.time(),
                    identity,
                ),
            )
            return self._owned(connection, identity, subject)

    def submit(
        self,
        identity: str,
        subject: RequestPrincipal,
        question: str,
        request_id: str,
        reading: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.recover()
        with self.db.transaction() as connection:
            conversation = self._owned(connection, identity, subject, lock=True)
            existing = self.db.one(
                connection,
                "SELECT * FROM conversation_turns WHERE conversation_id=? AND request_id=?",
                (identity, request_id),
            )
            if existing:
                prior = self.db.one(
                    connection,
                    "SELECT payload_json FROM conversation_turn_options WHERE turn_id=?",
                    (existing["id"],),
                )
                if existing["original_question"] != question or (
                    json.loads(prior["payload_json"]) if prior else {}
                ) != (reading or {}):
                    raise IdempotencyConflictError("CONVERSATION_REQUEST_CHANGED")
                return existing
            if conversation["archived"]:
                raise ConversationBusy("CONVERSATION_ARCHIVED")
            if conversation["active_turn_id"]:
                raise ConversationBusy("CONVERSATION_BUSY")
            turn_id, now = new_uuid7(), time.time()
            self.db.execute(
                connection,
                "INSERT INTO "
                "conversation_turns(id,conversation_id,request_id,sequence_number,original_question,resolved_question,state,created_at,updated_at,lease_expires_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id,
                    identity,
                    request_id,
                    conversation["next_sequence"],
                    question,
                    "",
                    "queued",
                    now,
                    now,
                    now + 180,
                ),
            )
            self.db.execute(
                connection,
                "INSERT INTO conversation_turn_options(turn_id,payload_json) VALUES(?,?)",
                (turn_id, json.dumps(reading or {}, ensure_ascii=False, sort_keys=True)),
            )
            title = (
                question[:60]
                if conversation["next_sequence"] == 1 and conversation["title"] == "新对话"
                else conversation["title"]
            )
            self.db.execute(
                connection,
                "UPDATE conversations SET active_turn_id=?, "
                "next_sequence=next_sequence+1,updated_at=?,title=? WHERE id=?",
                (turn_id, now, title, identity),
            )
            return self._turn(connection, turn_id)

    def _turn(self, connection: Any, identity: str) -> dict[str, Any]:
        row = self.db.one(connection, "SELECT * FROM conversation_turns WHERE id=?", (identity,))
        if not row:
            raise ResourceNotFoundError(identity)
        reading = self.db.one(
            connection,
            "SELECT payload_json FROM conversation_turn_options WHERE turn_id=?",
            (identity,),
        )
        row["reading"] = json.loads(reading["payload_json"]) if reading else {}
        return row

    def turn(self, identity: str) -> dict[str, Any]:
        with self.db.connection() as connection:
            return self._turn(connection, identity)

    def turns(
        self, conversation_id: str, *, before: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = self.db.rows(
                connection,
                "SELECT * FROM conversation_turns WHERE conversation_id=? AND "
                "sequence_number<? ORDER BY sequence_number DESC LIMIT ?",
                (conversation_id, before or 2147483647, limit),
            )
        return list(reversed(rows))

    def claim(self, identity: str, token: str) -> bool:
        with self.db.transaction() as connection:
            cursor = self.db.execute(
                connection,
                "UPDATE conversation_turns SET "
                "state='resolving',execution_token=?,lease_expires_at=?,updated_at=? WHERE "
                "id=? AND state='queued' AND cancel_requested=0",
                (token, time.time() + 60, time.time(), identity),
            )
            return bool(cursor.rowcount)

    def stage(self, identity: str, token: str, resolved: str) -> None:
        with self.db.transaction() as connection:
            self.db.execute(
                connection,
                "UPDATE conversation_turns SET "
                "state='retrieving',resolved_question=?,updated_at=? WHERE id=? AND "
                "execution_token=? AND state='resolving'",
                (resolved, time.time(), identity, token),
            )

    def heartbeat(self, identity: str, token: str) -> bool:
        with self.db.transaction() as connection:
            cursor = self.db.execute(
                connection,
                "UPDATE conversation_turns SET lease_expires_at=? WHERE id=? AND "
                "execution_token=? AND state IN ('resolving','retrieving') AND "
                "cancel_requested=0",
                (time.time() + 60, identity, token),
            )
            return bool(cursor.rowcount)

    def finish(
        self,
        identity: str,
        token: str,
        state: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.db.transaction() as connection:
            row = self._turn(connection, identity)
            if row["execution_token"] != token or row["state"] not in ACTIVE_STATES:
                return
            if row["cancel_requested"]:
                state, result, error = "cancelled", None, None
            self.db.execute(
                connection,
                "UPDATE conversation_turns SET "
                "state=?,result_json=?,rag_run_id=?,error_code=?,lease_expires_at=0,updated_at=?"
                " WHERE id=?",
                (
                    state,
                    json.dumps(result, ensure_ascii=False) if result else None,
                    result.get("rag_run_id") if result else None,
                    error,
                    time.time(),
                    identity,
                ),
            )
            self.db.execute(
                connection,
                "UPDATE conversations SET active_turn_id=NULL,updated_at=? WHERE id=? AND "
                "active_turn_id=?",
                (time.time(), row["conversation_id"], identity),
            )

    def cancel(self, identity: str, subject: RequestPrincipal) -> dict[str, Any]:
        with self.db.transaction() as connection:
            row = self._turn(connection, identity)
            self._owned(connection, row["conversation_id"], subject, lock=True)
            if row["state"] in ACTIVE_STATES:
                self.db.execute(
                    connection,
                    "UPDATE conversation_turns SET "
                    "cancel_requested=1,state='cancelled',updated_at=? WHERE id=?",
                    (time.time(), identity),
                )
                self.db.execute(
                    connection,
                    "UPDATE conversations SET active_turn_id=NULL,updated_at=? WHERE id=? "
                    "AND active_turn_id=?",
                    (time.time(), row["conversation_id"], identity),
                )
            return self._turn(connection, identity)

    def recover(self) -> int:
        with self.db.transaction() as connection:
            rows = self.db.rows(
                connection,
                "SELECT id,conversation_id FROM conversation_turns WHERE state IN "
                "('queued','resolving','retrieving') AND lease_expires_at<?",
                (time.time(),),
            )
            for row in rows:
                self.db.execute(
                    connection,
                    "UPDATE conversation_turns SET "
                    "state='failed',error_code='EXECUTION_INTERRUPTED',updated_at=? WHERE "
                    "id=? AND state IN ('queued','resolving','retrieving') AND "
                    "lease_expires_at<?",
                    (time.time(), row["id"], time.time()),
                )
                self.db.execute(
                    connection,
                    "UPDATE conversations SET active_turn_id=NULL WHERE id=? AND "
                    "active_turn_id=? AND EXISTS (SELECT 1 FROM conversation_turns WHERE "
                    "id=? AND state='failed')",
                    (row["conversation_id"], row["id"], row["id"]),
                )
            return len(rows)
