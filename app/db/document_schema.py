from __future__ import annotations

import sqlite3


DOCUMENT_SCHEMA_VERSION = 14


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


def _migrate_ocr_block_metadata(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'document_blocks'"
    ).fetchone() is None:
        return
    columns = _columns(conn, "document_blocks")
    if "confidence" not in columns:
        conn.execute("ALTER TABLE document_blocks ADD COLUMN confidence REAL")
    if "language" not in columns:
        conn.execute("ALTER TABLE document_blocks ADD COLUMN language TEXT")


def _migrate_phase6_classification_and_extraction(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "documents")
    if "selected_document_class" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN selected_document_class TEXT")
    if "classification_state" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN classification_state TEXT NOT NULL DEFAULT 'unclassified'")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_classifications (
            classification_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL,
            label TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            confidence REAL NOT NULL,
            classifier_name TEXT NOT NULL,
            classifier_version TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            decision_source TEXT NOT NULL,
            state TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            item_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, label, classifier_name, classifier_version, item_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS document_field_observations (
            observation_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            field_name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            literal_text TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            provider_name TEXT NOT NULL,
            provider_version TEXT NOT NULL,
            observation_state TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            supersedes_observation_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, schema_name, schema_version, field_name, item_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id),
            FOREIGN KEY (supersedes_observation_id) REFERENCES document_field_observations(observation_id)
        );

        CREATE TABLE IF NOT EXISTS document_field_decisions (
            field_decision_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            review_decision_id TEXT NOT NULL UNIQUE,
            selected_observation_id TEXT,
            applied_value_json TEXT,
            decision_kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (selected_observation_id) REFERENCES document_field_observations(observation_id)
        );

        CREATE TABLE IF NOT EXISTS document_metadata_sync (
            sync_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE,
            desired_hash TEXT NOT NULL,
            observed_hash TEXT,
            provider_version TEXT,
            state TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(proposal_id, provider, desired_hash),
            FOREIGN KEY (proposal_id) REFERENCES document_metadata_proposals(proposal_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_classifications_selected
            ON document_classifications(document_id, source_version_id, selected, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_field_observations_lookup
            ON document_field_observations(document_id, source_version_id, field_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_field_decisions_lookup
            ON document_field_decisions(document_id, source_version_id, field_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_metadata_sync_state
            ON document_metadata_sync(state, updated_at);
        """
    )


def _migrate_phase6_archive_visibility(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "documents")
    if "archive_text_visible" not in columns:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN archive_text_visible INTEGER NOT NULL DEFAULT 1"
        )


def _migrate_phase7_note_proposals(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_action_proposals (
            proposal_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            action_text TEXT NOT NULL,
            target_list_name TEXT NOT NULL,
            due_text TEXT,
            normalized_due_date TEXT,
            assignee_candidate TEXT,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            review_id TEXT,
            state TEXT NOT NULL,
            execution_ref TEXT,
            target_item_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, item_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS document_memory_proposals (
            proposal_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            fact_text TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, item_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_action_proposals_review
            ON document_action_proposals(review_id, state, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_action_proposals_document
            ON document_action_proposals(document_id, source_version_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_memory_proposals_document
            ON document_memory_proposals(document_id, source_version_id, state, created_at DESC);
        """
    )


def _migrate_phase8_contact_proposals(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_contact_proposals (
            proposal_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            proposed_fields_json TEXT NOT NULL,
            candidate_matches_json TEXT NOT NULL,
            provider_name TEXT,
            capability_status TEXT NOT NULL,
            proposed_operation TEXT NOT NULL,
            selected_contact_ref TEXT,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            review_id TEXT,
            state TEXT NOT NULL,
            execution_ref TEXT,
            target_contact_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, item_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_contact_proposals_review
            ON document_contact_proposals(review_id, state, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_contact_proposals_document
            ON document_contact_proposals(document_id, source_version_id, created_at DESC);
        """
    )


def _migrate_phase9_intelligence(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_analyses (
            analysis_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            analysis_kind TEXT NOT NULL,
            result_json TEXT NOT NULL,
            recurring_match_token TEXT,
            input_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, analysis_kind, input_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS document_literal_claims (
            claim_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            claim_kind TEXT NOT NULL,
            machine_label TEXT NOT NULL,
            literal_text TEXT NOT NULL,
            normalized_date TEXT,
            page_number INTEGER NOT NULL,
            block_id TEXT NOT NULL,
            confidence REAL NOT NULL,
            item_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, item_hash),
            FOREIGN KEY (document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_version_id) REFERENCES document_source_versions(source_version_id),
            FOREIGN KEY (run_id) REFERENCES document_processing_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_analyses_match
            ON document_analyses(recurring_match_token, analysis_kind, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_analyses_document
            ON document_analyses(document_id, source_version_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_document_literal_claims_document
            ON document_literal_claims(document_id, source_version_id, claim_kind, created_at DESC);
        """
    )


def _migrate_phase10_restricted_access_audit(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_restricted_access_audit (
            audit_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            actor_principal TEXT NOT NULL,
            purpose_code TEXT NOT NULL,
            operation TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            request_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            UNIQUE(request_id, operation),
            FOREIGN KEY (document_id) REFERENCES documents(document_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_restricted_audit_document
            ON document_restricted_access_audit(document_id, observed_at DESC);
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
        current = 7
    if current < 8:
        _migrate_ocr_block_metadata(conn)
        conn.execute("PRAGMA user_version = 8")
        conn.commit()
        current = 8
    if current < 9:
        _migrate_phase6_classification_and_extraction(conn)
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
        current = 9
    if current < 10:
        _migrate_phase6_archive_visibility(conn)
        conn.execute("PRAGMA user_version = 10")
        conn.commit()
        current = 10
    if current < 11:
        _migrate_phase7_note_proposals(conn)
        conn.execute("PRAGMA user_version = 11")
        conn.commit()
        current = 11
    if current < 12:
        _migrate_phase8_contact_proposals(conn)
        conn.execute("PRAGMA user_version = 12")
        conn.commit()
        current = 12
    if current < 13:
        _migrate_phase9_intelligence(conn)
        conn.execute("PRAGMA user_version = 13")
        conn.commit()
        current = 13
    if current < 14:
        _migrate_phase10_restricted_access_audit(conn)
        conn.execute("PRAGMA user_version = 14")
        conn.commit()
    return DOCUMENT_SCHEMA_VERSION
