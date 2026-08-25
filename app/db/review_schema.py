from __future__ import annotations

import sqlite3


def ensure_review_schema(conn: sqlite3.Connection) -> None:
    """Create the provider-neutral durable human-review authority."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_items (
            review_id TEXT PRIMARY KEY,
            review_kind TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            subject_version TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            source_ref TEXT,
            sensitivity TEXT NOT NULL,
            confidence REAL,
            validator_summary_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            target_operation TEXT,
            authorization_binding TEXT,
            state TEXT NOT NULL,
            expires_at TEXT,
            superseded_by_review_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(review_kind, subject_type, subject_id, subject_version, item_hash),
            FOREIGN KEY (superseded_by_review_id) REFERENCES review_items(review_id)
        );

        CREATE TABLE IF NOT EXISTS review_decisions (
            decision_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            actor_principal TEXT NOT NULL,
            reason TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            bound_item_hash TEXT NOT NULL,
            edited_value_ref TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            applied_at TEXT,
            action_receipt_ref TEXT,
            FOREIGN KEY (review_id) REFERENCES review_items(review_id)
        );

        CREATE INDEX IF NOT EXISTS idx_review_items_state_created
            ON review_items(state, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_review_items_subject
            ON review_items(subject_type, subject_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_review_decisions_review
            ON review_decisions(review_id, decided_at DESC);
        """
    )
