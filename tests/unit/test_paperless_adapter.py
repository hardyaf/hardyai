from __future__ import annotations

import httpx
import pytest

from app.integrations.paperless.adapter import PaperlessArchiveAdapter, PaperlessReadAdapter
from app.integrations.paperless.client import PaperlessClient


def test_paperless_archive_task_search_and_original_download(tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("test-token", encoding="utf-8")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Token test-token"
        assert "version=10" in request.headers["accept"]
        if request.url.path == "/api/documents/post_document/":
            return httpx.Response(200, json="task-123", headers={"X-Api-Version": "10", "X-Version": "3.0.5"})
        if request.url.path == "/api/tasks/":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "status": "SUCCESS",
                            "related_document_ids": [42],
                            "result_data": {"document_id": 42},
                        }
                    ]
                },
                headers={"X-Api-Version": "10", "X-Version": "3.0.5"},
            )
        if request.url.path == "/api/documents/":
            assert request.url.params["text"] == "utility"
            assert "query" not in request.url.params
            return httpx.Response(200, json={"results": [{"id": 42, "title": "Utility bill", "content": "Total due"}]}, headers={"X-Api-Version": "10", "X-Version": "3.0.5"})
        if request.url.path == "/api/documents/42/download/":
            assert request.url.params["original"] == "true"
            return httpx.Response(200, content=b"original", headers={"X-Api-Version": "10", "X-Version": "3.0.5"})
        if request.url.path == "/api/documents/42/" and request.method == "PATCH":
            assert __import__("json").loads(request.content) == {
                "set_permissions": {
                    "view": {"users": [7], "groups": []},
                    "change": {"users": [], "groups": []},
                }
            }
            return httpx.Response(200, json={"id": 42}, headers={"X-Api-Version": "10", "X-Version": "3.0.5"})
        return httpx.Response(404)

    client = PaperlessClient(
        base_url="http://paperless-webserver:8000",
        token_path=str(token),
        api_version=10,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    archive = PaperlessArchiveAdapter(client, read_user_id=7)
    reader = PaperlessReadAdapter(client)

    task_ref = archive.submit(stream=__import__("io").BytesIO(b"doc"), filename="doc.pdf", title="Doc")
    task = archive.task_status(task_ref)
    hits = reader.search(query="utility", limit=5)

    assert task_ref == "task-123"
    assert task.state == "succeeded"
    assert task.source_external_id == "42"
    archive.grant_read_access("42")
    assert hits[0].title == "Utility bill"
    assert b"".join(archive.download_original("42")) == b"original"
    assert ("POST", "/api/documents/post_document/") in seen
    client.close()


def test_paperless_client_fails_closed_on_api_or_server_drift(tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("test-token", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": []},
            headers={"X-Api-Version": "10", "X-Version": "3.1.0"},
        )

    client = PaperlessClient(
        base_url="http://paperless-webserver:8000",
        token_path=str(token),
        api_version=10,
        server_version="3.0.5",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="paperless_server_version_mismatch"):
        client.request("GET", "/api/documents/")
    assert client.ready() is False
    client.close()
