from __future__ import annotations

from app.db.sqlite_store import SQLiteStore
from app.tickets.repository import TicketRepository
from app.tickets.service import ActionTicketService
from app.tickets.types import TicketStatus
from app.core.main_jarvis import MainJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


def _capture(service: ActionTicketService, **overrides):
    values = {
        "request_id": "request-1",
        "session_id": "session-1",
        "context_reference": {},
        "user_id": "user-1",
        "agent_id": "jarvis",
        "source": "test",
        "intent": "lists.add_item",
        "skill_id": "skill.lists.core",
        "route": "micro_tool",
        "request_text": "add milk to groceries",
        "classification": {"intent": "lists.add_item", "confidence": 0.99, "entities": {}},
        "result_with_internal": {"status": "needs_input", "question": "Which list?"},
        "dialog": {"turn_complete": False, "mode": "command_action"},
        "assistant_text": "Which list?",
    }
    values.update(overrides)
    return service.capture_response(**values)


def test_conversation_is_excluded_but_action_clarifications_share_one_ticket(tmp_path):
    path = tmp_path / "capture.db"
    SQLiteStore(database_path=str(path)).close()
    repo = TicketRepository(database_path=str(path))
    service = ActionTicketService(
        repository=repo,
        enabled=True,
        review_delay_seconds=3600,
        review_max_attempts=3,
    )
    try:
        conversation = _capture(
            service,
            intent="conversation.general",
            skill_id="skill.conversation.general",
            route="main_jarvis",
            request_text="hello",
            result_with_internal={"status": "conversation", "message": "Hi"},
            dialog={"turn_complete": True},
            assistant_text="Hi",
        )
        assert conversation.ticket is None

        first = _capture(service)
        assert first.ticket["status"] == "waiting_clarification"
        second = _capture(
            service,
            request_id="request-2",
            context_reference=first.context_reference,
            request_text="groceries",
            result_with_internal={"status": "needs_input", "question": "What item?"},
            assistant_text="What item?",
        )
        assert second.ticket["ticket_id"] == first.ticket["ticket_id"]
        entries = repo.list_entries(first.ticket["ticket_id"])
        assert [item["entry_type"] for item in entries].count("user_clarification") == 1
        assert [item["verbatim_text"] for item in entries if item["actor_type"] == "user"] == [
            "add milk to groceries",
            "groceries",
        ]
    finally:
        repo.close()


def test_document_requests_never_open_action_tickets_even_when_forced(tmp_path):
    path = tmp_path / "document-ticket-boundary.db"
    SQLiteStore(database_path=str(path)).close()
    repo = TicketRepository(database_path=str(path))
    service = ActionTicketService(
        repository=repo,
        enabled=True,
        review_delay_seconds=3600,
        review_max_attempts=3,
    )
    try:
        capture = service.begin_request(
            request_id="document-request-1",
            session_id="document-session-1",
            context_reference={},
            user_id="operator",
            agent_id="jarvis",
            source="web",
            intent="documents.find",
            skill_id="skill.documents.local",
            route="main_jarvis_commitment",
            request_text="search documents for a private phrase",
            classification={
                "intent": "documents.find",
                "entities": {"query": "a private phrase"},
            },
            force=True,
        )
        assert capture.ticket is None
        assert capture.context_reference == {}
        assert repo.list_tickets(limit=20) == []
        assert repo.list_jobs(limit=20) == []
    finally:
        repo.close()


def test_duplicate_external_request_replays_without_second_domain_write(tmp_path):
    path = tmp_path / "dedupe.db"
    store = SQLiteStore(database_path=str(path))
    repo = TicketRepository(database_path=str(path))
    tickets = ActionTicketService(
        repository=repo,
        enabled=True,
        review_delay_seconds=3600,
        review_max_attempts=3,
    )
    lists = ListsService(default_list_names=["groceries"], sqlite_store=store)
    router = JarvisRouter(
        main_jarvis=MainJarvis(),
        session_store=SessionStore(persistence=store),
        runtime_power=RuntimePowerController(),
        event_log=EventLogService(persistence=store),
        memory_service=None,
        lists_service=lists,
        calendar_service=CalendarService(),
        home_service=HomeService(sqlite_store=store, default_switch_names=["office test light"]),
        action_ticket_service=tickets,
    )
    request = AskRequest(
        text="add eggs to groceries",
        request_id="discord:message-123",
        user_id="jordan",
        source="discord",
        context={
            "external_user_id": "123",
            "auto_channel_session": True,
            "channel_session_scope": "per_user",
            "session_channel": "discord.guild.1.channel.2",
        },
    )
    try:
        first = router.route(request)
        second = router.route(request)
        assert first["ticket"]["ticket_id"] == second["ticket"]["ticket_id"]
        assert second["result"]["idempotent_replay"] is True
        assert lists.get_items("groceries", owner_user_id="all")["items"] == ["eggs"]
        entries = repo.list_entries(first["ticket"]["ticket_id"])
        assert [item["entry_type"] for item in entries].count("user_request") == 1
    finally:
        repo.close()
        store.close()


def test_replay_ignores_assistant_success_prose_and_labels_limited_truth(tmp_path):
    path = tmp_path / "truthful-replay.db"
    repo = TicketRepository(database_path=str(path))
    service = ActionTicketService(
        repository=repo,
        enabled=True,
        review_delay_seconds=3600,
        review_max_attempts=3,
    )
    try:
        calendar = repo.create_ticket(
            origin_request_id="calendar-request",
            session_id="session",
            user_id="user-1",
            agent_id="jarvis",
            source="test",
            intent="calendar.create_event",
            skill_id="skill.calendar.core",
            route="micro_tool",
            title="calendar action",
            status=TicketStatus.UNVERIFIABLE,
        )
        repo.append_entry(
            ticket_id=str(calendar["ticket_id"]),
            request_id="calendar-request",
            entry_type="assistant_response",
            actor_type="assistant",
            verbatim_text="I definitely created it.",
            structured_payload={"result": {"status": "ok", "invented": True}},
            dedupe_key="calendar-assistant-prose",
        )
        replay = service.replay_response("calendar-request")
        assert replay["assistant_text"] == (
            "This replay was reconstructed from durable execution records."
        )
        assert replay["result"]["truth_scope"] == "local_calendar_unverifiable"
        assert "invented" not in replay["result"]
        assert replay["result"]["receipt_refs"] == []

        home = repo.create_ticket(
            origin_request_id="home-request",
            session_id="session",
            user_id="user-1",
            agent_id="jarvis",
            source="test",
            intent="home.set_switch",
            skill_id="skill.home.core",
            route="micro_tool",
            title="home action",
            status=TicketStatus.UNVERIFIABLE,
        )
        receipt = repo.record_operation_receipt(
            ticket_id=str(home["ticket_id"]),
            receipt={
                "operation_id": "home-operation",
                "idempotency_key": "home-receipt",
                "capability": "home.set_switch",
                "action": "set_switch",
                "resource_key": "home:office-light",
                "status": "committed",
                "committed_at": "2026-09-06T00:00:00+00:00",
                "expected_effect": {"switch_state": "on"},
                "validator_name": "home.sqlite_simulated",
                "validator_version": "1",
                "resource_locator": {"switch": "office-light"},
                "execution_observation": {},
                "result": {"status": "ok"},
            },
        )
        assert receipt["operation_id"] == "home-operation"
        replay = service.replay_response("home-request")
        assert replay["result"]["truth_scope"] == (
            "simulated_state_only_not_physical_device_truth"
        )
        assert replay["result"]["receipt_refs"] == ["home-operation"]
    finally:
        repo.close()
