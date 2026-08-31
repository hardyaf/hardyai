from __future__ import annotations

import hashlib
import importlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from app.core.types import Intent
from app.db.repositories import SkillCatalogRepository
from app.db.sqlite_store import SQLiteStore
from app.skills.tool_contracts import (
    ToolContractError,
    ToolDescriptor,
    compile_tool_descriptors,
    sanitize_model_text,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


REQUIRED_FRONTMATTER_FIELDS = {
    "skill_id",
    "skill_name",
    "skill_user",
    "skill_agents",
    "created_by",
    "intents",
    "execution_ref",
    "storage_type",
    "storage_ref",
    "micro_enabled",
    "micro_functions",
    "micro_failure_handoff",
    "main_handoff_context",
}

REQUIRED_SECTION_KEYS = {
    "purpose",
    "trigger patterns intent mapping",
    "input schema",
    "output schema",
    "execution steps",
    "clarification rules",
    "duplicate conflict handling",
    "storage contract",
    "failure behavior",
    "microjarvis contract",
    "main handoff context contract",
    "learnability checklist",
}

SECTION_KEY_ALIASES: dict[str, set[str]] = {
    "trigger patterns intent mapping": {
        "intent mapping",
    },
    "input schema": {
        "required inputs",
    },
    "execution steps": {
        "execution rules",
    },
    "duplicate conflict handling": {
        "duplicate and matching rules",
        "matching and alias rules",
        "event matching rules",
        "duplicate rules",
    },
    "storage contract": {
        "external system contract",
    },
    "main handoff context contract": {
        "main jarvis responsibilities",
    },
}

_KNOWN_NON_INTERACTIVE_LEGACY_INTENTS = {
    "calendar_inbox.reconcile",
    "private_notes.capture",
    "private_notes.compile_digest",
    "private_notes.deliver_digest",
}
_KNOWN_LEGACY_INTENTS = {intent.value for intent in Intent} | _KNOWN_NON_INTERACTIVE_LEGACY_INTENTS
_STALE_OPERATION_IDS = {
    "home.get_switch_state",
    "home.list_switches",
}


class SkillRegistryService:
    def __init__(
        self,
        sqlite_store: SQLiteStore | SkillCatalogRepository,
        repo_root: str | None = None,
    ) -> None:
        self._sqlite_store = sqlite_store
        self._repo_root = Path(repo_root).expanduser().resolve() if repo_root else Path.cwd().resolve()
        self._markdown_cache: dict[str, str] = {}

    @staticmethod
    def _execution_ref_is_importable(value: Any) -> bool:
        normalized = str(value or "").strip()
        if not normalized.startswith("app.skills.domains.") or normalized.count(":") != 1:
            return False
        module_name, attr_name = (item.strip() for item in normalized.split(":", 1))
        if not module_name or not attr_name:
            return False
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return False
        return callable(getattr(module, attr_name, None))

    def seed_defaults(self) -> None:
        now = _utc_now()
        micro_boot_docs = [
            ("app/prompts/microjarvis_identity.md", 20),
            ("app/prompts/microjarvis_capabilities.md", 40),
            ("app/prompts/micro_jarvis_skills.md", 60),
        ]
        main_boot_docs = [
            ("app/prompts/jarvis_identity.md", 20),
            ("app/prompts/jarvis_loop.md", 30),
            ("app/prompts/jarvis_capabilities.md", 40),
            ("app/prompts/agent_registry.md", 50),
            ("app/prompts/jarvis_system.md", 60),
        ]

        for doc_path, priority in micro_boot_docs:
            self._sqlite_store.upsert_model_boot_memory(
                model_name="microj",
                doc_path=doc_path,
                priority=priority,
                required=True,
            )
        for model_name in ("jarvis", "bigj"):
            for doc_path, priority in main_boot_docs:
                self._sqlite_store.upsert_model_boot_memory(
                    model_name=model_name,
                    doc_path=doc_path,
                    priority=priority,
                    required=True,
                )

        for profile in [
            {
                "agent_id": "jarvis",
                "display_name": "Jarvis",
                "wake_aliases": ["jarvis"],
                "personality_doc_path": "app/prompts/personas/jarvis_persona.md",
                "default_user_id": None,
            },
            {
                "agent_id": "catparty",
                "display_name": "CatParty",
                "wake_aliases": ["catparty"],
                "personality_doc_path": "app/prompts/personas/catparty_persona.md",
                "default_user_id": None,
            },
            {
                "agent_id": "kid_spark",
                "display_name": "Spark",
                "wake_aliases": ["spark"],
                "personality_doc_path": "app/prompts/personas/kid_spark_persona.md",
                "default_user_id": None,
            },
            {
                "agent_id": "kid_quest",
                "display_name": "Quest",
                "wake_aliases": ["quest"],
                "personality_doc_path": "app/prompts/personas/kid_quest_persona.md",
                "default_user_id": None,
            },
            {
                "agent_id": "child",
                "display_name": "Jarvis",
                "wake_aliases": [],
                "personality_doc_path": "app/prompts/personas/child_persona.md",
                "default_user_id": "child",
            },
        ]:
            self._sqlite_store.upsert_agent_profile(
                agent_id=str(profile["agent_id"]),
                display_name=str(profile["display_name"]),
                wake_aliases=[str(item) for item in profile["wake_aliases"]],
                personality_doc_path=str(profile["personality_doc_path"]),
                default_user_id=(
                    str(profile["default_user_id"])
                    if profile.get("default_user_id") is not None
                    else None
                ),
                active=True,
                updated_at=now,
            )

        existing_skill_tools = {
            str(item.get("skill_id") or "").strip(): (
                item.get("main_tools"),
                item.get("main_tools_contract_version"),
            )
            for item in self._sqlite_store.list_skills(active_only=False)
        }
        for skill in [
            {
                "skill_id": "skill.productivity.calendar",
                "skill_name": "Calendar",
                "skill_user": "all",
                "skill_agents": ["all"],
                "intents": [
                    "calendar.view",
                    "calendar.add_event",
                    "calendar.update_event",
                    "calendar.delete_event",
                ],
                "markdown_path": "app/prompts/skills/calendar_skill.md",
                "execution_ref": "app.skills.domains.calendar.handler:run",
                "created_by": "system",
                "storage_type": "api",
                "storage_ref": "google_calendar_oauth",
                "critical_level": 3,
                "micro_enabled": True,
                "micro_functions": [
                    {
                        "function_id": "calendar.view",
                        "intent": "calendar.view",
                        "supported_actions": ["read_calendar"],
                        "unsupported_or_escalate": [
                            "calendar.add_event",
                            "calendar.update_event",
                            "calendar.delete_event",
                            "calendar.invite",
                        ],
                    }
                ],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                        "required_missing_fields",
                    ],
                    "capability_context_keys": [
                        "last_calendar_person",
                        "last_event_reference",
                        "last_calendar_action",
                        "window",
                    ],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["pending_clarification", "main_agent_token_session"],
                    "domain_carryover": [
                        "last_calendar_person",
                        "last_event_reference",
                        "last_calendar_action",
                        "last_successful_action",
                    ],
                },
                "learnable_ready": True,
            },
            {
                "skill_id": "skill.home.lights",
                "skill_name": "Lights",
                "skill_user": "all",
                "skill_agents": ["all"],
                "intents": ["home.set_switch"],
                "markdown_path": "app/prompts/skills/lights_skill.md",
                "execution_ref": "app.skills.domains.lights.handler:run",
                "created_by": "system",
                "storage_type": "sql",
                "storage_ref": (
                    "app.skills.domains.lights.storage:SQLiteLightsStorage(switches,switch_actions_log)"
                ),
                "critical_level": 2,
                "micro_enabled": True,
                "micro_functions": [
                    {
                        "function_id": "lights.set_switch",
                        "intent": "home.set_switch",
                        "supported_actions": ["switch_on", "switch_off"],
                        "unsupported_or_escalate": ["bulk_ambiguous_scope"],
                    }
                ],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                    ],
                    "capability_context_keys": ["last_switch_name", "available_switches"],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["pending_clarification", "main_agent_token_session"],
                    "domain_carryover": ["last_switch_name", "last_successful_action"],
                },
                "learnable_ready": True,
            },
            {
                "skill_id": "skill.lists.core",
                "skill_name": "Lists",
                "skill_user": "all",
                "skill_agents": ["all"],
                "intents": [
                    "lists.create_list",
                    "lists.add_item",
                    "lists.get_items",
                    "lists.delete_list",
                    "lists.remove_item",
                    "lists.mark_item_done",
                ],
                "markdown_path": "app/prompts/skills/lists_skill.md",
                "execution_ref": "app.skills.domains.lists.handler:run",
                "created_by": "system",
                "storage_type": "sql",
                "storage_ref": "app.skills.domains.lists.storage:SQLiteListsStorage(lists,list_items)",
                "critical_level": 3,
                "micro_enabled": True,
                "micro_functions": [
                    {
                        "function_id": "lists.add_item",
                        "intent": "lists.add_item",
                        "supported_actions": ["add_item_to_existing_list"],
                        "unsupported_or_escalate": ["deictic_without_context", "create_list"],
                    },
                    {
                        "function_id": "lists.get_items",
                        "intent": "lists.get_items",
                        "supported_actions": ["read_existing_list"],
                        "unsupported_or_escalate": ["deictic_without_context"],
                    },
                ],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                        "required_missing_fields",
                    ],
                    "capability_context_keys": ["last_list_name", "available_lists"],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["pending_clarification", "main_agent_token_session"],
                    "domain_carryover": ["last_list_name", "last_successful_action"],
                },
                "learnable_ready": True,
            },
            {
                "skill_id": "skill.conversation.general",
                "skill_name": "Conversation",
                "skill_user": "all",
                "skill_agents": ["all"],
                "intents": ["conversation.general", "unknown"],
                "markdown_path": "app/prompts/skills/conversation_skill.md",
                "execution_ref": "app.skills.domains.conversation.handler:run",
                "created_by": "system",
                "storage_type": "hybrid",
                "storage_ref": (
                    "app.skills.domains.conversation.storage:ConversationSQLiteStorage("
                    "conversation_topics,conversation_topic_history"
                    ") + data/skill_history/conversation"
                ),
                "critical_level": 2,
                "micro_enabled": False,
                "micro_functions": [],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                    ],
                    "capability_context_keys": [],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["main_agent_token_session"],
                    "domain_carryover": ["last_successful_action"],
                },
                "learnable_ready": True,
            },
            {
                "skill_id": "skill.private_notes.digest",
                "skill_name": "Private Notes Digest",
                "skill_user": "all",
                "skill_agents": ["all"],
                "intents": [
                    "private_notes.capture",
                    "private_notes.compile_digest",
                    "private_notes.deliver_digest",
                ],
                "markdown_path": "app/prompts/skills/private_notes_skill.md",
                "execution_ref": "app.skills.domains.private_notes.handler:run",
                "created_by": "system",
                "storage_type": "sql",
                "storage_ref": (
                    "app.skills.domains.private_notes.storage:PrivateNotesSQLiteStorage("
                    "private_note_entries,private_note_digests)"
                ),
                "critical_level": 0,
                "micro_enabled": False,
                "micro_functions": [],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                        "required_missing_fields",
                        "agent_id",
                        "agent_display_name",
                        "main_agent_token_session",
                    ],
                    "capability_context_keys": [
                        "private_notes_channel_id",
                        "private_notes_owner_user_id",
                        "private_notes_pending_count",
                        "private_notes_last_capture_at",
                    ],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["main_agent_token_session"],
                    "domain_carryover": [
                        "private_notes_channel_id",
                        "private_notes_owner_user_id",
                        "private_notes_pending_count",
                    ],
                },
                "learnable_ready": True,
                "cron_enabled": True,
                "cron_expr": "config:private_notes_channels",
            },
            {
                "skill_id": "skill.calendar.inbox",
                "skill_name": "Calendar Inbox Reconciliation",
                "skill_user": "all",
                "skill_agents": ["all"],
                "intents": ["calendar_inbox.reconcile"],
                "markdown_path": "app/prompts/skills/calendar_inbox_skill.md",
                "execution_ref": "app.skills.domains.calendar_inbox.handler:run",
                "created_by": "system",
                "storage_type": "sql+api",
                "storage_ref": (
                    "app.skills.domains.calendar_inbox.storage:CalendarInboxSQLiteStorage("
                    "calendar_inbox_state,calendar_inbox_runs,calendar_inbox_messages,calendar_inbox_events);"
                    "google_gmail_readonly+google_calendar_events"
                ),
                "critical_level": 0,
                "micro_enabled": False,
                "micro_functions": [],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                        "required_missing_fields",
                        "agent_id",
                        "agent_display_name",
                        "main_agent_token_session",
                    ],
                    "capability_context_keys": [
                        "calendar_inbox_slot_key",
                        "calendar_inbox_last_status",
                        "calendar_inbox_last_counts",
                        "calendar_inbox_last_error_type",
                    ],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["main_agent_token_session"],
                    "domain_carryover": [
                        "calendar_inbox_last_status",
                        "calendar_inbox_last_counts",
                    ],
                },
                "learnable_ready": True,
                "cron_enabled": True,
                "cron_expr": "hourly:08-20@America/New_York",
            },
            {
                "skill_id": "skill.email.agent",
                "skill_name": "Shared Email Agent",
                "skill_user": "all",
                "skill_agents": ["jarvis", "catparty"],
                "intents": [
                    "email.list_recent",
                    "email.search",
                    "email.get_message",
                    "email.get_thread",
                    "email.summarize",
                    "email.discuss",
                    "email.status",
                    "email.mark_reviewed",
                    "email.snooze",
                    "email.dismiss",
                    "email.correct_category",
                    "email.mark_needs_reply",
                    "email.mark_complete",
                    "email.mark_spam",
                    "email.sync",
                    "email.promote_to_list",
                    "email.promote_to_calendar",
                    "email.promote_to_task",
                    "email.promote_to_wave",
                ],
                "markdown_path": "app/prompts/skills/email_agent_skill.md",
                "execution_ref": "app.skills.domains.email_agent.handler:run",
                "created_by": "system",
                "storage_type": "sql+api",
                "storage_ref": (
                    "app.skills.domains.email_agent.storage:EmailAgentSQLiteStorage("
                    "email_sync_state,email_sync_runs,email_messages,email_threads,email_summaries,"
                    "email_classifications,email_user_state,email_reference_sets,email_action_links,"
                    "email_label_operations,email_mailbox_operations);"
                    "google_gmail_readonly+isolated_gmail_mailbox_writer"
                ),
                "critical_level": 1,
                "micro_enabled": False,
                "micro_functions": [],
                "micro_failure_handoff": {
                    "baseline_context_keys": [
                        "micro_intent",
                        "micro_confidence",
                        "micro_entities",
                        "micro_ambiguity_flags",
                        "required_missing_fields",
                        "agent_id",
                        "agent_display_name",
                        "main_agent_token_session",
                    ],
                    "capability_context_keys": [
                        "last_email_query",
                        "last_email_reference_set_id",
                        "last_email_result_refs",
                        "focused_email_message_id",
                        "focused_email_thread_id",
                        "last_email_source_route",
                        "last_email_category_key",
                        "last_email_action_candidates",
                        "last_email_date_candidates",
                        "email_sync_last_status",
                        "email_sync_last_error_type",
                    ],
                },
                "main_handoff_context": {
                    "always_pass_from_session": ["main_agent_token_session"],
                    "domain_carryover": [
                        "last_email_reference_set_id",
                        "last_email_result_refs",
                        "focused_email_message_id",
                        "focused_email_thread_id",
                        "last_email_source_route",
                        "last_email_category_key",
                    ],
                },
                "learnable_ready": True,
                "cron_enabled": True,
                "cron_expr": "interval:10m",
            },
        ]:
            existing_main_tools, existing_main_tools_version = existing_skill_tools.get(
                str(skill["skill_id"]),
                (None, None),
            )
            self._sqlite_store.upsert_skill(
                skill_id=str(skill["skill_id"]),
                skill_name=str(skill["skill_name"]),
                skill_user=str(skill["skill_user"]),
                skill_agents=[str(item) for item in skill["skill_agents"]],
                intents=[str(item) for item in skill["intents"]],
                markdown_path=str(skill["markdown_path"]),
                execution_ref=str(skill["execution_ref"]),
                created_by=str(skill["created_by"]),
                storage_type=str(skill["storage_type"]),
                storage_ref=str(skill["storage_ref"]),
                micro_enabled=bool(skill.get("micro_enabled")),
                micro_functions=skill.get("micro_functions") if isinstance(skill.get("micro_functions"), list) else [],
                micro_failure_handoff=(
                    skill.get("micro_failure_handoff")
                    if isinstance(skill.get("micro_failure_handoff"), dict)
                    else {}
                ),
                main_handoff_context=(
                    skill.get("main_handoff_context")
                    if isinstance(skill.get("main_handoff_context"), dict)
                    else {}
                ),
                learnable_ready=bool(skill.get("learnable_ready")),
                critical_level=int(skill["critical_level"]),
                active=bool(skill.get("active", True)),
                cron_enabled=bool(skill.get("cron_enabled", False)),
                cron_expr=(
                    str(skill.get("cron_expr") or "").strip() or None
                    if bool(skill.get("cron_enabled", False))
                    else None
                ),
                main_tools=(
                    list(existing_main_tools) if isinstance(existing_main_tools, list) else None
                ),
                main_tools_contract_version=(
                    int(existing_main_tools_version)
                    if isinstance(existing_main_tools_version, int)
                    else None
                ),
                updated_at=now,
            )

        legacy_calendar = next(
            (
                skill
                for skill in self._sqlite_store.list_skills(active_only=False)
                if str(skill.get("skill_id") or "").strip() == "skill.calendar.core"
                and bool(skill.get("active"))
            ),
            None,
        )
        if legacy_calendar is not None:
            self._sqlite_store.upsert_skill(
                skill_id="skill.calendar.core",
                skill_name=str(legacy_calendar.get("skill_name") or "Calendar"),
                skill_user=str(legacy_calendar.get("skill_user") or "all"),
                skill_agents=[str(item) for item in legacy_calendar.get("skill_agents") or ["all"]],
                intents=[str(item) for item in legacy_calendar.get("intents") or []],
                markdown_path=str(legacy_calendar.get("markdown_path") or "app/prompts/skills/calendar_skill.md"),
                execution_ref=(str(legacy_calendar.get("execution_ref") or "").strip() or None),
                created_by=str(legacy_calendar.get("created_by") or "system"),
                storage_type=str(legacy_calendar.get("storage_type") or "hybrid"),
                storage_ref=(str(legacy_calendar.get("storage_ref") or "").strip() or None),
                micro_enabled=bool(legacy_calendar.get("micro_enabled")),
                micro_functions=(
                    list(legacy_calendar.get("micro_functions") or [])
                    if isinstance(legacy_calendar.get("micro_functions"), list)
                    else []
                ),
                micro_failure_handoff=(
                    dict(legacy_calendar.get("micro_failure_handoff") or {})
                    if isinstance(legacy_calendar.get("micro_failure_handoff"), dict)
                    else {}
                ),
                main_handoff_context=(
                    dict(legacy_calendar.get("main_handoff_context") or {})
                    if isinstance(legacy_calendar.get("main_handoff_context"), dict)
                    else {}
                ),
                learnable_ready=bool(legacy_calendar.get("learnable_ready")),
                critical_level=int(legacy_calendar.get("critical_level") or 0),
                active=False,
                cron_enabled=bool(legacy_calendar.get("cron_enabled")),
                cron_expr=(str(legacy_calendar.get("cron_expr") or "").strip() or None),
                main_tools=(
                    list(legacy_calendar.get("main_tools") or [])
                    if isinstance(legacy_calendar.get("main_tools"), list)
                    else None
                ),
                main_tools_contract_version=(
                    int(legacy_calendar["main_tools_contract_version"])
                    if isinstance(legacy_calendar.get("main_tools_contract_version"), int)
                    else None
                ),
                updated_at=now,
            )

    def resolve_agent_context(
        self,
        *,
        text: str,
        fallback_user_id: str,
        fallback_agent_id: str = "jarvis",
    ) -> dict[str, Any]:
        raw = str(text or "")
        stripped = raw.strip()
        fallback = self._sqlite_store.get_agent_profile(fallback_agent_id) or {
            "agent_id": fallback_agent_id.strip().lower() or "jarvis",
            "display_name": fallback_agent_id.strip() or "Jarvis",
            "wake_aliases": [fallback_agent_id.strip().lower() or "jarvis"],
            "personality_doc_path": None,
            "default_user_id": None,
            "active": True,
        }

        wake_alias = None
        matched_profile: dict[str, Any] | None = None
        normalized_text = raw

        patterns = [
            r"^\s*(?:hey|hi|hello|yo)\s+(?P<alias>[a-z0-9_-]+)\b[:,]?\s*(?P<rest>.*)$",
            r"^\s*(?P<alias>[a-z0-9_-]+)\s*[:,]\s*(?P<rest>.*)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, stripped, flags=re.IGNORECASE)
            if not match:
                continue
            alias = str(match.group("alias") or "").strip().lower()
            if not alias:
                continue
            profile = self._sqlite_store.find_agent_by_wake_alias(alias)
            if profile is None:
                continue
            wake_alias = alias
            matched_profile = profile
            rest = str(match.group("rest") or "").strip()
            normalized_text = rest or raw
            break

        if matched_profile is None:
            parts = stripped.split(maxsplit=1)
            if len(parts) == 2:
                alias = parts[0].strip(",:").lower()
                profile = self._sqlite_store.find_agent_by_wake_alias(alias)
                if profile is not None:
                    wake_alias = alias
                    matched_profile = profile
                    normalized_text = parts[1].strip() or raw

        profile = matched_profile or fallback
        resolved_user_id = (
            str(profile.get("default_user_id") or "").strip()
            or str(fallback_user_id or "").strip()
            or "local_user"
        )
        personality_doc_path = str(profile.get("personality_doc_path") or "").strip() or None

        return {
            "agent_id": str(profile.get("agent_id") or fallback["agent_id"]).strip().lower(),
            "display_name": str(profile.get("display_name") or fallback["display_name"]).strip(),
            "wake_alias": wake_alias,
            "normalized_text": normalized_text,
            "resolved_user_id": resolved_user_id,
            "personality_doc_path": personality_doc_path,
        }

    def get_agent_profile(self, agent_id: str) -> dict[str, Any] | None:
        return self._sqlite_store.get_agent_profile(agent_id)

    def list_agent_profiles(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        return self._sqlite_store.list_agent_profiles(active_only=active_only)

    def upsert_agent_profile(
        self,
        *,
        agent_id: str,
        display_name: str,
        wake_aliases: list[str],
        personality_doc_path: str,
        default_user_id: str | None,
        active: bool,
    ) -> dict[str, Any]:
        normalized_path = personality_doc_path.replace("\\", "/").strip()
        allowed_prefix = "app/prompts/personas/"
        if not normalized_path.startswith(allowed_prefix) or ".." in normalized_path.split("/"):
            raise ValueError("Personality documents must be inside app/prompts/personas/.")
        resolved = (self._repo_root / normalized_path).resolve()
        allowed_root = (self._repo_root / "app/prompts/personas").resolve()
        if allowed_root not in resolved.parents or not resolved.is_file():
            raise ValueError("Personality document does not exist in the allowed persona directory.")
        now = datetime.now(timezone.utc).isoformat()
        self._sqlite_store.upsert_agent_profile(
            agent_id=agent_id,
            display_name=display_name,
            wake_aliases=wake_aliases,
            personality_doc_path=normalized_path,
            default_user_id=default_user_id,
            active=active,
            updated_at=now,
        )
        profile = self._sqlite_store.get_agent_profile(agent_id)
        if profile is None:
            raise RuntimeError("Agent profile write did not persist.")
        return profile

    def resolve_skill(
        self,
        *,
        intent: str,
        user_id: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        return self._sqlite_store.find_skill_for_intent(intent=intent, user_id=user_id, agent_id=agent_id)

    def list_skills(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        return self._sqlite_store.list_skills(active_only=active_only)

    @staticmethod
    def _skill_matches_identity(
        skill: dict[str, Any],
        *,
        user_id: str,
        agent_id: str,
    ) -> bool:
        normalized_user = str(user_id or "").strip().casefold()
        normalized_agent = str(agent_id or "").strip().casefold()
        skill_user = str(skill.get("skill_user") or "all").strip().casefold()
        if skill_user not in {"all", normalized_user, normalized_agent}:
            return False
        agents = {
            str(item or "").strip().casefold()
            for item in skill.get("skill_agents") or []
            if str(item or "").strip()
        }
        return not agents or "all" in agents or normalized_agent in agents

    @staticmethod
    def tool_descriptors_for_skill(skill: dict[str, Any] | None) -> tuple[ToolDescriptor, ...]:
        if not isinstance(skill, dict) or skill.get("main_tools_contract_version") != 1:
            return ()
        descriptors: list[ToolDescriptor] = []
        for raw in skill.get("main_tools") or []:
            try:
                descriptors.append(
                    ToolDescriptor.from_mapping(
                        raw,
                        skill_id=str(skill.get("skill_id") or ""),
                    )
                )
            except ToolContractError:
                continue
        return tuple(descriptors)

    def resolve_tool(
        self,
        *,
        tool_id: str,
        user_id: str,
        agent_id: str,
    ) -> tuple[dict[str, Any], ToolDescriptor] | None:
        normalized_tool_id = str(tool_id or "").strip().casefold()
        matches: list[tuple[dict[str, Any], ToolDescriptor]] = []
        for skill in self.list_skills(active_only=True):
            if not self._skill_matches_identity(skill, user_id=user_id, agent_id=agent_id):
                continue
            for descriptor in self.tool_descriptors_for_skill(skill):
                if descriptor.tool_id == normalized_tool_id:
                    matches.append((skill, descriptor))
        if len(matches) > 1:
            raise ToolContractError("tool_owner_not_unique")
        return matches[0] if matches else None

    def discovery_cards(
        self,
        *,
        user_id: str,
        agent_id: str,
        request_context: dict[str, Any],
        availability_resolver: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        max_skills: int = 32,
    ) -> list[dict[str, Any]]:
        """Return only authorized, schema-free skill discovery metadata."""

        cards: list[dict[str, Any]] = []
        bounded_limit = max(1, min(int(max_skills), 64))
        for skill in self.list_skills(active_only=True):
            if not self._skill_matches_identity(skill, user_id=user_id, agent_id=agent_id):
                continue
            descriptors = tuple(
                item for item in self.tool_descriptors_for_skill(skill) if item.interactive
            )
            if not descriptors:
                continue
            try:
                availability = availability_resolver(skill, dict(request_context))
            except Exception:
                continue
            if not isinstance(availability, dict) or not (
                availability.get("configured") is True
                and availability.get("authorized_here") is True
            ):
                continue
            effects = sorted({item.effect for item in descriptors})
            domains = sorted({item.tool_id.partition(".")[0] for item in descriptors})
            purpose_parts: list[str] = []
            for descriptor in descriptors:
                part = sanitize_model_text(descriptor.purpose, max_chars=300)
                if part and part not in purpose_parts:
                    purpose_parts.append(part)
            purpose = sanitize_model_text(" ".join(purpose_parts), max_chars=600)
            cards.append(
                {
                    "skill_id": str(skill.get("skill_id") or "").strip(),
                    "title": sanitize_model_text(
                        skill.get("skill_name") or skill.get("skill_id"),
                        max_chars=120,
                    ),
                    "purpose": purpose,
                    "safe_tags": [
                        *(f"domain:{item}" for item in domains),
                        *(f"effect:{item}" for item in effects),
                    ][:12],
                    "availability": "available",
                }
            )
            if len(cards) >= bounded_limit:
                break
        return cards

    def runtime_capability_catalog(
        self,
        *,
        user_id: str,
        agent_id: str,
        max_skills: int = 32,
    ) -> list[dict[str, Any]]:
        """Return a safe, ephemeral projection of SQL-backed skill metadata."""
        normalized_user = str(user_id or "").strip().casefold() or "local_user"
        normalized_agent = str(agent_id or "").strip().casefold() or "jarvis"
        bounded_limit = max(1, min(int(max_skills), 64))
        catalog: list[dict[str, Any]] = []

        for skill in self.list_skills(active_only=True):
            skill_user = str(skill.get("skill_user") or "all").strip().casefold()
            if skill_user not in {"all", normalized_user, normalized_agent}:
                continue
            agents = {
                str(item or "").strip().casefold()
                for item in skill.get("skill_agents") or []
                if str(item or "").strip()
            }
            if agents and "all" not in agents and normalized_agent not in agents:
                continue

            intents = list(
                dict.fromkeys(
                    str(item or "").strip().casefold()
                    for item in skill.get("intents") or []
                    if str(item or "").strip()
                    and str(item or "").strip().casefold() not in _STALE_OPERATION_IDS
                )
            )
            micro_intents: list[str] = []
            for item in skill.get("micro_functions") or []:
                if not isinstance(item, dict):
                    continue
                micro_intent = str(item.get("intent") or item.get("function_id") or "").strip().casefold()
                if micro_intent and micro_intent not in micro_intents:
                    micro_intents.append(micro_intent)

            catalog.append(
                {
                    "skill_id": str(skill.get("skill_id") or "").strip(),
                    "skill_name": str(skill.get("skill_name") or skill.get("skill_id") or "").strip(),
                    "intents": intents,
                    "main_enabled": bool(intents) and self._execution_ref_is_importable(
                        skill.get("execution_ref")
                    ),
                    "micro_enabled": bool(skill.get("micro_enabled")),
                    "micro_intents": micro_intents,
                    "scheduled": bool(skill.get("cron_enabled")),
                }
            )
            if len(catalog) >= bounded_limit:
                break
        return catalog

    def registry_integrity_report(self) -> dict[str, Any]:
        """Return content-free catalog diagnostics without exposing implementation references."""

        active_skills = self.list_skills(active_only=True)
        all_skills = self.list_skills(active_only=False)
        issues: list[dict[str, Any]] = []
        owners: dict[str, set[str]] = {}

        for skill in active_skills:
            skill_id = str(skill.get("skill_id") or "").strip()
            legacy_intents = {
                str(item or "").strip().casefold()
                for item in skill.get("intents") or []
                if str(item or "").strip()
            }
            raw_tools = skill.get("main_tools") or []
            descriptors = self.tool_descriptors_for_skill(skill)
            tool_ids = {item.tool_id for item in descriptors}
            if len(descriptors) != len(raw_tools):
                issues.append({"code": "invalid_tool_descriptor", "skill_id": skill_id})
            if len(tool_ids) != len(descriptors):
                issues.append({"code": "duplicate_tool_id_in_skill", "skill_id": skill_id})
            for operation_id in legacy_intents | tool_ids:
                owners.setdefault(operation_id, set()).add(skill_id)

            importable = self._execution_ref_is_importable(skill.get("execution_ref"))
            if not importable:
                issues.append({"code": "active_handler_unimportable", "skill_id": skill_id})

            unknown_intents = sorted(legacy_intents - _KNOWN_LEGACY_INTENTS)
            for intent in unknown_intents:
                issues.append(
                    {
                        "code": "unknown_legacy_intent",
                        "skill_id": skill_id,
                        "operation_id": intent,
                    }
                )

            interactive_intents = legacy_intents - _KNOWN_NON_INTERACTIVE_LEGACY_INTENTS
            handoff = skill.get("main_handoff_context")
            if interactive_intents and (
                not bool(skill.get("learnable_ready"))
                or not isinstance(handoff, dict)
                or not handoff
            ):
                issues.append({"code": "interactive_contract_missing", "skill_id": skill_id})

        for operation_id, skill_ids in sorted(owners.items()):
            if len(skill_ids) > 1:
                issues.append(
                    {
                        "code": "duplicate_active_operation_owner",
                        "operation_id": operation_id,
                        "skill_ids": sorted(skill_ids),
                    }
                )

        for skill in all_skills:
            execution_ref = str(skill.get("execution_ref") or "").strip()
            if execution_ref and not self._execution_ref_is_importable(execution_ref):
                issues.append(
                    {
                        "code": "stale_execution_reference",
                        "skill_id": str(skill.get("skill_id") or "").strip(),
                    }
                )

        issues.sort(
            key=lambda item: (
                str(item.get("code") or ""),
                str(item.get("operation_id") or ""),
                str(item.get("skill_id") or ""),
            )
        )
        return {
            "status": "ok" if not issues else "issues_found",
            "active_skill_count": len(active_skills),
            "issue_count": len(issues),
            "issues": issues,
        }

    def sync_skills_from_markdown(self, *, skills_dir: str = "app/prompts/skills") -> dict[str, Any]:
        directory = Path(skills_dir)
        if not directory.is_absolute():
            directory = (self._repo_root / directory).resolve()
        existing_by_id = {
            str(skill.get("skill_id") or "").strip(): skill
            for skill in self._sqlite_store.list_skills(active_only=False)
        }

        synced: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        now = _utc_now()
        for path in sorted(directory.glob("*_skill.md")):
            relative_path = self._relative_repo_path(path)
            validation = self.validate_skill_markdown(markdown_path=relative_path)
            frontmatter = validation.get("frontmatter")
            if not isinstance(frontmatter, dict):
                frontmatter = {}

            skill_id = str(frontmatter.get("skill_id") or "").strip()
            if not skill_id:
                failed.append(
                    {
                        "markdown_path": relative_path,
                        "errors": ["missing skill_id in frontmatter"],
                    }
                )
                continue

            previous = existing_by_id.get(skill_id, {})
            critical_level_raw = frontmatter.get("critical_level", previous.get("critical_level", 0))
            try:
                critical_level = max(0, int(critical_level_raw))
            except (TypeError, ValueError):
                critical_level = 0

            cron_enabled_raw = frontmatter.get("cron_enabled", previous.get("cron_enabled", False))
            cron_enabled = bool(cron_enabled_raw)
            cron_expr_raw = frontmatter.get("cron_expr", previous.get("cron_expr"))
            cron_expr = str(cron_expr_raw).strip() if isinstance(cron_expr_raw, str) and cron_expr_raw.strip() else None

            active_from_frontmatter = bool(frontmatter.get("active", True))
            learnable_ready = bool(validation.get("ok"))
            active = active_from_frontmatter and learnable_ready

            micro_enabled = bool(frontmatter.get("micro_enabled", previous.get("micro_enabled", False)))
            micro_functions_raw = frontmatter.get("micro_functions")
            micro_functions: list[Any] = micro_functions_raw if isinstance(micro_functions_raw, list) else []
            micro_failure_handoff_raw = frontmatter.get("micro_failure_handoff")
            micro_failure_handoff = (
                micro_failure_handoff_raw if isinstance(micro_failure_handoff_raw, dict) else {}
            )
            main_handoff_context_raw = frontmatter.get("main_handoff_context")
            main_handoff_context = (
                main_handoff_context_raw if isinstance(main_handoff_context_raw, dict) else {}
            )
            main_tools_declared = "main_tools" in frontmatter
            main_tools_version_raw = frontmatter.get("main_tools_contract_version")
            compiled_tools, tool_diagnostics = compile_tool_descriptors(
                skill_id=skill_id,
                contract_version=main_tools_version_raw,
                declarations=frontmatter.get("main_tools") if main_tools_declared else None,
            )
            main_tools = (
                [descriptor.to_storage_dict() for descriptor in compiled_tools]
                if main_tools_declared
                else None
            )
            main_tools_contract_version = (
                int(main_tools_version_raw)
                if main_tools_declared
                and isinstance(main_tools_version_raw, int)
                and not isinstance(main_tools_version_raw, bool)
                and main_tools_version_raw == 1
                else None
            )
            skill_name = str(frontmatter.get("skill_name") or previous.get("skill_name") or skill_id)
            skill_user = str(frontmatter.get("skill_user") or previous.get("skill_user") or "all")
            skill_agents = (
                [str(item) for item in frontmatter.get("skill_agents", []) if str(item).strip()]
                if isinstance(frontmatter.get("skill_agents"), list)
                else [str(item) for item in previous.get("skill_agents", []) if str(item).strip()] or ["all"]
            )
            intents = (
                [str(item) for item in frontmatter.get("intents", []) if str(item).strip()]
                if isinstance(frontmatter.get("intents"), list)
                else [str(item) for item in previous.get("intents", []) if str(item).strip()]
            )
            execution_ref = str(frontmatter.get("execution_ref") or previous.get("execution_ref") or "")
            created_by = str(frontmatter.get("created_by") or previous.get("created_by") or "jarvis")
            storage_type = str(frontmatter.get("storage_type") or previous.get("storage_type") or "hybrid")
            storage_ref = (
                str(frontmatter.get("storage_ref") or "").strip()
                or str(previous.get("storage_ref") or "").strip()
                or None
            )

            incoming = {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "skill_user": skill_user,
                "skill_agents": [str(item).strip().lower() for item in skill_agents if str(item).strip()],
                "intents": [str(item).strip().lower() for item in intents if str(item).strip()],
                "markdown_path": relative_path,
                "execution_ref": execution_ref.strip() if execution_ref.strip() else None,
                "created_by": created_by,
                "storage_type": storage_type.strip().lower(),
                "storage_ref": storage_ref,
                "micro_enabled": bool(micro_enabled),
                "micro_functions": micro_functions if isinstance(micro_functions, list) else [],
                "micro_failure_handoff": micro_failure_handoff if isinstance(micro_failure_handoff, dict) else {},
                "main_handoff_context": main_handoff_context if isinstance(main_handoff_context, dict) else {},
                "learnable_ready": bool(learnable_ready),
                "critical_level": int(critical_level),
                "active": bool(active),
                "cron_enabled": bool(cron_enabled),
                "cron_expr": cron_expr,
                "main_tools": main_tools,
                "main_tools_contract_version": main_tools_contract_version,
            }

            updated = not self._skill_snapshot_equal(existing=previous, incoming=incoming)
            if updated:
                self._sqlite_store.upsert_skill(
                    skill_id=skill_id,
                    skill_name=skill_name,
                    skill_user=skill_user,
                    skill_agents=skill_agents,
                    intents=intents,
                    markdown_path=relative_path,
                    execution_ref=execution_ref,
                    created_by=created_by,
                    storage_type=storage_type,
                    storage_ref=storage_ref,
                    micro_enabled=micro_enabled,
                    micro_functions=micro_functions,
                    micro_failure_handoff=micro_failure_handoff,
                    main_handoff_context=main_handoff_context,
                    learnable_ready=learnable_ready,
                    critical_level=critical_level,
                    active=active,
                    cron_enabled=cron_enabled,
                    cron_expr=cron_expr,
                    main_tools=main_tools,
                    main_tools_contract_version=main_tools_contract_version,
                    updated_at=now,
                )
            record = {
                "skill_id": skill_id,
                "markdown_path": relative_path,
                "active": active,
                "learnable_ready": learnable_ready,
                "micro_enabled": micro_enabled,
                "updated": updated,
                "errors": [str(item) for item in validation.get("errors", []) if str(item).strip()],
                "tool_diagnostics": [dict(item) for item in tool_diagnostics],
            }
            synced.append(record)
            if not learnable_ready:
                failed.append(record)

        return {
            "status": "ok",
            "synced_count": len(synced),
            "failed_count": len(failed),
            "tool_diagnostic_count": sum(
                len(item.get("tool_diagnostics") or []) for item in synced
            ),
            "synced": synced,
            "failed": failed,
        }

    def load_markdown(self, path_value: str) -> str:
        path = Path(path_value)
        if not path.is_absolute():
            path = (self._repo_root / path).resolve()
        cache_key = str(path).lower()
        if cache_key in self._markdown_cache:
            return self._markdown_cache[cache_key]
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            content = ""
        self._markdown_cache[cache_key] = content
        return content

    def load_skill_markdown(self, skill: dict[str, Any] | None) -> str:
        if not isinstance(skill, dict):
            return ""
        markdown_path = str(skill.get("markdown_path") or "").strip()
        if not markdown_path:
            return ""
        return self.load_markdown(markdown_path)

    def load_skill_docs_for_intents(
        self,
        *,
        intents: list[str] | tuple[str, ...],
        user_id: str,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        seen_skill_ids: set[str] = set()
        for raw_intent in intents:
            intent = str(raw_intent or "").strip().lower()
            if not intent:
                continue
            skill = self.resolve_skill(
                intent=intent,
                user_id=user_id,
                agent_id=agent_id,
            )
            if not isinstance(skill, dict):
                continue
            skill_id = str(skill.get("skill_id") or "").strip()
            if not skill_id or skill_id in seen_skill_ids:
                continue
            content = self.load_skill_markdown(skill).strip()
            if not content:
                continue
            seen_skill_ids.add(skill_id)
            docs.append(
                {
                    "skill_id": skill_id,
                    "intent": intent,
                    "markdown_path": str(skill.get("markdown_path") or "").strip(),
                    "content": content,
                }
            )
        return docs

    def load_skill_runtime_docs_for_intents(
        self,
        *,
        intents: list[str] | tuple[str, ...],
        user_id: str,
        agent_id: str,
        max_chars_per_skill: int = 6000,
    ) -> list[dict[str, Any]]:
        """Load a compact execution contract instead of the construction document."""
        docs = self.load_skill_docs_for_intents(
            intents=intents,
            user_id=user_id,
            agent_id=agent_id,
        )
        compact_docs: list[dict[str, Any]] = []
        for doc in docs:
            source = str(doc.get("content") or "").strip()
            compact = self._compact_skill_runtime_contract(
                source,
                max_chars=max(1000, int(max_chars_per_skill)),
            )
            compact_docs.append(
                {
                    **doc,
                    "content": compact,
                    "source_chars": len(source),
                    "runtime_chars": len(compact),
                }
            )
        return compact_docs

    @classmethod
    def _compact_skill_runtime_contract(cls, markdown: str, *, max_chars: int) -> str:
        frontmatter, body = cls._split_frontmatter(str(markdown or ""))
        metadata = {
            key: frontmatter.get(key)
            for key in ("skill_id", "skill_name", "intents", "execution_ref", "micro_enabled")
            if frontmatter.get(key) is not None
        }
        parts = ["# Runtime Skill Contract", json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))]
        preferred_sections = (
            "purpose",
            "when to use this skill",
            "do not use this skill",
            "trigger patterns intent mapping",
            "intent mapping",
            "input schema",
            "required inputs",
            "context resolution rules",
            "clarification rules",
            "safety rules",
            "safe defaults",
            "execution steps",
            "execution rules",
            "failure behavior",
            "microjarvis contract",
            "main handoff context contract",
            "main jarvis responsibilities",
        )
        headings = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
        sections: dict[str, str] = {}
        for index, match in enumerate(headings):
            raw_heading = str(match.group(1) or "").strip()
            normalized = re.sub(r"[^a-z0-9\s]+", " ", raw_heading.casefold())
            normalized = re.sub(r"\s+", " ", normalized).strip()
            if normalized not in preferred_sections:
                continue
            section_end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            sections.setdefault(normalized, body[match.start() : section_end].strip())

        selected = [sections[key] for key in preferred_sections if key in sections]
        header_chars = len("\n\n".join(parts))
        per_section_chars = max(240, min(900, (max_chars - header_chars - 32) // max(len(selected), 1)))
        for section in selected:
            if len(section) > per_section_chars:
                section = f"{section[: per_section_chars - 3]}..."
            parts.append(section)

        compact = "\n\n".join(part for part in parts if part).strip()
        if len(compact) <= max_chars:
            return compact
        return f"{compact[: max_chars - 3]}..."

    def validate_skill_markdown(self, *, markdown_path: str) -> dict[str, Any]:
        markdown = self.load_markdown(markdown_path)
        if not markdown:
            return {
                "ok": False,
                "errors": [f"unable to read markdown: {markdown_path}"],
                "frontmatter": {},
                "sections": [],
            }
        frontmatter, body = self._split_frontmatter(markdown)
        errors: list[str] = []
        if not frontmatter:
            errors.append("missing yaml frontmatter block")
        else:
            missing_fields = sorted(field for field in REQUIRED_FRONTMATTER_FIELDS if field not in frontmatter)
            for field in missing_fields:
                errors.append(f"missing frontmatter field: {field}")

        headings = self._canonicalize_section_headings(self._extract_h2_headings(body))
        missing_sections = sorted(section for section in REQUIRED_SECTION_KEYS if section not in headings)
        for section in missing_sections:
            errors.append(f"missing required section: {section}")

        micro_functions = frontmatter.get("micro_functions") if isinstance(frontmatter, dict) else None
        if isinstance(frontmatter, dict) and frontmatter.get("micro_enabled") and not isinstance(micro_functions, list):
            errors.append("micro_enabled=true requires micro_functions list")
        if isinstance(frontmatter, dict) and frontmatter.get("micro_enabled") and isinstance(micro_functions, list):
            if not micro_functions:
                errors.append("micro_enabled=true requires at least one micro_functions entry")

        tool_diagnostics: tuple[dict[str, str], ...] = ()
        if isinstance(frontmatter, dict) and "main_tools" in frontmatter:
            _, tool_diagnostics = compile_tool_descriptors(
                skill_id=str(frontmatter.get("skill_id") or ""),
                contract_version=frontmatter.get("main_tools_contract_version"),
                declarations=frontmatter.get("main_tools"),
            )

        return {
            "ok": not errors,
            "errors": errors,
            "tool_diagnostics": [dict(item) for item in tool_diagnostics],
            "frontmatter": frontmatter,
            "sections": sorted(headings),
        }

    def load_model_boot_memory(self, *, model_name: str, agent_id: str | None = None) -> list[dict[str, Any]]:
        normalized_model_name = model_name.strip().lower()
        docs: list[dict[str, Any]] = []
        for row in self._sqlite_store.list_model_boot_memory(normalized_model_name):
            doc_path = str(row.get("doc_path") or "").strip()
            if not doc_path:
                continue
            if self._should_skip_boot_doc_for_model(model_name=normalized_model_name, doc_path=doc_path):
                continue
            if self._is_deprecated_eager_skill_boot_doc(
                model_name=normalized_model_name,
                doc_path=doc_path,
            ):
                continue
            docs.append(
                {
                    "doc_path": doc_path,
                    "content": self.load_markdown(doc_path),
                    "priority": int(row.get("priority") or 100),
                    "required": bool(row.get("required")),
                }
            )
        if agent_id:
            profile = self._sqlite_store.get_agent_profile(agent_id)
            personality_doc = str((profile or {}).get("personality_doc_path") or "").strip()
            if personality_doc and model_name.strip().lower() in {"jarvis", "bigj"}:
                docs.append(
                    {
                        "doc_path": personality_doc,
                        "content": self.load_markdown(personality_doc),
                        "priority": 10,
                        "required": False,
                    }
                )
        docs.sort(key=lambda item: (int(item.get("priority", 100)), str(item.get("doc_path") or "")))
        return docs

    @staticmethod
    def _should_skip_boot_doc_for_model(*, model_name: str, doc_path: str) -> bool:
        normalized_model_name = model_name.strip().lower()
        normalized_path = str(doc_path or "").strip().replace("\\", "/").lower()
        if normalized_model_name == "microj":
            if normalized_path.endswith("/jarvis_identity.md") or normalized_path.endswith("/jarvis_capabilities.md"):
                return True
            if normalized_path.endswith("/jarvis_loop.md"):
                return True
            if normalized_path.endswith("/agent_registry.md"):
                return True
            if normalized_path.endswith("/jarvis_system.md"):
                return True
            if "/personas/" in normalized_path:
                return True
        if normalized_model_name in {"jarvis", "bigj"}:
            if normalized_path.endswith("/microjarvis_identity.md") or normalized_path.endswith(
                "/microjarvis_capabilities.md"
            ):
                return True
        return False

    @staticmethod
    def _is_deprecated_eager_skill_boot_doc(*, model_name: str, doc_path: str) -> bool:
        normalized = str(doc_path or "").strip().replace("\\", "/").lower()
        if normalized.endswith("/skills/critical_skills.md"):
            return True
        if normalized.endswith("/skills/conversation_skill.md"):
            return True
        if normalized.endswith("/skills/micro_jarvis_skills.md") or normalized.endswith("/prompts/micro_jarvis_skills.md"):
            return model_name.strip().lower() != "microj"
        return False

    def is_micro_allowed_for_intent(self, *, skill: dict[str, Any] | None, intent: str) -> bool:
        if not isinstance(skill, dict):
            return False
        if not bool(skill.get("active")):
            return False
        if not bool(skill.get("learnable_ready")):
            return False
        if not bool(skill.get("micro_enabled")):
            return False
        normalized_intent = str(intent or "").strip().lower()
        if not normalized_intent:
            return False
        micro_functions = skill.get("micro_functions")
        if not isinstance(micro_functions, list):
            return False
        for entry in micro_functions:
            if isinstance(entry, str) and entry.strip().lower() == normalized_intent:
                return True
            if isinstance(entry, dict):
                candidate = str(entry.get("intent") or "").strip().lower()
                if candidate == normalized_intent:
                    return True
        return False

    def record_skill_run(
        self,
        *,
        skill: dict[str, Any] | None,
        session_id: str | None,
        user_id: str,
        intent: str | None,
        route: str | None,
        status: str,
        confidence: float | None,
        latency_ms: int | None = None,
    ) -> str | None:
        if not isinstance(skill, dict):
            return None
        skill_id = str(skill.get("skill_id") or "").strip()
        if not skill_id:
            return None
        return self._sqlite_store.record_skill_run(
            skill_id=skill_id,
            session_id=session_id,
            user_id=user_id,
            intent=intent,
            route=route,
            status=status,
            confidence=confidence,
            latency_ms=latency_ms,
            created_at=_utc_now(),
        )

    def compile_critical_skills_markdown(
        self,
        *,
        output_path: str = "app/prompts/skills/critical_skills.md",
        min_critical_level: int = 1,
        compile_if_stale: bool = False,
    ) -> dict[str, Any]:
        resolved_output = Path(output_path)
        if not resolved_output.is_absolute():
            resolved_output = (self._repo_root / resolved_output).resolve()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = self._artifact_metadata_path(resolved_output)

        critical_threshold = max(0, int(min_critical_level))
        selected_skills: list[dict[str, Any]] = []
        missing_markdown_paths: list[str] = []
        stale_inputs: list[dict[str, Any]] = []
        for skill in self.list_skills(active_only=True):
            critical_level = int(skill.get("critical_level") or 0)
            if critical_level < critical_threshold:
                continue
            if not bool(skill.get("learnable_ready")):
                continue
            skill_id = str(skill.get("skill_id") or "").strip()
            markdown_path = str(skill.get("markdown_path") or "").strip()
            markdown = self.load_skill_markdown(skill).strip()
            stale_inputs.append(
                {
                    "skill_id": skill_id,
                    "critical_level": critical_level,
                    "usage_count": int(skill.get("usage_count") or 0),
                    "updated_at": str(skill.get("updated_at") or ""),
                    "markdown_path": markdown_path,
                    "markdown_hash": self._sha256_text(markdown),
                }
            )
            if not markdown:
                missing_markdown_paths.append(markdown_path)
                continue
            selected_skills.append(
                {
                    "skill_id": skill_id,
                    "skill_name": str(skill.get("skill_name") or "").strip(),
                    "critical_level": critical_level,
                    "usage_count": int(skill.get("usage_count") or 0),
                    "intents": [str(item) for item in skill.get("intents") or [] if str(item).strip()],
                    "markdown_path": markdown_path,
                    "markdown": markdown,
                }
            )

        selected_skills.sort(
            key=lambda row: (
                -int(row.get("critical_level") or 0),
                -int(row.get("usage_count") or 0),
                str(row.get("skill_id") or ""),
            ),
        )
        stale_inputs.sort(
            key=lambda row: (
                -int(row.get("critical_level") or 0),
                -int(row.get("usage_count") or 0),
                str(row.get("skill_id") or ""),
            )
        )

        source_descriptor = {
            "artifact": "critical_skills",
            "min_critical_level": critical_threshold,
            "inputs": stale_inputs,
        }
        source_hash = self._sha256_json(source_descriptor)
        previous_meta = self._read_json_file(metadata_path)
        if (
            compile_if_stale
            and resolved_output.exists()
            and isinstance(previous_meta, dict)
            and str(previous_meta.get("source_hash") or "") == source_hash
        ):
            return {
                "status": "skipped",
                "reason": "up_to_date",
                "output_path": self._relative_repo_path(resolved_output),
                "metadata_path": str(metadata_path),
                "source_hash": source_hash,
                "skill_count": len(selected_skills),
                "missing_markdown_count": len({path for path in missing_markdown_paths if path}),
                "min_critical_level": critical_threshold,
            }

        compiled_at = _utc_now()
        lines: list[str] = [
            "# Critical Skills (Compiled)",
            "",
            "Auto-generated from SQL `skills` registry.",
            f"- min_critical_level: {critical_threshold}",
            f"- skill_count: {len(selected_skills)}",
            "",
        ]
        if missing_markdown_paths:
            missing_paths = sorted({path for path in missing_markdown_paths if path})
            lines.extend(
                [
                    "## Missing Skill Markdown",
                    "",
                    "These skills were in scope but had no readable markdown:",
                    *[f"- `{path}`" for path in missing_paths],
                    "",
                ]
            )

        if not selected_skills:
            lines.extend(
                [
                    "## No Critical Skills",
                    "",
                    "No active skills matched the critical threshold at compile time.",
                    "",
                ]
            )
        else:
            for index, skill in enumerate(selected_skills, start=1):
                skill_id = str(skill.get("skill_id") or "")
                skill_name = str(skill.get("skill_name") or skill_id)
                critical_level = int(skill.get("critical_level") or 0)
                intents = [str(item) for item in skill.get("intents") or [] if str(item).strip()]
                markdown_path = str(skill.get("markdown_path") or "")
                lines.extend(
                    [
                        f"## {index}. {skill_name} (`{skill_id}`)",
                        "",
                        f"- critical_level: {critical_level}",
                        f"- intents: {', '.join(intents) if intents else 'none'}",
                        f"- markdown_path: `{markdown_path}`",
                        "",
                        str(skill.get("markdown") or "").strip(),
                        "",
                    ]
                )

        compiled_markdown = "\n".join(lines).strip() + "\n"
        content_hash = self._sha256_text(compiled_markdown)
        resolved_output.write_text(compiled_markdown, encoding="utf-8")
        self._markdown_cache.pop(str(resolved_output).lower(), None)
        self._write_json_file(
            metadata_path,
            {
                "artifact": "critical_skills",
                "compiled_at": compiled_at,
                "source_hash": source_hash,
                "content_hash": content_hash,
                "output_path": self._relative_repo_path(resolved_output),
                "min_critical_level": critical_threshold,
                "skill_count": len(selected_skills),
                "missing_markdown_count": len({path for path in missing_markdown_paths if path}),
            },
        )
        return {
            "status": "ok",
            "compiled_at": compiled_at,
            "output_path": str(resolved_output),
            "metadata_path": str(metadata_path),
            "source_hash": source_hash,
            "content_hash": content_hash,
            "skill_count": len(selected_skills),
            "missing_markdown_count": len({path for path in missing_markdown_paths if path}),
            "min_critical_level": critical_threshold,
        }

    def compile_micro_skills_markdown(
        self,
        *,
        output_path: str = "app/prompts/micro_jarvis_skills.md",
        compile_if_stale: bool = False,
    ) -> dict[str, Any]:
        resolved_output = Path(output_path)
        if not resolved_output.is_absolute():
            resolved_output = (self._repo_root / resolved_output).resolve()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = self._artifact_metadata_path(resolved_output)

        selected: list[dict[str, Any]] = []
        stale_inputs: list[dict[str, Any]] = []
        for skill in self.list_skills(active_only=True):
            if not bool(skill.get("learnable_ready")) or not bool(skill.get("micro_enabled")):
                continue
            micro_functions = skill.get("micro_functions")
            if not isinstance(micro_functions, list) or not micro_functions:
                continue
            skill_id = str(skill.get("skill_id") or "").strip()
            stale_inputs.append(
                {
                    "skill_id": skill_id,
                    "updated_at": str(skill.get("updated_at") or ""),
                    "micro_functions": micro_functions,
                    "markdown_path": str(skill.get("markdown_path") or "").strip(),
                }
            )
            selected.append(
                {
                    "skill_id": skill_id,
                    "skill_name": str(skill.get("skill_name") or "").strip(),
                    "micro_functions": micro_functions,
                    "markdown_path": str(skill.get("markdown_path") or "").strip(),
                }
            )

        selected.sort(key=lambda row: str(row.get("skill_id") or ""))
        stale_inputs.sort(key=lambda row: str(row.get("skill_id") or ""))
        source_descriptor = {
            "artifact": "micro_skills",
            "inputs": stale_inputs,
        }
        source_hash = self._sha256_json(source_descriptor)
        previous_meta = self._read_json_file(metadata_path)
        if (
            compile_if_stale
            and resolved_output.exists()
            and isinstance(previous_meta, dict)
            and str(previous_meta.get("source_hash") or "") == source_hash
        ):
            return {
                "status": "skipped",
                "reason": "up_to_date",
                "output_path": str(resolved_output),
                "metadata_path": str(metadata_path),
                "source_hash": source_hash,
                "skill_count": len(selected),
            }

        compiled_at = _utc_now()
        lines: list[str] = [
            "# Micro Jarvis Skills (Compiled)",
            "",
            "Auto-generated micro execution allowlist from SQL `skills` registry.",
            f"- skill_count: {len(selected)}",
            "",
        ]
        if not selected:
            lines.extend(
                [
                    "## No Micro Skills",
                    "",
                    "No active learnable skills are currently enabled for micro execution.",
                    "",
                ]
            )
        else:
            for skill in selected:
                lines.extend(
                    [
                        f"## {skill['skill_name']} (`{skill['skill_id']}`)",
                        "",
                        f"- markdown_path: `{skill['markdown_path']}`",
                        "- micro_functions:",
                    ]
                )
                for entry in skill["micro_functions"]:
                    if isinstance(entry, dict):
                        function_id = str(entry.get("function_id") or "").strip()
                        intent = str(entry.get("intent") or "").strip()
                        lines.append(
                            f"  - {function_id or intent or 'unknown'}"
                            + (f" -> {intent}" if intent else "")
                        )
                    else:
                        lines.append(f"  - {str(entry).strip()}")
                lines.append("")

        compiled = "\n".join(lines).strip() + "\n"
        content_hash = self._sha256_text(compiled)
        resolved_output.write_text(compiled, encoding="utf-8")
        self._markdown_cache.pop(str(resolved_output).lower(), None)
        self._write_json_file(
            metadata_path,
            {
                "artifact": "micro_skills",
                "compiled_at": compiled_at,
                "source_hash": source_hash,
                "content_hash": content_hash,
                "output_path": self._relative_repo_path(resolved_output),
                "skill_count": len(selected),
            },
        )
        return {
            "status": "ok",
            "compiled_at": compiled_at,
            "output_path": str(resolved_output),
            "metadata_path": str(metadata_path),
            "source_hash": source_hash,
            "content_hash": content_hash,
            "skill_count": len(selected),
        }

    @staticmethod
    def _extract_h2_headings(markdown_body: str) -> set[str]:
        headings: set[str] = set()
        for match in re.finditer(r"^##\s+(.+?)\s*$", markdown_body or "", flags=re.MULTILINE):
            raw = str(match.group(1) or "")
            raw = re.sub(r"^\d+(?:\.\d+)?\s*[\.\-:]?\s*", "", raw)
            cleaned = re.sub(r"[^a-z0-9\s]+", " ", raw.strip().lower())
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                headings.add(cleaned)
        return headings

    @staticmethod
    def _canonicalize_section_headings(headings: set[str]) -> set[str]:
        canonical = {str(item).strip() for item in headings if str(item).strip()}
        for target, aliases in SECTION_KEY_ALIASES.items():
            if target in canonical:
                continue
            if any(alias in canonical for alias in aliases):
                canonical.add(target)
        return canonical

    @staticmethod
    def _split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
        match = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", markdown, flags=re.DOTALL)
        if not match:
            return {}, markdown
        frontmatter_text = str(match.group(1) or "")
        body = str(match.group(2) or "")
        try:
            loaded = yaml.safe_load(frontmatter_text)
        except Exception:
            loaded = None
        frontmatter = loaded if isinstance(loaded, dict) else {}
        return frontmatter, body

    def _relative_repo_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._repo_root)).replace("\\", "/")
        except Exception:
            return str(path.resolve())

    @staticmethod
    def _skill_snapshot_equal(*, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        if not isinstance(existing, dict):
            return False
        compare_keys = {
            "skill_name",
            "skill_user",
            "skill_agents",
            "intents",
            "markdown_path",
            "execution_ref",
            "created_by",
            "storage_type",
            "storage_ref",
            "micro_enabled",
            "micro_functions",
            "micro_failure_handoff",
            "main_handoff_context",
            "learnable_ready",
            "critical_level",
            "active",
            "cron_enabled",
            "cron_expr",
            "main_tools",
            "main_tools_contract_version",
        }
        for key in compare_keys:
            current_value = existing.get(key)
            new_value = incoming.get(key)
            if key in {"skill_agents", "intents"}:
                current_value = [str(item).strip().lower() for item in (current_value or []) if str(item).strip()]
                new_value = [str(item).strip().lower() for item in (new_value or []) if str(item).strip()]
            elif key in {"execution_ref", "storage_ref", "cron_expr"}:
                current_value = str(current_value).strip() if isinstance(current_value, str) and current_value.strip() else None
                new_value = str(new_value).strip() if isinstance(new_value, str) and new_value.strip() else None
            elif key in {"skill_user", "storage_type"}:
                current_value = str(current_value or "").strip().lower()
                new_value = str(new_value or "").strip().lower()
            elif key in {"critical_level"}:
                try:
                    current_value = int(current_value or 0)
                except (TypeError, ValueError):
                    current_value = 0
                try:
                    new_value = int(new_value or 0)
                except (TypeError, ValueError):
                    new_value = 0
            elif key == "main_tools_contract_version":
                current_value = int(current_value) if isinstance(current_value, int) else None
                new_value = int(new_value) if isinstance(new_value, int) else None
            elif key == "main_tools":
                current_value = current_value if isinstance(current_value, list) else None
                new_value = new_value if isinstance(new_value, list) else None
            elif key in {"micro_enabled", "learnable_ready", "active", "cron_enabled"}:
                current_value = bool(current_value)
                new_value = bool(new_value)
            if current_value != new_value:
                return False
        return True

    @staticmethod
    def _artifact_metadata_path(output_path: Path) -> Path:
        return output_path.with_suffix(output_path.suffix + ".meta.json")

    @staticmethod
    def _sha256_text(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _sha256_json(value: Any) -> str:
        normalized = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
        except Exception:
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
