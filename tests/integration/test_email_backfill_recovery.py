from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.jobs.repository import DurableJobRepository
from scripts.manage_email_backfill import backfill_status, enqueue_root, run_once


class RestartableBackfillService:
    def __init__(self) -> None:
        self.tokens: list[str | None] = []

    def process_historical_backfill_page(self, *, page_token, page_size):
        self.tokens.append(page_token)
        if page_token is None:
            return {
                "next_page_token": "page-2",
                "candidate_count": 2,
                "accepted_count": 2,
            }
        return {
            "next_page_token": None,
            "candidate_count": 1,
            "accepted_count": 1,
        }


def test_continuation_survives_crash_after_checkpoint_before_parent_completion(
    tmp_path,
    monkeypatch,
):
    repository = DurableJobRepository(str(tmp_path / "copied-core.db"))
    root = enqueue_root(
        repository,
        page_size=10,
        max_pages=10,
        max_messages=100,
    )
    service = RestartableBackfillService()
    original_complete = repository.complete_job
    calls = 0

    def fail_first_completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        return original_complete(**kwargs)

    monkeypatch.setattr(repository, "complete_job", fail_first_completion)
    first = run_once(repository, email_agent_service=service)
    future = datetime.now(UTC) + timedelta(minutes=2)
    second = run_once(repository, email_agent_service=service, now=future)
    third = run_once(repository, email_agent_service=service, now=future)
    status = backfill_status(
        repository,
        backfill_id=root["backfill_id"],
        limit=100,
    )

    assert first["status"] == "retry"
    assert second["status"] == "completed"
    assert third["status"] == "continued"
    assert service.tokens == [None, "page-2", None]
    assert status["job_counts"] == {"completed": 2}
    assert status["outcomes"]["accepted_count"] == 3
    assert status["provider_pagination_complete"] is True
    repository.close()
