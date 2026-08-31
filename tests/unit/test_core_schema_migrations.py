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
        assert initialize_schema(connection) == LATEST_SCHEMA_VERSION == 9
        assert evaluate_schema_reader_compatibility(connection).compatible is True
    finally:
        connection.close()


def test_fresh_version9_schema_has_typed_tools_list_operations_and_reader_records(tmp_path: Path) -> None:
    path = tmp_path / "fresh-v9.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == 9
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
        assert initialize_schema(connection) == 9
        row = connection.execute(
            """
            SELECT skill_id, main_tools_json, main_tools_contract_version
            FROM skills WHERE skill_id = 'skill.fixture.core'
            """
        ).fetchone()
        assert tuple(row) == ("skill.fixture.core", None, None)
        assert evaluate_schema_reader_compatibility(connection, reader_version=7).reason == (
            "additive_reader_bridge"
        )
        assert initialize_schema(connection) == 9
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 8"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 9"
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
        assert initialize_schema(connection) == 9
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
        assert initialize_schema(connection) == 9
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='list_operations'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_reader_compatibility WHERE schema_version = 9"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_pre_bridge_reader_refuses_version8_while_p1_reader_accepts(tmp_path: Path) -> None:
    path = tmp_path / "reader-boundary.db"
    _version7_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        assert initialize_schema(connection) == 9
        decision = evaluate_schema_reader_compatibility(connection, reader_version=7)
        assert decision.compatible is True
        assert decision.reason == "additive_reader_bridge"

        def pre_bridge_startup(reader_version: int) -> None:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > reader_version:
                raise RuntimeError("newer than supported")

        with pytest.raises(RuntimeError, match="newer than supported"):
            pre_bridge_startup(7)
    finally:
        connection.close()


def test_p1_reader_accepts_complete_additive_newer_chain_without_migration(tmp_path: Path) -> None:
    path = tmp_path / "newer.db"
    _newer_database(
        path,
        version=10,
        rows=((8, 7, "additive"), (9, 7, "additive"), (10, 6, "additive")),
    )
    connection = sqlite3.connect(path)
    try:
        assert initialize_schema(connection) == 10
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
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
