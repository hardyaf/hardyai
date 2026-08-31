from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import tarfile
from argparse import Namespace
from pathlib import Path

from scripts.manage_document_backup import (
    _sha256,
    _sqlite_backup,
    _tar_directory,
    reader_check,
    verify_backup,
)


REQUIRED_FILES = {
    "accepted-spool.tar",
    "core.db",
    "documents.db",
    "paperless-data.tar",
    "paperless-export.tar",
    "paperless-media.tar",
    "paperless-postgres.dump",
    "jarvis-artifacts.tar",
}


def _sqlite(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE canary (value TEXT NOT NULL)")
        connection.execute("INSERT INTO canary VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _manifest(generation: Path, *, schema_version: int = 2) -> None:
    files = {
        path.name: {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
        for path in generation.iterdir()
        if path.is_file() and path.name != "manifest.json"
    }
    (generation / "manifest.json").write_text(
        json.dumps({"schema_version": schema_version, "files": files}),
        encoding="utf-8",
    )


def _valid_generation(tmp_path: Path) -> Path:
    generation = tmp_path / "generation"
    generation.mkdir()
    source = tmp_path / "source.db"
    _sqlite(source, "original")
    _sqlite_backup(source, generation / "core.db")
    _sqlite_backup(source, generation / "documents.db")
    (generation / "paperless-postgres.dump").write_bytes(b"PGDMP\x01test")
    source_dir = tmp_path / "source-dir"
    source_dir.mkdir()
    (source_dir / "canary.txt").write_text("canary", encoding="utf-8")
    for name in REQUIRED_FILES:
        if name.endswith(".tar"):
            _tar_directory(source_dir, generation / name)
    _manifest(generation)
    return generation


def test_backup_verifier_accepts_complete_generation_and_detects_tampering(tmp_path, capsys) -> None:
    generation = _valid_generation(tmp_path)
    args = Namespace(generation_path=str(generation))

    assert verify_backup(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    with (generation / "paperless-media.tar").open("ab") as stream:
        stream.write(b"tampered")
    assert verify_backup(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert "paperless-media.tar" in output["failures"]


def test_backup_verifier_rejects_unsafe_tar_members(tmp_path, capsys) -> None:
    generation = _valid_generation(tmp_path)
    unsafe = generation / "accepted-spool.tar"
    with tarfile.open(unsafe, mode="w") as archive:
        member = tarfile.TarInfo("../outside.txt")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"nope"))
    _manifest(generation)

    assert verify_backup(Namespace(generation_path=str(generation))) == 1
    output = json.loads(capsys.readouterr().out)
    assert "accepted-spool.tar" in output["failures"]


def test_backup_verifier_rejects_manifest_path_traversal(tmp_path, capsys) -> None:
    generation = _valid_generation(tmp_path)
    manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["../outside"] = {"sha256": "0" * 64, "size_bytes": 0}
    (generation / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert verify_backup(Namespace(generation_path=str(generation))) == 1
    assert json.loads(capsys.readouterr().out)["failures"] == ["manifest.json"]


def test_backup_verifier_accepts_legacy_phase1_generation_without_artifacts(tmp_path, capsys) -> None:
    generation = _valid_generation(tmp_path)
    (generation / "jarvis-artifacts.tar").unlink()
    _manifest(generation, schema_version=1)

    assert verify_backup(Namespace(generation_path=str(generation))) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_document_reader_check_accepts_version_14_without_mutation(tmp_path, capsys) -> None:
    source = tmp_path / "documents.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE canary (value TEXT NOT NULL)")
        connection.execute("INSERT INTO canary VALUES ('unchanged')")
        connection.execute("PRAGMA user_version = 14")
        connection.commit()
    finally:
        connection.close()
    before = source.stat().st_mtime_ns

    assert reader_check(Namespace(source=str(source))) == 0
    assert json.loads(capsys.readouterr().out) == {
        "reason": "schema_not_newer",
        "result": "compatible",
        "version": 14,
    }
    assert source.stat().st_mtime_ns == before
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/manage_document_backup.py",
            "reader-check",
            "--source",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "reason": "schema_not_newer",
        "result": "compatible",
        "version": 14,
    }


def test_document_reader_check_rejects_newer_schema(tmp_path, capsys) -> None:
    source = tmp_path / "documents-newer.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA user_version = 15")
        connection.commit()
    finally:
        connection.close()

    assert reader_check(Namespace(source=str(source))) == 1
    assert json.loads(capsys.readouterr().out) == {
        "reason": "schema_newer",
        "result": "incompatible",
        "version": 15,
    }
