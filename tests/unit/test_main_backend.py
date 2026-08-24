from pathlib import Path
import shutil
from uuid import uuid4

from app.core.main_backend import OllamaMainConversationBackend, OllamaMainRepairBackend
from app.db.sqlite_store import SQLiteStore
from app.skills.registry_service import SkillRegistryService


def _make_scratch_dir() -> Path:
    profile_dir = Path("data") / "test_prompt_profiles" / str(uuid4())
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def test_main_backend_build_prompt_includes_identity_and_capability_profiles():
    profile_dir = _make_scratch_dir()
    (profile_dir / "jarvis_identity.md").write_text("IDENTITY_MARKER_JARVIS", encoding="utf-8")
    (profile_dir / "jarvis_capabilities.md").write_text("CAPABILITY_MARKER_JARVIS", encoding="utf-8")

    try:
        backend = OllamaMainRepairBackend(
            base_url="http://localhost:11434",
            model="test-model",
            prompt_profile_dir=str(profile_dir),
        )

        prompt = backend._build_prompt(
            text="set the heat to 68",
            context={},
        )

        assert "IDENTITY_MARKER_JARVIS" in prompt
        assert "CAPABILITY_MARKER_JARVIS" in prompt
    finally:
        shutil.rmtree(profile_dir.parent, ignore_errors=True)


def test_main_backend_build_prompt_uses_fallback_when_profiles_missing():
    profile_dir = _make_scratch_dir()
    try:
        backend = OllamaMainRepairBackend(
            base_url="http://localhost:11434",
            model="test-model",
            prompt_profile_dir=str(profile_dir),
        )

        prompt = backend._build_prompt(
            text="show grocery list",
            context={},
        )

        assert "Identity and behavior profile:" in prompt
        assert "(not provided)" in prompt
    finally:
        shutil.rmtree(profile_dir.parent, ignore_errors=True)


def test_main_conversation_backend_prompt_includes_profiles():
    profile_dir = _make_scratch_dir()
    (profile_dir / "jarvis_identity.md").write_text("IDENTITY_MARKER_JARVIS", encoding="utf-8")
    (profile_dir / "jarvis_capabilities.md").write_text("CAPABILITY_MARKER_JARVIS", encoding="utf-8")
    (profile_dir / "jarvis_conversation_skill.md").write_text("CONVERSATION_SKILL_MARKER", encoding="utf-8")

    try:
        backend = OllamaMainConversationBackend(
            base_url="http://localhost:11434",
            model="test-model",
            prompt_profile_dir=str(profile_dir),
        )

        prompt = backend._build_prompt(
            text="teach me how to make pasta",
            context={"micro_intent": "unknown"},
        )

        assert "IDENTITY_MARKER_JARVIS" in prompt
        assert "CAPABILITY_MARKER_JARVIS" in prompt
        assert "CONVERSATION_SKILL_MARKER" not in prompt
    finally:
        shutil.rmtree(profile_dir.parent, ignore_errors=True)


def test_main_conversation_backend_loads_child_reading_level_persona(tmp_path):
    store = SQLiteStore(database_path=str(tmp_path / "child-profile.db"))
    registry = SkillRegistryService(sqlite_store=store)
    registry.seed_defaults()
    backend = OllamaMainConversationBackend(
        base_url="http://localhost:11434",
        model="test-model",
        skill_registry=registry,
    )

    try:
        prompt = backend._build_prompt(
            text="Why does the moon change shape?",
            context={"agent_id": "child", "requested_by_user_id": "child"},
        )

        assert "You are Jarvis while speaking with a child." in prompt
        assert "Use common words, short sentences" in prompt
        assert "without baby talk or assumptions about age, interests, or identity" in prompt
        assert "This is a conversation-only profile." in prompt
    finally:
        store.close()


def test_main_conversation_backend_clean_response_extracts_direct_message_from_structured_dump():
    structured = (
        "Based on the provided hints and user input, here’s a structured breakdown.\n\n"
        "### Output Schema:\n"
        "- **Message**: \"Monkeys primarily eat fruits, leaves, and sometimes insects.\"\n"
        "### Summary:\n"
        "The Conversation Skill handled this successfully."
    )

    cleaned = OllamaMainConversationBackend._clean_response(structured)

    assert cleaned == "Monkeys primarily eat fruits, leaves, and sometimes insects."


def test_main_repair_backend_includes_relevant_skill_profile_on_demand():
    class RegistryStub:
        def load_model_boot_memory(self, *, model_name: str, agent_id: str):
            return [
                {"doc_path": "app/prompts/jarvis_identity.md", "content": "IDENTITY", "priority": 20},
                {"doc_path": "app/prompts/jarvis_loop.md", "content": "LOOP", "priority": 30},
                {"doc_path": "app/prompts/jarvis_capabilities.md", "content": "CAPS", "priority": 40},
                {"doc_path": "app/prompts/agent_registry.md", "content": "REGISTRY", "priority": 50},
                {"doc_path": "app/prompts/jarvis_system.md", "content": "SYSTEM", "priority": 60},
                {"doc_path": "app/prompts/personas/jarvis_persona.md", "content": "PERSONA", "priority": 10},
            ]

        def load_skill_docs_for_intents(self, *, intents: list[str], user_id: str, agent_id: str):
            if "lists.add_item" in [str(item) for item in intents]:
                return [{"content": "LISTS_SKILL_MARKER"}]
            return []

    backend = OllamaMainRepairBackend(
        base_url="http://localhost:11434",
        model="test-model",
        skill_registry=RegistryStub(),
    )
    prompt = backend._build_prompt(
        text="add milk to groceries",
        context={
            "agent_id": "jarvis",
            "micro_intent": "lists.add_item",
            "requested_by_user_id": "jordan",
        },
    )

    assert "LISTS_SKILL_MARKER" in prompt


def test_main_backend_deduplicates_identical_identity_and_persona_profiles():
    class RegistryStub:
        def load_model_boot_memory(self, *, model_name: str, agent_id: str):
            return [
                {"doc_path": "app/prompts/jarvis_identity.md", "content": "SAME PROFILE"},
                {"doc_path": "app/prompts/personas/jarvis_persona.md", "content": "SAME PROFILE"},
            ]

    backend = OllamaMainConversationBackend(
        base_url="http://localhost:11434",
        model="test-model",
        skill_registry=RegistryStub(),
    )

    prompt = backend._build_prompt(text="hello", context={"agent_id": "jarvis"})

    assert prompt.count("SAME PROFILE") == 1


def test_main_backend_keeps_eight_recent_turns_with_more_followup_context():
    backend = OllamaMainConversationBackend(base_url="http://localhost:11434", model="test-model")
    turns = [{"role": "user", "text": f"turn-{index} " + ("x" * 200)} for index in range(10)]

    prompt = backend._build_prompt(text="what did I mean?", context={"recent_turns": turns})

    assert "turn-1 " not in prompt
    assert "turn-2 " in prompt
    assert "turn-9 " in prompt
    assert "x" * 160 in prompt


def test_main_repair_prompt_includes_email_actions_and_scoped_capability_catalog():
    backend = OllamaMainRepairBackend(base_url="http://localhost:11434", model="test-model")
    prompt = backend._build_prompt(
        text="summarize today's emails",
        context={
            "runtime_capability_catalog": [
                {
                    "skill_id": "skill.email.agent",
                    "skill_name": "Shared Email Agent",
                    "intents": ["email.list_recent", "email.summarize"],
                    "main_intents": ["email.list_recent", "email.summarize"],
                    "main_enabled": True,
                    "micro_enabled": False,
                    "micro_intents": [],
                    "configured": True,
                    "authorized_here": True,
                    "availability": "available",
                    "access_note": "Available in this private channel.",
                    "execution_ref": "must-not-leak",
                    "storage_ref": "must-not-leak",
                }
            ]
        },
    )

    assert "email.list_recent" in prompt
    assert "summarize today's emails" in prompt
    assert '"authorized_here":true' in prompt
    assert '"main_intents":["email.list_recent","email.summarize"]' in prompt
    assert "must-not-leak" not in prompt


def test_main_conversation_prompt_can_explain_micro_from_runtime_catalog():
    backend = OllamaMainConversationBackend(base_url="http://localhost:11434", model="test-model")
    prompt = backend._build_prompt(
        text="what can Micro do?",
        context={
            "runtime_capability_catalog": [
                {
                    "skill_id": "skill.lists.core",
                    "skill_name": "Lists",
                    "intents": ["lists.add_item", "lists.create_list"],
                    "main_intents": ["lists.add_item", "lists.create_list"],
                    "main_enabled": True,
                    "micro_enabled": True,
                    "micro_intents": ["lists.add_item"],
                    "configured": True,
                    "authorized_here": True,
                    "availability": "available",
                }
            ]
        },
    )

    assert "Answer capability questions about both Main and Micro" in prompt
    assert "explicit ! commands" in prompt
    assert '"micro_intents":["lists.add_item"]' in prompt


def test_main_turn_decision_prompt_enforces_action_commitment_boundary():
    backend = OllamaMainConversationBackend(base_url="http://localhost:11434", model="test-model")
    prompt = backend._build_turn_decision_prompt(
        text="all unread",
        context={
            "recent_turns": [
                {"role": "user", "text": "can you summarize my emails"},
                {"role": "assistant", "text": "Which messages should I include?"},
            ],
            "runtime_capability_catalog": [
                {
                    "skill_id": "skill.email.agent",
                    "skill_name": "Shared Email Agent",
                    "intents": ["email.list_recent"],
                    "main_intents": ["email.list_recent"],
                    "configured": True,
                    "authorized_here": True,
                    "intent_contracts": [
                        {
                            "intent": "email.list_recent",
                            "purpose": "List or summarize a collection of recent messages.",
                            "operation": "read",
                            "entity_fields": ["query"],
                        }
                    ],
                }
            ],
        },
    )

    assert "conversation, clarify_action, or execute_action" in prompt
    assert "the router will execute only a valid action envelope" in prompt
    assert "Never put a promise" in prompt
    assert "A short follow-up can complete an action" in prompt
    assert '"main_intents":["email.list_recent"]' in prompt
    assert "List or summarize a collection of recent messages" in prompt
    assert "do not invent field names" in prompt
    assert "identify the requested object scope/cardinality" in prompt
    assert "Select by semantic purpose" in prompt
    assert "a clarification must not change the requested operation or scope" in prompt
    assert "all unread" in prompt


def test_main_turn_decision_backend_parses_json_without_ollama_format_flag(monkeypatch):
    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "response": (
                    '{"mode":"execute_action","intent":"email.list_recent",'
                    '"confidence":0.96,"reasoning":"ready","entities":{"query":"all unread"},'
                    '"missing_fields":[],"message":"","question":null,"source":"backend"}'
                )
            }

    calls = []

    def fake_post(url, *, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("app.core.main_backend.httpx.post", fake_post)
    backend = OllamaMainConversationBackend(base_url="http://localhost:11434", model="test-model")

    decision = backend.decide_turn(text="all unread", context={})

    assert decision is not None
    assert decision["mode"] == "execute_action"
    assert "format" not in calls[0]["json"]


def test_main_turn_decision_loads_compact_contracts_for_authorized_candidate_skills():
    class RegistryStub:
        def __init__(self):
            self.intent_calls = []

        def load_model_boot_memory(self, *, model_name: str, agent_id: str):
            return []

        def load_skill_runtime_docs_for_intents(self, *, intents: list[str], user_id: str, agent_id: str):
            self.intent_calls.append(list(intents))
            if "email.list_recent" in intents:
                return [
                    {
                        "content": (
                            "COLLECTION_CONTRACT: plural inbox summaries use email.list_recent; "
                            "email.summarize requires one E reference."
                        )
                    }
                ]
            return []

        load_skill_docs_for_intents = load_skill_runtime_docs_for_intents

    registry = RegistryStub()
    backend = OllamaMainConversationBackend(
        base_url="http://localhost:11434",
        model="test-model",
        skill_registry=registry,
    )

    prompt = backend._build_turn_decision_prompt(
        text="can you summarize my emails",
        context={
            "micro_intent": "conversation.general",
            "runtime_skill_intents": ["conversation.general"],
            "runtime_capability_catalog": [
                {
                    "main_intents": ["email.list_recent", "email.summarize"],
                    "configured": True,
                    "authorized_here": True,
                },
                {
                    "main_intents": ["home.set_switch"],
                    "configured": True,
                    "authorized_here": False,
                },
            ],
            "requested_by_user_id": "jordan",
            "agent_id": "jarvis",
        },
    )

    assert registry.intent_calls == [[
        "conversation.general",
        "email.list_recent",
        "email.summarize",
    ]]
    assert "COLLECTION_CONTRACT" in prompt
    assert "home.set_switch" not in registry.intent_calls[0]
