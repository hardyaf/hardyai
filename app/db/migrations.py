from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from app.db.core_schema import ensure_core_schema
from app.db.review_schema import ensure_review_schema


LATEST_SCHEMA_VERSION = 9
CORE_SCHEMA_READER_VERSION = 9


# Tests may replace this content-free hook to prove that every version-8 step
# rolls back atomically. Production leaves it unset.
_MIGRATION_STEP_HOOK: Callable[[int, str], None] | None = None


@dataclass(frozen=True)
class SchemaReaderDecision:
    version: int
    result: str
    reason: str

    @property
    def compatible(self) -> bool:
        return self.result == "compatible"


def evaluate_schema_reader_compatibility(
    conn: sqlite3.Connection,
    *,
    reader_version: int = CORE_SCHEMA_READER_VERSION,
) -> SchemaReaderDecision:
    """Evaluate whether this binary may read the database without mutating it."""

    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current <= reader_version:
        return SchemaReaderDecision(current, "compatible", "schema_not_newer")

    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_reader_compatibility'"
    ).fetchone()
    if table_exists is None:
        return SchemaReaderDecision(current, "incompatible", "compatibility_table_missing")

    try:
        rows = conn.execute(
            """
            SELECT schema_version, minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version > ? AND schema_version <= ?
            ORDER BY schema_version
            """,
            (reader_version, current),
        ).fetchall()
    except sqlite3.Error:
        return SchemaReaderDecision(current, "incompatible", "compatibility_table_invalid")

    by_version: dict[int, list[sqlite3.Row | tuple[object, ...]]] = {}
    try:
        for row in rows:
            by_version.setdefault(int(row[0]), []).append(row)
    except (TypeError, ValueError):
        return SchemaReaderDecision(current, "incompatible", "compatibility_row_invalid")

    for version in range(reader_version + 1, current + 1):
        version_rows = by_version.get(version, [])
        if not version_rows:
            return SchemaReaderDecision(current, "incompatible", "compatibility_row_missing")
        if len(version_rows) != 1:
            return SchemaReaderDecision(current, "incompatible", "compatibility_row_invalid")
        row = version_rows[0]
        try:
            minimum_reader_version = int(row[1])
            change_class = str(row[2] or "").strip().casefold()
        except (TypeError, ValueError):
            return SchemaReaderDecision(current, "incompatible", "compatibility_row_invalid")
        if minimum_reader_version < 0:
            return SchemaReaderDecision(current, "incompatible", "compatibility_row_invalid")
        if change_class != "additive":
            return SchemaReaderDecision(current, "incompatible", "change_not_additive")
        if minimum_reader_version > reader_version:
            return SchemaReaderDecision(current, "incompatible", "minimum_reader_too_new")

    return SchemaReaderDecision(current, "compatible", "additive_reader_bridge")


def _raise_newer_schema(current: int) -> None:
    raise RuntimeError(
        f"Database schema version {current} is newer than supported version {LATEST_SCHEMA_VERSION}."
    )


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")


def _migration_001_action_ticket_ledger(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS work_tickets (
            ticket_id TEXT PRIMARY KEY,
            root_ticket_id TEXT NOT NULL,
            parent_ticket_id TEXT,
            ticket_kind TEXT NOT NULL,
            remediation_generation INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            origin_request_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            source TEXT NOT NULL,
            intent TEXT NOT NULL,
            skill_id TEXT,
            route TEXT NOT NULL,
            resource_key TEXT,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_material_activity_at TEXT NOT NULL,
            completed_at TEXT,
            review_due_at TEXT,
            source_action_revision TEXT,
            expected_effect_hash TEXT,
            plane_work_item_id TEXT,
            plane_sync_status TEXT NOT NULL DEFAULT 'not_configured',
            terminal_reason TEXT,
            FOREIGN KEY (root_ticket_id) REFERENCES work_tickets(ticket_id),
            FOREIGN KEY (parent_ticket_id) REFERENCES work_tickets(ticket_id)
        );

        CREATE TABLE IF NOT EXISTS ticket_entries (
            entry_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id TEXT,
            created_at TEXT NOT NULL,
            verbatim_text TEXT,
            structured_payload_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            FOREIGN KEY (ticket_id) REFERENCES work_tickets(ticket_id),
            UNIQUE(ticket_id, sequence_number)
        );

        CREATE TABLE IF NOT EXISTS operation_receipts (
            operation_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            action TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            provider_resource_id TEXT,
            provider_revision TEXT,
            resource_key TEXT NOT NULL,
            outcome TEXT NOT NULL,
            committed_at TEXT,
            expected_effect_json TEXT NOT NULL,
            validator_name TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            resource_locator_json TEXT NOT NULL,
            execution_observation_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (ticket_id) REFERENCES work_tickets(ticket_id)
        );

        CREATE TABLE IF NOT EXISTS ticket_expectations (
            expectation_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE,
            capability TEXT NOT NULL,
            validator_name TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            resource_locator_json TEXT NOT NULL,
            expected_state_json TEXT NOT NULL,
            expected_state_hash TEXT NOT NULL,
            source_revision_at_execution TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES work_tickets(ticket_id),
            FOREIGN KEY (operation_id) REFERENCES operation_receipts(operation_id)
        );

        CREATE TABLE IF NOT EXISTS ticket_review_runs (
            review_run_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            source_action_revision TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            deterministic_verdict TEXT,
            model_verdict TEXT,
            model_name TEXT,
            prompt_version TEXT NOT NULL,
            context_pack_hash TEXT,
            source_evidence_json TEXT NOT NULL DEFAULT '{}',
            source_evidence_hash TEXT,
            discrepancy_json TEXT NOT NULL DEFAULT '[]',
            proposed_repair_json TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            error_code TEXT,
            FOREIGN KEY (ticket_id) REFERENCES work_tickets(ticket_id),
            UNIQUE(ticket_id, source_action_revision, attempt_number)
        );

        CREATE TABLE IF NOT EXISTS durable_jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            available_at TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            lease_owner TEXT,
            lease_expires_at TEXT,
            last_error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS external_identity_bindings (
            source TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            external_display_name TEXT,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            age_band TEXT,
            presentation_profile TEXT NOT NULL DEFAULT 'default',
            policy_profile TEXT NOT NULL DEFAULT 'adult',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (source, external_user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_work_tickets_status_due
            ON work_tickets(status, review_due_at);
        CREATE INDEX IF NOT EXISTS idx_work_tickets_root_generation
            ON work_tickets(root_ticket_id, remediation_generation);
        CREATE INDEX IF NOT EXISTS idx_work_tickets_session_created
            ON work_tickets(session_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_work_tickets_resource_completed
            ON work_tickets(resource_key, completed_at);
        CREATE INDEX IF NOT EXISTS idx_ticket_entries_ticket_sequence
            ON ticket_entries(ticket_id, sequence_number);
        CREATE INDEX IF NOT EXISTS idx_ticket_expectations_ticket
            ON ticket_expectations(ticket_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_operation_receipts_ticket
            ON operation_receipts(ticket_id, committed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ticket_review_runs_ticket
            ON ticket_review_runs(ticket_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim
            ON durable_jobs(job_type, status, available_at);
        CREATE INDEX IF NOT EXISTS idx_durable_jobs_lease
            ON durable_jobs(status, lease_expires_at);
        CREATE INDEX IF NOT EXISTS idx_identity_bindings_agent
            ON external_identity_bindings(agent_id, active);
        """
    )


def _migration_002_list_operation_ids(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'list_items'"
    ).fetchone()
    if table_exists is None:
        return
    columns = {
        str(row[1]).strip().lower()
        for row in conn.execute("PRAGMA table_info(list_items)").fetchall()
    }
    if "operation_id" not in columns:
        conn.execute("ALTER TABLE list_items ADD COLUMN operation_id TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_list_items_operation_id
        ON list_items(operation_id)
        WHERE operation_id IS NOT NULL
        """
    )


def _migration_003_worker_heartbeats(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            worker_type TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            status TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_error_code TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (worker_type, worker_id)
        );
        CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_seen
            ON worker_heartbeats(worker_type, last_seen_at DESC);
        """
    )


def _migration_004_memory_operation_ids(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1]).strip().lower()
        for row in conn.execute("PRAGMA table_info(memory_entries)").fetchall()
    }
    if "operation_id" not in columns:
        conn.execute("ALTER TABLE memory_entries ADD COLUMN operation_id TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_entries_operation_id
        ON memory_entries(operation_id)
        WHERE operation_id IS NOT NULL
        """
    )


def _migration_005_durable_job_control_plane(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1]).strip().lower()
        for row in conn.execute("PRAGMA table_info(durable_jobs)").fetchall()
    }
    additions = {
        "lease_fencing_token": "INTEGER NOT NULL DEFAULT 0",
        "progress_current": "INTEGER NOT NULL DEFAULT 0",
        "progress_total": "INTEGER",
        "current_stage": "TEXT",
        "stage_started_at": "TEXT",
        "cancel_requested_at": "TEXT",
        "cancelled_at": "TEXT",
        "priority": "INTEGER NOT NULL DEFAULT 100",
        "resource_class": "TEXT NOT NULL DEFAULT 'cpu_small'",
        "provider_operation_ref": "TEXT",
        "provider_reconcile_state": "TEXT",
        "total_deadline_at": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE durable_jobs ADD COLUMN {name} {declaration}")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_durable_jobs_priority_claim
            ON durable_jobs(job_type, status, priority, available_at);
        CREATE INDEX IF NOT EXISTS idx_durable_jobs_cancel
            ON durable_jobs(status, cancel_requested_at);
        """
    )


def _migration_006_shared_human_reviews(conn: sqlite3.Connection) -> None:
    ensure_review_schema(conn)


def _migration_007_shared_provenance_links(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS provenance_links (
            provenance_id TEXT PRIMARY KEY,
            source_domain TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_version TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            target_domain TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            link_kind TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            UNIQUE(source_domain, source_type, source_ref, source_version,
                   target_domain, target_type, target_ref, link_kind)
        );
        CREATE INDEX IF NOT EXISTS idx_provenance_source
            ON provenance_links(source_domain, source_type, source_ref, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_provenance_target
            ON provenance_links(target_domain, target_type, target_ref, created_at DESC);
        """
    )


def _migration_008_typed_main_tools(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1]).strip().casefold()
        for row in conn.execute("PRAGMA table_info(skills)").fetchall()
    }
    if "main_tools_json" not in columns:
        conn.execute("ALTER TABLE skills ADD COLUMN main_tools_json TEXT")
        _notify_migration_step(8, "add_main_tools_json")
    if "main_tools_contract_version" not in columns:
        conn.execute("ALTER TABLE skills ADD COLUMN main_tools_contract_version INTEGER")
        _notify_migration_step(8, "add_main_tools_contract_version")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_reader_compatibility (
            schema_version INTEGER PRIMARY KEY,
            minimum_reader_version INTEGER NOT NULL,
            change_class TEXT NOT NULL,
            description TEXT NOT NULL
        )
        """
    )
    _notify_migration_step(8, "create_reader_compatibility")
    conn.execute(
        """
        INSERT INTO schema_reader_compatibility (
            schema_version, minimum_reader_version, change_class, description
        )
        VALUES (8, 7, 'additive', 'Adds compiled Main tool metadata to the skill catalog.')
        ON CONFLICT(schema_version) DO UPDATE SET
            minimum_reader_version=excluded.minimum_reader_version,
            change_class=excluded.change_class,
            description=excluded.description
        """
    )
    _notify_migration_step(8, "record_reader_compatibility")


def _migration_009_lists_operation_idempotency(conn: sqlite3.Connection) -> None:
    conn.execute(
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
    _notify_migration_step(9, "create_list_operations")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_list_operations_owner_action_created
        ON list_operations(owner_user_id, action, created_at DESC)
        """
    )
    _notify_migration_step(9, "create_list_operations_index")
    conn.execute(
        """
        INSERT INTO schema_reader_compatibility (
            schema_version, minimum_reader_version, change_class, description
        )
        VALUES (9, 7, 'additive', 'Adds atomic Lists mutation operation identities and bounded results.')
        ON CONFLICT(schema_version) DO UPDATE SET
            minimum_reader_version=excluded.minimum_reader_version,
            change_class=excluded.change_class,
            description=excluded.description
        """
    )
    _notify_migration_step(9, "record_reader_compatibility")


def _notify_migration_step(version: int, step: str) -> None:
    if _MIGRATION_STEP_HOOK is not None:
        _MIGRATION_STEP_HOOK(version, step)


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migration_001_action_ticket_ledger,
    2: _migration_002_list_operation_ids,
    3: _migration_003_worker_heartbeats,
    4: _migration_004_memory_operation_ids,
    5: _migration_005_durable_job_control_plane,
    6: _migration_006_shared_human_reviews,
    7: _migration_007_shared_provenance_links,
    8: _migration_008_typed_main_tools,
    9: _migration_009_lists_operation_idempotency,
}


def apply_migrations(conn: sqlite3.Connection) -> int:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > LATEST_SCHEMA_VERSION:
        decision = evaluate_schema_reader_compatibility(conn)
        if not decision.compatible:
            _raise_newer_schema(current)
        return current
    for version in range(current + 1, LATEST_SCHEMA_VERSION + 1):
        migration = _MIGRATIONS[version]
        if version < 8:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
            continue
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {version}")
            _notify_migration_step(version, "set_user_version")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return LATEST_SCHEMA_VERSION


def initialize_schema(conn: sqlite3.Connection) -> int:
    """Run the baseline and ordered migrations through one schema authority."""

    decision = evaluate_schema_reader_compatibility(conn)
    if decision.version > LATEST_SCHEMA_VERSION:
        if not decision.compatible:
            _raise_newer_schema(decision.version)
        return decision.version
    ensure_core_schema(conn)
    return apply_migrations(conn)
