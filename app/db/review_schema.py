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


def ensure_action_approval_schema(conn: sqlite3.Connection) -> None:
    """Create the exact-call pre-action approval extension owned by Human Review."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS action_proposals (
            proposal_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            proposal_hash TEXT NOT NULL,
            root_request_id TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE,
            call_ordinal INTEGER NOT NULL CHECK (call_ordinal >= 1),
            session_id TEXT NOT NULL,
            principal_kind TEXT NOT NULL,
            principal_subject TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            requester_user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            source_interface TEXT NOT NULL,
            channel_scope TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            tool_id TEXT NOT NULL,
            contract_version INTEGER NOT NULL CHECK (contract_version >= 1),
            descriptor_hash TEXT NOT NULL,
            resource_version TEXT NOT NULL,
            authorization_binding TEXT NOT NULL,
            arguments_hash TEXT NOT NULL,
            destination_arguments_json TEXT,
            destination_arguments_hash TEXT NOT NULL,
            effect TEXT NOT NULL,
            effect_cardinality TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            persistence TEXT NOT NULL,
            destination_purpose TEXT NOT NULL,
            approver_principal TEXT NOT NULL,
            safe_action_summary TEXT NOT NULL,
            risk_summary TEXT NOT NULL,
            batch_manifest_json TEXT,
            batch_manifest_hash TEXT,
            transfer_manifest_json TEXT,
            transfer_binding_hash TEXT,
            state TEXT NOT NULL CHECK (
                state IN (
                    'pending', 'approved', 'executing', 'executed', 'rejected',
                    'expired', 'superseded', 'canceled', 'denied', 'failed_terminal'
                )
            ),
            expires_at TEXT NOT NULL,
            notification_guild_id TEXT,
            notification_channel_id TEXT,
            notification_message_id TEXT,
            decision_id TEXT,
            decided_by_principal TEXT,
            decision_guild_id TEXT,
            decision_channel_id TEXT,
            decision_message_id TEXT,
            outcome_guild_id TEXT,
            outcome_channel_id TEXT,
            outcome_message_id TEXT,
            execution_job_id TEXT,
            execution_fencing_token INTEGER,
            action_receipt_ref TEXT,
            terminal_reason_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            terminal_at TEXT,
            FOREIGN KEY (review_id) REFERENCES review_items(review_id),
            FOREIGN KEY (decision_id) REFERENCES review_decisions(decision_id),
            FOREIGN KEY (execution_job_id) REFERENCES durable_jobs(job_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_action_proposals_state_expiry
            ON action_proposals(state, expires_at, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_action_proposals_review
            ON action_proposals(review_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_action_proposals_requester
            ON action_proposals(requester_user_id, channel_scope, created_at DESC)
        """
    )
