from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from threading import RLock
from typing import Any, ContextManager
from uuid import uuid4

from app.db.connection import open_sqlite_connection
from app.db.migrations import initialize_schema
from app.db.transaction import sqlite_transaction


_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class ProvenanceRepository:
    def __init__(
        self,
        database_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        lock: RLock | None = None,
    ) -> None:
        if connection is None:
            if not database_path:
                raise ValueError("database_path is required when connection is not supplied")
            self._database_path, self._conn = open_sqlite_connection(database_path)
            self._owns_connection = True
            initialize_schema(self._conn)
        else:
            self._database_path = None
            self._conn = connection
            self._owns_connection = False
        self._lock = lock or RLock()

    def _transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Cursor]:
        return sqlite_transaction(conn=self._conn, lock=self._lock, immediate=immediate)

    def create(
        self,
        *,
        source_domain: str,
        source_type: str,
        source_ref: str,
        source_version: str,
        source_hash: str,
        target_domain: str,
        target_type: str,
        target_ref: str,
        link_kind: str,
        operation_id: str,
    ) -> dict[str, Any]:
        tokens = {
            "source_domain": source_domain,
            "source_type": source_type,
            "target_domain": target_domain,
            "target_type": target_type,
            "link_kind": link_kind,
        }
        normalized = {name: str(value or "").strip().casefold() for name, value in tokens.items()}
        if not all(_TOKEN.fullmatch(value) for value in normalized.values()):
            raise ValueError("provenance type token is invalid")
        bounded = {
            "source_ref": str(source_ref or "").strip()[:240],
            "source_version": str(source_version or "").strip()[:160],
            "target_ref": str(target_ref or "").strip()[:240],
            "operation_id": str(operation_id or "").strip()[:200],
        }
        if not all(bounded.values()) or not _HASH.fullmatch(str(source_hash or "").strip().casefold()):
            raise ValueError("provenance reference is incomplete")
        provenance_id = str(uuid4())
        observed_at = datetime.now(UTC).isoformat()
        with self._transaction(immediate=True) as cur:
            cur.execute(
                """
                INSERT INTO provenance_links (
                    provenance_id, source_domain, source_type, source_ref,
                    source_version, source_hash, target_domain, target_type,
                    target_ref, link_kind, operation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO NOTHING
                """,
                (
                    provenance_id,
                    normalized["source_domain"],
                    normalized["source_type"],
                    bounded["source_ref"],
                    bounded["source_version"],
                    str(source_hash).strip().casefold(),
                    normalized["target_domain"],
                    normalized["target_type"],
                    bounded["target_ref"],
                    normalized["link_kind"],
                    bounded["operation_id"],
                    observed_at,
                ),
            )
            row = cur.execute(
                "SELECT * FROM provenance_links WHERE operation_id = ?",
                (bounded["operation_id"],),
            ).fetchone()
        if row is None:
            raise RuntimeError("provenance insert did not produce a row")
        value = dict(row)
        if (
            value["source_hash"] != str(source_hash).strip().casefold()
            or value["target_ref"] != bounded["target_ref"]
        ):
            raise ValueError("provenance operation payload changed")
        return value

    def for_target(
        self,
        *,
        target_domain: str,
        target_type: str,
        target_ref: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM provenance_links
                WHERE target_domain = ? AND target_type = ? AND target_ref = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (
                    str(target_domain).strip().casefold(),
                    str(target_type).strip().casefold(),
                    str(target_ref).strip(),
                    max(1, min(int(limit), 100)),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        if self._owns_connection:
            with self._lock:
                self._conn.close()
