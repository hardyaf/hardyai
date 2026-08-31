from __future__ import annotations

import sqlite3


class CoreSchemaMigration:
    """Creates the baseline schema before ordered version migrations run."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = _NoopLock()

    def apply(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA user_version")
            fresh_database = int(cur.fetchone()[0]) == 0
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    last_activity_timestamp TEXT NOT NULL,
                    context_reference_json TEXT NOT NULL DEFAULT '{}',
                    context_version INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._ensure_column(
                cur=cur,
                table_name="sessions",
                column_name="context_reference_json",
                column_sql="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                cur=cur,
                table_name="sessions",
                column_name="context_version",
                column_sql="INTEGER NOT NULL DEFAULT 0",
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    route TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    response_summary TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS switches (
                    name TEXT PRIMARY KEY,
                    room_name TEXT,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS switch_actions_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    switch_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    state_after TEXT NOT NULL,
                    source_interface TEXT,
                    requested_by_user_id TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS model_boot_memory (
                    model_name TEXT NOT NULL,
                    doc_path TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    required INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (model_name, doc_path)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    skill_user TEXT NOT NULL,
                    skill_agents_json TEXT NOT NULL DEFAULT '["all"]',
                    intents_json TEXT NOT NULL DEFAULT '[]',
                    markdown_path TEXT NOT NULL,
                    execution_ref TEXT,
                    created_by TEXT NOT NULL,
                    storage_type TEXT NOT NULL,
                    storage_ref TEXT,
                    micro_enabled INTEGER NOT NULL DEFAULT 0,
                    micro_functions_json TEXT NOT NULL DEFAULT '[]',
                    micro_failure_handoff_json TEXT NOT NULL DEFAULT '{}',
                    main_handoff_context_json TEXT NOT NULL DEFAULT '{}',
                    learnable_ready INTEGER NOT NULL DEFAULT 0,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    success_rate REAL NOT NULL DEFAULT 1.0,
                    critical_level INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    cron_enabled INTEGER NOT NULL DEFAULT 0,
                    cron_expr TEXT,
                    last_used_at TEXT,
                    main_tools_json TEXT,
                    main_tools_contract_version INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="skill_agents_json",
                column_sql="TEXT NOT NULL DEFAULT '[\"all\"]'",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="intents_json",
                column_sql="TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="execution_ref",
                column_sql="TEXT",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="success_count",
                column_sql="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="run_count",
                column_sql="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="success_rate",
                column_sql="REAL NOT NULL DEFAULT 1.0",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="micro_enabled",
                column_sql="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="micro_functions_json",
                column_sql="TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="micro_failure_handoff_json",
                column_sql="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="main_handoff_context_json",
                column_sql="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                cur=cur,
                table_name="skills",
                column_name="learnable_ready",
                column_sql="INTEGER NOT NULL DEFAULT 0",
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_runs (
                    run_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    session_id TEXT,
                    user_id TEXT NOT NULL,
                    intent TEXT,
                    route TEXT,
                    status TEXT NOT NULL,
                    confidence REAL,
                    latency_ms INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    job_name TEXT NOT NULL,
                    cron_expr TEXT NOT NULL,
                    payload_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    last_status TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_profiles (
                    agent_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    wake_aliases_json TEXT NOT NULL,
                    personality_doc_path TEXT,
                    default_user_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS lists (
                    list_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    list_name TEXT NOT NULL,
                    list_name_normalized TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, list_name_normalized)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS list_items (
                    item_id TEXT PRIMARY KEY,
                    list_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    long_desc TEXT,
                    qty REAL,
                    checked INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0,
                    added_by TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    operation_id TEXT,
                    FOREIGN KEY (list_id) REFERENCES lists(list_id)
                )
                """
            )
            self._ensure_column(
                cur=cur,
                table_name="list_items",
                column_name="operation_id",
                column_sql="TEXT",
            )
            if fresh_database:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS list_operations (
                        operation_id TEXT PRIMARY KEY,
                        owner_user_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        target_ref TEXT NOT NULL,
                        arguments_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        result_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_topics (
                    topic_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    topic_key TEXT NOT NULL,
                    topic_label TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    mention_count INTEGER NOT NULL DEFAULT 1,
                    last_session_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(user_id, topic_key)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_topic_history (
                    history_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    topic_id TEXT,
                    session_id TEXT,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    route TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    status TEXT,
                    topic_key TEXT NOT NULL,
                    topic_label TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (topic_id) REFERENCES conversation_topics(topic_id)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_entries_session_id ON memory_entries(session_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_entries_intent ON memory_entries(intent)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_switch_actions_switch ON switch_actions_log(switch_name)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_boot_memory_model_priority "
                "ON model_boot_memory(model_name, priority)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_skills_user_active ON skills(skill_user, active)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_skills_active_critical_usage "
                "ON skills(active, critical_level DESC, usage_count DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_skill_runs_skill_created "
                "ON skill_runs(skill_id, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_enabled_expr "
                "ON scheduled_jobs(enabled, cron_expr)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_profiles_active ON agent_profiles(active)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_lists_owner_name ON lists(owner_user_id, list_name_normalized)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_list_items_list_pos ON list_items(list_id, position)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_list_items_operation_id "
                "ON list_items(operation_id) WHERE operation_id IS NOT NULL"
            )
            if fresh_database:
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_list_operations_owner_action_created "
                    "ON list_operations(owner_user_id, action, created_at DESC)"
                )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_topics_user_last_seen "
                "ON conversation_topics(user_id, last_seen_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_topic_history_user_time "
                "ON conversation_topic_history(user_id, timestamp DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_topic_history_topic_time "
                "ON conversation_topic_history(topic_key, timestamp DESC)"
            )
            self._conn.commit()

    def _ensure_column(
        self,
        *,
        cur: sqlite3.Cursor,
        table_name: str,
        column_name: str,
        column_sql: str,
    ) -> None:
        cur.execute(f"PRAGMA table_info({table_name})")
        rows = cur.fetchall()
        existing = {str(row["name"]).strip().lower() for row in rows}
        if column_name.strip().lower() in existing:
            return
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


class _NoopLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def ensure_core_schema(conn: sqlite3.Connection) -> None:
    CoreSchemaMigration(conn).apply()
