"""Conversation CRUD and reconnect-safe server-owned SSE turns."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ragkb.api.pagination import read_cursor, write_cursor
from ragkb.api.support import principal, require_local_tenant, require_role
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.conversation_service import ConversationService
from ragkb.infrastructure.conversations import ACTIVE_STATES, ConversationBusy
from ragkb.runtime_components import RuntimeComponents


class CreateConversation(BaseModel):
    space_id: str = Field(min_length=1, max_length=191)
    title: str = Field(default="新对话", min_length=1, max_length=100)


class UpdateConversation(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100, pattern=r".*\S.*")
    archived: bool | None = None


class SendTurn(BaseModel):
    question: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")
    client_request_id: str = Field(min_length=1, max_length=191)


def build_conversations_router(
    runtime: RuntimeComponents, service: ConversationService
) -> APIRouter:
    router = APIRouter(tags=["conversations"])
    repository = service.repository

    def subject_for(request: Request) -> RequestPrincipal:
        subject = principal(request)
        require_local_tenant(runtime, subject)
        require_role(subject, "reader", "knowledge_maintainer", "admin")
        return subject

    def owned(identity: str, subject: RequestPrincipal) -> dict[str, Any]:
        conversation = repository.get(identity, subject)
        if runtime.repository.get_space(conversation["space_id"])["tenant_id"] != subject.tenant_id:
            raise ResourceNotFoundError(identity)
        return conversation

    @router.get("/api/conversations")
    def conversations(
        request: Request,
        response: Response,
        space_id: str = "",
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> list[dict[str, Any]]:
        subject = subject_for(request)
        scope = f"conversations:{subject.tenant_id}:{subject.user_id}:{space_id}"
        page = repository.list_conversations(
            subject, space_id, limit=limit, after=read_cursor(cursor, scope, 0)
        )
        write_cursor(response, scope, page.next_key)
        return page.items

    @router.post("/api/conversations")
    def create(
        body: CreateConversation,
        request: Request,
        request_id: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        subject = subject_for(request)
        if runtime.repository.get_space(body.space_id)["tenant_id"] != subject.tenant_id:
            raise ResourceNotFoundError(body.space_id)
        return repository.create(body.space_id, body.title.strip() or "新对话", subject, request_id)

    @router.get("/api/conversations/{identity}")
    def detail(
        identity: str, request: Request, before: int | None = Query(default=None, ge=1)
    ) -> dict[str, Any]:
        subject = subject_for(request)
        conversation = owned(identity, subject)
        rows = repository.turns(identity, before=before, limit=50)
        return {
            "conversation": conversation,
            "turns": [service.present(row, conversation, subject) for row in rows],
            "next_before": rows[0]["sequence_number"]
            if rows and rows[0]["sequence_number"] > 1
            else None,
        }

    @router.patch("/api/conversations/{identity}")
    def update(identity: str, body: UpdateConversation, request: Request) -> dict[str, Any]:
        subject = subject_for(request)
        owned(identity, subject)
        try:
            return repository.update(
                identity,
                subject,
                title=body.title.strip() if body.title else None,
                archived=body.archived,
            )
        except ConversationBusy as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/api/conversations/{identity}/turns/{turn_id}")
    def turn_status(identity: str, turn_id: str, request: Request) -> dict[str, Any]:
        subject = subject_for(request)
        conversation = owned(identity, subject)
        turn = repository.turn(turn_id)
        if turn["conversation_id"] != identity:
            raise ResourceNotFoundError(turn_id)
        return service.present(turn, conversation, subject)

    @router.post("/api/conversations/{identity}/turns/{turn_id}:cancel")
    def cancel(identity: str, turn_id: str, request: Request) -> dict[str, Any]:
        subject = subject_for(request)
        conversation = owned(identity, subject)
        turn = repository.turn(turn_id)
        if turn["conversation_id"] != identity:
            raise ResourceNotFoundError(turn_id)
        return service.present(repository.cancel(turn_id, subject), conversation, subject)

    @router.post("/api/conversations/{identity}/turns:stream")
    def send(identity: str, body: SendTurn, request: Request) -> StreamingResponse:
        subject = subject_for(request)
        conversation = owned(identity, subject)
        try:
            turn = repository.submit(
                identity, subject, body.question.strip(), body.client_request_id
            )
        except ConversationBusy as error:
            raise HTTPException(409, str(error)) from error
        service.start(turn, conversation, subject)

        async def stream() -> AsyncIterator[str]:
            previous = None
            ticks = 0
            while True:
                current = await run_in_threadpool(repository.turn, turn["id"])
                if previous != current["state"]:
                    yield (
                        "event: turn\ndata: "
                        + json.dumps(
                            {
                                "id": current["id"],
                                "conversation_id": identity,
                                "state": current["state"],
                                "original_question": current["original_question"],
                                "sequence_number": current["sequence_number"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    previous = current["state"]
                if current["state"] not in ACTIVE_STATES:
                    presented = await run_in_threadpool(
                        service.present, current, conversation, subject
                    )
                    # Release only verified answers whose sources remain current.
                    yield (
                        "event: result\ndata: " + json.dumps(presented, ensure_ascii=False) + "\n\n"
                    )
                    return
                if await request.is_disconnected():
                    return
                ticks += 1
                if ticks % 20 == 0:
                    yield ": heartbeat\n\n"
                    await run_in_threadpool(repository.recover)
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
