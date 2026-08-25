#!/usr/bin/env python3
"""Prove staged intake recovers after the document coordinator restarts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from smoke_test_document_gateway import GatewayClient


def _compose(*args: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "deploy/docker/compose.yaml",
            "--profile",
            "documents",
            *args,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--operator-key-file", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    key = args.operator_key_file.read_text(encoding="utf-8").strip()
    if not key or not args.fixture.is_file():
        raise RuntimeError("operator key or synthetic fixture is unavailable")
    client = GatewayClient(args.base_url, key)
    stopped = False
    try:
        _compose("stop", "document-worker")
        stopped = True
        upload = client.upload(args.fixture)
        document_id = str(upload["document_id"])
        if upload.get("state") != "awaiting_enqueue" or upload.get("enqueue_confirmed") is not False:
            raise RuntimeError("upload did not report the durable enqueue interruption")

        _compose("up", "-d", "--no-build", "document-worker")
        stopped = False
        deadline = time.monotonic() + max(60, args.timeout_seconds)
        recovered: dict[str, object] = {}
        while time.monotonic() < deadline:
            recovered = client.json_get(f"/documents/{document_id}")
            if recovered.get("state") == "ready":
                break
            if recovered.get("state") == "failed":
                raise RuntimeError(f"document became terminal after restart: {recovered.get('failure_code')}")
            time.sleep(5)
        else:
            raise RuntimeError(f"document did not recover after worker restart: {recovered.get('state')}")

        _, _, source = client.request("GET", f"/documents/{document_id}/source")
        if hashlib.sha256(source).digest() != hashlib.sha256(args.fixture.read_bytes()).digest():
            raise RuntimeError("recovered source checksum does not match the fixture")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "document_id": document_id,
                    "initial_state": upload.get("state"),
                    "enqueue_confirmed_during_outage": upload.get("enqueue_confirmed"),
                    "recovered_state": recovered.get("state"),
                    "source_checksum_verified": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if stopped:
            _compose("up", "-d", "--no-build", "document-worker")


if __name__ == "__main__":
    raise SystemExit(main())
