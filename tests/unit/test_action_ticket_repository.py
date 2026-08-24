from __future__ import annotations

from datetime import timedelta

from app.db.sqlite_store import SQLiteStore
from app.tickets.repository import TicketRepository
from app.tickets.types import JobStatus, TicketStatus, iso_utc, utc_now
from app.workers.ticket_review_worker import TicketReviewWorker


def test_migrations_enable_sqlite_durability_and_are_idempotent(tmp_path):
    path = tmp_path / "tickets.db"
    store = SQLiteStore(database_path=str(path))
    repo = TicketRepository(database_path=str(path))
    try:
        assert repo._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert repo._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert repo._conn.execute("PRAGMA user_version").fetchone()[0] >= 2
        assert "operation_id" in {
            row[1] for row in repo._conn.execute("PRAGMA table_info(list_items)").fetchall()
        }
        second = TicketRepository(database_path=str(path))
        second.close()
    finally:
        repo.close()
        store.close()


def test_job_claim_is_atomic_and_expired_leases_are_bounded(tmp_path):
    path = tmp_path / "jobs.db"
    SQLiteStore(database_path=str(path)).close()
    first = TicketRepository(database_path=str(path))
    second = TicketRepository(database_path=str(path))
    try:
        job = first.enqueue_job(
            job_type="ticket_review",
            aggregate_id="ticket-1",
            idempotency_key="one-job",
            payload={"ticket_id": "ticket-1"},
            max_attempts=2,
        )
        assert len(first.claim_jobs(job_type="ticket_review", worker_id="a", limit=5, lease_seconds=30)) == 1
        assert second.claim_jobs(job_type="ticket_review", worker_id="b", limit=5, lease_seconds=30) == []

        first._conn.execute(
            "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?",
            (iso_utc(utc_now() - timedelta(seconds=1)), job["job_id"]),
        )
        first._conn.commit()
        recovered = second.claim_jobs(
            job_type="ticket_review", worker_id="b", limit=5, lease_seconds=30
        )
        assert len(recovered) == 1
        assert recovered[0]["attempt_count"] == 2
        second.retry_job(
            job_id=str(job["job_id"]),
            worker_id="b",
            error_code="test_failure",
            delay_seconds=0,
        )
        assert second.get_job(str(job["job_id"]))["status"] == JobStatus.DEAD_LETTER.value
    finally:
        second.close()
        first.close()


def test_ticket_entry_dedupe_and_identity_binding(tmp_path):
    path = tmp_path / "ledger.db"
    SQLiteStore(database_path=str(path)).close()
    repo = TicketRepository(database_path=str(path))
    try:
        ticket = repo.create_ticket(
            origin_request_id="request-1",
            session_id="session-1",
            user_id="user-1",
            agent_id="jarvis",
            source="test",
            intent="lists.get_items",
            skill_id="skill.lists.core",
            route="micro_tool",
            title="show groceries",
        )
        first = repo.append_entry(
            ticket_id=ticket["ticket_id"],
            request_id="request-1",
            entry_type="user_request",
            actor_type="user",
            verbatim_text="show groceries",
            dedupe_key="stable-entry",
        )
        replay = repo.append_entry(
            ticket_id=ticket["ticket_id"],
            request_id="request-1",
            entry_type="user_request",
            actor_type="user",
            verbatim_text="show groceries",
            dedupe_key="stable-entry",
        )
        assert first["entry_id"] == replay["entry_id"]
        assert len(repo.list_entries(ticket["ticket_id"])) == 1
        try:
            repo.transition_ticket(ticket_id=ticket["ticket_id"], status=TicketStatus.VERIFIED)
        except ValueError as exc:
            assert "captured -> verified" in str(exc)
        else:  # pragma: no cover - regression guard
            raise AssertionError("invalid lifecycle transition was accepted")
        binding = repo.upsert_identity_binding(
            source="discord",
            external_user_id="123456",
            external_display_name="New Handle",
            user_id="kid-one",
            agent_id="kid_spark",
            age_band="6-8",
            presentation_profile="child_simple",
            policy_profile="child_conversation_only",
        )
        assert binding["external_user_id"] == "123456"
        assert binding["active"] is True
    finally:
        repo.close()


def test_watchdog_reconciles_interrupted_execution_without_receipt(tmp_path):
    path = tmp_path / "watchdog.db"
    SQLiteStore(database_path=str(path)).close()
    repo = TicketRepository(database_path=str(path))
    try:
        ticket = repo.create_ticket(
            origin_request_id="request-interrupted",
            session_id="session-1",
            user_id="user-1",
            agent_id="jarvis",
            source="test",
            intent="lists.add_item",
            skill_id="skill.lists.core",
            route="micro_tool",
            title="add milk",
        )
        repo.transition_ticket(
            ticket_id=str(ticket["ticket_id"]),
            status=TicketStatus.EXECUTING,
        )
        watchdog = repo.enqueue_job(
            job_type="ticket_watchdog",
            aggregate_id=str(ticket["ticket_id"]),
            idempotency_key=f"watchdog:{ticket['ticket_id']}",
            payload={"ticket_id": ticket["ticket_id"]},
            available_at=iso_utc(utc_now() - timedelta(seconds=1)),
        )

        worker = TicketReviewWorker(
            repository=repo,
            review_service=object(),  # No review job is claimed in this regression.
            live_idle_seconds=0,
        )
        results = worker.run_once()

        assert results == [
            {
                "status": "reconciliation_required",
                "ticket_id": ticket["ticket_id"],
            }
        ]
        persisted = repo.get_ticket(str(ticket["ticket_id"]))
        assert persisted["status"] == TicketStatus.RECONCILIATION_REQUIRED.value
        assert persisted["terminal_reason"] == "execution_interrupted_before_receipt"
        assert repo.get_job(str(watchdog["job_id"]))["status"] == JobStatus.COMPLETED.value
    finally:
        repo.close()
