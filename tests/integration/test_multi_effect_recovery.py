from __future__ import annotations

from datetime import timedelta

from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lists.handler import run as run_lists
from app.skills.domains.lists.receipts import build_operation_receipt
from app.tickets.async_receipts import (
    AsyncChildOutcomeSink,
    TicketEffectManifestReservation,
    build_execution_manifest,
)
from app.tickets.remediation_service import RemediationService
from app.tickets.repository import TicketRepository, content_hash
from app.tickets.types import TicketStatus, iso_utc, utc_now
from app.tools.lists_service import ListsService
from app.workers.ticket_review_worker import TicketReviewWorker

class _NoopReviewService:
    def process_job(self, job):
        return {"status": "ignored", "job_id": job["job_id"]}


def _ticket(repository, request_id="multi-request"):
    ticket = repository.create_ticket(
        origin_request_id=request_id,
        session_id="session",
        user_id="user-1",
        agent_id="jarvis",
        source="test",
        intent="lists.add_item",
        skill_id="skill.lists.core",
        route="main_tool_loop",
        title="multi effect recovery",
    )
    return repository.transition_ticket(
        ticket_id=str(ticket["ticket_id"]), status=TicketStatus.EXECUTING
    )


def _manifest(count=1, parent="multi-parent", recovery_hash=None):
    return build_execution_manifest(
        parent_operation_id=parent,
        authorization_binding_hash=content_hash("authorization"),
        effect_cardinality="single" if count == 1 else "independent_batch",
        skill_id="skill.lists.core",
        tool_id="lists.add_item",
        contract_version=1,
        descriptor_hash=content_hash("descriptor"),
        resource_version=1,
        parent_arguments_hash=content_hash("parent arguments"),
        children=[
            {
                "child_operation_id": f"multi-child-{index}",
                "child_index": index,
                "target_hash": content_hash({"target": index}),
                "arguments_hash": content_hash({"arguments": index}),
            }
            for index in range(count)
        ],
        recovery_manifest_hash=recovery_hash,
        sensitivity="normal",
        persistence="standard",
    )


def _receipt(child_id):
    return {
        "operation_id": child_id,
        "idempotency_key": f"ticket-effect-receipt:v1:{child_id}",
        "capability": "lists.add_item",
        "action": "add_item",
        "resource_key": f"list:user-1:{child_id}",
        "status": "committed",
        "committed_at": "2026-09-06T00:00:00+00:00",
        "expected_effect": {"effect_hash": content_hash(child_id)},
        "validator_name": "lists.sqlite",
        "validator_version": "1",
        "resource_locator": {"resource_hash": content_hash(child_id)},
        "execution_observation": {},
        "result": {},
    }


def _due_watchdog(repository, ticket_id, key):
    repository.enqueue_job(
        job_type="ticket_watchdog",
        aggregate_id=ticket_id,
        idempotency_key=key,
        payload={"ticket_id": ticket_id},
        available_at=iso_utc(utc_now() - timedelta(seconds=1)),
        max_attempts=1,
    )


def test_restart_after_domain_effect_before_ticket_receipt_does_not_duplicate_child(tmp_path):
    path = tmp_path / "restart.db"
    repository = TicketRepository(str(path))
    ticket = _ticket(repository)
    ticket_id = str(ticket["ticket_id"])
    recovery_hash = content_hash("private recovery")
    manifest = _manifest(recovery_hash=recovery_hash)
    repository._conn.execute(
        "CREATE TABLE synthetic_effects (operation_id TEXT PRIMARY KEY, effect_count INTEGER NOT NULL, recovery_hash TEXT NOT NULL)"
    )
    repository._conn.commit()

    def reserve_effect(cursor, projection, manifest_hash, expected_recovery_hash):
        del projection, manifest_hash
        row = cursor.execute(
            "SELECT recovery_hash FROM synthetic_effects WHERE operation_id = 'multi-child-0'"
        ).fetchone()
        if row is None:
            cursor.execute(
                "INSERT INTO synthetic_effects VALUES ('multi-child-0', 1, ?)",
                (expected_recovery_hash,),
            )
            status = "created"
        else:
            assert row["recovery_hash"] == expected_recovery_hash
            status = "existing"
        return {"status": status, "recovery_manifest_hash": expected_recovery_hash}

    reservation = TicketEffectManifestReservation(repository).reserve(
        ticket_id=ticket_id,
        request_id="multi-request",
        manifest=manifest,
        domain_reservation=reserve_effect,
    )
    _due_watchdog(repository, ticket_id, "missing-receipt-watchdog")
    first_worker = TicketReviewWorker(
        repository=repository,
        review_service=_NoopReviewService(),
        live_idle_seconds=0,
        review_delay_seconds=0,
    )
    assert first_worker.run_once()[0]["status"] == "reconciliation_required"
    repository.close()

    restarted = TicketRepository(str(path))
    try:
        replay = TicketEffectManifestReservation(restarted).reserve(
            ticket_id=ticket_id,
            request_id="multi-request",
            manifest=manifest,
            domain_reservation=reserve_effect,
        )
        assert replay["domain_reservation"]["status"] == "existing"
        assert restarted._conn.execute(
            "SELECT effect_count FROM synthetic_effects WHERE operation_id = 'multi-child-0'"
        ).fetchone()["effect_count"] == 1
        AsyncChildOutcomeSink(repository=restarted).record_terminal_child(
            parent_operation_id="multi-parent",
            parent_manifest_hash=str(reservation["manifest_hash"]),
            child_operation_id="multi-child-0",
            effect_state="verified",
            receipt=_receipt("multi-child-0"),
        )
        TicketReviewWorker(
            repository=restarted,
            review_service=_NoopReviewService(),
            live_idle_seconds=0,
            review_delay_seconds=0,
        ).run_once()
        assert restarted.get_ticket(ticket_id)["status"] == "verification_pending"
        assert len(restarted.list_receipts(ticket_id)) == 1
    finally:
        restarted.close()


def test_partial_independent_batch_remains_reconcilable_after_watchdog(tmp_path):
    repository = TicketRepository(str(tmp_path / "partial.db"))
    try:
        ticket = _ticket(repository)
        ticket_id = str(ticket["ticket_id"])
        reservation = TicketEffectManifestReservation(repository).reserve(
            ticket_id=ticket_id,
            request_id="multi-request",
            manifest=_manifest(count=2),
        )
        sink = AsyncChildOutcomeSink(repository=repository)
        sink.record_terminal_child(
            parent_operation_id="multi-parent",
            parent_manifest_hash=str(reservation["manifest_hash"]),
            child_operation_id="multi-child-0",
            effect_state="verified",
            receipt=_receipt("multi-child-0"),
        )
        sink.record_terminal_child(
            parent_operation_id="multi-parent",
            parent_manifest_hash=str(reservation["manifest_hash"]),
            child_operation_id="multi-child-1",
            effect_state="dead_letter",
            reason_code="provider_attempts_exhausted",
        )
        results = TicketReviewWorker(
            repository=repository,
            review_service=_NoopReviewService(),
            live_idle_seconds=0,
            review_delay_seconds=0,
        ).run_once()
        assert any(item.get("effect_status") == "partial" for item in results)
        persisted = repository.get_ticket(ticket_id)
        assert persisted["status"] == "reconciliation_required"
        assert persisted["terminal_reason"] == "partial_effect_terminal"
        assert len(repository.list_receipts(ticket_id)) == 1
    finally:
        repository.close()


class _CrashAfterEffectGateway:
    def __init__(self, lists):
        self._lists = lists
        self._outcomes = {}
        self.calls = 0
        self.effects = 0
        self._crashed = False

    def execute_remediation(self, *, operation_id, capability, entities, context):
        self.calls += 1
        if operation_id not in self._outcomes:
            execution_context = {
                **dict(context),
                "list_owner_user_id": str(context.get("requested_by_user_id") or "all"),
            }
            result = run_lists(
                intent=capability,
                entities=dict(entities),
                services={"lists_service": self._lists},
                context=execution_context,
            )
            self.effects += 1
            receipt = build_operation_receipt(
                intent=capability,
                entities=dict(entities),
                context=execution_context,
                result=result,
                services={"lists_service": self._lists},
            )
            receipt["operation_id"] = operation_id
            receipt["idempotency_key"] = f"ticket-effect-receipt:v1:{operation_id}"
            self._outcomes[operation_id] = {
                "authorization_status": "authorized",
                "approval_status": "not_required",
                "result": result,
                "receipt": receipt,
            }
        if not self._crashed:
            self._crashed = True
            raise RuntimeError("crash_after_effect")
        return self._outcomes[operation_id]


def test_remediation_reserves_child_before_effect_and_reuses_operation_after_crash(tmp_path):
    path = tmp_path / "remediation-crash.db"
    store = SQLiteStore(str(path))
    repository = TicketRepository(str(path))
    lists = ListsService(default_list_names=["groceries"], sqlite_store=store)
    try:
        parent = repository.create_ticket(
            origin_request_id="parent-request",
            session_id="session",
            user_id="user-1",
            agent_id="jarvis",
            source="test",
            intent="lists.add_item",
            skill_id="skill.lists.core",
            route="main_tool_loop",
            title="repair list",
            status=TicketStatus.VERIFYING,
        )
        gateway = _CrashAfterEffectGateway(lists)
        service = RemediationService(
            repository=repository,
            lists_service=lists,
            review_delay_seconds=0,
            review_max_attempts=3,
            execution_gateway=gateway,
        )
        first = service.execute(
            parent_ticket=parent,
            capability="lists.add_item",
            entities={"list_name": "groceries", "item_text": "eggs"},
            reason="restore the missing item",
        )
        assert first["status"] == "reconciliation_required"
        assert gateway.effects == 1
        second = service.execute(
            parent_ticket=parent,
            capability="lists.add_item",
            entities={"list_name": "groceries", "item_text": "eggs"},
            reason="restore the missing item",
        )
        assert second["ticket_id"] == first["ticket_id"]
        assert gateway.calls == 2
        assert gateway.effects == 1
        assert lists.get_items("groceries", owner_user_id="all")["items"] == ["eggs"]
        assert len(repository.list_receipts(str(second["ticket_id"]))) == 1
        assert len(repository.list_execution_manifests(str(second["ticket_id"]))) == 1
    finally:
        repository.close()
        store.close()
