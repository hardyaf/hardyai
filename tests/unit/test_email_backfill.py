from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.jobs.repository import DurableJobRepository
from scripts.manage_email_backfill import (
    backfill_status,
    cancel_backfill,
    enqueue_root,
    run_once,
)


class FakeBackfillService:
    def __init__(self) -> None:
        self.page_sizes: list[int] = []

    def process_historical_backfill_page(self, *, page_token, page_size):
        self.page_sizes.append(page_size)
        if page_token is None:
            return {
                "next_page_token": "page-2",
                "candidate_count": 2,
                "accepted_count": 2,
            }
        return {
            "next_page_token": "provider-still-has-more",
            "candidate_count": 1,
            "accepted_count": 1,
        }


def test_backfill_run_once_enforces_root_message_budget_and_reports_partial(tmp_path):
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    root = enqueue_root(
        repository,
        page_size=2,
        max_pages=5,
        max_messages=3,
    )
    service = FakeBackfillService()

    first = run_once(repository, email_agent_service=service)
    second = run_once(repository, email_agent_service=service)
    status = backfill_status(
        repository,
        backfill_id=root["backfill_id"],
        limit=100,
        coverage={
            "earliest_indexed_at": "2026-01-01T00:00:00Z",
            "latest_indexed_at": "2026-08-31T00:00:00Z",
            "message_count": 3,
        },
    )

    assert first["status"] == "continued"
    assert first["continuation_enqueued"] is True
    assert second["status"] == "partial"
    assert service.page_sizes == [2, 1]
    assert status["job_counts"] == {"completed": 2}
    assert status["outcomes"]["candidate_count"] == 3
    assert status["outcomes"]["accepted_count"] == 3
    assert status["budget_exhausted_partial"] is True
    assert status["provider_pagination_complete"] is False
    repository.close()


def test_backfill_cancel_stops_only_unclaimed_jobs(tmp_path):
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    root = enqueue_root(
        repository,
        page_size=10,
        max_pages=2,
        max_messages=20,
    )

    result = cancel_backfill(repository, backfill_id=root["backfill_id"])
    rows = repository.list_jobs(job_type="email.projection_backfill.v1")

    assert result["cancelled_unclaimed_jobs"] == 1
    assert rows[0]["status"] == "cancelled"
    repository.close()


@pytest.mark.parametrize(
    "values",
    [
        {"page_size": 0, "max_pages": 1, "max_messages": 1},
        {"page_size": 1, "max_pages": 0, "max_messages": 1},
        {"page_size": 1, "max_pages": 1, "max_messages": 0},
    ],
)
def test_backfill_enqueue_refuses_unbounded_or_empty_budget(tmp_path, values):
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    with pytest.raises(ValueError):
        enqueue_root(repository, **values)
    assert repository.list_jobs(job_type="email.projection_backfill.v1") == []
    repository.close()


def test_checkpoint_payload_is_fenced_to_the_active_job_lease(tmp_path):
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    root = enqueue_root(
        repository,
        page_size=1,
        max_pages=1,
        max_messages=1,
    )
    claimed = repository.claim_jobs(
        job_type="email.projection_backfill.v1",
        worker_id="worker-a",
        limit=1,
        lease_seconds=60,
        now=datetime.now(UTC),
    )[0]

    assert repository.checkpoint_payload(
        job_id=root["job_id"],
        worker_id="worker-b",
        fencing_token=int(claimed["lease_fencing_token"]),
        payload={"changed": True},
    ) is False
    assert repository.checkpoint_payload(
        job_id=root["job_id"],
        worker_id="worker-a",
        fencing_token=int(claimed["lease_fencing_token"]),
        payload={"changed": True},
    ) is True
    assert repository.get_job(root["job_id"])["payload"] == {"changed": True}
    repository.close()
