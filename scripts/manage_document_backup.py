from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_BACKUP_FILES = {
    "accepted-spool.tar",
    "core.db",
    "documents.db",
    "paperless-data.tar",
    "paperless-export.tar",
    "paperless-media.tar",
    "paperless-postgres.dump",
}
_PHASE3_BACKUP_FILES = {"jarvis-artifacts.tar"}
_TAR_BACKUP_FILES = {
    "accepted-spool.tar",
    "jarvis-artifacts.tar",
    "paperless-data.tar",
    "paperless-export.tar",
    "paperless-media.tar",
}


def _contained(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.relative_to(root.expanduser().resolve())
    return resolved


def _run(command: list[str], *, stdout=None) -> None:
    subprocess.run(command, cwd=REPO_ROOT, check=True, stdout=stdout)


def _compose_prefix(*, compose_file: Path, env_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
        "--profile",
        "documents",
    ]


def _sqlite_backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
        row = destination_connection.execute("PRAGMA quick_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"SQLite quick_check failed for {destination}")
    finally:
        destination_connection.close()
        source_connection.close()


def _tar_directory(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"backup source contains a symlink: {path}")
    with tarfile.open(destination, mode="w") as archive:
        archive.add(source, arcname=source.name, recursive=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_tar(path: Path) -> None:
    with tarfile.open(path, mode="r:*") as archive:
        for member in archive.getmembers():
            normalized = member.name.replace("\\", "/")
            parts = [part for part in normalized.split("/") if part not in {"", "."}]
            if (
                normalized.startswith("/")
                or re.match(r"^[A-Za-z]:", normalized)
                or ".." in parts
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                raise RuntimeError(f"unsafe backup archive member in {path.name}")


def _validated_manifest(generation: Path) -> dict[str, Any]:
    manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, 2}:
        raise RuntimeError("unsupported backup manifest")
    files = manifest.get("files")
    required_files = set(_REQUIRED_BACKUP_FILES)
    if manifest.get("schema_version") == 2:
        required_files.update(_PHASE3_BACKUP_FILES)
    if not isinstance(files, dict) or not required_files.issubset(files):
        raise RuntimeError("backup manifest is missing required artifacts")
    for name, expected in files.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not re.fullmatch(r"[A-Za-z0-9._-]+", name)
            or not isinstance(expected, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(expected.get("sha256") or ""))
            or not isinstance(expected.get("size_bytes"), int)
            or expected["size_bytes"] < 0
        ):
            raise RuntimeError("backup manifest contains an invalid artifact entry")
    return manifest


def create_backup(args: argparse.Namespace) -> int:
    requested_storage_root = Path(args.storage_root).expanduser()
    if not requested_storage_root.is_absolute():
        raise RuntimeError("--storage-root must be an existing absolute directory")
    storage_root = requested_storage_root.resolve()
    if not storage_root.is_dir():
        raise RuntimeError("--storage-root must be an existing absolute directory")
    backup_root = _contained(Path(args.backup_root), storage_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    generation = str(args.generation).strip()
    if not generation or not generation.replace("-", "").replace("_", "").isalnum():
        raise RuntimeError("--generation may contain only letters, numbers, '-' and '_'")
    destination = backup_root / generation
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)

    compose_file = Path(args.compose_file).expanduser().resolve()
    env_file = Path(args.env_file).expanduser().resolve()
    compose = _compose_prefix(compose_file=compose_file, env_file=env_file)
    export_host = storage_root / "paperless" / "export" / generation
    if export_host.exists():
        raise RuntimeError(f"Paperless export generation already exists: {export_host}")
    export_host.mkdir(mode=0o700, parents=False, exist_ok=False)

    # Export while Paperless is healthy, then stop all writers for the cross-store barrier.
    _run(
        compose
        + [
            "exec",
            "-T",
            "paperless-webserver",
            "document_exporter",
            f"/usr/src/paperless/export/{generation}",
        ]
    )
    stopped = False
    try:
        _run(
            compose
            + [
                "stop",
                "document-gateway",
                "document-worker",
                "paperless-webserver",
                "jarvis",
            ]
        )
        stopped = True
        _sqlite_backup(Path(args.core_database).expanduser().resolve(), destination / "core.db")
        _sqlite_backup(storage_root / "jarvis" / "documents.db", destination / "documents.db")
        with (destination / "paperless-postgres.dump").open("wb") as dump:
            _run(
                compose
                + [
                    "exec",
                    "-T",
                    "paperless-db",
                    "pg_dump",
                    "-U",
                    "paperless",
                    "-d",
                    "paperless",
                    "--format=custom",
                ],
                stdout=dump,
            )
        for source, name in (
            (storage_root / "paperless" / "data", "paperless-data.tar"),
            (storage_root / "paperless" / "media", "paperless-media.tar"),
            (export_host, "paperless-export.tar"),
            (storage_root / "jarvis" / "spool", "accepted-spool.tar"),
            (storage_root / "jarvis" / "artifacts", "jarvis-artifacts.tar"),
        ):
            _tar_directory(source, destination / name)
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "generation": generation,
            "created_at": datetime.now(UTC).isoformat(),
            "source_revision": args.source_revision,
            "paperless_server_version": os.getenv("PAPERLESS_SERVER_VERSION", "3.0.5"),
            "paperless_api_version": os.getenv("PAPERLESS_API_VERSION", "10"),
            "files": {},
        }
        for path in sorted(destination.iterdir()):
            if path.is_file() and path.name != "manifest.json":
                os.chmod(path, 0o600)
                manifest["files"][path.name] = {
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(destination / "manifest.json", 0o600)
        print(json.dumps({"status": "ok", "generation": generation, "path": str(destination)}))
    finally:
        if stopped and not args.leave_stopped:
            _run(
                compose
                + [
                    "up",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                    "jarvis",
                    "paperless-webserver",
                    "document-worker",
                    "document-gateway",
                ]
            )
    return 0


def verify_backup(args: argparse.Namespace) -> int:
    generation = Path(args.generation_path).expanduser().resolve()
    try:
        manifest = _validated_manifest(generation)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError):
        print(json.dumps({"status": "failed", "failures": ["manifest.json"]}))
        return 1
    failures: list[str] = []
    for name, expected in dict(manifest.get("files") or {}).items():
        path = generation / name
        if (
            not path.is_file()
            or path.stat().st_size != expected.get("size_bytes")
            or _sha256(path) != expected.get("sha256")
        ):
            failures.append(name)
    for name in ("core.db", "documents.db"):
        try:
            connection = sqlite3.connect(f"file:{(generation / name).as_posix()}?mode=ro", uri=True)
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
            if not row or row[0] != "ok":
                failures.append(name)
        except sqlite3.Error:
            failures.append(name)
    postgres_dump = generation / "paperless-postgres.dump"
    try:
        with postgres_dump.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                failures.append(postgres_dump.name)
    except OSError:
        failures.append(postgres_dump.name)
    tar_backup_files = set(_TAR_BACKUP_FILES)
    if manifest.get("schema_version") == 1:
        tar_backup_files.difference_update(_PHASE3_BACKUP_FILES)
    for name in tar_backup_files:
        try:
            _inspect_tar(generation / name)
        except (OSError, tarfile.TarError, RuntimeError):
            failures.append(name)
    print(json.dumps({"status": "failed" if failures else "ok", "failures": sorted(set(failures))}))
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or verify a coordinated document backup.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--storage-root", required=True)
    backup.add_argument("--backup-root", required=True, help="must remain inside the encrypted storage root")
    backup.add_argument("--generation", required=True)
    backup.add_argument("--source-revision", required=True)
    backup.add_argument("--core-database", default="data/jarvis_v2.db")
    backup.add_argument("--compose-file", default="deploy/docker/compose.yaml")
    backup.add_argument("--env-file", default=".env")
    backup.add_argument("--leave-stopped", action="store_true")
    backup.set_defaults(handler=create_backup)
    verify = commands.add_parser("verify")
    verify.add_argument("generation_path")
    verify.set_defaults(handler=verify_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
