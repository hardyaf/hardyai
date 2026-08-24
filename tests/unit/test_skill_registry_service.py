from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app.db.sqlite_store import SQLiteStore
from app.skills.registry_service import SkillRegistryService


def test_skill_registry_resolves_agent_alias_and_skill():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-skill-registry-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "registry.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        context = registry.resolve_agent_context(
            text="hey catparty add milk to groceries",
            fallback_user_id="local_user",
        )
        assert context["agent_id"] == "catparty"
        assert context["resolved_user_id"] == "local_user"
        assert context["normalized_text"] == "add milk to groceries"

        skill = registry.resolve_skill(
            intent="lists.add_item",
            user_id=context["resolved_user_id"],
            agent_id=context["agent_id"],
        )
        assert skill is not None
        assert skill["skill_id"] == "skill.lists.core"
        assert skill["execution_ref"] == "app.skills.domains.lists.handler:run"

        email_skill = registry.resolve_skill(
            intent="email.list_recent",
            user_id="jordan",
            agent_id="jarvis",
        )
        assert email_skill is not None
        assert email_skill["skill_id"] == "skill.email.agent"
        assert email_skill["execution_ref"] == "app.skills.domains.email_agent.handler:run"

        run_id = registry.record_skill_run(
            skill=skill,
            session_id="test-session",
            user_id=context["resolved_user_id"],
            intent="lists.add_item",
            route="micro_tool",
            status="ok",
            confidence=0.92,
        )
        assert isinstance(run_id, str) and run_id

        runs = store.recent_skill_runs(limit=5)
        assert runs
        assert runs[0]["skill_id"] == "skill.lists.core"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_runtime_capability_catalog_is_safe_sql_projection():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-capability-catalog-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        store = SQLiteStore(database_path=str(scratch / "catalog.db"))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        catalog = registry.runtime_capability_catalog(user_id="jordan", agent_id="jarvis")
        by_id = {str(item["skill_id"]): item for item in catalog}

        assert "skill.email.agent" in by_id
        assert "email.list_recent" in by_id["skill.email.agent"]["intents"]
        assert by_id["skill.email.agent"]["micro_enabled"] is False
        assert by_id["skill.lists.core"]["micro_intents"] == [
            "lists.add_item",
            "lists.get_items",
        ]
        assert "execution_ref" not in by_id["skill.email.agent"]
        assert "storage_ref" not in by_id["skill.email.agent"]
        assert "markdown_path" not in by_id["skill.email.agent"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_loads_persona_doc_for_agent_boot_memory():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-boot-memory-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "boot.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        docs = registry.load_model_boot_memory(model_name="jarvis", agent_id="catparty")
        paths = {str(item["doc_path"]) for item in docs}
        assert "app/prompts/jarvis_identity.md" in paths
        assert "app/prompts/jarvis_capabilities.md" in paths
        assert "app/prompts/personas/catparty_persona.md" in paths
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_compiles_critical_skills_markdown():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-critical-skills-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "critical.db"
        output_path = scratch / "critical_skills.md"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        compiled = registry.compile_critical_skills_markdown(
            output_path=str(output_path),
            min_critical_level=1,
        )
        assert compiled["status"] == "ok"
        assert compiled["skill_count"] >= 4
        assert Path(compiled["output_path"]).exists()

        markdown = output_path.read_text(encoding="utf-8")
        assert "# Critical Skills (Compiled)" in markdown
        assert "skill.lists.core" in markdown
        assert "skill.calendar.core" in markdown
        meta_path = Path(str(compiled["metadata_path"]))
        assert meta_path.exists()
        first_hash = str(compiled["source_hash"])

        compiled_cached = registry.compile_critical_skills_markdown(
            output_path=str(output_path),
            min_critical_level=1,
            compile_if_stale=True,
        )
        assert compiled_cached["status"] == "skipped"
        assert str(compiled_cached["source_hash"]) == first_hash

        micro_output_path = scratch / "micro_jarvis_skills.md"
        micro_compiled = registry.compile_micro_skills_markdown(
            output_path=str(micro_output_path),
            compile_if_stale=True,
        )
        assert micro_compiled["status"] == "ok"
        micro_markdown = micro_output_path.read_text(encoding="utf-8")
        assert "lists.add_item" in micro_markdown
        assert "lists.get_items" in micro_markdown
        micro_cached = registry.compile_micro_skills_markdown(
            output_path=str(micro_output_path),
            compile_if_stale=True,
        )
        assert micro_cached["status"] == "skipped"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_sync_from_markdown_sets_micro_contract_and_learnable_flags():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-skill-sync-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "sync.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        sync = registry.sync_skills_from_markdown()
        assert sync["status"] == "ok"
        assert sync["synced_count"] >= 4
        assert sync["failed_count"] == 0
        assert any(str(item.get("markdown_path") or "").endswith("lists_skill.md") for item in sync["synced"])

        skills = {str(skill["skill_id"]): skill for skill in registry.list_skills(active_only=False)}
        lists_skill = skills["skill.lists.core"]
        assert lists_skill["learnable_ready"] is True
        assert lists_skill["active"] is True
        assert lists_skill["micro_enabled"] is True
        assert isinstance(lists_skill["micro_functions"], list)
        assert registry.is_micro_allowed_for_intent(skill=lists_skill, intent="lists.add_item") is True
        assert registry.is_micro_allowed_for_intent(skill=lists_skill, intent="lists.get_items") is True
        assert registry.is_micro_allowed_for_intent(skill=lists_skill, intent="lists.create_list") is False

        private_notes = skills["skill.private_notes.digest"]
        assert private_notes["learnable_ready"] is True
        assert private_notes["active"] is True
        assert private_notes["micro_enabled"] is False
        assert private_notes["cron_enabled"] is True
        assert private_notes["cron_expr"] == "config:private_notes_channels"
        assert private_notes["intents"] == [
            "private_notes.capture",
            "private_notes.compile_digest",
            "private_notes.deliver_digest",
        ]

        calendar_inbox = skills["skill.calendar.inbox"]
        assert calendar_inbox["learnable_ready"] is True
        assert calendar_inbox["active"] is True
        assert calendar_inbox["micro_enabled"] is False
        assert calendar_inbox["cron_enabled"] is True
        assert calendar_inbox["cron_expr"] == "hourly:08-20@America/New_York"
        assert calendar_inbox["intents"] == ["calendar_inbox.reconcile"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_boot_memory_includes_persona_and_core_docs_in_order():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-boot-order-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "boot-order.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        docs = registry.load_model_boot_memory(model_name="jarvis", agent_id="jarvis")
        ordered_paths = [str(item.get("doc_path") or "") for item in docs]
        expected = [
            "app/prompts/personas/jarvis_persona.md",
            "app/prompts/jarvis_identity.md",
            "app/prompts/jarvis_loop.md",
            "app/prompts/jarvis_capabilities.md",
            "app/prompts/agent_registry.md",
            "app/prompts/jarvis_system.md",
        ]
        indexes = [ordered_paths.index(path) for path in expected]
        assert indexes == sorted(indexes)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_loads_relevant_skill_docs_on_demand():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-skill-docs-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "skill-docs.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        docs = registry.load_skill_docs_for_intents(
            intents=["lists.add_item", "lists.get_items", "unsupported.intent"],
            user_id="local_user",
            agent_id="jarvis",
        )

        assert len(docs) == 1
        assert docs[0]["skill_id"] == "skill.lists.core"
        assert docs[0]["intent"] == "lists.add_item"
        assert "skill_id: skill.lists.core" in str(docs[0]["content"])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_loads_compact_runtime_contract_for_model_prompt():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-runtime-skill-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        store = SQLiteStore(database_path=str(scratch / "runtime-skill.db"))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()

        full = registry.load_skill_docs_for_intents(
            intents=["email.summarize"],
            user_id="local_user",
            agent_id="jarvis",
        )[0]
        runtime = registry.load_skill_runtime_docs_for_intents(
            intents=["email.summarize"],
            user_id="local_user",
            agent_id="jarvis",
        )[0]

        assert runtime["runtime_chars"] < runtime["source_chars"]
        assert len(str(runtime["content"])) <= 6000
        assert "# Runtime Skill Contract" in str(runtime["content"])
        assert "## Purpose" in str(runtime["content"])
        assert "## Clarification Rules" in str(runtime["content"])
        assert "## Storage Contract" not in str(runtime["content"])
        assert len(str(full["content"])) == runtime["source_chars"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_skill_registry_micro_boot_memory_is_slim_and_includes_micro_skills_bundle():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-micro-boot-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "micro-boot.db"
        store = SQLiteStore(database_path=str(db_path))
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()
        # Simulate legacy rows from older layouts; loader should still keep micro boot slim.
        store.upsert_model_boot_memory(model_name="microj", doc_path="app/prompts/jarvis_identity.md", priority=5, required=True)
        store.upsert_model_boot_memory(model_name="microj", doc_path="app/prompts/jarvis_capabilities.md", priority=6, required=True)
        store.upsert_model_boot_memory(model_name="microj", doc_path="app/prompts/jarvis_loop.md", priority=7, required=True)

        docs = registry.load_model_boot_memory(model_name="microj", agent_id="jarvis")
        paths = [str(item.get("doc_path") or "") for item in docs]
        assert "app/prompts/microjarvis_identity.md" in paths
        assert "app/prompts/microjarvis_capabilities.md" in paths
        assert "app/prompts/micro_jarvis_skills.md" in paths
        assert "app/prompts/jarvis_identity.md" not in paths
        assert "app/prompts/jarvis_capabilities.md" not in paths
        assert "app/prompts/jarvis_loop.md" not in paths
        assert "app/prompts/agent_registry.md" not in paths
        assert "app/prompts/jarvis_system.md" not in paths
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
