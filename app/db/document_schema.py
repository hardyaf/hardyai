from __future__ import annotations

import sqlite3


DOCUMENT_SCHEMA_VERSION = 7


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1]).strip().casefold()
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _migrate_phase2(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "documents")
    additions = {
        "sensitivity": "TEXT NOT NULL DEFAULT 'private'",
        "processing_state": "TEXT NOT NULL DEFAULT 'not_requested'",
        "active_source_version_id": "TEXT",
        "active_run_id": "TEXT",
        "search_visible": "INTEGER NOT NULL DEFAULT 1",
        "source_availability": "TEXT NOT NULL DEFAULT 'available'",
    }
    for name, declaration in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {declaration}")
    intake_columns = _columns(conn, "document_intakes")
    if "ingest_route" not in intake_columns:
        conn.execute("ALTER TABLE document_intakes ADD COLUMN ingest_route TEXT NOT NULL DEFAULT 'web'")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_source_versions (
            source_version_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            original_sha256 TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            ingest_route TEXT NOT NULL,
            external_version TEXT,
            received_at TEXT NOT NULL,
            archived_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(document_id, source_ref, original_sha256),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_ref) REFERENCES document_archive_sources(source_ref)
        );

        CREATE TABLE IF NOT EXISTS document_processing_runs (
            run_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            status TEXT NOT NULL,
            route TEXT NOT NULL,
            parser_name TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parser_image_digest TEXT,
            configuration_sha256 TEXT NOT NULL,
            artifact_schema_version TEXT NOT NULL,
            resource_lane TEXT NOT NULL,
            fallback_from_run_id TEXT,
            request_key TEXT,
            provider_operation_ref TEXT,
            fencing_token INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (fallback_from_run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS document_stage_commits (
            stage_commit_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            stage_version TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            result_hash TEXT NOT NULL,
            committed_at TEXT NOT NULL,
            UNIQUE(run_id, stage, stage_version),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS document_metadata_proposals (
            proposal_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            proposed_value_json TEXT NOT NULL,
            value_hash TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            review_id TEXT,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(document_id, source_version_id, field_name, value_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id)
        );

        CREATE TABLE IF NOT EXISTS document_provider_snapshots (
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            external_version TEXT,
            document_id TEXT,
            observed_hash TEXT,
            observed_state TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (provider, external_id),
            FOREIGN KEY (document_id) REFERENCES documents(document_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_runs_source_status
            ON document_processing_runs(source_version_id, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_runs_provider_operation
            ON document_processing_runs(provider_operation_ref);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_document_runs_request_key
            ON document_processing_runs(request_key) WHERE request_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_document_metadata_review
            ON document_metadata_proposals(review_id, state);
        CREATE INDEX IF NOT EXISTS idx_document_provider_seen
            ON document_provider_snapshots(provider, last_seen_at);
        """
    )


def _migrate_phase3(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_artifacts (
            artifact_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            storage_key TEXT NOT NULL,
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

        CREATE TABLE IF NOT EXISTS document_pages (
            run_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            width REAL NOT NULL,
            height REAL NOT NULL,
            coordinate_space TEXT NOT NULL,
            rotation_degrees INTEGER NOT NULL DEFAULT 0,
            quality_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id, page_number),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS document_blocks (
            run_id TEXT NOT NULL,
            block_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            block_kind TEXT NOT NULL,
            reading_order INTEGER NOT NULL,
            literal_text TEXT NOT NULL,
            bbox_json TEXT,
            char_span_json TEXT,
            provider_ref TEXT,
            sensitivity TEXT NOT NULL,
            PRIMARY KEY (run_id, block_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id)
        );

        CREATE TABLE IF NOT EXISTS document_text_layers (
            layer_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            layer_kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_ref TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            transformation_name TEXT NOT NULL,
            transformation_version TEXT NOT NULL,
            configuration_sha256 TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id),
            FOREIGN KEY (artifact_id) REFERENCES document_artifacts(artifact_id)
        );

        CREATE TABLE IF NOT EXISTS document_tables (
            run_id TEXT NOT NULL,
            table_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            reading_order INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            bbox_json TEXT,
            provider_ref TEXT,
            sensitivity TEXT NOT NULL,
            PRIMARY KEY (run_id, table_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id)
        );

        CREATE TABLE IF NOT EXISTS document_table_cells (
            run_id TEXT NOT NULL,
            table_id TEXT NOT NULL,
            cell_id TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            column_index INTEGER NOT NULL,
            row_span INTEGER NOT NULL DEFAULT 1,
            column_span INTEGER NOT NULL DEFAULT 1,
            literal_text TEXT NOT NULL,
            bbox_json TEXT,
            provider_ref TEXT,
            PRIMARY KEY (run_id, table_id, cell_id),
            FOREIGN KEY (run_id, table_id) REFERENCES document_tables(run_id, table_id)
        );

        CREATE TABLE IF NOT EXISTS document_stage_messages (
            message_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            restricted_detail_ref TEXT,
            occurred_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_artifacts_run
            ON document_artifacts(run_id, artifact_kind);
        CREATE INDEX IF NOT EXISTS idx_document_artifacts_storage_key
            ON document_artifacts(storage_key);
        CREATE INDEX IF NOT EXISTS idx_document_blocks_lookup
            ON document_blocks(document_id, run_id, page_number, reading_order);
        CREATE INDEX IF NOT EXISTS idx_document_blocks_text
            ON document_blocks(document_id, literal_text);
        CREATE INDEX IF NOT EXISTS idx_document_tables_lookup
            ON document_tables(document_id, run_id, page_number, reading_order);
        """
    )


def _migrate_append_only_runs(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'document_processing_runs'"
    ).fetchone()
    table_sql = str(row[0] if row else "").replace("\n", " ").casefold()
    if "unique(source_version_id,route,configuration_sha256)" not in table_sql.replace(" ", ""):
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            CREATE TABLE document_processing_runs_new (
                run_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                status TEXT NOT NULL,
                route TEXT NOT NULL,
                parser_name TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                parser_image_digest TEXT,
                configuration_sha256 TEXT NOT NULL,
                artifact_schema_version TEXT NOT NULL,
                resource_lane TEXT NOT NULL,
                fallback_from_run_id TEXT,
                request_key TEXT,
                provider_operation_ref TEXT,
                fencing_token INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(document_id),
                FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
                FOREIGN KEY (fallback_from_run_id) REFERENCES document_processing_runs(run_id)
            );
            INSERT INTO document_processing_runs_new (
                run_id, document_id, source_version_id, status, route,
                parser_name, parser_version, parser_image_digest,
                configuration_sha256, artifact_schema_version, resource_lane,
                fallback_from_run_id, provider_operation_ref, fencing_token,
                error_code, started_at, completed_at, created_at, updated_at
            ) SELECT
                run_id, document_id, source_version_id, status, route,
                parser_name, parser_version, parser_image_digest,
                configuration_sha256, artifact_schema_version, resource_lane,
                fallback_from_run_id, provider_operation_ref, fencing_token,
                error_code, started_at, completed_at, created_at, updated_at
              FROM document_processing_runs;
            DROP TABLE document_processing_runs;
            ALTER TABLE document_processing_runs_new RENAME TO document_processing_runs;
            CREATE INDEX IF NOT EXISTS idx_document_runs_source_status
                ON document_processing_runs(source_version_id, status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_document_runs_provider_operation
                ON document_processing_runs(provider_operation_ref);
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violation = conn.execute("PRAGMA foreign_key_check").fetchone()
    if violation is not None:
        raise RuntimeError("Document run migration failed foreign-key verification")


def _migrate_run_request_keys(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "document_processing_runs")
    if "request_key" not in columns:
        conn.execute("ALTER TABLE document_processing_runs ADD COLUMN request_key TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_document_runs_request_key "
        "ON document_processing_runs(request_key) WHERE request_key IS NOT NULL"
    )


def _migrate_reusable_artifact_storage_keys(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'document_artifacts'"
    ).fetchone()
    table_sql = " ".join(str(row[0] if row else "").split()).casefold()
    if "storage_key text not null unique" not in table_sql:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_artifacts_storage_key "
            "ON document_artifacts(storage_key)"
        )
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            CREATE TABLE document_artifacts_new (
                artifact_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                artifact_kind TEXT NOT NULL,
                storage_key TEXT NOT NULL,
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
            INSERT INTO document_artifacts_new (
                artifact_id, document_id, source_version_id, run_id,
                artifact_kind, storage_key, sha256, size_bytes,
                schema_version, sensitivity, created_at
            ) SELECT
                artifact_id, document_id, source_version_id, run_id,
                artifact_kind, storage_key, sha256, size_bytes,
                schema_version, sensitivity, created_at
              FROM document_artifacts;
            DROP TABLE document_artifacts;
            ALTER TABLE document_artifacts_new RENAME TO document_artifacts;
            CREATE INDEX idx_document_artifacts_run
                ON document_artifacts(run_id, artifact_kind);
            CREATE INDEX idx_document_artifacts_storage_key
                ON document_artifacts(storage_key);
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violation = conn.execute("PRAGMA foreign_key_check").fetchone()
    if violation is not None:
        raise RuntimeError("Document artifact migration failed foreign-key verification")


def _migrate_ingress_receipts(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_ingress_receipts (
            ingress_source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (ingress_source, external_id),
            FOREIGN KEY (document_id) REFERENCES documents(document_id)
        );
        CREATE INDEX IF NOT EXISTS idx_document_ingress_receipts_document
            ON document_ingress_receipts(document_id, created_at);
        """
    )


def initialize_document_schema(conn: sqlite3.Connection) -> int:
    """Initialize the private Documents ledger; this DB is never mounted by core Jarvis."""

    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > DOCUMENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Document schema version {current} is newer than supported version {DOCUMENT_SCHEMA_VERSION}."
        )
    if current < 1:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                state TEXT NOT NULL,
                source_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, sha256)
            );

            CREATE TABLE IF NOT EXISTS document_intakes (
                intake_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL UNIQUE,
                original_filename TEXT NOT NULL,
                spool_key TEXT,
                archive_task_ref TEXT,
                durable_job_id TEXT,
                failure_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );

            CREATE TABLE IF NOT EXISTS document_archive_sources (
                source_ref TEXT PRIMARY KEY,
                document_id TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                verified_sha256 TEXT NOT NULL,
                verified_at TEXT NOT NULL,
                UNIQUE(provider, external_id),
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );

            CREATE INDEX IF NOT EXISTS idx_documents_owner_state
                ON documents(owner_id, state, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_document_intakes_enqueue
                ON documents(state, updated_at);
            CREATE INDEX IF NOT EXISTS idx_document_sources_external
                ON document_archive_sources(provider, external_id);
            """
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        current = 1
    if current < 2:
        _migrate_phase2(conn)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        current = 2
    if current < 3:
        _migrate_phase3(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        current = 3
    if current < 4:
        _migrate_append_only_runs(conn)
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        current = 4
    if current < 5:
        _migrate_run_request_keys(conn)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        current = 5
    if current < 6:
        _migrate_reusable_artifact_storage_keys(conn)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        current = 6
    if current < 7:
        _migrate_ingress_receipts(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    return DOCUMENT_SCHEMA_VERSION
