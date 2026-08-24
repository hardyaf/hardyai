import shutil
from pathlib import Path
from uuid import uuid4

from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
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

        memories = store.recent_memory_entries(limit=20)
        assert len(memories) == 1
        assert memories[0].request_text == "add milk to groceries"

        markdown_files = list(md_path.rglob("*.md"))
        assert markdown_files, "Expected markdown memory mirror files to be written."
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
