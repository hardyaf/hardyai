from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.connection import (  # noqa: E402
    open_readonly_sqlite_connection,
    open_sqlite_connection,
)


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _table_exists(connection: Any, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _legacy_audit(database_path: str) -> dict[str, Any]:
    _, connection = open_readonly_sqlite_connection(database_path)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if not _table_exists(connection, "email_label_operations"):
            return {
                "integrity_check": integrity,
                "legacy_table_present": False,
                "counts": {},
                "oldest_queued_at": None,
                "active_claim_count": 0,
            }
        counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT status, COUNT(*) FROM email_label_operations GROUP BY status ORDER BY status"
            ).fetchall()
        }
        oldest = connection.execute(
            "SELECT MIN(created_at) FROM email_label_operations WHERE status='queued'"
        ).fetchone()[0]
        return {
            "integrity_check": integrity,
            "legacy_table_present": True,
            "counts": counts,
            "oldest_queued_at": str(oldest) if oldest else None,
            "active_claim_count": counts.get("claimed", 0),
        }
    finally:
        connection.close()


def _verify_backup(backup_path: str) -> str:
    resolved, connection = open_readonly_sqlite_connection(backup_path)
    try:
        result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if result.casefold() != "ok":
            raise RuntimeError("Verified backup failed SQLite integrity_check.")
        return resolved.name
    finally:
        connection.close()


def _legacy_quarantine(
    database_path: str,
    *,
    apply: bool,
    verified_backup: str | None,
) -> dict[str, Any]:
    before = _legacy_audit(database_path)
    queued_count = int(before.get("counts", {}).get("queued", 0))
    if not apply:
        return {
            "mode": "dry_run",
            "eligible_count": queued_count,
            "active_claim_count": int(before.get("active_claim_count", 0)),
            "would_call_provider": False,
        }
    if not verified_backup:
        raise RuntimeError("--verified-backup is required with --apply.")
    backup_name = _verify_backup(verified_backup)
    if int(before.get("active_claim_count", 0)) != 0:
        raise RuntimeError("Legacy quarantine requires zero active label claims.")

    _, connection = open_sqlite_connection(database_path)
    now = _iso_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        updated = connection.execute(
            """
            UPDATE email_label_operations
            SET status='cancelled', lease_owner=NULL, lease_expires_at=NULL,
                last_error_code='legacy_automatic_reconciliation_quarantined',
                updated_at=?, completed_at=?
            WHERE status='queued'
            """,
            (now, now),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    after = _legacy_audit(database_path)
    return {
        "mode": "apply",
        "backup_verified": True,
        "backup_name": backup_name,
        "cancelled_count": int(updated.rowcount),
        "remaining_queued_count": int(after.get("counts", {}).get("queued", 0)),
        "active_claim_count": int(after.get("active_claim_count", 0)),
        "would_call_provider": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit and manage durable Email operations.")
    parser.add_argument("--database", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("legacy-audit")
    quarantine = commands.add_parser("legacy-quarantine")
    mode = quarantine.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    quarantine.add_argument("--verified-backup")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "legacy-audit":
        result = _legacy_audit(args.database)
    else:
        result = _legacy_quarantine(
            args.database,
            apply=bool(args.apply),
            verified_backup=args.verified_backup,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
