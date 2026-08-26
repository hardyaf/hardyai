from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.operations.document_health import document_operational_health


def main() -> int:
    parser = argparse.ArgumentParser(description="Content-free Documents operational health check.")
    parser.add_argument("--core-database", default="data/jarvis_v2.db", type=Path)
    parser.add_argument("--documents-database", type=Path)
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--spool-quota-bytes", type=int, default=2 * 1024 * 1024 * 1024)
    parser.add_argument("--min-free-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--max-spool-age-seconds", type=int, default=3600)
    parser.add_argument("--max-heartbeat-age-seconds", type=int, default=120)
    parser.add_argument("--max-backup-age-seconds", type=int, default=172800)
    args = parser.parse_args()
    storage_root = (args.storage_root or Path(os.environ.get("DOCUMENTS_STORAGE_ROOT", ""))).expanduser()
    if not storage_root.is_absolute() or not storage_root.is_dir():
        raise RuntimeError("an existing absolute --storage-root is required")
    documents_database = args.documents_database or storage_root / "jarvis" / "documents.db"
    result = document_operational_health(
        core_database=args.core_database.expanduser().resolve(),
        documents_database=documents_database.expanduser().resolve(),
        storage_root=storage_root.resolve(),
        spool_quota_bytes=max(1, args.spool_quota_bytes),
        min_free_bytes=max(1, args.min_free_bytes),
        max_spool_age_seconds=max(1, args.max_spool_age_seconds),
        max_heartbeat_age_seconds=max(1, args.max_heartbeat_age_seconds),
        max_backup_age_seconds=max(1, args.max_backup_age_seconds),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
