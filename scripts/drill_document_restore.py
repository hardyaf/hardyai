#!/usr/bin/env python3
"""Restore a document backup into isolated encrypted drill volumes and verify it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

import yaml

from manage_document_backup import _contained, _inspect_tar, _validated_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, stdin=None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        stdin=stdin,
        text=stdin is None,
        capture_output=capture,
    )


def _extract(archive_path: Path, destination: Path) -> None:
    _inspect_tar(archive_path)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:*") as archive:
        archive.extractall(destination, filter="data")


def _quick_check(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not row or row[0] != "ok":
        raise RuntimeError(f"SQLite quick_check failed for {path.name}")


def _verify_artifacts(database: Path, artifact_root: Path) -> int:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT storage_key, sha256, size_bytes FROM document_artifacts"
        ).fetchall()
    finally:
        connection.close()
    root = artifact_root.resolve()
    for storage_key, expected_sha256, expected_size in rows:
        candidate = (root / str(storage_key)).resolve()
        if root not in candidate.parents or candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("restored artifact path is invalid or missing")
        if candidate.stat().st_size != int(expected_size):
            raise RuntimeError("restored artifact size mismatch")
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != str(expected_sha256):
            raise RuntimeError("restored artifact hash mismatch")
    return len(rows)


def _wait_exec(container: str, command: list[str], timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, *command],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for restore-drill container {container}")


def _assert_resource_absent(kind: str, name: str) -> None:
    result = subprocess.run(
        ["docker", kind, "inspect", name],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        raise RuntimeError(f"refusing to reuse existing Docker {kind}: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generation_path", type=Path)
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--drill-name", required=True)
    parser.add_argument("--secrets-root", default="/etc/hardyai/documents", type=Path)
    parser.add_argument("--owner-uid", type=int, default=getattr(os, "getuid", lambda: 1001)())
    parser.add_argument("--owner-gid", type=int, default=getattr(os, "getgid", lambda: 1001)())
    parser.add_argument("--backend-uid", type=int, default=999)
    args = parser.parse_args()

    if not args.drill_name.replace("-", "").replace("_", "").isalnum():
        raise RuntimeError("--drill-name may contain only letters, numbers, '-' and '_'")
    storage_root = args.storage_root.expanduser().resolve()
    generation = _contained(args.generation_path, storage_root)
    manifest = _validated_manifest(generation)
    destination = _contained(storage_root / "restore-drills" / args.drill_name, storage_root)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)

    shutil.copy2(generation / "core.db", destination / "core.db")
    drill_jarvis = destination / "jarvis"
    drill_jarvis.mkdir(mode=0o700)
    shutil.copy2(generation / "documents.db", drill_jarvis / "documents.db")
    os.chmod(destination / "core.db", 0o600)
    os.chmod(drill_jarvis / "documents.db", 0o600)
    _quick_check(destination / "core.db")
    _quick_check(drill_jarvis / "documents.db")
    _extract(generation / "paperless-data.tar", destination)
    _extract(generation / "paperless-media.tar", destination)
    _extract(generation / "paperless-export.tar", destination / "export")
    _extract(generation / "accepted-spool.tar", drill_jarvis)
    restored_artifact_count = 0
    if manifest.get("schema_version") == 2:
        _extract(generation / "jarvis-artifacts.tar", drill_jarvis)
        restored_artifact_count = _verify_artifacts(
            drill_jarvis / "documents.db",
            drill_jarvis / "artifacts",
        )
    postgres_root = destination / "postgres"
    valkey_root = destination / "valkey"
    postgres_root.mkdir(mode=0o700)
    valkey_root.mkdir(mode=0o700)

    compose = yaml.safe_load((REPO_ROOT / "deploy/docker/compose.yaml").read_text(encoding="utf-8"))
    postgres_image = str(compose["services"]["paperless-db"]["image"])
    valkey_image = str(compose["services"]["paperless-broker"]["image"])
    paperless_image = str(compose["services"]["paperless-webserver"]["image"])
    helper_image = str(compose["services"]["jarvis"]["image"])
    secret_root = args.secrets_root.expanduser().resolve()
    for name in ("paperless_db_password", "paperless_secret_key", "paperless_read_token"):
        if not (secret_root / name).is_file():
            raise RuntimeError(f"restore-drill secret is missing: {name}")

    tag = args.drill_name[-24:].replace("_", "-").casefold()
    network = f"hardyai-restore-{tag}"
    database_container = f"hardyai-restore-{tag}-db"
    broker_container = f"hardyai-restore-{tag}-broker"
    web_container = f"hardyai-restore-{tag}-web"
    for container in (database_container, broker_container, web_container):
        _assert_resource_absent("container", container)
    _assert_resource_absent("network", network)

    _run(
        [
            "docker", "run", "--rm", "--network", "none", "--user", "0",
            "-v", f"{destination}:/drill", helper_image,
            "chown", "-R", f"{args.backend_uid}:{args.owner_gid}", "/drill/postgres", "/drill/valkey",
        ]
    )
    _run(
        [
            "docker", "run", "--rm", "--network", "none", "--user", "0",
            "-v", f"{destination}:/drill", helper_image,
            "chown", "-R", f"{args.owner_uid}:{args.owner_gid}",
            "/drill/data", "/drill/media", "/drill/export", "/drill/jarvis",
        ]
    )

    created_containers: list[str] = []
    network_created = False
    try:
        _run(["docker", "network", "create", "--internal", network])
        network_created = True
        _run(
            [
                "docker", "run", "-d", "--name", database_container, "--network", network,
                "--user", f"{args.backend_uid}:{args.owner_gid}", "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
                "--tmpfs", "/run/postgresql:rw,noexec,nosuid,nodev",
                "--shm-size", "256m", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "-e", "POSTGRES_DB=paperless", "-e", "POSTGRES_USER=paperless",
                "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/paperless_db_password",
                "-v", f"{postgres_root}:/var/lib/postgresql",
                "-v", f"{secret_root / 'paperless_db_password'}:/run/secrets/paperless_db_password:ro",
                postgres_image,
            ]
        )
        created_containers.append(database_container)
        _wait_exec(database_container, ["pg_isready", "-U", "paperless", "-d", "paperless"])
        with (generation / "paperless-postgres.dump").open("rb") as dump:
            _run(
                [
                    "docker", "exec", "-i", database_container, "pg_restore",
                    "-U", "paperless", "-d", "paperless", "--exit-on-error",
                    "--no-owner", "--no-privileges",
                ],
                stdin=dump,
            )
        count_result = _run(
            [
                "docker", "exec", database_container, "psql", "-U", "paperless", "-d", "paperless",
                "-At", "-c", "SELECT count(*) FROM documents_document;",
            ],
            capture=True,
        )
        document_count = int(count_result.stdout.strip())
        if document_count <= 0:
            raise RuntimeError("restored PostgreSQL contains no documents")

        _run(
            [
                "docker", "run", "-d", "--name", broker_container, "--network", network,
                "--user", f"{args.backend_uid}:{args.owner_gid}", "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "-v", f"{valkey_root}:/data",
                valkey_image, "valkey-server", "--appendonly", "yes", "--appendfsync", "everysec",
            ]
        )
        created_containers.append(broker_container)
        _wait_exec(broker_container, ["valkey-cli", "ping"])

        _run(
            [
                "docker", "run", "-d", "--name", web_container, "--network", network,
                "--user", f"{args.owner_uid}:{args.owner_gid}", "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,nodev",
                "--tmpfs", f"/run:exec,mode=0755,uid={args.owner_uid},gid={args.owner_gid}",
                "--tmpfs", f"/usr/src/paperless/consume:mode=0700,uid={args.owner_uid},gid={args.owner_gid}",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "-e", f"PAPERLESS_REDIS=redis://{broker_container}:6379",
                "-e", f"PAPERLESS_DBHOST={database_container}", "-e", "PAPERLESS_DBENGINE=postgresql",
                "-e", "PAPERLESS_DBNAME=paperless", "-e", "PAPERLESS_DBUSER=paperless",
                "-e", "PAPERLESS_DBPASS_FILE=/run/secrets/paperless_db_password",
                "-e", "PAPERLESS_SECRET_KEY_FILE=/run/secrets/paperless_secret_key",
                "-e", "PAPERLESS_TIME_ZONE=America/New_York", "-e", "PAPERLESS_OCR_LANGUAGE=eng",
                "-e", "PAPERLESS_OCR_MODE=auto", "-e", "PAPERLESS_CONSUMER_DELETE_DUPLICATES=true",
                "-e", f"PAPERLESS_URL=http://{web_container}:8000",
                "-v", f"{destination / 'data'}:/usr/src/paperless/data",
                "-v", f"{destination / 'media'}:/usr/src/paperless/media",
                "-v", f"{destination / 'export'}:/usr/src/paperless/export",
                "-v", f"{drill_jarvis}:/restore:ro",
                "-v", f"{secret_root / 'paperless_db_password'}:/run/secrets/paperless_db_password:ro",
                "-v", f"{secret_root / 'paperless_secret_key'}:/run/secrets/paperless_secret_key:ro",
                "-v", f"{secret_root / 'paperless_read_token'}:/run/secrets/paperless_read_token:ro",
                paperless_image,
            ]
        )
        created_containers.append(web_container)
        _wait_exec(
            web_container,
            [
                "/command/with-contenv", "python", "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).read(1)",
            ],
            timeout_seconds=180,
        )
        verifier = REPO_ROOT / "scripts/verify_restored_document_archive.py"
        with verifier.open("rb") as source:
            validation = subprocess.run(
                [
                    "docker", "exec", "-i", web_container, "/command/with-contenv", "python", "-",
                    "--database", "/restore/documents.db",
                    "--token-file", "/run/secrets/paperless_read_token",
                ],
                cwd=REPO_ROOT,
                check=False,
                stdin=source,
                capture_output=True,
            )
        if validation.returncode != 0:
            detail = validation.stderr.decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"restored archive verification failed: {detail}")
        validation_result = json.loads(validation.stdout)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "destination": str(destination),
                    "generation": manifest.get("generation"),
                    "source_revision": manifest.get("source_revision"),
                    "postgres_document_count": document_count,
                    "verified_artifacts": restored_artifact_count,
                    "archive": validation_result,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        for container in reversed(created_containers):
            subprocess.run(
                ["docker", "rm", "-f", container],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if network_created:
            subprocess.run(
                ["docker", "network", "rm", network],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    raise SystemExit(main())
