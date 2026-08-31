import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from app.core.types import Intent, SessionOwner, SessionState
from app.core.persistence_policy import persistence_policy
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.db.sqlite_store import SQLiteStore
from app.memory.composite_memory_store import CompositeMemoryStore
from app.memory.markdown_memory_store import MarkdownMemoryStore
from app.memory.sqlite_memory_store import SQLiteMemoryStore
from app.schemas.api import AskRequest
from app.services.event_log import EventLogService
from app.services.memory_service import MemoryService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


def test_router_persists_sessions_events_and_memory():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "jarvis_test.db"
        md_path = scratch / "memory_md"
        store = SQLiteStore(database_path=str(db_path))
        memory_chain = CompositeMemoryStore(
            stores=[
                SQLiteMemoryStore(store),
                MarkdownMemoryStore(base_dir=str(md_path)),
            ]
        )
        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(persistence=store),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(persistence=store),
            memory_service=MemoryService(store=memory_chain),
            lists_service=ListsService(),
            calendar_service=CalendarService(),
            home_service=HomeService(),
        )

        response = router.route(
            AskRequest(
                text="add milk to groceries",
                session_id="persist-1",
                user_id="jordan",
                source="web",
            )
        )

        assert response["result"]["status"] == "ok"

        session = store.get_session("persist-1")
        assert session is not None
        assert session["owner"] == "micro_jarvis"

        events = store.recent_events(limit=20)
        assert any(item["event_type"] == "input.received" for item in events)
        assert any(item["event_type"] == "tool.executed" for item in events)
        received = next(item for item in events if item["event_type"] == "input.received")
        assert "text" not in received["payload"]
        assert "normalized_text" not in received["payload"]
        assert received["payload"]["text_chars"] == len("add milk to groceries")
        micro = next(item for item in events if item["event_type"] == "micro.decision")
        assert "entities" not in micro["payload"]
        assert micro["payload"]["entity_fields"]

        memories = store.recent_memory_entries(limit=20)
        assert len(memories) == 1
        assert memories[0].request_text == "add milk to groceries"

        markdown_files = list(md_path.rglob("*.md"))
        assert markdown_files, "Expected markdown memory mirror files to be written."
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_document_intent_fails_closed_when_skill_error_loses_policy_marker():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    canary = "DOCUMENT-QUERY-MUST-NOT-PERSIST"

    try:
        store = SQLiteStore(database_path=str(scratch / "jarvis_test.db"))
        memory_chain = CompositeMemoryStore(
            stores=[
                SQLiteMemoryStore(store),
                MarkdownMemoryStore(base_dir=str(scratch / "memory_md")),
            ]
        )
        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(persistence=store),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(persistence=store),
            memory_service=MemoryService(store=memory_chain),
            lists_service=ListsService(),
            calendar_service=CalendarService(),
            home_service=HomeService(),
        )
        session = router._session_store.get_or_create(
            "document-policy-failure",
            "operator",
            "web",
        )
        session.owner = SessionOwner.MAIN
        session.state = SessionState.IDLE

        response = router._build_response(
            session=session,
            intent=Intent.DOCUMENTS_FIND,
            classification={
                "intent": "documents.find",
                "confidence": 1.0,
                "entities": {"query": canary},
                "recommended_owner": "main_jarvis",
            },
            route="main_jarvis_repair",
            result={"status": "error", "message": "Skill execution failed."},
            request_text=f"search documents for {canary}",
            user_id="operator",
        )

        assert response["delivery"]["memory"]["status"] == "not_applicable"
        assert response["delivery"]["conversation_history"]["status"] == "not_applicable"
        assert response["delivery"]["ticket"]["status"] == "not_applicable"
        assert not store.recent_memory_entries(limit=20)
        persisted_session = store.get_session("document-policy-failure")
        assert canary not in str(persisted_session)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_typed_seam_strengthens_legacy_policy_aliases_without_changing_legacy_default():
    assert persistence_policy("restricted_read").name.value == "restricted_read"
    assert persistence_policy(
        "restricted_read",
        canonicalize_legacy_aliases=True,
    ).name.value == "no_store"
    assert persistence_policy(
        "sensitive_domain",
        canonicalize_legacy_aliases=True,
    ).name.value == "redacted"


@pytest.mark.parametrize("policy", ["redacted", "no_store"])
def test_typed_tool_policy_keeps_live_answer_out_of_every_generic_sink(policy):
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    canary = f"TOOL-{policy.upper()}-PRIVATE-CONTENT"

    try:
        store = SQLiteStore(database_path=str(scratch / "jarvis_test.db"))
        memory_chain = CompositeMemoryStore(
            stores=[
                SQLiteMemoryStore(store),
                MarkdownMemoryStore(base_dir=str(scratch / "memory_md")),
            ]
        )
        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(persistence=store),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(persistence=store),
            memory_service=MemoryService(store=memory_chain),
            lists_service=ListsService(),
            calendar_service=CalendarService(),
            home_service=HomeService(),
        )
        session = router._session_store.get_or_create(
            f"typed-policy-{policy}",
            "operator",
            "discord",
        )
        session.owner = SessionOwner.MAIN
        session.state = SessionState.IDLE

        response = router._build_response(
            session=session,
            intent=Intent.UNKNOWN,
            classification={
                "intent": "unknown",
                "confidence": 1.0,
                "entities": {},
                "recommended_owner": "main_jarvis",
            },
            route="main_tool_loop",
            result={
                "status": "responded",
                "message": f"Live answer containing {canary}",
                "_persistence_policy": policy,
            },
            request_text=f"private request containing {canary}",
            user_id="operator",
        )

        assert canary in response["assistant"]["text"]
        assert response["delivery"]["memory"]["status"] == "not_applicable"
        assert response["delivery"]["conversation_history"]["status"] == "not_applicable"
        assert response["delivery"]["ticket"]["status"] == "not_applicable"
        assert not store.recent_memory_entries(limit=20)
        assert canary not in str(store.get_session(f"typed-policy-{policy}"))
        assert canary not in str(store.recent_events(limit=100))
        markdown_files = list((scratch / "memory_md").rglob("*.md"))
        assert markdown_files == []
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
