from __future__ import annotations

import sqlite3
import hashlib
import json
from datetime import UTC, datetime
from threading import RLock
from typing import Any, ContextManager
from uuid import uuid4

from app.db.document_connection import open_document_connection
from app.db.transaction import sqlite_transaction
from app.skills.domains.documents.types import (
    ArtifactKind,
    ArchiveSourceRecord,
    DocumentRecord,
    DocumentState,
    ProcessingRoute,
    ProcessingState,
    Sensitivity,
    StagedDocument,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DocumentStorageError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DocumentRepository:
    """Private metadata ledger; callers must mount its DB on encrypted storage."""

    def __init__(self, database_path: str) -> None:
        self._database_path, self._conn = open_document_connection(database_path)
        self._lock = RLock()

    @property
    def database_path(self) -> str:
        return str(self._database_path)

    def _transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Cursor]:
        return sqlite_transaction(conn=self._conn, lock=self._lock, immediate=immediate)

    @staticmethod
    def _record(row: sqlite3.Row) -> DocumentRecord:
        value = dict(row)
        return DocumentRecord(
            document_id=str(value["document_id"]),
            intake_id=str(value["intake_id"]),
            owner_id=str(value["owner_id"]),
            title=str(value["title"]),
            original_filename=str(value["original_filename"]),
            media_type=str(value["media_type"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]),
            state=DocumentState(str(value["state"])),
            spool_key=str(value["spool_key"]) if value["spool_key"] is not None else None,
            archive_task_ref=(
                str(value["archive_task_ref"]) if value["archive_task_ref"] is not None else None
            ),
            source_ref=str(value["source_ref"]) if value["source_ref"] is not None else None,
            durable_job_id=(
                str(value["durable_job_id"]) if value["durable_job_id"] is not None else None
            ),
            failure_code=str(value["failure_code"]) if value["failure_code"] is not None else None,
            sensitivity=Sensitivity(str(value["sensitivity"])),
            processing_state=ProcessingState(str(value["processing_state"])),
            source_version_id=(
                str(value["active_source_version_id"])
                if value["active_source_version_id"] is not None
                else None
            ),
            active_run_id=str(value["active_run_id"]) if value["active_run_id"] is not None else None,
            search_visible=bool(value["search_visible"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )

    @staticmethod
    def _select_sql() -> str:
        return """
            SELECT d.*, i.intake_id, i.original_filename, i.spool_key,
                   i.archive_task_ref, i.durable_job_id, i.failure_code
            FROM documents AS d
            JOIN document_intakes AS i ON i.document_id = d.document_id
        """

    def create_or_get(self, *, owner_id: str, staged: StagedDocument) -> tuple[DocumentRecord, bool]:
        created_at = _now()
        document_id = str(uuid4())
        intake_id = str(uuid4())
        with self._transaction(immediate=True) as cur:
            existing = cur.execute(
                self._select_sql() + " WHERE d.owner_id = ? AND d.sha256 = ?",
                (owner_id, staged.sha256),
            ).fetchone()
            if existing is not None:
                return self._record(existing), False
            cur.execute(
                """
                INSERT INTO documents (
                    document_id, owner_id, title, sha256, size_bytes,
                    media_type, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    owner_id,
                    staged.title,
                    staged.sha256,
                    staged.size_bytes,
                    staged.media_type,
                    DocumentState.AWAITING_ENQUEUE.value,
                    created_at,
                    created_at,
                ),
            )
            cur.execute(
                """
                INSERT INTO document_intakes (
                    intake_id, document_id, original_filename, spool_key, ingest_route,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake_id,
                    document_id,
                    staged.original_filename,
                    staged.spool_key,
                    staged.ingest_route,
                    created_at,
                    created_at,
                ),
            )
            row = cur.execute(
                self._select_sql() + " WHERE d.document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("document intake insert did not produce a row")
        return self._record(row), True

    def get(self, document_id: str, *, owner_id: str | None = None) -> DocumentRecord | None:
        sql = self._select_sql() + " WHERE d.document_id = ?"
        values: list[Any] = [document_id]
        if owner_id is not None:
            sql += " AND d.owner_id = ?"
            values.append(owner_id)
        with self._lock:
            row = self._conn.execute(sql, values).fetchone()
        return self._record(row) if row is not None else None

    def get_for_ingress(
        self,
        *,
        ingress_source: str,
        external_id: str,
        owner_id: str,
    ) -> DocumentRecord | None:
        with self._lock:
            row = self._conn.execute(
                self._select_sql()
                + " JOIN document_ingress_receipts AS r ON r.document_id = d.document_id"
                + " WHERE r.ingress_source = ? AND r.external_id = ? AND r.owner_id = ?",
                (ingress_source, external_id, owner_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    def bind_ingress_receipt(
        self,
        *,
        ingress_source: str,
        external_id: str,
        owner_id: str,
        document_id: str,
    ) -> DocumentRecord:
        created_at = _now()
        with self._transaction(immediate=True) as cur:
            existing = cur.execute(
                """
                SELECT document_id, owner_id
                FROM document_ingress_receipts
                WHERE ingress_source = ? AND external_id = ?
                """,
                (ingress_source, external_id),
            ).fetchone()
            if existing is not None:
                if str(existing["document_id"]) != document_id or str(existing["owner_id"]) != owner_id:
                    raise DocumentStorageError("document_ingress_receipt_conflict")
            else:
                owned = cur.execute(
                    "SELECT 1 FROM documents WHERE document_id = ? AND owner_id = ?",
                    (document_id, owner_id),
                ).fetchone()
                if owned is None:
                    raise DocumentStorageError("document_ingress_owner_conflict")
                cur.execute(
                    """
                    INSERT INTO document_ingress_receipts (
                        ingress_source, external_id, owner_id, document_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (ingress_source, external_id, owner_id, document_id, created_at),
                )
            row = cur.execute(
                self._select_sql() + " WHERE d.document_id = ? AND d.owner_id = ?",
                (document_id, owner_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("document ingress receipt did not resolve a document")
        return self._record(row)

    def awaiting_enqueue(self, *, limit: int = 100) -> list[DocumentRecord]:
        with self._lock:
            rows = self._conn.execute(
                self._select_sql()
                + " WHERE d.state = ? ORDER BY d.created_at LIMIT ?",
                (DocumentState.AWAITING_ENQUEUE.value, max(1, min(int(limit), 1000))),
            ).fetchall()
        return [self._record(row) for row in rows]

    def state_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS count FROM documents GROUP BY state"
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def mark_enqueued(self, *, document_id: str, durable_job_id: str) -> DocumentRecord:
        return self._set_state(
            document_id=document_id,
            state=DocumentState.QUEUED,
            intake_updates={"durable_job_id": durable_job_id, "failure_code": None},
        )

    def mark_archiving(self, *, document_id: str, task_ref: str) -> DocumentRecord:
        return self._set_state(
            document_id=document_id,
            state=DocumentState.ARCHIVING,
            intake_updates={"archive_task_ref": task_ref, "failure_code": None},
        )

    def mark_failure(self, *, document_id: str, error_code: str, terminal: bool) -> DocumentRecord:
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            intake = cur.execute(
                "SELECT archive_task_ref FROM document_intakes WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if intake is None:
                raise KeyError(document_id)
            state = (
                DocumentState.FAILED
                if terminal
                else DocumentState.ARCHIVING
                if intake["archive_task_ref"] is not None
                else DocumentState.QUEUED
            )
            cur.execute(
                "UPDATE documents SET state = ?, updated_at = ? WHERE document_id = ?",
                (state.value, observed_at, document_id),
            )
            cur.execute(
                "UPDATE document_intakes SET failure_code = ?, updated_at = ? WHERE document_id = ?",
                (error_code[:120], observed_at, document_id),
            )
            row = cur.execute(
                self._select_sql() + " WHERE d.document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._record(row)

    def mark_ready(
        self,
        *,
        document_id: str,
        provider: str,
        external_id: str,
        verified_sha256: str,
    ) -> DocumentRecord:
        source_ref = str(uuid4())
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            record_row = cur.execute(
                """
                SELECT d.sha256, d.size_bytes, d.media_type, d.created_at,
                       i.original_filename, i.ingest_route
                FROM documents AS d
                JOIN document_intakes AS i ON i.document_id = d.document_id
                WHERE d.document_id = ?
                """,
                (document_id,),
            ).fetchone()
            if record_row is None:
                raise KeyError(document_id)
            document_source = cur.execute(
                "SELECT source_ref, external_id FROM document_archive_sources WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if document_source is not None and str(document_source["external_id"]) != external_id:
                raise DocumentStorageError("archive_source_conflict")
            existing = cur.execute(
                "SELECT source_ref, document_id FROM document_archive_sources WHERE provider = ? AND external_id = ?",
                (provider, external_id),
            ).fetchone()
            if existing is not None:
                if str(existing["document_id"]) != document_id:
                    raise DocumentStorageError("archive_source_conflict")
                source_ref = str(existing["source_ref"])
            else:
                cur.execute(
                    """
                    INSERT INTO document_archive_sources (
                        source_ref, document_id, provider, external_id, verified_sha256, verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (source_ref, document_id, provider, external_id, verified_sha256, observed_at),
                )
            source_version = cur.execute(
                """
                SELECT source_version_id FROM document_source_versions
                WHERE document_id = ? AND source_ref = ? AND original_sha256 = ?
                """,
                (document_id, source_ref, verified_sha256),
            ).fetchone()
            source_version_id = (
                str(source_version["source_version_id"])
                if source_version is not None
                else str(uuid4())
            )
            if source_version is None:
                cur.execute(
                    """
                    INSERT INTO document_source_versions (
                        source_version_id, document_id, source_ref, original_sha256,
                        media_type, size_bytes, original_filename, ingest_route,
                        received_at, archived_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_version_id,
                        document_id,
                        source_ref,
                        verified_sha256,
                        record_row["media_type"],
                        record_row["size_bytes"],
                        record_row["original_filename"],
                        record_row["ingest_route"],
                        record_row["created_at"],
                        observed_at,
                        observed_at,
                    ),
                )
            cur.execute(
                """
                UPDATE documents
                SET state = ?, source_ref = ?, active_source_version_id = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (
                    DocumentState.READY.value,
                    source_ref,
                    source_version_id,
                    observed_at,
                    document_id,
                ),
            )
            cur.execute(
                "UPDATE document_intakes SET failure_code = NULL, updated_at = ? WHERE document_id = ?",
                (observed_at, document_id),
            )
            row = cur.execute(self._select_sql() + " WHERE d.document_id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._record(row)

    def clear_spool(self, *, document_id: str, expected_spool_key: str) -> DocumentRecord:
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE document_intakes
                SET spool_key = NULL, updated_at = ?
                WHERE document_id = ? AND spool_key = ?
                """,
                (observed_at, document_id, expected_spool_key),
            )
            row = cur.execute(self._select_sql() + " WHERE d.document_id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._record(row)

    def archive_source(self, source_ref: str) -> ArchiveSourceRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_archive_sources WHERE source_ref = ?",
                (source_ref,),
            ).fetchone()
        return ArchiveSourceRecord(**dict(row)) if row is not None else None

    def document_for_external_id(
        self,
        *,
        provider: str,
        external_id: str,
        visible_only: bool = True,
    ) -> DocumentRecord | None:
        visibility = " AND d.search_visible = 1" if visible_only else ""
        with self._lock:
            row = self._conn.execute(
                self._select_sql()
                + " JOIN document_archive_sources AS s ON s.document_id = d.document_id"
                + f" WHERE s.provider = ? AND s.external_id = ?{visibility}",
                (provider, external_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    def reconcile_discovered_origin(
        self,
        *,
        provider: str,
        external_id: str,
        external_version: str | None,
        owner_id: str,
        title: str,
        original_filename: str,
        media_type: str,
        sha256: str,
        size_bytes: int,
        observed_at: str,
    ) -> tuple[DocumentRecord, bool]:
        existing = self.document_for_external_id(
            provider=provider,
            external_id=external_id,
            visible_only=False,
        )
        if existing is not None and existing.owner_id != owner_id:
            raise DocumentStorageError("paperless_origin_owner_conflict")
        created = existing is None
        with self._transaction(immediate=True) as cur:
            if existing is None:
                duplicate = cur.execute(
                    "SELECT document_id FROM documents WHERE owner_id = ? AND sha256 = ?",
                    (owner_id, sha256),
                ).fetchone()
                if duplicate is not None:
                    cur.execute(
                        """
                        INSERT INTO document_provider_snapshots (
                            provider, external_id, external_version, document_id,
                            observed_hash, observed_state, last_seen_at
                        ) VALUES (?, ?, ?, NULL, ?, 'duplicate_conflict', ?)
                        ON CONFLICT(provider, external_id) DO UPDATE SET
                            external_version = excluded.external_version,
                            observed_hash = excluded.observed_hash,
                            observed_state = excluded.observed_state,
                            last_seen_at = excluded.last_seen_at
                        """,
                        (provider, external_id, external_version, sha256, observed_at),
                    )
                    raise DocumentStorageError("paperless_origin_duplicate_conflict")
                document_id = str(uuid4())
                intake_id = str(uuid4())
                source_ref = str(uuid4())
                source_version_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO documents (
                        document_id, owner_id, title, sha256, size_bytes, media_type,
                        state, source_ref, sensitivity, processing_state,
                        active_source_version_id, search_visible, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        document_id,
                        owner_id,
                        title,
                        sha256,
                        int(size_bytes),
                        media_type,
                        DocumentState.READY.value,
                        source_ref,
                        Sensitivity.PRIVATE.value,
                        ProcessingState.NOT_REQUESTED.value,
                        source_version_id,
                        observed_at,
                        observed_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO document_intakes (
                        intake_id, document_id, original_filename, spool_key,
                        ingest_route, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, 'paperless', ?, ?)
                    """,
                    (intake_id, document_id, original_filename, observed_at, observed_at),
                )
                cur.execute(
                    """
                    INSERT INTO document_archive_sources (
                        source_ref, document_id, provider, external_id,
                        verified_sha256, verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (source_ref, document_id, provider, external_id, sha256, observed_at),
                )
                cur.execute(
                    """
                    INSERT INTO document_source_versions (
                        source_version_id, document_id, source_ref, original_sha256,
                        media_type, size_bytes, original_filename, ingest_route,
                        external_version, received_at, archived_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'paperless', ?, ?, ?, ?)
                    """,
                    (
                        source_version_id,
                        document_id,
                        source_ref,
                        sha256,
                        media_type,
                        int(size_bytes),
                        original_filename,
                        external_version,
                        observed_at,
                        observed_at,
                        observed_at,
                    ),
                )
            else:
                document_id = existing.document_id
                source_ref = str(existing.source_ref)
                if existing.sha256 != sha256:
                    source_version_id = str(uuid4())
                    cur.execute(
                        """
                        INSERT INTO document_source_versions (
                            source_version_id, document_id, source_ref, original_sha256,
                            media_type, size_bytes, original_filename, ingest_route,
                            external_version, received_at, archived_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'paperless', ?, ?, ?, ?)
                        """,
                        (
                            source_version_id,
                            document_id,
                            source_ref,
                            sha256,
                            media_type,
                            int(size_bytes),
                            original_filename,
                            external_version,
                            observed_at,
                            observed_at,
                            observed_at,
                        ),
                    )
                    cur.execute(
                        """
                        UPDATE documents
                        SET title = ?, sha256 = ?, size_bytes = ?, media_type = ?,
                            active_source_version_id = ?, active_run_id = NULL,
                            processing_state = ?, search_visible = 1,
                            source_availability = 'available', updated_at = ?
                        WHERE document_id = ?
                        """,
                        (
                            title,
                            sha256,
                            int(size_bytes),
                            media_type,
                            source_version_id,
                            ProcessingState.NOT_REQUESTED.value,
                            observed_at,
                            document_id,
                        ),
                    )
                    cur.execute(
                        """
                        UPDATE document_archive_sources
                        SET verified_sha256 = ?, verified_at = ? WHERE source_ref = ?
                        """,
                        (sha256, observed_at, source_ref),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE documents SET title = ?, search_visible = 1,
                            source_availability = 'available', updated_at = ?
                        WHERE document_id = ?
                        """,
                        (title, observed_at, document_id),
                    )
            cur.execute(
                """
                INSERT INTO document_provider_snapshots (
                    provider, external_id, external_version, document_id,
                    observed_hash, observed_state, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'present', ?)
                ON CONFLICT(provider, external_id) DO UPDATE SET
                    external_version = excluded.external_version,
                    document_id = excluded.document_id,
                    observed_hash = excluded.observed_hash,
                    observed_state = excluded.observed_state,
                    last_seen_at = excluded.last_seen_at
                """,
                (provider, external_id, external_version, document_id, sha256, observed_at),
            )
            row = cur.execute(
                self._select_sql() + " WHERE d.document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Paperless origin reconciliation did not produce a row")
        return self._record(row), created

    def mark_missing_provider_origins(self, *, provider: str, observed_at: str) -> int:
        with self._transaction(immediate=True) as cur:
            rows = cur.execute(
                """
                SELECT document_id FROM document_provider_snapshots
                WHERE provider = ? AND document_id IS NOT NULL
                  AND last_seen_at < ? AND observed_state = 'present'
                """,
                (provider, observed_at),
            ).fetchall()
            document_ids = [str(row["document_id"]) for row in rows]
            for document_id in document_ids:
                cur.execute(
                    """
                    UPDATE documents SET source_availability = 'unavailable',
                        search_visible = 0, updated_at = ? WHERE document_id = ?
                    """,
                    (observed_at, document_id),
                )
            cur.execute(
                """
                UPDATE document_provider_snapshots
                SET observed_state = 'missing'
                WHERE provider = ? AND last_seen_at < ? AND observed_state = 'present'
                """,
                (provider, observed_at),
            )
        return len(document_ids)

    def latest_provider_observation(self, *, provider: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(last_seen_at) AS observed_at FROM document_provider_snapshots WHERE provider = ?",
                (provider,),
            ).fetchone()
        return str(row["observed_at"]) if row is not None and row["observed_at"] else None

    def create_processing_run(
        self,
        *,
        document_id: str,
        route: ProcessingRoute | str,
        parser_name: str,
        parser_version: str,
        parser_image_digest: str | None,
        configuration_sha256: str,
        artifact_schema_version: str = "1",
        resource_lane: str = "cpu",
        fallback_from_run_id: str | None = None,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        record = self.get(document_id)
        if record is None or not record.source_version_id:
            raise DocumentStorageError("document_source_not_ready")
        run_id = str(uuid4())
        observed_at = _now()
        route_value = ProcessingRoute(route).value
        normalized_request_key = str(request_key or "").strip()[:240] or (
            f"automatic:{record.source_version_id}:{route_value}:{configuration_sha256}"
        )
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO document_processing_runs (
                    run_id, document_id, source_version_id, status, route,
                    parser_name, parser_version, parser_image_digest,
                    configuration_sha256, artifact_schema_version, resource_lane,
                    fallback_from_run_id, request_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    run_id,
                    document_id,
                    record.source_version_id,
                    ProcessingState.QUEUED.value,
                    route_value,
                    str(parser_name)[:80],
                    str(parser_version)[:80],
                    str(parser_image_digest)[:160] if parser_image_digest else None,
                    str(configuration_sha256),
                    str(artifact_schema_version)[:40],
                    str(resource_lane)[:40],
                    fallback_from_run_id,
                    normalized_request_key,
                    observed_at,
                    observed_at,
                ),
            )
            row = cur.execute(
                """
                SELECT * FROM document_processing_runs WHERE request_key = ?
                """,
                (normalized_request_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("processing run creation did not produce a row")
            cur.execute(
                """
                UPDATE documents SET processing_state = ?, updated_at = ?
                WHERE document_id = ? AND processing_state != ?
                """,
                (
                    ProcessingState.QUEUED.value,
                    observed_at,
                    document_id,
                    ProcessingState.COMPLETE.value,
                ),
            )
        return dict(row)

    def pending_processing_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM document_processing_runs
                WHERE status = ? ORDER BY created_at LIMIT ?
                """,
                (ProcessingState.QUEUED.value, max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_processing_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_processing_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def begin_processing_run(self, *, run_id: str, fencing_token: int) -> dict[str, Any]:
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE document_processing_runs
                SET status = ?, fencing_token = ?, started_at = COALESCE(started_at, ?),
                    error_code = NULL, updated_at = ?
                WHERE run_id = ? AND status IN (?, ?, ?)
                  AND fencing_token <= ?
                """,
                (
                    ProcessingState.PROCESSING.value,
                    int(fencing_token),
                    observed_at,
                    observed_at,
                    run_id,
                    ProcessingState.QUEUED.value,
                    ProcessingState.PROCESSING.value,
                    ProcessingState.FAILED.value,
                    int(fencing_token),
                ),
            )
            row = cur.execute(
                "SELECT * FROM document_processing_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if int(row["fencing_token"]) != int(fencing_token):
                raise DocumentStorageError("stale_processing_fence")
            cur.execute(
                "UPDATE documents SET processing_state = ?, updated_at = ? WHERE document_id = ?",
                (ProcessingState.PROCESSING.value, observed_at, row["document_id"]),
            )
        return dict(row)

    def set_processing_operation(
        self,
        *,
        run_id: str,
        fencing_token: int,
        operation_ref: str,
    ) -> bool:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE document_processing_runs
                SET provider_operation_ref = ?, updated_at = ?
                WHERE run_id = ? AND fencing_token = ? AND status = ?
                """,
                (
                    str(operation_ref)[:240],
                    _now(),
                    run_id,
                    int(fencing_token),
                    ProcessingState.PROCESSING.value,
                ),
            )
            return int(cur.rowcount or 0) == 1

    def clear_processing_operation(
        self,
        *,
        run_id: str,
        fencing_token: int,
        expected_operation_ref: str,
    ) -> bool:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE document_processing_runs
                SET provider_operation_ref = NULL, updated_at = ?
                WHERE run_id = ? AND fencing_token = ? AND status = ?
                  AND provider_operation_ref = ?
                """,
                (
                    _now(),
                    run_id,
                    int(fencing_token),
                    ProcessingState.PROCESSING.value,
                    str(expected_operation_ref),
                ),
            )
            return int(cur.rowcount or 0) == 1

    def commit_stage(
        self,
        *,
        run_id: str,
        fencing_token: int,
        stage: str,
        stage_version: str,
        result_hash: str,
    ) -> bool:
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            run = cur.execute(
                "SELECT status, fencing_token FROM document_processing_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            if (
                str(run["status"]) != ProcessingState.PROCESSING.value
                or int(run["fencing_token"]) != int(fencing_token)
            ):
                raise DocumentStorageError("stale_processing_fence")
            cur.execute(
                """
                INSERT INTO document_stage_commits (
                    stage_commit_id, run_id, stage, stage_version,
                    fencing_token, result_hash, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage, stage_version) DO NOTHING
                """,
                (
                    str(uuid4()),
                    run_id,
                    str(stage)[:80],
                    str(stage_version)[:40],
                    int(fencing_token),
                    str(result_hash),
                    observed_at,
                ),
            )
            row = cur.execute(
                """
                SELECT fencing_token, result_hash FROM document_stage_commits
                WHERE run_id = ? AND stage = ? AND stage_version = ?
                """,
                (run_id, str(stage)[:80], str(stage_version)[:40]),
            ).fetchone()
        if row is None:
            raise RuntimeError("stage commit did not produce a row")
        if int(row["fencing_token"]) != int(fencing_token) or str(row["result_hash"]) != result_hash:
            raise DocumentStorageError("stage_commit_conflict")
        return True

    def store_artifact(
        self,
        *,
        artifact_id: str,
        document_id: str,
        source_version_id: str,
        run_id: str,
        artifact_kind: ArtifactKind | str,
        storage_key: str,
        sha256: str,
        size_bytes: int,
        schema_version: str,
        sensitivity: Sensitivity | str,
    ) -> dict[str, Any]:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO document_artifacts (
                    artifact_id, document_id, source_version_id, run_id,
                    artifact_kind, storage_key, sha256, size_bytes,
                    schema_version, sensitivity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, artifact_kind, sha256) DO NOTHING
                """,
                (
                    artifact_id,
                    document_id,
                    source_version_id,
                    run_id,
                    ArtifactKind(artifact_kind).value,
                    storage_key,
                    sha256,
                    int(size_bytes),
                    str(schema_version)[:40],
                    Sensitivity(sensitivity).value,
                    _now(),
                ),
            )
            row = cur.execute(
                """
                SELECT * FROM document_artifacts
                WHERE run_id = ? AND artifact_kind = ? AND sha256 = ?
                """,
                (run_id, ArtifactKind(artifact_kind).value, sha256),
            ).fetchone()
        if row is None:
            raise RuntimeError("artifact insert did not produce a row")
        return dict(row)

    def replace_normalized_projection(
        self,
        *,
        run_id: str,
        document_id: str,
        source_version_id: str,
        fencing_token: int,
        pages: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        tables: list[dict[str, Any]],
        sensitivity: Sensitivity | str,
    ) -> None:
        with self._transaction(immediate=True) as cur:
            run = cur.execute(
                "SELECT fencing_token, status FROM document_processing_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if (
                run is None
                or str(run["status"]) != ProcessingState.PROCESSING.value
                or int(run["fencing_token"]) != int(fencing_token)
            ):
                raise DocumentStorageError("stale_processing_fence")
            cur.execute("DELETE FROM document_table_cells WHERE run_id = ?", (run_id,))
            cur.execute("DELETE FROM document_tables WHERE run_id = ?", (run_id,))
            cur.execute("DELETE FROM document_blocks WHERE run_id = ?", (run_id,))
            cur.execute("DELETE FROM document_pages WHERE run_id = ?", (run_id,))
            for page in pages:
                cur.execute(
                    """
                    INSERT INTO document_pages (
                        run_id, page_number, width, height, coordinate_space,
                        rotation_degrees, quality_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        int(page["page_number"]),
                        float(page["width"]),
                        float(page["height"]),
                        str(page["coordinate_space"]),
                        int(page.get("rotation_degrees") or 0),
                        json.dumps(page.get("quality") or {}, separators=(",", ":"), sort_keys=True),
                    ),
                )
            for table in tables:
                table_id = str(table["table_id"])
                cur.execute(
                    """
                    INSERT INTO document_tables (
                        run_id, table_id, document_id, source_version_id,
                        page_number, reading_order, row_count, column_count,
                        bbox_json, provider_ref, sensitivity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        table_id,
                        document_id,
                        source_version_id,
                        int(table["page_number"]),
                        int(table.get("reading_order") or 0),
                        int(table.get("row_count") or 0),
                        int(table.get("column_count") or 0),
                        json.dumps(table.get("bbox")) if table.get("bbox") is not None else None,
                        str(table.get("provider_ref"))[:240] if table.get("provider_ref") else None,
                        Sensitivity(sensitivity).value,
                    ),
                )
                for cell in table.get("cells") or []:
                    text = str(cell.get("text") or "")
                    if len(text) > 20000:
                        raise DocumentStorageError("document_table_cell_too_large")
                    cur.execute(
                        """
                        INSERT INTO document_table_cells (
                            run_id, table_id, cell_id, row_index, column_index,
                            row_span, column_span, literal_text, bbox_json, provider_ref
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            table_id,
                            str(cell["cell_id"]),
                            int(cell["row_index"]),
                            int(cell["column_index"]),
                            max(1, int(cell.get("row_span") or 1)),
                            max(1, int(cell.get("column_span") or 1)),
                            text,
                            json.dumps(cell.get("bbox")) if cell.get("bbox") is not None else None,
                            str(cell.get("provider_ref"))[:240] if cell.get("provider_ref") else None,
                        ),
                    )
            for block in blocks:
                text = str(block.get("text") or "")
                if len(text) > 20000:
                    raise DocumentStorageError("document_block_too_large")
                cur.execute(
                    """
                    INSERT INTO document_blocks (
                        run_id, block_id, document_id, source_version_id,
                        page_number, block_kind, reading_order, literal_text,
                        bbox_json, char_span_json, provider_ref, sensitivity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(block["block_id"]),
                        document_id,
                        source_version_id,
                        int(block["page_number"]),
                        str(block.get("kind") or "other")[:40],
                        int(block.get("reading_order") or 0),
                        text,
                        json.dumps(block.get("bbox")) if block.get("bbox") is not None else None,
                        json.dumps(block.get("char_span")) if block.get("char_span") is not None else None,
                        str(block.get("provider_ref"))[:240] if block.get("provider_ref") else None,
                        Sensitivity(sensitivity).value,
                    ),
                )

    def finish_processing_run(
        self,
        *,
        run_id: str,
        fencing_token: int,
        state: ProcessingState | str,
        error_code: str | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        target = ProcessingState(state)
        if target not in {
            ProcessingState.COMPLETE,
            ProcessingState.NEEDS_REVIEW,
            ProcessingState.PROCESSING_INCOMPLETE,
            ProcessingState.FAILED,
            ProcessingState.CANCELLED,
        }:
            raise ValueError("processing run target is not terminal")
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE document_processing_runs
                SET status = ?, error_code = ?, completed_at = ?, updated_at = ?
                WHERE run_id = ? AND fencing_token = ? AND status = ?
                """,
                (
                    target.value,
                    str(error_code)[:120] if error_code else None,
                    observed_at,
                    observed_at,
                    run_id,
                    int(fencing_token),
                    ProcessingState.PROCESSING.value,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                raise DocumentStorageError("stale_processing_fence")
            row = cur.execute(
                "SELECT * FROM document_processing_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            cur.execute(
                """
                UPDATE documents
                SET processing_state = ?, active_run_id = CASE WHEN ? THEN ? ELSE active_run_id END,
                    updated_at = ?
                WHERE document_id = ?
                """,
                (target.value, 1 if activate else 0, run_id, observed_at, row["document_id"]),
            )
        return dict(row)

    def search_blocks(self, *, owner_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        terms = [item.casefold() for item in str(query or "").split() if item][:8]
        if not terms:
            return []
        clauses = ["LOWER(b.literal_text) LIKE ?" for _ in terms]
        values: list[Any] = [f"%{term}%" for term in terms]
        values.extend([owner_id, ProcessingState.COMPLETE.value, max(1, min(int(limit), 100))])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT b.document_id, b.run_id, b.block_id, b.page_number,
                       b.block_kind, b.literal_text, b.bbox_json, b.char_span_json,
                       d.title, d.sensitivity
                FROM document_blocks AS b
                JOIN documents AS d ON d.document_id = b.document_id
                WHERE ({' OR '.join(clauses)}) AND d.owner_id = ?
                  AND d.search_visible = 1 AND d.processing_state = ?
                  AND d.active_run_id = b.run_id
                ORDER BY b.document_id, b.page_number, b.reading_order
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def evidence_blocks(
        self,
        *,
        document_id: str,
        owner_id: str,
        block_id: str | None = None,
        page_number: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = [
            "b.document_id = ?",
            "d.owner_id = ?",
            "d.search_visible = 1",
            "d.active_run_id = b.run_id",
            "d.processing_state = ?",
        ]
        values: list[Any] = [document_id, owner_id, ProcessingState.COMPLETE.value]
        if block_id:
            clauses.append("b.block_id = ?")
            values.append(str(block_id)[:120])
        if page_number is not None:
            clauses.append("b.page_number = ?")
            values.append(max(1, int(page_number)))
        values.append(max(1, min(int(limit), 100)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT b.run_id, b.block_id, b.page_number, b.block_kind,
                       b.reading_order, b.literal_text, b.bbox_json,
                       b.char_span_json, b.provider_ref, d.title, d.sensitivity
                FROM document_blocks AS b
                JOIN documents AS d ON d.document_id = b.document_id
                WHERE {' AND '.join(clauses)}
                ORDER BY b.page_number, b.reading_order LIMIT ?
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def processing_status(self, *, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT d.processing_state, d.active_run_id, d.active_source_version_id,
                       r.route, r.parser_name, r.parser_version, r.error_code,
                       r.started_at, r.completed_at
                FROM documents AS d
                LEFT JOIN document_processing_runs AS r ON r.run_id = d.active_run_id
                WHERE d.document_id = ?
                """,
                (document_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_metadata_proposal(
        self,
        *,
        document_id: str,
        field_name: str,
        proposed_value: Any,
        sensitivity: Sensitivity | str,
    ) -> dict[str, Any]:
        record = self.get(document_id)
        if record is None or not record.source_version_id:
            raise DocumentStorageError("document_source_not_ready")
        allowed_fields = {"safe_title", "archive_class", "filing_tag"}
        normalized_field = str(field_name or "").strip().casefold()
        if normalized_field not in allowed_fields:
            raise ValueError("unsupported metadata proposal field")
        encoded = json.dumps(proposed_value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if len(encoded) > 1000:
            raise ValueError("metadata proposal is too large")
        value_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        proposal_id = str(uuid4())
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO document_metadata_proposals (
                    proposal_id, document_id, source_version_id, field_name,
                    proposed_value_json, value_hash, sensitivity, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?)
                ON CONFLICT(document_id, source_version_id, field_name, value_hash) DO NOTHING
                """,
                (
                    proposal_id,
                    document_id,
                    record.source_version_id,
                    normalized_field,
                    encoded,
                    value_hash,
                    Sensitivity(sensitivity).value,
                    observed_at,
                    observed_at,
                ),
            )
            row = cur.execute(
                """
                SELECT * FROM document_metadata_proposals
                WHERE document_id = ? AND source_version_id = ?
                  AND field_name = ? AND value_hash = ?
                """,
                (document_id, record.source_version_id, normalized_field, value_hash),
            ).fetchone()
        if row is None:
            raise RuntimeError("metadata proposal did not produce a row")
        value = dict(row)
        value.pop("proposed_value_json", None)
        return value

    def bind_metadata_review(
        self,
        *,
        document_id: str,
        proposal_id: str,
        review_id: str,
    ) -> bool:
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                UPDATE document_metadata_proposals
                SET review_id = ?, updated_at = ?
                WHERE document_id = ? AND proposal_id = ?
                  AND (review_id IS NULL OR review_id = ?)
                """,
                (review_id, _now(), document_id, proposal_id, review_id),
            )
            return int(cur.rowcount or 0) == 1

    def _set_state(
        self,
        *,
        document_id: str,
        state: DocumentState,
        intake_updates: dict[str, Any],
    ) -> DocumentRecord:
        allowed_columns = {"durable_job_id", "archive_task_ref", "failure_code"}
        if not set(intake_updates).issubset(allowed_columns):
            raise ValueError("unsupported intake update")
        observed_at = _now()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                "UPDATE documents SET state = ?, updated_at = ? WHERE document_id = ?",
                (state.value, observed_at, document_id),
            )
            if int(cur.rowcount or 0) != 1:
                raise KeyError(document_id)
            assignments = [f"{column} = ?" for column in intake_updates]
            values = list(intake_updates.values())
            assignments.append("updated_at = ?")
            values.extend([observed_at, document_id])
            cur.execute(
                f"UPDATE document_intakes SET {', '.join(assignments)} WHERE document_id = ?",
                values,
            )
            row = cur.execute(self._select_sql() + " WHERE d.document_id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._record(row)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
