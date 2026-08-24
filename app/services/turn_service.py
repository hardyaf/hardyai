from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.api.principals import RequestPrincipal, trusted_ask_request
from app.core.router import JarvisRouter
from app.schemas.api import AskRequest


class TurnQueueFullError(RuntimeError):
    pass


class TurnTimeoutError(RuntimeError):
    pass


class TurnService:
    """Bounded async admission around the synchronous first-pass router."""

    def __init__(
        self,
        *,
        router: JarvisRouter,
        max_concurrency: int = 1,
        queue_capacity: int = 8,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._router = router
        self._max_concurrency = max(1, int(max_concurrency))
        self._queue_capacity = max(0, int(queue_capacity))
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._admission_lock = asyncio.Lock()
        self._session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._admitted = 0

    async def route(
        self,
        payload: AskRequest,
        *,
        principal: RequestPrincipal,
    ) -> dict[str, Any]:
        trusted_payload = trusted_ask_request(payload, principal)
        async with self._admission_lock:
            maximum_admitted = self._max_concurrency + self._queue_capacity
            if self._admitted >= maximum_admitted:
                raise TurnQueueFullError("turn_queue_full")
            self._admitted += 1

        session_key = self._session_key(trusted_payload)
        try:
            async with self._semaphore:
                async with self._session_locks[session_key]:
                    try:
                        return await asyncio.wait_for(
                            asyncio.to_thread(self._router.route, trusted_payload),
                            timeout=self._timeout_seconds,
                        )
                    except TimeoutError as exc:
                        raise TurnTimeoutError("turn_timeout") from exc
        finally:
            async with self._admission_lock:
                self._admitted = max(0, self._admitted - 1)

    @staticmethod
    def _session_key(payload: AskRequest) -> str:
        if payload.session_id:
            return f"session:{payload.session_id}"
        session_channel = str(payload.context.get("session_channel") or "").strip().casefold()
        if session_channel:
            return f"channel:{payload.user_id}:{session_channel}"
        return f"request:{payload.user_id}:{payload.source}"

    def status(self) -> dict[str, int | float]:
        return {
            "max_concurrency": self._max_concurrency,
            "queue_capacity": self._queue_capacity,
            "admitted": self._admitted,
            "timeout_seconds": self._timeout_seconds,
        }
