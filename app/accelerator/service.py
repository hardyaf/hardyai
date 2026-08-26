from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.accelerator.repository import AcceleratorLeaseRepository
from app.accelerator.types import AcceleratorAdmissionError, AcceleratorLease


LANE_PRIORITIES: dict[str, int] = {
    "main_conversation": 100,
    "main_repair": 95,
    "micro": 90,
    "runtime_health": 85,
    "research_decision": 60,
    "action_ticket_review": 50,
    "email_summary": 40,
    "email_classifier": 35,
    "document_vlm": 10,
}


@dataclass(frozen=True)
class AcceleratorLeaseGuard:
    lease: AcceleratorLease
    lost: asyncio.Event


class AcceleratorAdmissionQueue:
    def __init__(
        self,
        repository: AcceleratorLeaseRepository,
        *,
        lease_seconds: float = 30.0,
        heartbeat_seconds: float = 5.0,
        poll_seconds: float = 0.05,
    ) -> None:
        self.repository = repository
        self.lease_seconds = max(5.0, min(float(lease_seconds), 900.0))
        self.heartbeat_seconds = max(0.5, min(float(heartbeat_seconds), self.lease_seconds / 2))
        self.poll_seconds = max(0.01, min(float(poll_seconds), 1.0))

    @asynccontextmanager
    async def lease(self, *, lane: str, wait_seconds: float) -> AsyncIterator[AcceleratorLeaseGuard]:
        normalized = str(lane or "").strip().casefold()
        if normalized not in LANE_PRIORITIES:
            raise AcceleratorAdmissionError("accelerator_lane_not_allowed")
        bounded_wait = max(0.1, min(float(wait_seconds), 900.0))
        waiter_id = await asyncio.to_thread(
            self.repository.enqueue,
            lane=normalized,
            priority=LANE_PRIORITIES[normalized],
            wait_seconds=bounded_wait,
        )
        deadline = asyncio.get_running_loop().time() + bounded_wait
        acquired: AcceleratorLease | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        lost = asyncio.Event()
        try:
            while asyncio.get_running_loop().time() < deadline:
                acquired = await asyncio.to_thread(
                    self.repository.try_acquire,
                    waiter_id=waiter_id,
                    lease_seconds=self.lease_seconds,
                )
                if acquired is not None:
                    break
                await asyncio.sleep(self.poll_seconds)
            if acquired is None:
                raise AcceleratorAdmissionError("accelerator_admission_timeout")
            heartbeat_task = asyncio.create_task(self._heartbeat(acquired, lost))
            yield AcceleratorLeaseGuard(lease=acquired, lost=lost)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except BaseException:
                    pass
            if acquired is not None:
                await asyncio.to_thread(self.repository.release, lease=acquired)
            else:
                await asyncio.to_thread(self.repository.cancel_waiter, waiter_id)

    async def _heartbeat(self, lease: AcceleratorLease, lost: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                renewed = await asyncio.to_thread(
                    self.repository.heartbeat,
                    lease=lease,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                renewed = False
            if not renewed:
                lost.set()
                return
