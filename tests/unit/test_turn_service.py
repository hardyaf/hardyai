from __future__ import annotations

import asyncio
import threading
import time

from app.api.principals import PrincipalKind, RequestPrincipal
from app.schemas.api import AskRequest
from app.services.turn_service import TurnQueueFullError, TurnService


def _principal() -> RequestPrincipal:
    return RequestPrincipal(
        subject="test",
        kind=PrincipalKind.TEST,
        user_id="test-user",
        source="test",
        scopes=frozenset({"ask"}),
        authenticated_by="test",
    )


class _RecordingRouter:
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def route(self, payload: AskRequest) -> dict:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            return {"session_id": payload.session_id, "source": payload.source}
        finally:
            with self.lock:
                self.active -= 1


def test_router_work_runs_off_the_event_loop():
    async def scenario():
        router = _RecordingRouter(delay=0.1)
        service = TurnService(router=router, max_concurrency=1, queue_capacity=1)
        task = asyncio.create_task(
            service.route(
                AskRequest(text="hello", session_id="one"),
                principal=_principal(),
            )
        )
        await asyncio.sleep(0.01)
        assert not task.done()
        marker = []
        await asyncio.sleep(0)
        marker.append("responsive")
        assert marker == ["responsive"]
        await task

    asyncio.run(scenario())


def test_same_session_turns_are_serialized():
    async def scenario():
        router = _RecordingRouter()
        service = TurnService(router=router, max_concurrency=2, queue_capacity=2)
        await asyncio.gather(
            service.route(
                AskRequest(text="first", session_id="shared"),
                principal=_principal(),
            ),
            service.route(
                AskRequest(text="second", session_id="shared"),
                principal=_principal(),
            ),
        )
        assert router.max_active == 1

    asyncio.run(scenario())


def test_queue_capacity_fails_closed():
    async def scenario():
        router = _RecordingRouter(delay=0.1)
        service = TurnService(router=router, max_concurrency=1, queue_capacity=0)
        active = asyncio.create_task(
            service.route(
                AskRequest(text="first", session_id="one"),
                principal=_principal(),
            )
        )
        await asyncio.sleep(0.01)
        try:
            await service.route(
                AskRequest(text="second", session_id="two"),
                principal=_principal(),
            )
        except TurnQueueFullError:
            pass
        else:  # pragma: no cover - assertion guard
            raise AssertionError("full queue accepted an additional turn")
        await active

    asyncio.run(scenario())
