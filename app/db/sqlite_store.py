from __future__ import annotations

import json
from threading import Lock
from typing import Any
from uuid import uuid4

from app.memory.types import MemoryEntry
from app.db.connection import open_sqlite_connection
from app.db.migrations import initialize_schema


class SQLiteStore:
    def __init__(self, database_path: str) -> None:
        self._database_path, self._conn = open_sqlite_connection(database_path)
        self._lock = Lock()
        initialize_schema(self._conn)

    @property
    def database_path(self) -> str:
        return str(self._database_path)


    def upsert_session(
        self,
        session_id: str,
        user_id: str,
        source: str,
        state: str,
        owner: str,
        last_activity_timestamp: str,
        context_reference: dict[str, Any] | None = None,
        context_version: int | None = None,
    ) -> None:
        context_payload = context_reference if isinstance(context_reference, dict) else {}
        context_json = json.dumps(context_payload, ensure_ascii=True)
        resolved_context_version = self._resolve_context_version(
            context_payload=context_payload,
            context_version=context_version,
        )
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO sessions (
                    session_id, user_id, source, state, owner, last_activity_timestamp,
                    context_reference_json, context_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    source=excluded.source,
                    state=excluded.state,
                    owner=excluded.owner,
                    last_activity_timestamp=excluded.last_activity_timestamp,
                    context_reference_json=excluded.context_reference_json,
                    context_version=excluded.context_version
                """,
                (
                    session_id,
                    user_id,
                    source,
                    state,
                    owner,
                    last_activity_timestamp,
                    context_json,
                    resolved_context_version,
                ),
            )
            self._conn.commit()

    def insert_event(
        self,
        timestamp: str,
        event_type: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO events (timestamp, event_type, session_id, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, event_type, session_id, json.dumps(payload, ensure_ascii=True)),
            )
            self._conn.commit()

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 2000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT timestamp, event_type, session_id, payload_json
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (bounded,),
            )
            rows = cur.fetchall()
        events = []
        for row in reversed(rows):
            payload = {}
            raw_payload = row["payload_json"]
            if isinstance(raw_payload, str):
                try:
                    loaded = json.loads(raw_payload)
                    if isinstance(loaded, dict):
                        payload = loaded
                except json.JSONDecodeError:
                    payload = {"raw": raw_payload}
            events.append(
                {
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "session_id": row["session_id"],
                    "payload": payload,
                }
            )
        return events

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT session_id, user_id, source, state, owner, last_activity_timestamp,
                       context_reference_json, context_version
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "source": row["source"],
            "state": row["state"],
            "owner": row["owner"],
            "last_activity_timestamp": row["last_activity_timestamp"],
            "context_reference": self._json_object(row["context_reference_json"]),
            "context_version": int(row["context_version"]) if row["context_version"] is not None else 0,
        }

    @staticmethod
    def _resolve_context_version(
        *,
        context_payload: dict[str, Any],
        context_version: int | None,
    ) -> int:
        if isinstance(context_version, int):
            return max(0, context_version)
        if isinstance(context_version, float):
            return max(0, int(context_version))
        if isinstance(context_version, str):
            cleaned = context_version.strip()
            if cleaned:
                try:
                    return max(0, int(cleaned))
                except ValueError:
                    pass

        raw = context_payload.get("context_version")
        if isinstance(raw, int):
            return max(0, raw)
        if isinstance(raw, float):
            return max(0, int(raw))
        if isinstance(raw, str):
            cleaned = raw.strip()
            if cleaned:
                try:
                    return max(0, int(cleaned))
                except ValueError:
                    return 0
        return 0

    def clear_all(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for table in (
                "ticket_review_runs",
                "ticket_expectations",
                "operation_receipts",
                "ticket_entries",
                "durable_jobs",
                "work_tickets",
                "external_identity_bindings",
                "worker_heartbeats",
            ):
                cur.execute(f"DELETE FROM {table}")
            cur.execute("DELETE FROM events")
            cur.execute("DELETE FROM sessions")
            cur.execute("DELETE FROM memory_entries")
            cur.execute("DELETE FROM switch_actions_log")
            cur.execute("DELETE FROM switches")
            cur.execute("DELETE FROM list_operations")
            cur.execute("DELETE FROM list_items")
            cur.execute("DELETE FROM lists")
            cur.execute("DELETE FROM skill_runs")
            cur.execute("DELETE FROM conversation_topic_history")
            cur.execute("DELETE FROM conversation_topics")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_memory_entry(self, entry: MemoryEntry) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO memory_entries (
                    timestamp, session_id, user_id, source, intent, route,
                    request_text, response_summary, metadata_json, operation_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp,
                    entry.session_id,
                    entry.user_id,
                    entry.source,
                    entry.intent,
                    entry.route,
                    entry.request_text,
                    entry.response_summary,
                    json.dumps(entry.metadata, ensure_ascii=True),
                    entry.operation_id,
                ),
            )
            self._conn.commit()

    def recent_memory_entries(self, limit: int = 50) -> list[MemoryEntry]:
        bounded = max(1, min(limit, 2000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT timestamp, session_id, user_id, source, intent, route,
                       request_text, response_summary, metadata_json, operation_id
                FROM memory_entries
                ORDER BY id DESC
                LIMIT ?
                """,
                (bounded,),
            )
            rows = cur.fetchall()

        entries: list[MemoryEntry] = []
        for row in reversed(rows):
            metadata: dict[str, Any] = {}
            raw_metadata = row["metadata_json"]
            if isinstance(raw_metadata, str):
                try:
                    loaded = json.loads(raw_metadata)
                    if isinstance(loaded, dict):
                        metadata = loaded
                except json.JSONDecodeError:
                    metadata = {"raw": raw_metadata}
            entries.append(
                MemoryEntry(
                    timestamp=row["timestamp"],
                    session_id=row["session_id"],
                    user_id=row["user_id"],
                    source=row["source"],
                    intent=row["intent"],
                    route=row["route"],
                    request_text=row["request_text"],
                    response_summary=row["response_summary"],
                    metadata=metadata,
                    operation_id=row["operation_id"],
                )
            )
        return entries

    def upsert_switch(
        self,
        *,
        name: str,
        state: str,
        updated_at: str,
        room_name: str | None = None,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO switches (name, room_name, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    room_name=COALESCE(excluded.room_name, switches.room_name),
                    state=excluded.state,
                    updated_at=excluded.updated_at
                """,
                (name, room_name, state, updated_at),
            )
            self._conn.commit()

    def list_switches(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT name, room_name, state, updated_at
                FROM switches
                ORDER BY name ASC
                """
            )
            rows = cur.fetchall()
        return [
            {
                "name": row["name"],
                "room_name": row["room_name"],
                "state": row["state"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_switch(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT name, room_name, state, updated_at
                FROM switches
                WHERE name = ?
                """,
                (name,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "name": row["name"],
            "room_name": row["room_name"],
            "state": row["state"],
            "updated_at": row["updated_at"],
        }

    def insert_switch_action_log(
        self,
        *,
        timestamp: str,
        switch_name: str,
        action: str,
        state_after: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO switch_actions_log (
                    timestamp, switch_name, action, state_after, source_interface, requested_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    switch_name,
                    action,
                    state_after,
                    source_interface,
                    requested_by_user_id,
                ),
            )
            self._conn.commit()

    def recent_switch_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 1000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT timestamp, switch_name, action, state_after, source_interface, requested_by_user_id
                FROM switch_actions_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (bounded,),
            )
            rows = cur.fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "switch_name": row["switch_name"],
                "action": row["action"],
                "state_after": row["state_after"],
                "source_interface": row["source_interface"],
                "requested_by_user_id": row["requested_by_user_id"],
            }
            for row in rows
        ]

    @staticmethod
    def _json_array(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, list):
                return [str(item).strip() for item in loaded if str(item).strip()]
            text = value.strip()
            if text:
                return [text]
        return []

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(loaded, dict):
                return loaded
        return {}

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return []
            if isinstance(loaded, list):
                return list(loaded)
            if isinstance(loaded, dict):
                maybe_items = loaded.get("items")
                if isinstance(maybe_items, list):
                    return list(maybe_items)
        return []

    def upsert_model_boot_memory(
        self,
        *,
        model_name: str,
        doc_path: str,
        priority: int = 100,
        required: bool = True,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO model_boot_memory (model_name, doc_path, priority, required)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(model_name, doc_path) DO UPDATE SET
                    priority=excluded.priority,
                    required=excluded.required
                """,
                (
                    model_name.strip().lower(),
                    doc_path.strip(),
                    int(priority),
                    1 if required else 0,
                ),
            )
            self._conn.commit()

    def list_model_boot_memory(self, model_name: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT model_name, doc_path, priority, required
                FROM model_boot_memory
                WHERE model_name = ?
                ORDER BY priority ASC, doc_path ASC
                """,
                (model_name.strip().lower(),),
            )
            rows = cur.fetchall()
        return [
            {
                "model_name": row["model_name"],
                "doc_path": row["doc_path"],
                "priority": int(row["priority"]),
                "required": bool(int(row["required"])),
            }
            for row in rows
        ]

    def upsert_skill(
        self,
        *,
        skill_id: str,
        skill_name: str,
        skill_user: str,
        skill_agents: list[str],
        intents: list[str],
        markdown_path: str,
        execution_ref: str | None,
        created_by: str,
        storage_type: str,
        storage_ref: str | None,
        micro_enabled: bool = False,
        micro_functions: list[Any] | None = None,
        micro_failure_handoff: dict[str, Any] | None = None,
        main_handoff_context: dict[str, Any] | None = None,
        learnable_ready: bool = False,
        critical_level: int = 0,
        active: bool = True,
        cron_enabled: bool = False,
        cron_expr: str | None = None,
        main_tools: list[dict[str, Any]] | None = None,
        main_tools_contract_version: int | None = None,
        updated_at: str,
    ) -> None:
        agents = [item.strip().lower() for item in skill_agents if str(item).strip()]
        if not agents:
            agents = ["all"]
        intents_normalized = [item.strip().lower() for item in intents if str(item).strip()]
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO skills (
                    skill_id, skill_name, skill_user, skill_agents_json, intents_json,
                    markdown_path, execution_ref, created_by, storage_type, storage_ref,
                    micro_enabled, micro_functions_json, micro_failure_handoff_json,
                    main_handoff_context_json, learnable_ready,
                    critical_level, active, cron_enabled, cron_expr,
                    main_tools_json, main_tools_contract_version, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    skill_name=excluded.skill_name,
                    skill_user=excluded.skill_user,
                    skill_agents_json=excluded.skill_agents_json,
                    intents_json=excluded.intents_json,
                    markdown_path=excluded.markdown_path,
                    execution_ref=excluded.execution_ref,
                    created_by=excluded.created_by,
                    storage_type=excluded.storage_type,
                    storage_ref=excluded.storage_ref,
                    micro_enabled=excluded.micro_enabled,
                    micro_functions_json=excluded.micro_functions_json,
                    micro_failure_handoff_json=excluded.micro_failure_handoff_json,
                    main_handoff_context_json=excluded.main_handoff_context_json,
                    learnable_ready=excluded.learnable_ready,
                    critical_level=excluded.critical_level,
                    active=excluded.active,
                    cron_enabled=excluded.cron_enabled,
                    cron_expr=excluded.cron_expr,
                    main_tools_json=excluded.main_tools_json,
                    main_tools_contract_version=excluded.main_tools_contract_version,
                    updated_at=excluded.updated_at
                """,
                (
                    skill_id.strip(),
                    skill_name.strip(),
                    skill_user.strip().lower() or "all",
                    json.dumps(agents, ensure_ascii=True),
                    json.dumps(intents_normalized, ensure_ascii=True),
                    markdown_path.strip(),
                    execution_ref.strip() if isinstance(execution_ref, str) and execution_ref.strip() else None,
                    created_by.strip(),
                    storage_type.strip().lower(),
                    storage_ref.strip() if isinstance(storage_ref, str) and storage_ref.strip() else None,
                    1 if bool(micro_enabled) else 0,
                    json.dumps(micro_functions if isinstance(micro_functions, list) else [], ensure_ascii=True),
                    json.dumps(
                        micro_failure_handoff if isinstance(micro_failure_handoff, dict) else {},
                        ensure_ascii=True,
                    ),
                    json.dumps(
                        main_handoff_context if isinstance(main_handoff_context, dict) else {},
                        ensure_ascii=True,
                    ),
                    1 if bool(learnable_ready) else 0,
                    max(0, int(critical_level)),
                    1 if active else 0,
                    1 if cron_enabled else 0,
                    cron_expr.strip() if isinstance(cron_expr, str) and cron_expr.strip() else None,
                    json.dumps(main_tools, ensure_ascii=True, sort_keys=True)
                    if isinstance(main_tools, list)
                    else None,
                    int(main_tools_contract_version)
                    if isinstance(main_tools_contract_version, int)
                    and not isinstance(main_tools_contract_version, bool)
                    else None,
                    updated_at,
                ),
            )
            self._conn.commit()

    def list_skills(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = (
            """
            SELECT skill_id, skill_name, skill_user, skill_agents_json, intents_json,
                   markdown_path, execution_ref, created_by, storage_type, storage_ref,
                   micro_enabled, micro_functions_json, micro_failure_handoff_json,
                   main_handoff_context_json, learnable_ready, usage_count,
                   success_count, run_count, success_rate, critical_level, active,
                   cron_enabled, cron_expr, last_used_at,
                   main_tools_json, main_tools_contract_version, updated_at
            FROM skills
            """
        )
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY critical_level DESC, usage_count DESC, updated_at DESC"

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        skills: list[dict[str, Any]] = []
        for row in rows:
            skills.append(
                {
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "skill_user": row["skill_user"],
                    "skill_agents": self._json_array(row["skill_agents_json"]),
                    "intents": self._json_array(row["intents_json"]),
                    "markdown_path": row["markdown_path"],
                    "execution_ref": row["execution_ref"],
                    "created_by": row["created_by"],
                    "storage_type": row["storage_type"],
                    "storage_ref": row["storage_ref"],
                    "micro_enabled": bool(int(row["micro_enabled"])),
                    "micro_functions": self._json_list(row["micro_functions_json"]),
                    "micro_failure_handoff": self._json_object(row["micro_failure_handoff_json"]),
                    "main_handoff_context": self._json_object(row["main_handoff_context_json"]),
                    "learnable_ready": bool(int(row["learnable_ready"])),
                    "usage_count": int(row["usage_count"]),
                    "success_count": int(row["success_count"]),
                    "run_count": int(row["run_count"]),
                    "success_rate": float(row["success_rate"]),
                    "critical_level": int(row["critical_level"]),
                    "active": bool(int(row["active"])),
                    "cron_enabled": bool(int(row["cron_enabled"])),
                    "cron_expr": row["cron_expr"],
                    "last_used_at": row["last_used_at"],
                    "main_tools": self._json_list(row["main_tools_json"])
                    if row["main_tools_json"] is not None
                    else None,
                    "main_tools_contract_version": int(row["main_tools_contract_version"])
                    if row["main_tools_contract_version"] is not None
                    else None,
                    "updated_at": row["updated_at"],
                }
            )
        return skills

    def find_skill_for_intent(
        self,
        *,
        intent: str,
        user_id: str,
        agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_intent = intent.strip().lower()
        normalized_user = user_id.strip().lower()
        normalized_agent = (agent_id or "").strip().lower()
        if not normalized_intent:
            return None

        candidates: list[dict[str, Any]] = []
        for skill in self.list_skills(active_only=True):
            intents = {item.strip().lower() for item in skill.get("intents") or [] if item}
            if normalized_intent not in intents:
                continue

            skill_user = str(skill.get("skill_user") or "all").strip().lower()
            if skill_user not in {"all", normalized_user, normalized_agent}:
                continue

            agents = {item.strip().lower() for item in skill.get("skill_agents") or [] if item}
            if agents and "all" not in agents and normalized_agent and normalized_agent not in agents:
                continue
            if agents and "all" not in agents and not normalized_agent:
                continue
            candidates.append(skill)

        if not candidates:
            return None

        def ownership_order(skill: dict[str, Any]) -> tuple[int, int, int, str]:
            critical = int(skill.get("critical_level") or 0)
            skill_user = str(skill.get("skill_user") or "all").strip().lower()
            agents = {item.strip().lower() for item in skill.get("skill_agents") or [] if item}
            user_specificity = 0 if skill_user == normalized_user else 1
            agent_specificity = 0 if normalized_agent and normalized_agent in agents else 1
            return (
                user_specificity,
                agent_specificity,
                -critical,
                str(skill.get("skill_id") or ""),
            )

        # Runtime counters are telemetry, not authority. Duplicate ownership is
        # reported separately; this stable order keeps resolution deterministic
        # while an operator repairs the catalog.
        candidates.sort(key=ownership_order)
        return candidates[0]

    def record_skill_run(
        self,
        *,
        skill_id: str,
        session_id: str | None,
        user_id: str,
        intent: str | None,
        route: str | None,
        status: str,
        confidence: float | None,
        latency_ms: int | None,
        created_at: str,
    ) -> str:
        run_id = str(uuid4())
        normalized_status = status.strip().lower() if isinstance(status, str) else "unknown"
        success_statuses = {"ok", "success", "executed", "conversation", "planned"}
        is_success = normalized_status in success_statuses

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO skill_runs (
                    run_id, skill_id, session_id, user_id, intent, route, status, confidence, latency_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    skill_id.strip(),
                    session_id.strip() if isinstance(session_id, str) and session_id.strip() else None,
                    user_id.strip(),
                    intent.strip().lower() if isinstance(intent, str) and intent.strip() else None,
                    route.strip() if isinstance(route, str) and route.strip() else None,
                    normalized_status,
                    float(confidence) if isinstance(confidence, (float, int)) else None,
                    int(latency_ms) if isinstance(latency_ms, int) else None,
                    created_at,
                ),
            )

            cur.execute(
                """
                SELECT usage_count, success_count, run_count
                FROM skills
                WHERE skill_id = ?
                """,
                (skill_id.strip(),),
            )
            row = cur.fetchone()
            if row is not None:
                usage_count = int(row["usage_count"]) + 1
                run_count = int(row["run_count"]) + 1
                success_count = int(row["success_count"]) + (1 if is_success else 0)
                success_rate = float(success_count) / float(run_count) if run_count else 0.0
                cur.execute(
                    """
                    UPDATE skills
                    SET usage_count = ?, success_count = ?, run_count = ?, success_rate = ?, last_used_at = ?, updated_at = ?
                    WHERE skill_id = ?
                    """,
                    (
                        usage_count,
                        success_count,
                        run_count,
                        success_rate,
                        created_at,
                        created_at,
                        skill_id.strip(),
                    ),
                )

            self._conn.commit()
        return run_id

    def recent_skill_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 2000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT run_id, skill_id, session_id, user_id, intent, route, status, confidence, latency_ms, created_at
                FROM skill_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (bounded,),
            )
            rows = cur.fetchall()
        return [
            {
                "run_id": row["run_id"],
                "skill_id": row["skill_id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "intent": row["intent"],
                "route": row["route"],
                "status": row["status"],
                "confidence": row["confidence"],
                "latency_ms": row["latency_ms"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def upsert_conversation_topic(
        self,
        *,
        user_id: str,
        topic_key: str,
        topic_label: str,
        session_id: str | None,
        timestamp: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        normalized_user = user_id.strip() or "local_user"
        normalized_key = topic_key.strip().lower() or "general_conversation"
        normalized_label = topic_label.strip() or "General Conversation"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=True)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT topic_id, mention_count, metadata_json
                FROM conversation_topics
                WHERE user_id = ? AND topic_key = ?
                """,
                (normalized_user, normalized_key),
            )
            row = cur.fetchone()
            if row is None:
                topic_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO conversation_topics (
                        topic_id, user_id, topic_key, topic_label,
                        first_seen_at, last_seen_at, mention_count, last_session_id, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        topic_id,
                        normalized_user,
                        normalized_key,
                        normalized_label,
                        timestamp,
                        timestamp,
                        1,
                        session_id.strip() if isinstance(session_id, str) and session_id.strip() else None,
                        metadata_json,
                    ),
                )
            else:
                topic_id = str(row["topic_id"])
                mention_count = int(row["mention_count"]) + 1
                existing_metadata = self._json_object(row["metadata_json"])
                merged_metadata = dict(existing_metadata)
                if isinstance(metadata, dict):
                    merged_metadata.update(metadata)
                cur.execute(
                    """
                    UPDATE conversation_topics
                    SET topic_label = ?, last_seen_at = ?, mention_count = ?, last_session_id = ?, metadata_json = ?
                    WHERE topic_id = ?
                    """,
                    (
                        normalized_label,
                        timestamp,
                        mention_count,
                        session_id.strip() if isinstance(session_id, str) and session_id.strip() else None,
                        json.dumps(merged_metadata, ensure_ascii=True),
                        topic_id,
                    ),
                )
            self._conn.commit()
        return topic_id

    def insert_conversation_topic_history(
        self,
        *,
        timestamp: str,
        topic_id: str | None,
        session_id: str | None,
        user_id: str,
        agent_id: str,
        route: str,
        intent: str,
        status: str | None,
        topic_key: str,
        topic_label: str,
        user_text: str,
        assistant_text: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        history_id = str(uuid4())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO conversation_topic_history (
                    history_id, timestamp, topic_id, session_id, user_id, agent_id,
                    route, intent, status, topic_key, topic_label, user_text, assistant_text, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    timestamp,
                    topic_id.strip() if isinstance(topic_id, str) and topic_id.strip() else None,
                    session_id.strip() if isinstance(session_id, str) and session_id.strip() else None,
                    user_id.strip() or "local_user",
                    agent_id.strip().lower() if isinstance(agent_id, str) and agent_id.strip() else "jarvis",
                    route.strip() if isinstance(route, str) and route.strip() else "main_jarvis",
                    intent.strip().lower() if isinstance(intent, str) and intent.strip() else "conversation.general",
                    status.strip().lower() if isinstance(status, str) and status.strip() else None,
                    topic_key.strip().lower() or "general_conversation",
                    topic_label.strip() or "General Conversation",
                    user_text.strip() if isinstance(user_text, str) else "",
                    assistant_text.strip() if isinstance(assistant_text, str) and assistant_text.strip() else None,
                    json.dumps(metadata or {}, ensure_ascii=True),
                ),
            )
            self._conn.commit()
        return history_id

    def list_conversation_topics(
        self,
        *,
        user_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 5000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT topic_id, user_id, topic_key, topic_label, first_seen_at, last_seen_at,
                       mention_count, last_session_id, metadata_json
                FROM conversation_topics
                WHERE user_id = ?
                ORDER BY mention_count DESC, last_seen_at DESC
                LIMIT ?
                """,
                (user_id.strip() or "local_user", bounded),
            )
            rows = cur.fetchall()
        return [
            {
                "topic_id": row["topic_id"],
                "user_id": row["user_id"],
                "topic_key": row["topic_key"],
                "topic_label": row["topic_label"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "mention_count": int(row["mention_count"]),
                "last_session_id": row["last_session_id"],
                "metadata": self._json_object(row["metadata_json"]),
            }
            for row in rows
        ]

    def recent_conversation_topic_history(
        self,
        *,
        user_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 5000))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT history_id, timestamp, topic_id, session_id, user_id, agent_id,
                       route, intent, status, topic_key, topic_label, user_text, assistant_text, metadata_json
                FROM conversation_topic_history
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (user_id.strip() or "local_user", bounded),
            )
            rows = cur.fetchall()
        return [
            {
                "history_id": row["history_id"],
                "timestamp": row["timestamp"],
                "topic_id": row["topic_id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "agent_id": row["agent_id"],
                "route": row["route"],
                "intent": row["intent"],
                "status": row["status"],
                "topic_key": row["topic_key"],
                "topic_label": row["topic_label"],
                "user_text": row["user_text"],
                "assistant_text": row["assistant_text"],
                "metadata": self._json_object(row["metadata_json"]),
            }
            for row in rows
        ]

    def upsert_scheduled_job(
        self,
        *,
        job_id: str,
        skill_id: str,
        job_name: str,
        cron_expr: str,
        payload: dict[str, Any] | None,
        enabled: bool,
        created_by: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        payload_json = json.dumps(payload or {}, ensure_ascii=True)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO scheduled_jobs (
                    job_id, skill_id, job_name, cron_expr, payload_json, enabled,
                    last_run_at, next_run_at, last_status, created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    skill_id=excluded.skill_id,
                    job_name=excluded.job_name,
                    cron_expr=excluded.cron_expr,
                    payload_json=excluded.payload_json,
                    enabled=excluded.enabled,
                    created_by=excluded.created_by,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id.strip(),
                    skill_id.strip(),
                    job_name.strip(),
                    cron_expr.strip().lower(),
                    payload_json,
                    1 if enabled else 0,
                    created_by.strip(),
                    created_at,
                    updated_at,
                ),
            )
            self._conn.commit()

    def list_scheduled_jobs(
        self,
        *,
        enabled_only: bool = True,
        cron_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if enabled_only:
            clauses.append("enabled = 1")
        if isinstance(cron_expr, str) and cron_expr.strip():
            clauses.append("cron_expr = ?")
            params.append(cron_expr.strip().lower())

        query = (
            """
            SELECT job_id, skill_id, job_name, cron_expr, payload_json, enabled,
                   last_run_at, next_run_at, last_status, created_by, created_at, updated_at
            FROM scheduled_jobs
            """
        )
        if clauses:
            query += f" WHERE {' AND '.join(clauses)}"
        query += " ORDER BY job_name ASC, job_id ASC"

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        return [
            {
                "job_id": row["job_id"],
                "skill_id": row["skill_id"],
                "job_name": row["job_name"],
                "cron_expr": row["cron_expr"],
                "payload": self._json_object(row["payload_json"]),
                "enabled": bool(int(row["enabled"])),
                "last_run_at": row["last_run_at"],
                "next_run_at": row["next_run_at"],
                "last_status": row["last_status"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def mark_scheduled_job_run(
        self,
        *,
        job_id: str,
        last_status: str,
        last_run_at: str,
        next_run_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE scheduled_jobs
                SET last_run_at = ?, next_run_at = ?, last_status = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    last_run_at,
                    next_run_at,
                    last_status.strip().lower(),
                    updated_at or last_run_at,
                    job_id.strip(),
                ),
            )
            self._conn.commit()

    def upsert_agent_profile(
        self,
        *,
        agent_id: str,
        display_name: str,
        wake_aliases: list[str],
        personality_doc_path: str | None,
        default_user_id: str | None,
        active: bool,
        updated_at: str,
    ) -> None:
        aliases = [item.strip().lower() for item in wake_aliases if str(item).strip()]
        if not aliases:
            aliases = [agent_id.strip().lower()]
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO agent_profiles (
                    agent_id, display_name, wake_aliases_json, personality_doc_path, default_user_id, active, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    wake_aliases_json=excluded.wake_aliases_json,
                    personality_doc_path=excluded.personality_doc_path,
                    default_user_id=excluded.default_user_id,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (
                    agent_id.strip().lower(),
                    display_name.strip(),
                    json.dumps(aliases, ensure_ascii=True),
                    personality_doc_path.strip() if isinstance(personality_doc_path, str) and personality_doc_path.strip() else None,
                    default_user_id.strip() if isinstance(default_user_id, str) and default_user_id.strip() else None,
                    1 if active else 0,
                    updated_at,
                ),
            )
            self._conn.commit()

    def list_agent_profiles(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = (
            "SELECT agent_id, display_name, wake_aliases_json, personality_doc_path, default_user_id, active, updated_at "
            "FROM agent_profiles"
        )
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY agent_id ASC"

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()
        profiles: list[dict[str, Any]] = []
        for row in rows:
            profiles.append(
                {
                    "agent_id": row["agent_id"],
                    "display_name": row["display_name"],
                    "wake_aliases": self._json_array(row["wake_aliases_json"]),
                    "personality_doc_path": row["personality_doc_path"],
                    "default_user_id": row["default_user_id"],
                    "active": bool(int(row["active"])),
                    "updated_at": row["updated_at"],
                }
            )
        return profiles

    def get_agent_profile(self, agent_id: str) -> dict[str, Any] | None:
        normalized = agent_id.strip().lower()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT agent_id, display_name, wake_aliases_json, personality_doc_path, default_user_id, active, updated_at
                FROM agent_profiles
                WHERE agent_id = ?
                """,
                (normalized,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "agent_id": row["agent_id"],
            "display_name": row["display_name"],
            "wake_aliases": self._json_array(row["wake_aliases_json"]),
            "personality_doc_path": row["personality_doc_path"],
            "default_user_id": row["default_user_id"],
            "active": bool(int(row["active"])),
            "updated_at": row["updated_at"],
        }

    def find_agent_by_wake_alias(self, alias: str) -> dict[str, Any] | None:
        target = alias.strip().lower()
        if not target:
            return None
        for profile in self.list_agent_profiles(active_only=True):
            aliases = {item.strip().lower() for item in profile.get("wake_aliases") or [] if item}
            if target in aliases:
                return profile
        return None

    def upsert_list(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        list_name_normalized: str,
        created_by: str,
        created_at: str,
        updated_at: str,
    ) -> dict[str, Any]:
        provisional_id = str(uuid4())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO lists (
                    list_id, owner_user_id, list_name, list_name_normalized, created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, list_name_normalized) DO UPDATE SET
                    list_name=excluded.list_name,
                    updated_at=excluded.updated_at
                """,
                (
                    provisional_id,
                    owner_user_id.strip().lower(),
                    list_name.strip(),
                    list_name_normalized.strip().lower(),
                    created_by.strip(),
                    created_at,
                    updated_at,
                ),
            )
            cur.execute(
                """
                SELECT list_id, owner_user_id, list_name, list_name_normalized, created_by, created_at, updated_at
                FROM lists
                WHERE owner_user_id = ? AND list_name_normalized = ?
                """,
                (
                    owner_user_id.strip().lower(),
                    list_name_normalized.strip().lower(),
                ),
            )
            row = cur.fetchone()
            self._conn.commit()
        if row is None:
            return {
                "list_id": provisional_id,
                "owner_user_id": owner_user_id.strip().lower(),
                "list_name": list_name.strip(),
                "list_name_normalized": list_name_normalized.strip().lower(),
                "created_by": created_by.strip(),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        return {
            "list_id": row["list_id"],
            "owner_user_id": row["owner_user_id"],
            "list_name": row["list_name"],
            "list_name_normalized": row["list_name_normalized"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_lists(self, owner_user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT list_id, owner_user_id, list_name, list_name_normalized, created_by, created_at, updated_at
                FROM lists
                WHERE owner_user_id = ?
                ORDER BY list_name ASC
                """,
                (owner_user_id.strip().lower(),),
            )
            rows = cur.fetchall()
        return [
            {
                "list_id": row["list_id"],
                "owner_user_id": row["owner_user_id"],
                "list_name": row["list_name"],
                "list_name_normalized": row["list_name_normalized"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_list_by_normalized_name(self, owner_user_id: str, list_name_normalized: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT list_id, owner_user_id, list_name, list_name_normalized, created_by, created_at, updated_at
                FROM lists
                WHERE owner_user_id = ? AND list_name_normalized = ?
                """,
                (
                    owner_user_id.strip().lower(),
                    list_name_normalized.strip().lower(),
                ),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "list_id": row["list_id"],
            "owner_user_id": row["owner_user_id"],
            "list_name": row["list_name"],
            "list_name_normalized": row["list_name_normalized"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_list_by_id(self, owner_user_id: str, list_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT list_id, owner_user_id, list_name, list_name_normalized,
                       created_by, created_at, updated_at
                FROM lists
                WHERE owner_user_id = ? AND list_id = ?
                """,
                (owner_user_id.strip().lower(), list_id.strip()),
            ).fetchone()
        if row is None:
            return None
        return {
            "list_id": row["list_id"],
            "owner_user_id": row["owner_user_id"],
            "list_name": row["list_name"],
            "list_name_normalized": row["list_name_normalized"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_list_with_operation(
        self,
        *,
        owner_user_id: str,
        list_name: str,
        list_name_normalized: str,
        created_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        owner = owner_user_id.strip().lower()
        display_name = list_name.strip()
        normalized_name = list_name_normalized.strip().lower()
        action = "lists.create_collection"
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                existing_operation = cur.execute(
                    """
                    SELECT owner_user_id, action, target_ref, arguments_hash, status, result_json
                    FROM list_operations WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if existing_operation is not None:
                    if (
                        str(existing_operation["owner_user_id"]) != owner
                        or str(existing_operation["action"]) != action
                        or str(existing_operation["arguments_hash"]) != arguments_hash
                        or str(existing_operation["status"]) != "completed"
                    ):
                        raise ValueError("list_operation_id_conflict")
                    list_row = cur.execute(
                        """
                        SELECT list_id, owner_user_id, list_name, list_name_normalized,
                               created_by, created_at, updated_at
                        FROM lists WHERE owner_user_id = ? AND list_id = ?
                        """,
                        (owner, str(existing_operation["target_ref"])),
                    ).fetchone()
                    if list_row is None:
                        raise ValueError("list_operation_replay_target_missing")
                    replay_result = json.loads(str(existing_operation["result_json"] or "{}"))
                    self._conn.commit()
                    return {
                        **dict(list_row),
                        "created": bool(replay_result.get("created")),
                        "idempotent_replay": True,
                    }

                list_row = cur.execute(
                    """
                    SELECT list_id, owner_user_id, list_name, list_name_normalized,
                           created_by, created_at, updated_at
                    FROM lists
                    WHERE owner_user_id = ? AND list_name_normalized = ?
                    """,
                    (owner, normalized_name),
                ).fetchone()
                created = list_row is None
                if list_row is None:
                    list_id = str(uuid4())
                    cur.execute(
                        """
                        INSERT INTO lists (
                            list_id, owner_user_id, list_name, list_name_normalized,
                            created_by, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            list_id,
                            owner,
                            display_name,
                            normalized_name,
                            created_by.strip(),
                            timestamp,
                            timestamp,
                        ),
                    )
                    list_row = cur.execute(
                        """
                        SELECT list_id, owner_user_id, list_name, list_name_normalized,
                               created_by, created_at, updated_at
                        FROM lists WHERE list_id = ?
                        """,
                        (list_id,),
                    ).fetchone()
                if list_row is None:
                    raise ValueError("list_create_failed")
                result_json = json.dumps(
                    {"created": created, "list_id": str(list_row["list_id"])},
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                cur.execute(
                    """
                    INSERT INTO list_operations (
                        operation_id, owner_user_id, action, target_ref, arguments_hash,
                        status, result_json, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)
                    """,
                    (
                        operation_id,
                        owner,
                        action,
                        str(list_row["list_id"]),
                        arguments_hash,
                        result_json,
                        timestamp,
                        timestamp,
                    ),
                )
                self._conn.commit()
                return {**dict(list_row), "created": created, "idempotent_replay": False}
            except Exception:
                self._conn.rollback()
                raise

    def add_list_items_with_operation(
        self,
        *,
        owner_user_id: str,
        list_id: str,
        item_names: list[str],
        added_by: str,
        timestamp: str,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        owner = owner_user_id.strip().lower()
        target_list_id = list_id.strip()
        action = "lists.add_items"
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                existing_operation = cur.execute(
                    """
                    SELECT owner_user_id, action, target_ref, arguments_hash, status, result_json
                    FROM list_operations WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if existing_operation is not None:
                    if (
                        str(existing_operation["owner_user_id"]) != owner
                        or str(existing_operation["action"]) != action
                        or str(existing_operation["target_ref"]) != target_list_id
                        or str(existing_operation["arguments_hash"]) != arguments_hash
                        or str(existing_operation["status"]) != "completed"
                    ):
                        raise ValueError("list_operation_id_conflict")
                    replay_result = json.loads(str(existing_operation["result_json"] or "{}"))
                    item_ids = replay_result.get("item_ids")
                    if not isinstance(item_ids, list) or any(not isinstance(item, str) for item in item_ids):
                        raise ValueError("list_operation_result_invalid")
                    rows: list[dict[str, Any]] = []
                    for item_id in item_ids:
                        row = cur.execute(
                            """
                            SELECT item_id, list_id, item_name, checked, position, added_at, updated_at
                            FROM list_items WHERE item_id = ? AND list_id = ?
                            """,
                            (item_id, target_list_id),
                        ).fetchone()
                        if row is None:
                            raise ValueError("list_operation_replay_target_missing")
                        rows.append(dict(row))
                    self._conn.commit()
                    return {
                        "list_id": target_list_id,
                        "items": rows,
                        "existing_item_count": int(replay_result.get("existing_item_count") or 0),
                        "idempotent_replay": True,
                    }

                list_row = cur.execute(
                    "SELECT list_id FROM lists WHERE owner_user_id = ? AND list_id = ?",
                    (owner, target_list_id),
                ).fetchone()
                if list_row is None:
                    raise ValueError("list_collection_not_authorized")
                aggregate = cur.execute(
                    """
                    SELECT COUNT(*) AS item_count, COALESCE(MAX(position), 0) AS max_position
                    FROM list_items WHERE list_id = ?
                    """,
                    (target_list_id,),
                ).fetchone()
                existing_count = int(aggregate["item_count"] if aggregate is not None else 0)
                next_position = int(aggregate["max_position"] if aggregate is not None else 0) + 1
                inserted: list[dict[str, Any]] = []
                for index, item_name in enumerate(item_names):
                    item_id = str(uuid4())
                    position = next_position + index
                    cur.execute(
                        """
                        INSERT INTO list_items (
                            item_id, list_id, item_name, long_desc, qty, checked, position,
                            added_by, added_at, updated_at, operation_id
                        ) VALUES (?, ?, ?, NULL, NULL, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            target_list_id,
                            item_name,
                            position,
                            added_by.strip(),
                            timestamp,
                            timestamp,
                            f"{operation_id}:{index + 1}",
                        ),
                    )
                    inserted.append(
                        {
                            "item_id": item_id,
                            "list_id": target_list_id,
                            "item_name": item_name,
                            "checked": False,
                            "position": position,
                            "added_at": timestamp,
                            "updated_at": timestamp,
                        }
                    )
                cur.execute(
                    "UPDATE lists SET updated_at = ? WHERE list_id = ?",
                    (timestamp, target_list_id),
                )
                result_json = json.dumps(
                    {
                        "existing_item_count": existing_count,
                        "item_ids": [str(item["item_id"]) for item in inserted],
                        "list_id": target_list_id,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                cur.execute(
                    """
                    INSERT INTO list_operations (
                        operation_id, owner_user_id, action, target_ref, arguments_hash,
                        status, result_json, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)
                    """,
                    (
                        operation_id,
                        owner,
                        action,
                        target_list_id,
                        arguments_hash,
                        result_json,
                        timestamp,
                        timestamp,
                    ),
                )
                self._conn.commit()
                return {
                    "list_id": target_list_id,
                    "items": inserted,
                    "existing_item_count": existing_count,
                    "idempotent_replay": False,
                }
            except Exception:
                self._conn.rollback()
                raise

    def add_list_item(
        self,
        *,
        list_id: str,
        item_name: str,
        added_by: str,
        long_desc: str | None,
        qty: float | None,
        checked: bool,
        added_at: str,
        updated_at: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        item_id = str(uuid4())
        with self._lock:
            cur = self._conn.cursor()
            if operation_id:
                cur.execute(
                    """
                    SELECT item_id, list_id, item_name, long_desc, qty, checked, position,
                           added_by, added_at, updated_at, operation_id
                    FROM list_items WHERE operation_id = ?
                    """,
                    (operation_id,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return {
                        "item_id": existing["item_id"],
                        "list_id": existing["list_id"],
                        "item_name": existing["item_name"],
                        "long_desc": existing["long_desc"],
                        "qty": existing["qty"],
                        "checked": bool(int(existing["checked"])),
                        "position": int(existing["position"]),
                        "added_by": existing["added_by"],
                        "added_at": existing["added_at"],
                        "updated_at": existing["updated_at"],
                        "operation_id": existing["operation_id"],
                        "idempotent_replay": True,
                    }
            cur.execute(
                "SELECT COALESCE(MAX(position), 0) AS max_pos FROM list_items WHERE list_id = ?",
                (list_id.strip(),),
            )
            row = cur.fetchone()
            max_pos = int(row["max_pos"]) if row is not None else 0
            position = max_pos + 1
            cur.execute(
                """
                INSERT INTO list_items (
                    item_id, list_id, item_name, long_desc, qty, checked, position, added_by, added_at, updated_at,
                    operation_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    list_id.strip(),
                    item_name.strip(),
                    long_desc.strip() if isinstance(long_desc, str) and long_desc.strip() else None,
                    float(qty) if isinstance(qty, (float, int)) else None,
                    1 if checked else 0,
                    position,
                    added_by.strip(),
                    added_at,
                    updated_at,
                    operation_id,
                ),
            )
            self._conn.commit()
        return {
            "item_id": item_id,
            "list_id": list_id.strip(),
            "item_name": item_name.strip(),
            "long_desc": long_desc.strip() if isinstance(long_desc, str) and long_desc.strip() else None,
            "qty": float(qty) if isinstance(qty, (float, int)) else None,
            "checked": checked,
            "position": position,
            "added_by": added_by.strip(),
            "added_at": added_at,
            "updated_at": updated_at,
            "operation_id": operation_id,
            "idempotent_replay": False,
        }

    def list_list_items(self, list_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT item_id, list_id, item_name, long_desc, qty, checked, position, added_by, added_at, updated_at,
                       operation_id
                FROM list_items
                WHERE list_id = ?
                ORDER BY position ASC, added_at ASC
                """,
                (list_id.strip(),),
            )
            rows = cur.fetchall()
        return [
            {
                "item_id": row["item_id"],
                "list_id": row["list_id"],
                "item_name": row["item_name"],
                "long_desc": row["long_desc"],
                "qty": row["qty"],
                "checked": bool(int(row["checked"])),
                "position": int(row["position"]),
                "added_by": row["added_by"],
                "added_at": row["added_at"],
                "updated_at": row["updated_at"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]

    def delete_list(self, list_id: str) -> bool:
        target_list_id = list_id.strip()
        if not target_list_id:
            return False
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM list_items WHERE list_id = ?", (target_list_id,))
            cur.execute("DELETE FROM lists WHERE list_id = ?", (target_list_id,))
            deleted = int(cur.rowcount or 0)
            self._conn.commit()
        return deleted > 0

    def delete_all_list_items(self, list_id: str, *, updated_at: str | None = None) -> int:
        target_list_id = list_id.strip()
        if not target_list_id:
            return 0
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM list_items WHERE list_id = ?", (target_list_id,))
            deleted_count = int(cur.rowcount or 0)
            if updated_at:
                cur.execute(
                    "UPDATE lists SET updated_at = ? WHERE list_id = ?",
                    (updated_at, target_list_id),
                )
            self._conn.commit()
        return deleted_count

    def delete_list_item(self, item_id: str, *, updated_at: str | None = None) -> bool:
        target_item_id = item_id.strip()
        if not target_item_id:
            return False
        with self._lock:
            cur = self._conn.cursor()
            list_id: str | None = None
            cur.execute("SELECT list_id FROM list_items WHERE item_id = ?", (target_item_id,))
            existing = cur.fetchone()
            if existing is not None:
                list_id = str(existing["list_id"])
            cur.execute("DELETE FROM list_items WHERE item_id = ?", (target_item_id,))
            deleted = int(cur.rowcount or 0) > 0
            if deleted and list_id and updated_at:
                cur.execute(
                    "UPDATE lists SET updated_at = ? WHERE list_id = ?",
                    (updated_at, list_id),
                )
            self._conn.commit()
        return deleted

    def set_list_item_checked(
        self,
        *,
        item_id: str,
        checked: bool,
        updated_at: str,
    ) -> bool:
        target_item_id = item_id.strip()
        if not target_item_id:
            return False
        with self._lock:
            cur = self._conn.cursor()
            list_id: str | None = None
            cur.execute("SELECT list_id FROM list_items WHERE item_id = ?", (target_item_id,))
            row = cur.fetchone()
            if row is not None:
                list_id = str(row["list_id"])
            cur.execute(
                """
                UPDATE list_items
                SET checked = ?, updated_at = ?
                WHERE item_id = ?
                """,
                (1 if checked else 0, updated_at, target_item_id),
            )
            updated = int(cur.rowcount or 0) > 0
            if updated and list_id:
                cur.execute(
                    "UPDATE lists SET updated_at = ? WHERE list_id = ?",
                    (updated_at, list_id),
                )
            self._conn.commit()
        return updated
