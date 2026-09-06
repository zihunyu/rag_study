"""Synchronous Worker boundary for HTTP requests with cooperative cancellation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any, BinaryIO, cast

import httpx

from ragkb.application.cancellation import run_cancellable


def cancellable_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    async def upload(handle: BinaryIO) -> AsyncIterator[bytes]:
        while chunk := handle.read(1024 * 1024):
            yield chunk
            await asyncio.sleep(0)

    async def request() -> httpx.Response:
        content = kwargs.get("content")
        if hasattr(content, "read"):
            handle = cast(BinaryIO, content)
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                "Content-Length": str(os.fstat(handle.fileno()).st_size - handle.tell()),
            }
            kwargs["content"] = upload(handle)
        async with asyncio.timeout(float(kwargs["timeout"])):
            async with httpx.AsyncClient() as client:
                return await client.request(method, url, **kwargs)

    return run_cancellable(request)
