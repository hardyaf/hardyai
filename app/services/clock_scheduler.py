from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class ClockJob:
    name: str
    callback: Callable[..., Any]


class BoundedClockScheduler:
    """Small app-level clock runner; domain services own due-slot and retry semantics."""

    MAX_JOBS = 20

    def __init__(self, *, jobs: list[ClockJob], poll_seconds: float = 60.0) -> None:
        self._jobs = list(jobs[: self.MAX_JOBS])
        self._poll_seconds = max(30.0, float(poll_seconds))

    async def run_once(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        results: list[dict[str, Any]] = []
        for job in self._jobs:
            try:
                value = await asyncio.to_thread(job.callback, now=current)
                results.append({"job_name": job.name, "status": "ok", "result": value})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive app scheduler boundary
                print(f"[clock-scheduler] {job.name} failed: {type(exc).__name__}: {exc}")
                results.append(
                    {
                        "job_name": job.name,
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                )
        return results

    async def run_forever(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self._poll_seconds)
