from __future__ import annotations

from datetime import timedelta

from app.db.sqlite_store import SQLiteStore
from app.memory.sqlite_memory_store import SQLiteMemoryStore
from app.services.durable_write_service import DurableWriteService, MEMORY_WRITE_JOB
from app.services.memory_service import MemoryService
from app.tickets.repository import TicketRepository
from app.tickets.types import utc_now


def _build(tmp_path):
    database_path = tmp_path / "durable-writes.db"
    sqlite_store = SQLiteStore(database_path=str(database_path))
    memory = MemoryService(store=SQLiteMemoryStore(sqlite_store))
    repository = TicketRepository(database_path=str(database_path))
    service = DurableWriteService(
        repository=repository,
        memory_service=memory,
        worker_id="test-durable-writer",
    )
    return memory, repository, service


def _enqueue(service: DurableWriteService, request_id: str = "request-1") -> dict:
    return service.enqueue_memory_interaction(
        request_id=request_id,
        session_id="session-1",
        user_id="user-1",
        source="dashboard",
        intent="conversation.general",
        route="main_jarvis",
        request_text="hello",
        response_summary="hello back",
        metadata={"owner": "main_jarvis"},
    )


def test_memory_write_is_durable_before_worker_commit(tmp_path):
    memory, repository, service = _build(tmp_path)
    delivery = _enqueue(service)

    assert delivery["status"] == "queued"
    assert memory.recent() == []
    assert repository.get_job(delivery["job_id"])["status"] == "pending"

    assert service.run_once() == 1
    assert len(memory.recent()) == 1
    assert repository.get_job(delivery["job_id"])["status"] == "completed"


def test_recovered_lease_commits_idempotently(tmp_path):
    memory, repository, service = _build(tmp_path)
    delivery = _enqueue(service, request_id="crash-window")
    claimed = repository.claim_jobs(
        job_type=MEMORY_WRITE_JOB,
        worker_id="crashed-worker",
        limit=1,
        lease_seconds=1,
    )
    assert len(claimed) == 1

    service._commit_memory_job(claimed[0])
    reclaimed = repository.claim_jobs(
        job_type=MEMORY_WRITE_JOB,
        worker_id="test-durable-writer",
        limit=1,
        lease_seconds=30,
        now=utc_now() + timedelta(seconds=2),
    )
    assert len(reclaimed) == 1
    service._commit_memory_job(reclaimed[0])
    assert repository.complete_job(
        job_id=delivery["job_id"],
        worker_id="test-durable-writer",
    )

    assert len(memory.recent()) == 1
