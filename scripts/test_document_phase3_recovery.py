#!/usr/bin/env python3
"""Exercise worker and Docling outage recovery through the production gateway."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

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
            "--profile",
            "documents-phase3",
            *args,
        ],
        check=True,
    )


def _reprocess(client: GatewayClient, document_id: str, label: str) -> dict[str, object]:
    body = json.dumps({"idempotency_key": f"phase3-{label}-{uuid4()}"}).encode("utf-8")
    _, _, payload = client.request(
        "POST",
        f"/documents/{document_id}/reprocess",
        body=body,
        content_type="application/json",
    )
    value = json.loads(payload)
    if not isinstance(value, dict) or not value.get("run_id"):
        raise RuntimeError("reprocess response did not contain a run ID")
    return value


def _wait_for_run(
    client: GatewayClient,
    document_id: str,
    run_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    state: dict[str, object] = {}
    while time.monotonic() < deadline:
        state = client.json_get(f"/documents/{document_id}")
        if state.get("processing_state") == "complete" and state.get("active_run_id") == run_id:
            return state
        if state.get("processing_state") in {
            "failed",
            "processing_incomplete",
            "needs_review",
            "cancelled",
        }:
            raise RuntimeError(f"processing reached terminal state: {state.get('processing_state')}")
        time.sleep(3)
    raise RuntimeError(f"processing recovery timed out: {state.get('processing_state')}")


def _core_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            value = json.load(response)
        return value.get("status") == "ok"
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--operator-key-file", required=True, type=Path)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=420)
    parser.add_argument("--core-health-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--skip-core-health", action="store_true")
    args = parser.parse_args()

    key = args.operator_key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("operator key file is empty")
    client = GatewayClient(args.base_url, key)
    worker_stopped = False
    provider_stopped = False
    report: dict[str, object] = {"status": "passed"}
    try:
        _compose("stop", "document-worker")
        worker_stopped = True
        worker_request = _reprocess(client, args.document_id, "worker-restart")
        if worker_request.get("enqueue_confirmed") is not False:
            raise RuntimeError("reprocess unexpectedly enqueued while the worker was stopped")
        _compose("up", "-d", "--no-build", "--pull", "never", "document-worker")
        worker_stopped = False
        worker_result = _wait_for_run(
            client,
            args.document_id,
            str(worker_request["run_id"]),
            timeout_seconds=max(60, args.timeout_seconds),
        )
        report["worker_restart"] = {
            "recovered": True,
            "run_id": worker_request["run_id"],
            "processing_state": worker_result.get("processing_state"),
        }

        _compose("stop", "docling-serve")
        provider_stopped = True
        provider_request = _reprocess(client, args.document_id, "provider-restart")
        time.sleep(8)
        outage_state = client.json_get(f"/documents/{args.document_id}")
        if outage_state.get("processing_state") not in {"queued", "processing"}:
            raise RuntimeError("provider outage did not retain a recoverable processing state")
        core_healthy = True if args.skip_core_health else _core_healthy(args.core_health_url)
        if not core_healthy:
            raise RuntimeError("core health failed during the Docling outage")
        _compose("up", "-d", "--no-build", "--pull", "never", "docling-serve")
        provider_stopped = False
        provider_result = _wait_for_run(
            client,
            args.document_id,
            str(provider_request["run_id"]),
            timeout_seconds=max(60, args.timeout_seconds),
        )
        report["provider_restart"] = {
            "recoverable_state": outage_state.get("processing_state"),
            "core_healthy_during_outage": core_healthy,
            "recovered": True,
            "run_id": provider_request["run_id"],
            "processing_state": provider_result.get("processing_state"),
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        if worker_stopped:
            _compose("up", "-d", "--no-build", "--pull", "never", "document-worker")
        if provider_stopped:
            _compose("up", "-d", "--no-build", "--pull", "never", "docling-serve")


if __name__ == "__main__":
    raise SystemExit(main())
