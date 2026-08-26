from __future__ import annotations

import sqlite3

from app.db.document_schema import DOCUMENT_SCHEMA_VERSION, initialize_document_schema


def test_v5_artifact_storage_key_migration_preserves_links_and_allows_run_reuse(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE documents (document_id TEXT PRIMARY KEY);
        CREATE TABLE document_source_versions (source_version_id TEXT PRIMARY KEY);
        CREATE TABLE document_processing_runs (run_id TEXT PRIMARY KEY);
        CREATE TABLE document_artifacts (
            artifact_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            storage_key TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            schema_version TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, artifact_kind, sha256),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );
        CREATE TABLE document_text_layers (
            layer_id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            FOREIGN KEY (artifact_id) REFERENCES document_artifacts(artifact_id)
        );
        INSERT INTO documents VALUES ('document-1');
        INSERT INTO document_source_versions VALUES ('source-1');
        INSERT INTO document_processing_runs VALUES ('run-1');
        INSERT INTO document_processing_runs VALUES ('run-2');
        INSERT INTO document_artifacts VALUES (
            'artifact-1', 'document-1', 'source-1', 'run-1', 'markdown',
            'sha256/aa/shared.md', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            12, '1', 'private', '2026-08-25T00:00:00+00:00'
        );
        INSERT INTO document_text_layers VALUES ('layer-1', 'artifact-1');
        PRAGMA user_version = 5;
        """
    )

    assert initialize_document_schema(connection) == DOCUMENT_SCHEMA_VERSION == 14
    connection.execute(
        """
        INSERT INTO document_artifacts VALUES (
            'artifact-2', 'document-1', 'source-1', 'run-2', 'markdown',
            'sha256/aa/shared.md', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            12, '1', 'private', '2026-08-25T00:01:00+00:00'
        )
        """
    )
    connection.commit()

    assert connection.execute("SELECT count(*) FROM document_artifacts").fetchone()[0] == 2
    assert connection.execute("SELECT artifact_id FROM document_text_layers").fetchone()[0] == "artifact-1"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v6_migration_adds_durable_channel_ingress_receipts(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("DROP TABLE document_ingress_receipts")
    connection.execute("PRAGMA user_version = 6")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(document_ingress_receipts)")
    }
    assert columns == {"ingress_source", "external_id", "owner_id", "document_id", "created_at"}
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v7_migration_adds_ocr_confidence_and_language_columns(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("PRAGMA user_version = 7")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    columns = {row[1] for row in connection.execute("PRAGMA table_info(document_blocks)")}
    assert {"confidence", "language"} <= columns
    connection.close()


def test_v8_migration_adds_phase6_append_only_classification_and_fields(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("DROP TABLE document_metadata_sync")
    connection.execute("DROP TABLE document_field_decisions")
    connection.execute("DROP TABLE document_field_observations")
    connection.execute("DROP TABLE document_classifications")
    connection.execute("PRAGMA user_version = 8")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'document_%'"
        )
    }
    assert {
        "document_classifications",
        "document_field_observations",
        "document_field_decisions",
        "document_metadata_sync",
    } <= tables
    document_columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    assert {"selected_document_class", "classification_state"} <= document_columns
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v9_migration_adds_archive_text_visibility_gate(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("PRAGMA user_version = 9")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    assert "archive_text_visible" in columns
    connection.close()


def test_v10_migration_adds_phase7_note_proposal_tables(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("DROP TABLE document_memory_proposals")
    connection.execute("DROP TABLE document_action_proposals")
    connection.execute("PRAGMA user_version = 10")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'document_%_proposals'"
        )
    }
    assert {"document_action_proposals", "document_memory_proposals"} <= tables
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v11_migration_adds_phase8_contact_proposals(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("DROP TABLE document_contact_proposals")
    connection.execute("PRAGMA user_version = 11")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(document_contact_proposals)")
    }
    assert {
        "proposed_fields_json",
        "candidate_matches_json",
        "capability_status",
        "selected_contact_ref",
        "review_id",
    } <= columns
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v12_migration_adds_phase9_analyses_and_literal_claims(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("DROP TABLE document_literal_claims")
    connection.execute("DROP TABLE document_analyses")
    connection.execute("PRAGMA user_version = 12")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('document_analyses','document_literal_claims')"
        )
    }
    assert tables == {"document_analyses", "document_literal_claims"}
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v13_migration_adds_content_free_restricted_access_audit(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "documents.db")
    initialize_document_schema(connection)
    connection.execute("DROP TABLE document_restricted_access_audit")
    connection.execute("PRAGMA user_version = 13")
    connection.commit()

    assert initialize_document_schema(connection) == 14
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(document_restricted_access_audit)")
    }
    assert columns == {
        "audit_id", "document_id", "actor_principal", "purpose_code", "operation",
        "outcome", "reason_code", "request_id", "observed_at",
    }
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
