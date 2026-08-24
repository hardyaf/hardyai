from __future__ import annotations

import json
from pathlib import Path
import shutil
from uuid import uuid4

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from tests.router_support import RegistryBackedTestRouter as JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.db.sqlite_store import SQLiteStore
from app.schemas.api import AskRequest
from app.services.conversation_history_service import ConversationHistoryService
from app.services.event_log import EventLogService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService


def _workspace_tmp_dir() -> Path:
    root = Path.cwd() / ".pytest_tmp_conversation_history"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"case-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_conversation_history_service_records_topic_and_files():
    root = _workspace_tmp_dir()
    try:
        db_path = root / "jarvis.db"
        history_dir = root / "conversation_history"
        store = SQLiteStore(database_path=str(db_path))
        service = ConversationHistoryService(
            persistence=store,
            base_dir=str(history_dir),
        )

        first = service.record_turn(
            session_id="conv-1",
            user_id="jordan",
            agent_id="jarvis",
            route="main_jarvis",
            intent="conversation.general",
            status="conversation",
            user_text="Who are you exactly?",
            assistant_text="I am Jarvis.",
            metadata={"source": "test"},
        )
        second = service.record_turn(
            session_id="conv-2",
            user_id="jordan",
            agent_id="jarvis",
            route="main_jarvis",
            intent="conversation.general",
            status="conversation",
            user_text="What is your name again?",
            assistant_text="Still Jarvis.",
            metadata={"source": "test"},
        )

        assert first is not None
        assert second is not None
        assert first["topic_key"] == "identity"
        assert second["topic_key"] == "identity"

        topics = store.list_conversation_topics(user_id="jordan", limit=10)
        assert len(topics) == 1
        assert topics[0]["topic_key"] == "identity"
        assert topics[0]["mention_count"] == 2

        history_rows = store.recent_conversation_topic_history(user_id="jordan", limit=10)
        assert len(history_rows) == 2
        assert history_rows[0]["topic_key"] == "identity"
        assert history_rows[1]["topic_key"] == "identity"

        user_dir = history_dir / "jordan"
        history_file = user_dir / "history.jsonl"
        snapshot_file = user_dir / "topics_snapshot.json"
        assert history_file.exists()
        assert snapshot_file.exists()

        lines = [line for line in history_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2
        loaded_line = json.loads(lines[0])
        assert loaded_line["topic_key"] == "identity"

        snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
        assert snapshot["user_id"] == "jordan"
        assert snapshot["topic_count"] == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_router_conversation_turn_writes_conversation_topic_history():
    root = _workspace_tmp_dir()
    try:
        db_path = root / "jarvis.db"
        history_dir = root / "conversation_history"
        store = SQLiteStore(database_path=str(db_path))
        conversation_history = ConversationHistoryService(
            persistence=store,
            base_dir=str(history_dir),
        )

        router = JarvisRouter(
            micro_jarvis=MicroJarvis(),
            main_jarvis=MainJarvis(),
            session_store=SessionStore(persistence=store),
            runtime_power=RuntimePowerController(),
            event_log=EventLogService(persistence=store),
            memory_service=None,
            conversation_history_service=conversation_history,
            lists_service=ListsService(default_list_names=["groceries", "to-do"], sqlite_store=store),
            calendar_service=CalendarService(),
            home_service=HomeService(
                sqlite_store=store,
                default_switch_names=["office test light", "kitchen light"],
            ),
        )

        response = router.route(
            AskRequest(
                text="Who are you?",
                session_id="conversation-topic-router",
                user_id="jordan",
                source="web",
                context={},
            )
        )
        assert response["route"] == "main_jarvis"
        assert response["intent"] in {"conversation.general", "unknown"}

        rows = store.recent_conversation_topic_history(user_id="jordan", limit=10)
        assert len(rows) >= 1
        assert rows[0]["session_id"] == "conversation-topic-router"
        assert rows[0]["topic_key"] == "identity"
        assert rows[0]["intent"] in {"conversation.general", "unknown"}
    finally:
        shutil.rmtree(root, ignore_errors=True)
