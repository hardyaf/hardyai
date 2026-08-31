from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.migrations import evaluate_schema_reader_compatibility  # noqa: E402


def _resolve(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _integrity(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def backup(source: Path, destination_dir: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = destination_dir / f"{source.stem}-{stamp}.sqlite3"
    source_conn = sqlite3.connect(str(source))
    target_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    if _integrity(target).lower() != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError("Backup failed SQLite integrity_check")
    return target


def restore(source: Path, target: Path, *, replace: bool) -> Path | None:
    if _integrity(source).lower() != "ok":
        raise RuntimeError("Refusing to restore a backup that fails integrity_check")
    preserved: Path | None = None
    if target.exists():
        if not replace:
            raise RuntimeError("Target exists; pass --replace to perform a guarded restore")
        preserved = target.with_name(
            f"{target.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(target, preserved)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.restore-{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)
    return preserved


def reader_check(source: Path) -> int:
    if not source.is_file() or source.is_symlink():
        print('{"reason":"source_unavailable","result":"incompatible","version":null}')
        return 1
    try:
        conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            decision = evaluate_schema_reader_compatibility(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        print('{"reason":"database_unreadable","result":"incompatible","version":null}')
        return 1
    print(
        json.dumps(
            {
                "version": decision.version,
                "result": decision.result,
                "reason": decision.reason,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0 if decision.compatible else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up, verify, or restore the Jarvis SQLite database.")
    parser.add_argument(
        "--database",
        default=os.getenv("DATABASE_PATH", "./data/jarvis_v2.db"),
        help="Database path (defaults to DATABASE_PATH).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup")
    backup_parser.add_argument("--destination", default="./data/backups")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--source")
    reader_parser = commands.add_parser("reader-check")
    reader_parser.add_argument("--source", required=True)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("source")
    restore_parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    database = _resolve(args.database)

    if args.command == "backup":
        created = backup(database, _resolve(args.destination))
        print(f"backup={created}")
        return 0
    if args.command == "verify":
        result = _integrity(_resolve(args.source) if args.source else database)
        print(f"integrity_check={result}")
        return 0 if result.lower() == "ok" else 1
    if args.command == "reader-check":
        return reader_check(_resolve(args.source))
    preserved = restore(_resolve(args.source), database, replace=bool(args.replace))
    print(f"restored={database}")
    if preserved:
        print(f"previous_database={preserved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
