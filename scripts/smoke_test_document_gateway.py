#!/usr/bin/env python3
"""Run a bounded, synthetic-only smoke test against the document gateway."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import mimetypes
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any


class GatewayClient:
    def __init__(self, base_url: str, operator_key: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("base URL must use http with an explicit host")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.operator_key = operator_key

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", "localhost")
        connection.putheader("X-Jarvis-Operator-Key", self.operator_key)
        connection.putheader("Connection", "close")
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        if content_type:
            connection.putheader("Content-Type", content_type)
        connection.endheaders(body)
        response = connection.getresponse()
        payload = response.read()
        headers = {name.casefold(): value for name, value in response.getheaders()}
        status = response.status
        connection.close()
        if status >= 400:
            detail = payload.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"gateway request failed: {method} {path}: HTTP {status}: {detail}")
        return status, headers, payload

    def upload(self, path: Path) -> dict[str, Any]:
        boundary = f"hardyai-{secrets.token_hex(12)}"
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        title = f"HardyAI synthetic {path.suffix.lstrip('.').upper()} canary"
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="title"\r\n\r\n'
            f"{title}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        body = prefix + path.read_bytes() + suffix
        _, _, payload = self.request(
            "POST",
            "/documents",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        value = json.loads(payload)
        if not isinstance(value, dict) or not value.get("document_id"):
            raise RuntimeError("gateway upload response did not contain a document ID")
        return value

    def json_get(self, path: str) -> dict[str, Any]:
        _, _, payload = self.request("GET", path)
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise RuntimeError(f"gateway returned non-object JSON for {path}")
        return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--operator-key-file", required=True, type=Path)
    parser.add_argument("--fixture-directory", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=420)
    args = parser.parse_args()

    key = args.operator_key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("operator key file is empty")
    fixtures = [args.fixture_directory / f"hardyai-canary.{suffix}" for suffix in ("pdf", "jpg", "png")]
    if not all(path.is_file() for path in fixtures):
        raise RuntimeError("one or more synthetic fixtures are missing")

    client = GatewayClient(args.base_url, key)
    ready = client.json_get("/documents/ready")
    if ready.get("status") != "ready":
        raise RuntimeError(f"document gateway is not ready: {ready.get('status')}")

    uploads = {path.name: client.upload(path) for path in fixtures}
    deadline = time.monotonic() + max(30, args.timeout_seconds)
    states: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        states = {
            name: client.json_get(f"/documents/{value['document_id']}")
            for name, value in uploads.items()
        }
        failed = {name: value for name, value in states.items() if value.get("state") in {"failed", "dead_letter"}}
        if failed:
            raise RuntimeError(f"one or more synthetic documents failed: {failed}")
        if all(value.get("state") == "ready" for value in states.values()):
            break
        time.sleep(5)
    else:
        safe_states = {name: value.get("state") for name, value in states.items()}
        raise RuntimeError(f"timed out waiting for OCR: {safe_states}")

    duplicate = client.upload(fixtures[0])
    if duplicate.get("document_id") != uploads[fixtures[0].name].get("document_id") or not duplicate.get("duplicate"):
        raise RuntimeError("exact duplicate upload did not return the existing document")

    # "Utility" appears only in the rendered page body, not in the supplied title,
    # so a hit proves OCR-backed full-text search rather than title matching.
    encoded_query = urllib.parse.urlencode({"query": "Utility", "limit": 20})
    search: dict[str, Any] = {}
    search_deadline = time.monotonic() + 90
    expected_ids = {value["document_id"] for value in uploads.values()}
    while time.monotonic() < search_deadline:
        search = client.json_get(f"/documents/search?{encoded_query}")
        found_ids = {
            item.get("document_id")
            for item in search.get("results", [])
            if isinstance(item, dict)
        }
        if expected_ids.issubset(found_ids):
            break
        time.sleep(3)
    else:
        raise RuntimeError("OCR search did not return every synthetic fixture")

    verified_downloads = []
    for path in fixtures:
        document_id = uploads[path.name]["document_id"]
        _, headers, source = client.request("GET", f"/documents/{document_id}/source")
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        if hashlib.sha256(source).hexdigest() != expected:
            raise RuntimeError(f"download checksum mismatch for {path.name}")
        if headers.get("x-document-sha256") != expected:
            raise RuntimeError(f"download checksum header mismatch for {path.name}")
        verified_downloads.append(path.name)

    report = {
        "status": "passed",
        "gateway_ready": ready.get("status"),
        "documents": {
            name: {
                "document_id": value["document_id"],
                "state": states[name].get("state"),
            }
            for name, value in uploads.items()
        },
        "duplicate_idempotency": True,
        "search_result_count": len(search.get("results", [])),
        "verified_downloads": verified_downloads,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
