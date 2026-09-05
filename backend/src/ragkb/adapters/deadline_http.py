"""Synchronous port backed by cancellable async HTTP and a persistent connection pool."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any

import httpx


class DeadlineHttpClient:
    def __init__(self, **kwargs: Any) -> None:
        self.client = httpx.AsyncClient(**kwargs)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, daemon=True, name="rag-provider-http"
        )
        self.lock = threading.Lock()
        self.started = False
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        timeout = float(kwargs["timeout"].read)
        with self.lock:
            if self.closed:
                raise RuntimeError("MODEL_HTTP_CLIENT_CLOSED")
            if not self.started:
                self.thread.start()
                self.started = True

        async def send() -> httpx.Response:
            try:
                async with asyncio.timeout(timeout):
                    return await self.client.post(url, **kwargs)
            except TimeoutError as error:
                raise httpx.ReadTimeout("MODEL_PROVIDER_TOTAL_TIMEOUT") from error

        future = asyncio.run_coroutine_threadsafe(send(), self.loop)
        try:
            return future.result(timeout=timeout + 0.1)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise httpx.ReadTimeout("MODEL_PROVIDER_TOTAL_TIMEOUT") from error

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.started:
                try:
                    asyncio.run_coroutine_threadsafe(self.client.aclose(), self.loop).result(5)
                finally:
                    self.loop.call_soon_threadsafe(self.loop.stop)
                    self.thread.join(1)
            else:
                self.loop.run_until_complete(self.client.aclose())
            if not self.loop.is_running():
                self.loop.close()
