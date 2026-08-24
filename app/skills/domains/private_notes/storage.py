from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any
from uuid import uuid4

from app.db.connection import open_sqlite_connection
from app.db.domain_schema import ensure_private_notes_schema


class PrivateNotesSQLiteStorage:
    """Domain-owned durable storage for silent notes and their delivery lifecycle."""

    def __init__(self, database_path: str) -> None:
        path, self._conn = open_sqlite_connection(database_path)
        self.database_path = str(path)
        self._lock = threading.RLock()
        ensure_private_notes_schema(self._conn)


    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def capture_note(
        self,
        *,
        external_message_id: str,
        owner_user_id: str,
        guild_id: str,
        channel_id: str,
        author_external_user_id: str,
        author_display_name: str | None,
        note_text: str,
        captured_at: str,
    ) -> dict[str, Any]:
        note_id = str(uuid4())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO private_note_entries (
                    note_id, external_message_id, owner_user_id, guild_id, channel_id,
                    author_external_user_id, author_display_name, note_text, captured_at,
                    digest_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending', ?, ?)
                """,
                (
                    note_id,
                    external_message_id,
                    owner_user_id,
                    guild_id,
                    channel_id,
                    author_external_user_id,
                    author_display_name,
                    note_text,
                    captured_at,
                    captured_at,
                    captured_at,
                ),
            )
            inserted = cur.rowcount == 1
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM private_note_entries WHERE external_message_id = ?",
                (external_message_id,),
            ).fetchone()
        stored = dict(row) if row is not None else {"note_id": note_id}
        stored["note_status"] = stored.pop("status", "pending")
        stored["status"] = "captured" if inserted else "duplicate"
        return stored

    def claim_digest(
        self,
        *,
        digest_id: str,
        owner_user_id: str,
        agent_id: str,
        guild_id: str,
        channel_id: str,
        delivery_channel_id: str,
        local_date: str,
        timezone_name: str,
        scheduled_for: str,
        now: str,
        max_notes: int,
        skip_if_empty: bool,
    ) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            existing = cur.execute(
                "SELECT * FROM private_note_digests WHERE channel_id = ? AND local_date = ?",
                (channel_id, local_date),
            ).fetchone()
            if existing is not None:
                notes = cur.execute(
                    "SELECT * FROM private_note_entries WHERE digest_id = ? ORDER BY captured_at, note_id",
                    (existing["digest_id"],),
                ).fetchall()
                self._conn.commit()
                return {"digest": self._digest_row(existing), "notes": [dict(row) for row in notes]}

            notes = cur.execute(
                """
                SELECT * FROM private_note_entries
                WHERE owner_user_id = ? AND channel_id = ? AND status = 'pending'
                ORDER BY captured_at, note_id
                LIMIT ?
                """,
                (owner_user_id, channel_id, max(1, int(max_notes))),
            ).fetchall()
            status = "preparing" if notes else ("skipped" if skip_if_empty else "ready")
            summary_text = None if notes else f"Evening notes - {local_date}\n\nNo notes were captured today."
            cur.execute(
                """
                INSERT INTO private_note_digests (
                    digest_id, owner_user_id, agent_id, guild_id, channel_id,
                    delivery_channel_id, local_date, timezone_name, scheduled_for,
                    status, note_count, summary_text, discord_message_ids_json,
                    delivery_attempts, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', 0, NULL, ?, ?)
                """,
                (
                    digest_id,
                    owner_user_id,
                    agent_id,
                    guild_id,
                    channel_id,
                    delivery_channel_id,
                    local_date,
                    timezone_name,
                    scheduled_for,
                    status,
                    len(notes),
                    summary_text,
                    now,
                    now,
                ),
            )
            if notes:
                note_ids = [str(row["note_id"]) for row in notes]
                placeholders = ",".join("?" for _ in note_ids)
                cur.execute(
                    f"UPDATE private_note_entries SET digest_id = ?, status = 'claimed', updated_at = ? "
                    f"WHERE note_id IN ({placeholders})",
                    (digest_id, now, *note_ids),
                )
            self._conn.commit()
            digest = self._conn.execute(
                "SELECT * FROM private_note_digests WHERE digest_id = ?",
                (digest_id,),
            ).fetchone()
        return {
            "digest": self._digest_row(digest) if digest is not None else {},
            "notes": [dict(row) for row in notes],
        }

    def save_digest_summary(self, *, digest_id: str, summary_text: str, now: str) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                UPDATE private_note_digests
                SET summary_text = ?, status = 'ready', last_error = NULL, updated_at = ?
                WHERE digest_id = ?
                """,
                (summary_text, now, digest_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM private_note_digests WHERE digest_id = ?",
                (digest_id,),
            ).fetchone()
        return self._digest_row(row) if row is not None else {}

    def record_delivery_part(self, *, digest_id: str, message_id: str, now: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT discord_message_ids_json FROM private_note_digests WHERE digest_id = ?",
                (digest_id,),
            ).fetchone()
            message_ids = self._json_list(row["discord_message_ids_json"] if row is not None else "[]")
            if message_id not in message_ids:
                message_ids.append(message_id)
            self._conn.execute(
                """
                UPDATE private_note_digests
                SET discord_message_ids_json = ?, status = 'delivering', updated_at = ?
                WHERE digest_id = ?
                """,
                (json.dumps(message_ids), now, digest_id),
            )
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM private_note_digests WHERE digest_id = ?",
                (digest_id,),
            ).fetchone()
        return self._digest_row(updated) if updated is not None else {}

    def mark_delivered(self, *, digest_id: str, now: str) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                UPDATE private_note_digests
                SET status = 'delivered', last_error = NULL, updated_at = ?
                WHERE digest_id = ?
                """,
                (now, digest_id),
            )
            cur.execute(
                """
                UPDATE private_note_entries
                SET status = 'digested', updated_at = ?
                WHERE digest_id = ?
                """,
                (now, digest_id),
            )
            self._conn.commit()

    def mark_delivery_failed(
        self,
        *,
        digest_id: str,
        error: str,
        now: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT delivery_attempts FROM private_note_digests WHERE digest_id = ?",
                (digest_id,),
            ).fetchone()
            attempts = int(row["delivery_attempts"] if row is not None else 0) + 1
            status = "dead_letter" if attempts >= max(1, int(max_attempts)) else "failed"
            cur.execute(
                """
                UPDATE private_note_digests
                SET delivery_attempts = ?, status = ?, last_error = ?, updated_at = ?
                WHERE digest_id = ?
                """,
                (attempts, status, error[:500], now, digest_id),
            )
            if status == "dead_letter":
                cur.execute(
                    """
                    UPDATE private_note_entries
                    SET digest_id = NULL, status = 'pending', updated_at = ?
                    WHERE digest_id = ?
                    """,
                    (now, digest_id),
                )
            self._conn.commit()
            updated = cur.execute(
                "SELECT * FROM private_note_digests WHERE digest_id = ?",
                (digest_id,),
            ).fetchone()
        return self._digest_row(updated) if updated is not None else {}

    def pending_note_count(self, *, owner_user_id: str, channel_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS count FROM private_note_entries
                WHERE owner_user_id = ? AND channel_id = ? AND status = 'pending'
                """,
                (owner_user_id, channel_id),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def purge_digested_notes(
        self,
        *,
        owner_user_id: str,
        channel_id: str,
        captured_before: str,
    ) -> int:
        """Delete only raw notes already covered by a successfully delivered digest."""
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM private_note_entries
                WHERE owner_user_id = ? AND channel_id = ?
                  AND status = 'digested' AND captured_at < ?
                """,
                (owner_user_id, channel_id, captured_before),
            )
            deleted = max(0, int(cur.rowcount))
            self._conn.commit()
        return deleted

    def get_digest(self, *, channel_id: str, local_date: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM private_note_digests WHERE channel_id = ? AND local_date = ?",
                (channel_id, local_date),
            ).fetchone()
        return self._digest_row(row) if row is not None else None

    @staticmethod
    def _json_list(raw: Any) -> list[str]:
        try:
            parsed = json.loads(str(raw or "[]"))
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    @classmethod
    def _digest_row(cls, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        value = dict(row)
        value["discord_message_ids"] = cls._json_list(value.pop("discord_message_ids_json", "[]"))
        return value
