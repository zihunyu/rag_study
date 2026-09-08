"""Authorize sensitive bytes at ASGI send, serialized with cross-process revocation."""

from __future__ import annotations

import json

from anyio import fail_after, from_thread, to_thread
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ragkb.infrastructure.accounts import AccountService


class AccountReleaseMiddleware:
    def __init__(self, app: ASGIApp, service: AccountService):
        self.app, self.service = app, service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        protected = (
            scope.get("method") == "GET"
            or path.startswith(("/api/ask", "/api/conversations", "/api/rag-runs"))
            or path.endswith(":read")
        )
        if (
            scope["type"] != "http"
            or not self.service.enabled
            or not protected
            or path.startswith(("/api/auth/", "/health/"))
        ):
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        started, rejected = False, False

        async def guarded_send(message: Message) -> None:
            nonlocal start, started, rejected
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":
                with fail_after(10):
                    await send(message)
                return
            if rejected:
                return
            subject = scope.get("state", {}).get("principal")
            space = scope.get("state", {}).get("account_space", "")

            async def emit(allowed: bool) -> None:
                nonlocal started, rejected
                assert start is not None
                if not allowed:
                    rejected = True
                    if not started:
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 403,
                                "headers": [
                                    (b"content-type", b"application/json"),
                                    (b"cache-control", b"no-store"),
                                ],
                            }
                        )
                        body = json.dumps(
                            {
                                "code": "KNOWLEDGE_BASE_ACCESS_REVOKED",
                                "message": "权限已变更，请刷新",
                            }
                        ).encode()
                    elif any(
                        k == b"content-type" and b"text/event-stream" in v
                        for k, v in start.get("headers", [])
                    ):
                        body = (
                            b"event: access_revoked\ndata: "
                            b'{"code":"KNOWLEDGE_BASE_ACCESS_REVOKED"}\n\n'
                        )
                    else:
                        body = b""
                    await send({"type": "http.response.body", "body": body, "more_body": False})
                    return
                if not started:
                    await send(start)
                    started = True
                await send(message)

            async def bounded_emit(allowed: bool) -> None:
                with fail_after(10):
                    await emit(allowed)

            def release() -> None:
                # Keep the database advisory/file lock until the bytes are accepted by
                # the server. No model calls run under this lock. Revocation uses it too.
                with self.service.guard():
                    allowed = bool(
                        subject
                        and self.service.recheck(
                            subject.user_id, subject.scope_tokens, (space,) if space else ()
                        )
                    )
                    from_thread.run(bounded_emit, allowed)

            if subject is None or (start and start["status"] >= 400):
                await emit(True)
            else:
                await to_thread.run_sync(release)

        await self.app(scope, receive, guarded_send)
