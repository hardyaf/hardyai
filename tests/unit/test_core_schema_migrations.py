from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import app.db.migrations as migrations_module
from app.db.migrations import (
    LATEST_SCHEMA_VERSION,
    evaluate_schema_reader_compatibility,
    initialize_schema,
)
from scripts.manage_database import reader_check


def _version7_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE skills (
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
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO skills (
                skill_id, skill_name, skill_user, markdown_path, created_by,
                storage_type, updated_at
            ) VALUES (
                'skill.fixture.core', 'Fixture', 'all',
                'app/prompts/skills/fixture_skill.md', 'test', 'sql',
                '2026-08-30T00:00:00+00:00'
            )
            """
        )
        connection.execute("PRAGMA user_version = 7")
        connection.commit()
    finally:
        connection.close()


def _version8_database(path: Path) -> None:
    _version7_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE skills ADD COLUMN main_tools_json TEXT")
        connection.execute("ALTER TABLE skills ADD COLUMN main_tools_contract_version INTEGER")
        connection.execute(
            """
            CREATE TABLE schema_reader_compatibility (
                schema_version INTEGER PRIMARY KEY,
                minimum_reader_version INTEGER NOT NULL,
                change_class TEXT NOT NULL,
                description TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_reader_compatibility VALUES (?, ?, ?, ?)",
            (8, 7, "additive", "typed tools"),
        )
        connection.execute("PRAGMA user_version = 8")
        connection.commit()
    finally:
        connection.close()


def _version9_database(path: Path) -> None:
    _version8_database(path)
    connection = sqlite3.connect(path)
    try:
        migrations_module._migration_009_lists_operation_idempotency(connection)
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    finally:
        connection.close()


def _version10_email_operation_database(path: Path) -> None:
    _version9_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE email_messages (
                gmail_message_id TEXT PRIMARY KEY,
                gmail_thread_id TEXT NOT NULL,
                rfc_message_id TEXT,
                source_route_key TEXT NOT NULL,
                gmail_history_id TEXT NOT NULL,
                internal_date INTEGER NOT NULL,
                sender_name TEXT,
                sender_email TEXT,
                recipient_headers_json TEXT NOT NULL DEFAULT '[]',
                subject TEXT NOT NULL,
                snippet TEXT NOT NULL,
                gmail_label_ids_json TEXT NOT NULL DEFAULT '[]',
                attachment_metadata_json TEXT NOT NULL DEFAULT '[]',
                canonical_body_hash TEXT NOT NULL,
                list_id TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                content_changed_at TEXT
            )
            """
        )
        migrations_module._migration_010_email_managed_label_operations(connection)
        connection.execute(
            """
            INSERT INTO email_messages(
                gmail_message_id, gmail_thread_id, source_route_key, gmail_history_id,
                internal_date, subject, snippet, canonical_body_hash,
                first_seen_at, last_seen_at
            ) VALUES (
                'message-existing', 'thread-existing', 'work', '1', 1,
                'Subject', 'Snippet', 'body-hash',
                '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO email_tool_operations (
                operation_id, tool_id, contract_version, owner_user_id,
                discord_channel_id, arguments_hash, effect_cardinality,
                expected_child_count, recovery_manifest_json, recovery_manifest_hash,
                status, result_json, created_at
            ) VALUES (
                'operation-existing', 'email.apply_labels', 1, 'operator', '100',
                'arguments-hash', 'independent_batch', 1, '{}', 'manifest-hash',
                'queued', '{}', '2026-09-01T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO email_managed_label_operations (
                child_operation_id, parent_operation_id, child_index,
                gmail_message_id, action, managed_label_refs_json,
                arguments_hash, idempotency_key, status, next_attempt_at,
                created_at, updated_at
            ) VALUES (
                'child-existing', 'operation-existing', 1, 'message-existing',
                'apply', '["label_v1_existing"]', 'child-hash', 'child-key',
                'queued', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z',
                '2026-09-01T00:00:00Z'
            )
            """
        )
        connection.execute("PRAGMA user_version = 10")
        connection.commit()
    finally:
        connection.close()


def _version11_email_operation_database(path: Path) -> None:
    _version10_email_operation_database(path)
    connection = sqlite3.connect(path)
    try:
        migrations_module._migration_011_email_reversible_mailbox_state(connection)
        connection.execute("PRAGMA user_version = 11")
        connection.commit()
    finally:
        connection.close()


def _version12_home_database(path: Path) -> None:
    _version11_email_operation_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE switches (name TEXT PRIMARY KEY, room_name TEXT, state TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE switch_actions_log (
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
        connection.execute(
            "INSERT INTO switches VALUES ('office light', NULL, 'off', '2026-09-01T00:00:00Z')"
        )
        connection.execute(
            """
            INSERT INTO switch_actions_log (
                timestamp, switch_name, action, state_after, source_interface, requested_by_user_id
            ) VALUES ('2026-09-01T00:00:00Z', 'office light', 'off', 'off', 'test', 'operator')
            """
        )
        migrations_module._migration_012_action_approval_proposals(connection)
        connection.execute("PRAGMA user_version = 12")
        connection.commit()
    finally:
        connection.close()


def _version13_email_database(path: Path) -> None:
    _version12_home_database(path)
    connection = sqlite3.connect(path)
    try:
        migrations_module._migration_013_home_operation_idempotency(connection)
        connection.execute("PRAGMA user_version = 13")
        connection.commit()
    finally:
        connection.close()


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _newer_database(
    path: Path,
    *,
    version: int = 8,
    rows: tuple[tuple[int, int, str], ...] = ((8, 7, "additive"),),
    create_compatibility_table: bool = True,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE canary (value TEXT NOT NULL)")
        connection.execute("INSERT INTO canary VALUES ('unchanged')")
        if create_compatibility_table:
            connection.execute(
                """
                CREATE TABLE schema_reader_compatibility (
                    schema_version INTEGER NOT NULL,
                    minimum_reader_version INTEGER NOT NULL,
                    change_class TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                "INSERT INTO schema_reader_compatibility VALUES (?, ?, ?)",
                rows,
            )
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    finally:
        connection.close()


def test_current_core_schema_initializes_at_reader_version(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "current.db")
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION == 14
        assert evaluate_schema_reader_compatibility(connection).compatible is True
    finally:
        connection.close()


def test_fresh_version14_schema_has_typed_tools_and_operation_ledgers(tmp_path: Path) -> None:
    path = tmp_path / "fresh-v14.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert {"main_tools_json", "main_tools_contract_version"}.issubset(
            _column_names(connection, "skills")
        )
        assert tuple(connection.execute(
            """
            SELECT minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version = 8
            """
        ).fetchone()) == (7, "additive")
        assert tuple(connection.execute(
            "SELECT minimum_reader_version, change_class FROM schema_reader_compatibility "
            "WHERE schema_version = 14"
        ).fetchone()) == (7, "additive")
        assert {"idempotency_key", "operation_identity_hash", "parent_manifest_hash"}.issubset(
            _column_names(connection, "email_tool_operations")
        )
        assert {"parent_operation_id", "parent_manifest_hash", "child_index", "arguments_hash"}.issubset(
            _column_names(connection, "email_mailbox_operations")
        )
        assert tuple(connection.execute(
            """
            SELECT minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version = 13
            """
        ).fetchone()) == (7, "additive")
        assert {"operation_id", "arguments_hash"}.issubset(
            _column_names(connection, "switch_actions_log")
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_switch_actions_operation_id'"
        ).fetchone() is not None
        assert tuple(connection.execute(
            """
            SELECT minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version = 11
            """
        ).fetchone()) == (10, "additive")
        assert tuple(connection.execute(
            """
            SELECT minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version = 12
            """
        ).fetchone()) == (7, "additive")
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_proposals'"
        ).fetchone() is not None
        for table in (
            "email_managed_labels",
            "email_message_managed_labels",
            "email_tool_operations",
            "email_managed_label_operations",
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None
        assert tuple(connection.execute(
            """
            SELECT minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version = 10
            """
        ).fetchone()) == (7, "additive")
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='list_operations'"
        ).fetchone() is not None
        assert tuple(connection.execute(
            """
            SELECT minimum_reader_version, change_class
            FROM schema_reader_compatibility
            WHERE schema_version = 9
            """
        ).fetchone()) == (7, "additive")
    finally:
        connection.close()


def test_populated_version7_upgrade_is_additive_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-v7.db"
    _version7_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        row = connection.execute(
            """
            SELECT skill_id, main_tools_json, main_tools_contract_version
            FROM skills WHERE skill_id = 'skill.fixture.core'
            """
        ).fetchone()
        assert tuple(row) == ("skill.fixture.core", None, None)
        assert evaluate_schema_reader_compatibility(connection, reader_version=10).reason == (
            "additive_reader_bridge"
        )
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 8"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 9"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 10"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 11"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 12"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 13"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    [
        "add_main_tools_json",
        "add_main_tools_contract_version",
        "create_reader_compatibility",
        "record_reader_compatibility",
        "set_user_version",
    ],
)
def test_migration8_rolls_back_every_step_and_retries_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"atomic-{failure_step}.db"
    _version7_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    def fail_after_step(version: int, step: str) -> None:
        if version == 8 and step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert "main_tools_json" not in _column_names(connection, "skills")
        assert "main_tools_contract_version" not in _column_names(connection, "skills")
        assert connection.execute(
            "SELECT skill_name FROM skills WHERE skill_id = 'skill.fixture.core'"
        ).fetchone()[0] == "Fixture"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_reader_compatibility'"
        ).fetchone() is None

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert {"main_tools_json", "main_tools_contract_version"}.issubset(
            _column_names(connection, "skills")
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    [
        "create_list_operations",
        "create_list_operations_index",
        "record_reader_compatibility",
        "set_user_version",
    ],
)
def test_migration9_rolls_back_every_step_and_retries_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"lists-atomic-{failure_step}.db"
    _version8_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    def fail_after_step(version: int, step: str) -> None:
        if version == 9 and step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='list_operations'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 9"
        ).fetchone()[0] == 0

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='list_operations'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 9"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    [
        "create_email_managed_label_tables",
        "record_reader_compatibility",
        "set_user_version",
    ],
)
def test_migration10_rolls_back_every_step_and_retries_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"email-atomic-{failure_step}.db"
    _version9_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    def fail_after_step(version: int, step: str) -> None:
        if version == 10 and step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='email_tool_operations'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 10"
        ).fetchone()[0] == 0

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='email_tool_operations'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 10"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    [
        "rebuild_email_operation_tables",
        "record_reader_compatibility",
        "set_user_version",
    ],
)
def test_migration11_preserves_email_operations_and_rolls_back_every_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"email-state-atomic-{failure_step}.db"
    _version10_email_operation_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    def fail_after_step(version: int, step: str) -> None:
        if version == 11 and step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert connection.execute(
            "SELECT tool_id FROM email_tool_operations WHERE operation_id='operation-existing'"
        ).fetchone()[0] == "email.apply_labels"
        assert connection.execute(
            "SELECT parent_operation_id FROM email_managed_label_operations "
            "WHERE child_operation_id='child-existing'"
        ).fetchone()[0] == "operation-existing"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version=11"
        ).fetchone()[0] == 1
        connection.execute(
            """
            INSERT INTO email_tool_operations (
                operation_id, tool_id, contract_version, owner_user_id,
                discord_channel_id, arguments_hash, effect_cardinality,
                expected_child_count, recovery_manifest_json, recovery_manifest_hash,
                status, result_json, created_at
            ) VALUES (
                'operation-read', 'email.set_read_state', 1, 'operator', '100',
                'read-hash', 'independent_batch', 1, '{}', 'read-manifest',
                'reserved', '{}', '2026-09-01T00:00:00Z'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO email_tool_operations (
                    operation_id, tool_id, contract_version, owner_user_id,
                    discord_channel_id, arguments_hash, effect_cardinality,
                    expected_child_count, recovery_manifest_json, recovery_manifest_hash,
                    status, result_json, created_at
                ) VALUES (
                    'operation-forbidden', 'email.delete_messages', 1, 'operator', '100',
                    'delete-hash', 'independent_batch', 1, '{}', 'delete-manifest',
                    'reserved', '{}', '2026-09-01T00:00:00Z'
                )
                """
            )
    finally:
        connection.close()


def test_version10_reader_accepts_additive_version11_bridge(tmp_path: Path) -> None:
    path = tmp_path / "reader-boundary.db"
    _version7_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        decision = evaluate_schema_reader_compatibility(connection, reader_version=10)
        assert decision.compatible is True
        assert decision.reason == "additive_reader_bridge"

        def pre_bridge_startup(reader_version: int) -> None:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > reader_version:
                raise RuntimeError("newer than supported")

        with pytest.raises(RuntimeError, match="newer than supported"):
            pre_bridge_startup(10)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    ["create_action_proposals", "record_reader_compatibility", "set_user_version"],
)
def test_migration12_is_atomic_preserves_rows_and_reopens_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"action-approval-atomic-{failure_step}.db"
    _version11_email_operation_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    def fail_after_step(version: int, step: str) -> None:
        if version == 12 and step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 11
        assert connection.execute(
            "SELECT tool_id FROM email_tool_operations WHERE operation_id='operation-existing'"
        ).fetchone()[0] == "email.apply_labels"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_proposals'"
        ).fetchone() is None

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == 14
        assert initialize_schema(connection) == 14
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version=12"
        ).fetchone()[0] == 1
        required = {
            "proposal_id",
            "review_id",
            "destination_arguments_json",
            "batch_manifest_json",
            "batch_manifest_hash",
            "transfer_manifest_json",
            "transfer_binding_hash",
            "outcome_guild_id",
            "outcome_channel_id",
            "outcome_message_id",
        }
        assert required.issubset(_column_names(connection, "action_proposals"))
        assert connection.execute(
            "SELECT tool_id FROM email_tool_operations WHERE operation_id='operation-existing'"
        ).fetchone()[0] == "email.apply_labels"
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    [
        "add_switch_operation_id",
        "add_switch_arguments_hash",
        "create_switch_operation_index",
        "record_reader_compatibility",
        "set_user_version",
    ],
)
def test_migration13_is_atomic_preserves_home_rows_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"home-operation-atomic-{failure_step}.db"
    _version12_home_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    def fail_after_step(version: int, step: str) -> None:
        if version == 13 and step == failure_step:
            raise RuntimeError(f"injected failure after {step}")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        assert connection.execute(
            "SELECT state FROM switches WHERE name='office light'"
        ).fetchone()[0] == "off"
        assert connection.execute("SELECT COUNT(*) FROM switch_actions_log").fetchone()[0] == 1

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == 14
        assert initialize_schema(connection) == 14
        assert {"operation_id", "arguments_hash"}.issubset(
            _column_names(connection, "switch_actions_log")
        )
        legacy = connection.execute(
            "SELECT operation_id, arguments_hash FROM switch_actions_log"
        ).fetchone()
        assert tuple(legacy) == (None, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version=13"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure_step",
    ["ensure_email_schema", "add_mailbox_parent_manifest_hash", "set_user_version"],
)
def test_migration14_is_atomic_preserves_p5f_rows_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    path = tmp_path / f"email-p8d-atomic-{failure_step}.db"
    _version13_email_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    def fail_after_step(version: int, step: str) -> None:
        if version == 14 and step == failure_step:
            raise RuntimeError("injected migration14 failure")

    monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", fail_after_step)
    try:
        with pytest.raises(RuntimeError, match="migration14"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 13
        assert connection.execute(
            "SELECT tool_id FROM email_tool_operations WHERE operation_id='operation-existing'"
        ).fetchone()[0] == "email.apply_labels"

        monkeypatch.setattr(migrations_module, "_MIGRATION_STEP_HOOK", None)
        assert initialize_schema(connection) == 14
        assert initialize_schema(connection) == 14
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        parent = connection.execute(
            "SELECT tool_id, idempotency_key, parent_manifest_hash "
            "FROM email_tool_operations WHERE operation_id='operation-existing'"
        ).fetchone()
        assert tuple(parent) == ("email.apply_labels", None, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version=14"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_p1_reader_accepts_complete_additive_newer_chain_without_migration(tmp_path: Path) -> None:
    path = tmp_path / "newer.db"
    _newer_database(
        path,
        version=14,
        rows=(
            (8, 7, "additive"),
            (9, 7, "additive"),
            (10, 7, "additive"),
            (11, 10, "additive"),
            (12, 7, "additive"),
            (13, 7, "additive"),
            (14, 7, "additive"),
        ),
    )
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        assert connection.execute("SELECT value FROM canary").fetchone()[0] == "unchanged"
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("version", "rows", "create_table", "reason"),
    [
        (8, (), False, "compatibility_table_missing"),
        (9, ((8, 7, "additive"),), True, "compatibility_row_missing"),
        (8, ((8, 7, "destructive"),), True, "change_not_additive"),
        (8, ((8, 8, "additive"),), True, "minimum_reader_too_new"),
    ],
)
def test_version7_reader_fails_closed_for_unproven_newer_schema(
    tmp_path: Path,
    version: int,
    rows: tuple[tuple[int, int, str], ...],
    create_table: bool,
    reason: str,
) -> None:
    path = tmp_path / f"denied-{reason}.db"
    _newer_database(path, version=version, rows=rows, create_compatibility_table=create_table)
    connection = sqlite3.connect(path)
    try:
        decision = evaluate_schema_reader_compatibility(connection, reader_version=7)
        assert decision.compatible is False
        assert decision.reason == reason
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version
    finally:
        connection.close()


def test_core_reader_check_is_immutable_and_emits_only_fixed_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "reader.db"
    _newer_database(path)
    before = path.stat().st_mtime_ns

    assert reader_check(path) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "reason": "schema_not_newer",
        "result": "compatible",
        "version": 8,
    }
    assert path.stat().st_mtime_ns == before
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/manage_database.py",
            "reader-check",
            "--source",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == output
