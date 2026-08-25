from __future__ import annotations

import signal
from threading import Event
from typing import Any
from uuid import uuid4

from app.config import settings
from app.tickets.repository import TicketRepository
from app.tickets.review_service import TicketReviewService
from app.tickets.types import TicketStatus
from app.services.offline_runtime_policy import validate_offline_runtime


class TicketReviewWorker:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        review_service: TicketReviewService,
        worker_id: str | None = None,
        batch_size: int = 5,
        lease_seconds: float = 300.0,
        poll_seconds: float = 10.0,
        live_idle_seconds: float = 15.0,
    ) -> None:
        self._repository = repository
        self._review_service = review_service
        self.worker_id = worker_id or f"ticket-review-{uuid4()}"
        self._batch_size = max(1, min(int(batch_size), 100))
        self._lease_seconds = max(10.0, float(lease_seconds))
        self._poll_seconds = max(1.0, min(float(poll_seconds), 60.0))
        self._live_idle_seconds = max(0.0, float(live_idle_seconds))
        self._stop = Event()

    def request_stop(self) -> None:
        self._stop.set()

    def run_once(self) -> list[dict[str, Any]]:
        self._repository.record_worker_heartbeat(
            worker_type="ticket_review",
            worker_id=self.worker_id,
            status="polling",
        )
        if self._repository.has_recent_live_input(within_seconds=self._live_idle_seconds):
            self._repository.record_worker_heartbeat(
                worker_type="ticket_review",
                worker_id=self.worker_id,
                status="yielding_to_live_inference",
                metadata={"live_idle_seconds": self._live_idle_seconds},
            )
            return []
        watchdog_jobs = self._repository.claim_jobs(
            job_type="ticket_watchdog",
            worker_id=self.worker_id,
            limit=self._batch_size,
            lease_seconds=60.0,
        )
        results: list[dict[str, Any]] = []
        for watchdog in watchdog_jobs:
            ticket_id = str(
                (watchdog.get("payload") or {}).get("ticket_id")
                or watchdog.get("aggregate_id")
                or ""
            )
            ticket = self._repository.get_ticket(ticket_id)
            if ticket and str(ticket.get("status") or "") in {
                TicketStatus.CAPTURED.value,
                TicketStatus.EXECUTING.value,
            } and not self._repository.list_receipts(ticket_id):
                self._repository.transition_ticket(
                    ticket_id=ticket_id,
                    status=TicketStatus.RECONCILIATION_REQUIRED,
                    terminal_reason="execution_interrupted_before_receipt",
                )
                results.append({"status": "reconciliation_required", "ticket_id": ticket_id})
            else:
                results.append({"status": "watchdog_cleared", "ticket_id": ticket_id})
            self._repository.complete_job(
                job_id=str(watchdog["job_id"]),
                worker_id=self.worker_id,
            )

        jobs = self._repository.claim_jobs(
            job_type="ticket_review",
            worker_id=self.worker_id,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        for job in jobs:
            try:
                result = self._review_service.process_job(job)
                self._repository.complete_job(job_id=str(job["job_id"]), worker_id=self.worker_id)
                results.append(result)
            except Exception as exc:
                attempt = int(job.get("attempt_count") or 1)
                delay = min(300.0, float(2 ** max(0, attempt - 1)))
                self._repository.retry_job(
                    job_id=str(job["job_id"]),
                    worker_id=self.worker_id,
                    error_code=type(exc).__name__,
                    delay_seconds=delay,
                )
                persisted_job = self._repository.get_job(str(job["job_id"])) or {}
                if persisted_job.get("status") == "dead_letter":
                    ticket_id = str(
                        (job.get("payload") or {}).get("ticket_id")
                        or job.get("aggregate_id")
                        or ""
                    )
                    if ticket_id:
                        updated_ticket = self._repository.transition_ticket(
                            ticket_id=ticket_id,
                            status=TicketStatus.ESCALATED,
                            terminal_reason=f"verification_attempts_exhausted:{type(exc).__name__}",
                        )
                        if settings.plane_enabled and updated_ticket:
                            self._repository.enqueue_job(
                                job_type="plane_sync",
                                aggregate_id=ticket_id,
                                idempotency_key=(
                                    f"plane-sync:{ticket_id}:{updated_ticket.get('version')}:"
                                    f"{updated_ticket.get('status')}"
                                ),
                                payload={"ticket_id": ticket_id},
                            )
                    results.append({"status": "dead_letter", "error_code": type(exc).__name__})
                else:
                    results.append({"status": "retry", "error_code": type(exc).__name__})
        errors = [item for item in results if item.get("status") in {"retry", "dead_letter"}]
        self._repository.record_worker_heartbeat(
            worker_type="ticket_review",
            worker_id=self.worker_id,
            status="degraded" if errors else "idle",
            last_error_code=str(errors[-1].get("error_code")) if errors else None,
            metadata={
                "claimed_count": len(jobs),
                "watchdog_count": len(watchdog_jobs),
                "retry_count": len(errors),
            },
        )
        return results

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._poll_seconds)


def main() -> int:
    if not settings.action_ticket_review_enabled:
        raise RuntimeError("ACTION_TICKET_REVIEW_ENABLED must be true to run the review worker.")
    validate_offline_runtime(settings, entrypoint="ticket-review-worker")
    from app.runtime import ticket_repository, ticket_review_service

    worker = TicketReviewWorker(
        repository=ticket_repository,
        review_service=ticket_review_service,
        batch_size=settings.action_ticket_review_batch_size,
        lease_seconds=max(settings.action_ticket_review_model_timeout_seconds + 60.0, 120.0),
        poll_seconds=settings.action_ticket_review_poll_seconds,
        live_idle_seconds=settings.action_ticket_review_live_idle_seconds,
    )
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
