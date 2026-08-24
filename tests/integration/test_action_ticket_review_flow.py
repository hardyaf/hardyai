from __future__ import annotations

from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lists.handler import run as run_lists
from app.skills.domains.lists.receipts import build_operation_receipt
from app.tickets.context_builder import ReviewContextBuilder
from app.tickets.remediation_policy import RemediationPolicy
from app.tickets.remediation_service import RemediationService
from app.tickets.repository import TicketRepository
from app.tickets.review_backend import EvidenceOnlyReviewBackend
from app.tickets.review_service import TicketReviewService
from app.tickets.service import ActionTicketService
from app.tickets.verifier_registry import VerifierRegistry
from app.tickets.verifiers.lists import ListsSourceVerifier
from app.tools.lists_service import ListsService


def _capture_add(*, repository, lists, item: str):
    request_id = f"add-{item}"
    context = {
        "request_id": request_id,
        "requested_by_user_id": "jordan",
        "list_owner_user_id": "jordan",
        "agent_id": "jarvis",
        "source_interface": "test",
    }
    classification = {
        "intent": "lists.add_item",
        "confidence": 1.0,
        "entities": {"list_name": "groceries", "item_text": item},
    }
    tickets = ActionTicketService(
        repository=repository,
        enabled=True,
        review_delay_seconds=0,
        review_max_attempts=3,
    )
    started = tickets.begin_request(
        request_id=request_id,
        session_id="review-flow",
        context_reference={},
        user_id="jordan",
        agent_id="jarvis",
        source="test",
        intent="lists.add_item",
        skill_id="skill.lists.core",
        route="micro_tool",
        request_text=f"add {item} to groceries",
        classification=classification,
    )
    result = run_lists(
        intent="lists.add_item",
        entities={"list_name": "groceries", "item_text": item},
        services={"lists_service": lists},
        context=context,
    )
    receipt = build_operation_receipt(
        intent="lists.add_item",
        entities={"list_name": "groceries", "item_text": item},
        context=context,
        result=result,
        services={"lists_service": lists},
    )
    internal = dict(result)
    internal["_operation_receipt"] = receipt
    captured = tickets.capture_response(
        request_id=request_id,
        session_id="review-flow",
        context_reference=started.context_reference,
        user_id="jordan",
        agent_id="jarvis",
        source="test",
        intent="lists.add_item",
        skill_id="skill.lists.core",
        route="micro_tool",
        request_text=f"add {item} to groceries",
        classification=classification,
        result_with_internal=internal,
        dialog={"turn_complete": True, "mode": "command_action"},
        assistant_text=f"Added {item}.",
    )
    return captured.ticket


def _review_service(*, repository, lists, auto_remediation: bool):
    registry = VerifierRegistry()
    registry.register(ListsSourceVerifier(lists_service=lists))
    remediation = RemediationService(
        repository=repository,
        lists_service=lists,
        review_delay_seconds=0,
        review_max_attempts=3,
    )
    return TicketReviewService(
        repository=repository,
        verifier_registry=registry,
        context_builder=ReviewContextBuilder(repository=repository, max_chars=20_000),
        review_backend=EvidenceOnlyReviewBackend(),
        remediation_policy=RemediationPolicy(max_generation=3),
        remediation_service=remediation,
        auto_remediation_enabled=auto_remediation,
    )


def test_correct_list_action_verifies_from_current_source(tmp_path):
    path = tmp_path / "review.db"
    store = SQLiteStore(database_path=str(path))
    lists = ListsService(default_list_names=["groceries"], sqlite_store=store)
    repository = TicketRepository(database_path=str(path))
    try:
        ticket = _capture_add(repository=repository, lists=lists, item="milk")
        job = repository.claim_jobs(
            job_type="ticket_review", worker_id="test", limit=1, lease_seconds=60
        )[0]
        outcome = _review_service(
            repository=repository, lists=lists, auto_remediation=False
        ).process_job(job)
        assert outcome["ticket"]["status"] == "verified"
        assert repository.list_review_runs(ticket["ticket_id"])[0]["model_verdict"] == "correct"
    finally:
        repository.close()
        store.close()


def test_incorrect_list_action_creates_and_executes_bounded_child_repair(tmp_path):
    path = tmp_path / "repair.db"
    store = SQLiteStore(database_path=str(path))
    lists = ListsService(default_list_names=["groceries"], sqlite_store=store)
    repository = TicketRepository(database_path=str(path))
    try:
        parent = _capture_add(repository=repository, lists=lists, item="eggs")
        removed = lists.remove_item("groceries", "eggs", owner_user_id="all")
        assert removed["status"] == "ok"
        job = repository.claim_jobs(
            job_type="ticket_review", worker_id="test", limit=1, lease_seconds=60
        )[0]
        outcome = _review_service(
            repository=repository, lists=lists, auto_remediation=True
        ).process_job(job)
        assert outcome["status"] == "remediation_queued"
        child = outcome["child_ticket"]
        assert child["parent_ticket_id"] == parent["ticket_id"]
        assert child["remediation_generation"] == 1
        assert lists.get_items("groceries", owner_user_id="all")["items"] == ["eggs"]

        child_job = next(
            item
            for item in repository.claim_jobs(
                job_type="ticket_review", worker_id="child", limit=5, lease_seconds=60
            )
            if item["aggregate_id"] == child["ticket_id"]
        )
        child_outcome = _review_service(
            repository=repository, lists=lists, auto_remediation=True
        ).process_job(child_job)
        assert child_outcome["ticket"]["status"] == "verified"
    finally:
        repository.close()
        store.close()

