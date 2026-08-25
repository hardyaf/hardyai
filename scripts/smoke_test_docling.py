#!/usr/bin/env python3
"""Run a bounded, content-free Docling adapter smoke test against one local PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.integrations.docling.adapter import DoclingParserAdapter
from app.integrations.docling.client import DoclingClient
from app.skills.domains.documents.quality import evaluate_native_artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--base-url", default="http://docling-serve:5001")
    parser.add_argument("--api-key-path", default="/run/secrets/docling_api_key")
    parser.add_argument("--server-version", default="1.30.0")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-polls", type=int, default=300)
    parser.add_argument("--skip-auth-denial", action="store_true")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if source.is_symlink() or not source.is_file() or source.suffix.casefold() != ".pdf":
        raise RuntimeError("smoke source must be one local regular PDF")
    size = source.stat().st_size
    if size <= 0 or size > 50 * 1024 * 1024 or source.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("smoke source is not a bounded PDF")

    if not args.skip_auth_denial:
        with httpx.Client(
            base_url=args.base_url,
            timeout=httpx.Timeout(max(1.0, min(args.timeout_seconds, 30.0))),
            follow_redirects=False,
        ) as unauthorized_client, source.open("rb") as unauthorized_stream:
            response = unauthorized_client.post(
                "/v1/convert/file/async",
                files={"files": (source.name, unauthorized_stream, "application/pdf")},
                data={
                    "from_formats": ["pdf"],
                    "to_formats": ["json", "md"],
                    "do_ocr": "false",
                    "force_ocr": "false",
                },
            )
            if response.status_code not in {401, 403}:
                raise RuntimeError("docling_conversion_endpoint_does_not_require_api_key")

    client = DoclingClient(
        base_url=args.base_url,
        api_key_path=args.api_key_path,
        server_version=args.server_version,
        timeout_seconds=args.timeout_seconds,
    )
    adapter = DoclingParserAdapter(client, provider_version=args.server_version)
    try:
        if not adapter.ready():
            raise RuntimeError("docling_not_ready_or_version_mismatch")
        with source.open("rb") as stream:
            submission = adapter.submit(
                stream=stream,
                filename=source.name,
                media_type="application/pdf",
            )
        operation = None
        for _ in range(max(1, min(args.max_polls, 900))):
            operation = adapter.status(submission.operation_ref)
            if operation.state not in {"pending", "started", "running"}:
                break
            time.sleep(max(0.1, min(args.poll_seconds, 10.0)))
        if operation is None or operation.state != "success":
            raise RuntimeError(operation.error_code if operation else "docling_poll_exhausted")
        artifact = evaluate_native_artifact(
            adapter.result(
                operation_ref=submission.operation_ref,
                document_id="synthetic-smoke-document",
                source_version_id="synthetic-smoke-source",
                run_id="synthetic-smoke-run",
            )
        )
        evidence_count = sum(
            int(block.page_number > 0 and bool(block.provider_ref)) for block in artifact.blocks
        )
        payload = {
            "status": "complete" if artifact.quality.processing_complete else "processing_incomplete",
            "provider": artifact.provider_name,
            "provider_version": artifact.provider_version,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "page_count": len(artifact.pages),
            "block_count": len(artifact.blocks),
            "table_count": len(artifact.tables),
            "reading_order_complete": artifact.quality.reading_order_complete,
            "evidence_coverage": evidence_count / max(1, len(artifact.blocks)),
            "markdown_sha256": hashlib.sha256(artifact.markdown.encode("utf-8")).hexdigest(),
            "quality_reasons": list(artifact.quality.review_reasons),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if artifact.quality.processing_complete else 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
