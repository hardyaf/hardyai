#!/usr/bin/env python3
"""Evaluate content-free Phase 3 result metadata against an external sealed corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_HASH = set("0123456789abcdef")
_FORBIDDEN_RESULT_KEYS = {"text", "literal_text", "snippet", "markdown", "content", "source_bytes"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _validate_manifest(manifest: dict[str, Any], corpus_root: Path) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != "1" or not isinstance(manifest.get("cases"), list):
        raise ValueError("unsupported benchmark manifest")
    cases = manifest["cases"]
    if not 1 <= len(cases) <= 500:
        raise ValueError("benchmark case count is outside bounds")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("benchmark case must be an object")
        fixture_id = str(case.get("fixture_id") or "")
        digest = str(case.get("sha256") or "")
        relative = Path(str(case.get("relative_path") or ""))
        if not fixture_id or fixture_id in seen:
            raise ValueError("fixture IDs must be non-empty and unique")
        if len(digest) != 64 or any(character not in _HASH for character in digest):
            raise ValueError(f"invalid fixture hash: {fixture_id}")
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"fixture path escapes corpus root: {fixture_id}")
        source = (corpus_root / relative).resolve()
        if corpus_root not in source.parents or source.is_symlink() or not source.is_file():
            raise ValueError(f"fixture is missing or unsafe: {fixture_id}")
        if _digest(source) != digest:
            raise ValueError(f"fixture hash mismatch: {fixture_id}")
        seen.add(fixture_id)
    return cases


def _assert_content_free(value: Any, *, path: str = "results") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_RESULT_KEYS:
                raise ValueError(f"content-bearing benchmark result key is forbidden: {path}.{key}")
            _assert_content_free(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_content_free(item, path=f"{path}[{index}]")


def evaluate(manifest: dict[str, Any], results: dict[str, Any], corpus_root: Path) -> dict[str, Any]:
    cases = _validate_manifest(manifest, corpus_root)
    _assert_content_free(results)
    rows = results.get("cases")
    if not isinstance(rows, list):
        raise ValueError("results.cases must be a list")
    by_id = {str(row.get("fixture_id")): row for row in rows if isinstance(row, dict)}
    outcomes: list[dict[str, Any]] = []
    for case in cases:
        fixture_id = str(case["fixture_id"])
        row = by_id.get(fixture_id) or {}
        checks = {
            "completed": row.get("status") == "complete",
            "route": row.get("route") == case.get("expected_route"),
            "pages": int(row.get("page_count") or 0) == int(case.get("expected_pages") or 0),
            "blocks": int(row.get("block_count") or 0) >= int(case.get("minimum_blocks") or 0),
            "reading_order": bool(row.get("reading_order_complete")),
            "evidence": float(row.get("evidence_coverage") or 0.0) == 1.0,
            "table": (not bool(case.get("requires_table"))) or int(row.get("table_count") or 0) > 0,
        }
        outcomes.append({"fixture_id": fixture_id, "passed": all(checks.values()), "checks": checks})
    passed = sum(int(row["passed"]) for row in outcomes)
    return {
        "case_count": len(outcomes),
        "passed": passed,
        "failed": len(outcomes) - passed,
        "pass_rate": passed / max(1, len(outcomes)),
        "outcomes": outcomes,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".benchmark-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--routing-policy-version", required=True)
    args = parser.parse_args()
    root = args.corpus_root.expanduser().resolve()
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    results = _load(args.results)
    metrics = evaluate(manifest, results, root)
    report = {
        "schema_version": "1",
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_id": manifest.get("corpus_id"),
        "corpus_split": manifest.get("split"),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "routing_policy_version": args.routing_policy_version,
        "provider": results.get("provider"),
        "provider_version": results.get("provider_version"),
        "provider_image_digest": results.get("provider_image_digest"),
        "configuration_sha256": results.get("configuration_sha256"),
        "host": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
        "metrics": metrics,
    }
    _atomic_write(args.output, json.dumps(report, indent=2, sort_keys=True).encode("ascii") + b"\n")
    print(json.dumps({"status": "passed" if metrics["failed"] == 0 else "failed", **metrics}))
    return 0 if metrics["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
