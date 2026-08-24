from __future__ import annotations

import signal
from threading import Event
from typing import Any
from uuid import uuid4

from app.config import settings
from app.integrations.plane.sync_service import PlaneSyncService
from app.tickets.repository import TicketRepository


class PlaneSyncWorker:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        sync_service: PlaneSyncService,
        worker_id: str | None = None,
        batch_size: int = 5,
        poll_seconds: float = 10.0,
    ) -> None:
        self._repository = repository
        self._sync_service = sync_service
        self.worker_id = worker_id or f"plane-sync-{uuid4()}"
        self._batch_size = max(1, min(int(batch_size), 100))
        self._poll_seconds = max(1.0, min(float(poll_seconds), 60.0))
        self._stop = Event()

    def request_stop(self) -> None:
        self._stop.set()

    def run_once(self) -> list[dict[str, Any]]:
        self._repository.record_worker_heartbeat(
            worker_type="plane_sync",
            worker_id=self.worker_id,
            status="polling",
        )
        jobs = self._repository.claim_jobs(
            job_type="plane_sync",
            worker_id=self.worker_id,
            limit=self._batch_size,
            lease_seconds=max(settings.plane_api_timeout_seconds * 3.0, 60.0),
        )
        results: list[dict[str, Any]] = []
        for job in jobs:
            ticket_id = str((job.get("payload") or {}).get("ticket_id") or job["aggregate_id"])
            try:
                result = self._sync_service.sync_ticket(ticket_id)
                self._repository.complete_job(job_id=str(job["job_id"]), worker_id=self.worker_id)
                results.append(result)
            except Exception as exc:
                self._repository.update_plane_mapping(
                    ticket_id=ticket_id,
                    plane_work_item_id=None,
                    sync_status=f"retry:{type(exc).__name__}"[:120],
                )
                attempt = int(job.get("attempt_count") or 1)
                self._repository.retry_job(
                    job_id=str(job["job_id"]),
                    worker_id=self.worker_id,
                    error_code=type(exc).__name__,
                    delay_seconds=min(300.0, float(2 ** max(0, attempt - 1))),
                )
                persisted_job = self._repository.get_job(str(job["job_id"])) or {}
                final_status = "dead_letter" if persisted_job.get("status") == "dead_letter" else "retry"
                if final_status == "dead_letter":
                    self._repository.update_plane_mapping(
                        ticket_id=ticket_id,
                        plane_work_item_id=None,
                        sync_status=f"dead_letter:{type(exc).__name__}"[:120],
                    )
                results.append({"status": final_status, "error_code": type(exc).__name__})
        errors = [item for item in results if item.get("status") in {"retry", "dead_letter"}]
        self._repository.record_worker_heartbeat(
            worker_type="plane_sync",
            worker_id=self.worker_id,
            status="degraded" if errors else "idle",
            last_error_code=str(errors[-1].get("error_code")) if errors else None,
            metadata={"claimed_count": len(jobs), "retry_count": len(errors)},
        )
        return results

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._poll_seconds)


def main() -> int:
    if not settings.plane_enabled:
        raise RuntimeError("PLANE_ENABLED must be true to run the Plane sync worker.")
    from app.runtime import plane_sync_service, ticket_repository

    if plane_sync_service is None:
        raise RuntimeError("Plane integration is not configured.")
    worker = PlaneSyncWorker(repository=ticket_repository, sync_service=plane_sync_service)
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
