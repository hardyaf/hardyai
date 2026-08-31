from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import yaml
import pytest

from app.db.sqlite_store import SQLiteStore
from app.skills.registry_service import SkillRegistryService
from app.skills.tool_contracts import ToolContractError, ToolDescriptor


def _upsert_skill(
    store: SQLiteStore,
    *,
    skill_id: str,
    intents: list[str],
    execution_ref: str = "app.skills.domains.lists.handler:run",
    active: bool = True,
    learnable_ready: bool = True,
    main_handoff_context: dict | None = None,
    storage_ref: str = "fixture-storage",
) -> None:
    store.upsert_skill(
        skill_id=skill_id,
        skill_name=skill_id,
        skill_user="all",
        skill_agents=["all"],
        intents=intents,
        markdown_path="app/prompts/skills/lists_skill.md",
        execution_ref=execution_ref,
        created_by="test",
        storage_type="sql",
        storage_ref=storage_ref,
        micro_enabled=False,
        micro_functions=[],
        micro_failure_handoff={},
        main_handoff_context=(
            {"always_pass_from_session": ["pending_clarification"]}
            if main_handoff_context is None
            else main_handoff_context
        ),
        learnable_ready=learnable_ready,
        critical_level=1,
        active=active,
        updated_at="2026-08-30T00:00:00+00:00",
    )


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
        assert all(
            "home.get_switch_state" not in item["intents"]
            and "home.list_switches" not in item["intents"]
            for item in catalog
        )
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
        assert "skill.productivity.calendar" in markdown
        assert "(`skill.calendar.core`)" not in markdown
        assert "legacy_skill_ids:\n  - skill.calendar.core" in markdown
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
        assert sync["tool_diagnostic_count"] == 0
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

        assert skills["skill.core.memory"]["active"] is False
        assert skills["skill.home.lights"]["intents"] == ["home.set_switch"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _typed_tool_declaration(*, tool_id: str, effect: str = "read") -> dict:
    return {
        "tool_id": tool_id,
        "contract_version": 1,
        "purpose": "Read one bounded fixture resource.",
        "interactive": True,
        "effect": effect,
        "approval_rule": "none",
        "approval_conditions": [],
        "idempotency": "not_applicable",
        "sensitivity": "private",
        "persistence": "redacted",
        "effect_cardinality": "single",
        "runtime_dependencies": [],
        "transferable_observation_fields": [
            {"pattern": "/summary", "scope": "same_domain"}
        ],
        "timeout_seconds": 10,
        "max_result_items": 5,
        "max_observation_chars": 1000,
        "legacy_intents": ["fixture.read"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 120}
            },
        },
        "observation_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {
                "summary": {"type": "string", "minLength": 0, "maxLength": 500}
            },
        },
    }


def _write_typed_skill_markdown(root: Path, declarations: list[dict]) -> Path:
    skills_dir = root / "app" / "prompts" / "skills"
    skills_dir.mkdir(parents=True)
    frontmatter = {
        "skill_id": "skill.fixture.core",
        "skill_name": "Fixture",
        "skill_user": "all",
        "skill_agents": ["all"],
        "created_by": "test",
        "intents": ["fixture.read"],
        "execution_ref": "app.skills.domains.lists.handler:run",
        "storage_type": "sql",
        "storage_ref": "fixture",
        "micro_enabled": False,
        "micro_functions": [],
        "micro_failure_handoff": {},
        "main_handoff_context": {"always_pass_from_session": ["pending_clarification"]},
        "main_tools_contract_version": 1,
        "main_tools": declarations,
    }
    headings = [
        "Purpose",
        "Trigger Patterns / Intent Mapping",
        "Input Schema",
        "Output Schema",
        "Execution Steps",
        "Clarification Rules",
        "Duplicate / Conflict Handling",
        "Storage Contract",
        "Failure Behavior",
        "MicroJarvis Contract",
        "Main Handoff Context Contract",
        "Learnability Checklist",
    ]
    body = "\n\n".join(f"## {heading}\n\nFixture." for heading in headings)
    (skills_dir / "fixture_skill.md").write_text(
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n{body}\n",
        encoding="utf-8",
    )
    return skills_dir


def test_markdown_tool_compilation_persists_only_valid_descriptors(tmp_path: Path) -> None:
    valid = _typed_tool_declaration(tool_id="fixture.read")
    invalid = _typed_tool_declaration(tool_id="fixture.invalid", effect="provider_admin")
    skills_dir = _write_typed_skill_markdown(tmp_path, [valid, invalid])

    store = SQLiteStore(database_path=str(tmp_path / "registry.db"))
    registry = SkillRegistryService(sqlite_store=store, repo_root=str(tmp_path))
    result = registry.sync_skills_from_markdown(skills_dir=str(skills_dir))

    assert result["synced_count"] == 1
    assert result["tool_diagnostic_count"] == 1
    assert result["synced"][0]["tool_diagnostics"] == [
        {"code": "effect_invalid", "tool_id": "fixture.invalid"}
    ]
    skill = registry.list_skills(active_only=True)[0]
    assert skill["main_tools_contract_version"] == 1
    assert [item["tool_id"] for item in skill["main_tools"]] == ["fixture.read"]
    resolved = registry.resolve_tool(
        tool_id="fixture.read",
        user_id="operator",
        agent_id="jarvis",
    )
    assert resolved is not None
    assert resolved[1].tool_id == "fixture.read"

    cards = registry.discovery_cards(
        user_id="operator",
        agent_id="jarvis",
        request_context={"source_interface": "web"},
        availability_resolver=lambda _skill, _context: {
            "configured": True,
            "authorized_here": True,
        },
    )
    assert cards == [
        {
            "skill_id": "skill.fixture.core",
            "title": "Fixture",
            "purpose": "Read one bounded fixture resource.",
            "safe_tags": ["domain:fixture", "effect:read"],
            "availability": "available",
        }
    ]
    assert "input_schema" not in cards[0]
    assert registry.discovery_cards(
        user_id="operator",
        agent_id="jarvis",
        request_context={"source_interface": "web"},
        availability_resolver=lambda _skill, _context: {
            "configured": True,
            "authorized_here": False,
        },
    ) == []


def test_discovery_card_purpose_summarizes_every_interactive_tool(tmp_path: Path) -> None:
    read_tool = _typed_tool_declaration(tool_id="fixture.read")
    add_tool = _typed_tool_declaration(tool_id="fixture.add_items")
    add_tool["purpose"] = "Add an explicit item array to one fixture collection."
    skills_dir = _write_typed_skill_markdown(tmp_path, [read_tool, add_tool])
    store = SQLiteStore(database_path=str(tmp_path / "registry.db"))
    registry = SkillRegistryService(sqlite_store=store, repo_root=str(tmp_path))

    registry.sync_skills_from_markdown(skills_dir=str(skills_dir))
    cards = registry.discovery_cards(
        user_id="operator",
        agent_id="jarvis",
        request_context={"source_interface": "web"},
        availability_resolver=lambda _skill, _context: {
            "configured": True,
            "authorized_here": True,
        },
    )

    assert cards[0]["purpose"] == (
        "Read one bounded fixture resource. "
        "Add an explicit item array to one fixture collection."
    )


def test_fresh_and_upgraded_version7_databases_compile_identical_descriptors(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    skills_dir = _write_typed_skill_markdown(
        source_root,
        [_typed_tool_declaration(tool_id="fixture.read")],
    )
    fresh_store = SQLiteStore(database_path=str(tmp_path / "fresh.db"))

    version7_path = tmp_path / "version7.db"
    initial_store = SQLiteStore(database_path=str(version7_path))
    initial_store.close()
    connection = sqlite3.connect(version7_path)
    connection.execute("ALTER TABLE skills DROP COLUMN main_tools_json")
    connection.execute("ALTER TABLE skills DROP COLUMN main_tools_contract_version")
    connection.execute("DROP TABLE schema_reader_compatibility")
    connection.execute("PRAGMA user_version = 7")
    connection.commit()
    connection.close()
    upgraded_store = SQLiteStore(database_path=str(version7_path))

    fresh_registry = SkillRegistryService(fresh_store, repo_root=str(source_root))
    upgraded_registry = SkillRegistryService(upgraded_store, repo_root=str(source_root))
    fresh_registry.sync_skills_from_markdown(skills_dir=str(skills_dir))
    upgraded_registry.sync_skills_from_markdown(skills_dir=str(skills_dir))

    fresh_skill = fresh_registry.list_skills(active_only=True)[0]
    upgraded_skill = upgraded_registry.list_skills(active_only=True)[0]
    assert fresh_skill["main_tools_contract_version"] == 1
    assert fresh_skill["main_tools"] == upgraded_skill["main_tools"]
    fresh_store.close()
    upgraded_store.close()


def test_duplicate_active_tool_owners_fail_resolution_and_integrity() -> None:
    class Catalog:
        @staticmethod
        def list_skills(*, active_only: bool = True) -> list[dict]:
            rows = []
            for skill_id in ("skill.fixture.one", "skill.fixture.two"):
                raw = _typed_tool_declaration(tool_id="fixture.read")
                descriptor = ToolDescriptor.from_mapping({**raw, "skill_id": skill_id})
                rows.append(
                    {
                        "skill_id": skill_id,
                        "skill_name": skill_id,
                        "skill_user": "all",
                        "skill_agents": ["all"],
                        "intents": [],
                        "execution_ref": "app.skills.domains.lists.handler:run",
                        "main_tools": [descriptor.to_storage_dict()],
                        "main_tools_contract_version": 1,
                        "learnable_ready": True,
                        "active": True,
                    }
                )
            return rows if active_only else rows

    registry = SkillRegistryService(Catalog())
    with pytest.raises(ToolContractError, match="tool_owner_not_unique"):
        registry.resolve_tool(
            tool_id="fixture.read",
            user_id="operator",
            agent_id="jarvis",
        )
    report = registry.registry_integrity_report()
    assert any(
        issue.get("code") == "duplicate_active_operation_owner"
        and issue.get("operation_id") == "fixture.read"
        for issue in report["issues"]
    )


def test_calendar_upgrade_preserves_and_deactivates_legacy_row_independent_of_counters():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-calendar-upgrade-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        store = SQLiteStore(database_path=str(scratch / "calendar.db"))
        _upsert_skill(
            store,
            skill_id="skill.calendar.core",
            intents=["calendar.view"],
            execution_ref="app.skills.domains.calendar.handler:run",
            storage_ref="legacy-calendar-storage",
        )
        for index in range(5):
            store.record_skill_run(
                skill_id="skill.calendar.core",
                session_id=None,
                user_id="fixture-user",
                intent="calendar.view",
                route="fixture",
                status="ok",
                confidence=1.0,
                latency_ms=index,
                created_at=f"2026-08-30T00:00:0{index}+00:00",
            )

        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))
        registry.seed_defaults()
        skills = {str(row["skill_id"]): row for row in registry.list_skills(active_only=False)}

        assert skills["skill.calendar.core"]["active"] is False
        assert skills["skill.calendar.core"]["storage_ref"] == "legacy-calendar-storage"
        assert skills["skill.calendar.core"]["usage_count"] == 5
        assert skills["skill.productivity.calendar"]["active"] is True
        resolved = registry.resolve_skill(
            intent="calendar.view",
            user_id="fixture-user",
            agent_id="jarvis",
        )
        assert resolved is not None
        assert resolved["skill_id"] == "skill.productivity.calendar"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_registry_integrity_report_is_content_free_and_detects_each_failure_class():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-integrity-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        store = SQLiteStore(database_path=str(scratch / "integrity.db"))
        _upsert_skill(store, skill_id="skill.fixture.one", intents=["lists.add_item"])
        _upsert_skill(store, skill_id="skill.fixture.two", intents=["lists.add_item"])
        _upsert_skill(
            store,
            skill_id="skill.fixture.bad-handler",
            intents=["fixture.unknown"],
            execution_ref="app.skills.domains.missing.handler:run",
            learnable_ready=False,
            main_handoff_context={},
        )
        registry = SkillRegistryService(sqlite_store=store, repo_root=str(Path.cwd()))

        report = registry.registry_integrity_report()
        codes = {str(issue["code"]) for issue in report["issues"]}

        assert {
            "duplicate_active_operation_owner",
            "active_handler_unimportable",
            "unknown_legacy_intent",
            "interactive_contract_missing",
            "stale_execution_reference",
        } <= codes
        serialized = str(report)
        assert "app.skills.domains" not in serialized
        assert "storage_ref" not in serialized
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
