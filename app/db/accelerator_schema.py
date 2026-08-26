from __future__ import annotations

import sqlite3


ACCELERATOR_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accelerator_resources (
    resource_id TEXT PRIMARY KEY,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_lane TEXT,
    lease_priority INTEGER,
    lease_expires_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accelerator_waiters (
    waiter_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    lane TEXT NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    acquired_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_accelerator_waiters_claim
    ON accelerator_waiters(resource_id, status, priority DESC, created_at, waiter_id);
CREATE INDEX IF NOT EXISTS idx_accelerator_waiters_expiry
    ON accelerator_waiters(status, expires_at);
"""


def ensure_accelerator_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(ACCELERATOR_SCHEMA_SQL)
