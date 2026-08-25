from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, BinaryIO

from app.integrations.paperless.client import PaperlessClient
from app.skills.domains.documents.ports import ArchiveOrigin, ArchiveSearchHit, ArchiveTask


def _results(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        return [item for item in value["results"] if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _bounded_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _document_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[0-9]+", normalized):
        raise RuntimeError("paperless_invalid_document_id")
    return normalized


def _task_document_id(row: dict[str, Any]) -> str | None:
    candidates: list[Any] = []
    if row.get("related_document") is not None:
        candidates.append(row["related_document"])
    related_ids = row.get("related_document_ids")
    if isinstance(related_ids, list):
        candidates.extend(related_ids)
    result_data = row.get("result_data")
    if isinstance(result_data, dict) and result_data.get("document_id") is not None:
        candidates.append(result_data["document_id"])
    normalized = {_document_id(value) for value in candidates}
    if not normalized:
        return None
    if len(normalized) != 1:
        raise RuntimeError("paperless_ambiguous_task_document")
    return normalized.pop()


class PaperlessArchiveAdapter:
    provider_name = "paperless"

    def __init__(self, client: PaperlessClient, *, read_user_id: int | None = None) -> None:
        self.client = client
        self.read_user_id = int(read_user_id) if read_user_id is not None else None

    def submit(self, *, stream: BinaryIO, filename: str, title: str) -> str:
        response = self.client.request(
            "POST",
            "/api/documents/post_document/",
            files={"document": (filename, stream, "application/octet-stream")},
            data={"title": title},
        )
        value = response.json()
        task_ref = value if isinstance(value, str) else value.get("task_id") if isinstance(value, dict) else None
        normalized = str(task_ref or "").strip().strip('"')
        if not normalized or len(normalized) > 200:
            raise RuntimeError("paperless_invalid_task_response")
        return normalized

    def task_status(self, task_ref: str) -> ArchiveTask:
        response = self.client.request("GET", "/api/tasks/", params={"task_id": task_ref})
        rows = _results(response.json())
        if not rows:
            return ArchiveTask(task_ref=task_ref, state="pending")
        row = rows[0]
        status = str(row.get("status") or "").strip().casefold()
        related = _task_document_id(row)
        if status in {"success", "succeeded", "completed"} and related is not None:
            return ArchiveTask(task_ref=task_ref, state="succeeded", source_external_id=related)
        if status in {"failure", "failed", "revoked"}:
            result = _bounded_text(row.get("result_data", row.get("result")), 1000)
            duplicate_match = re.search(
                r"duplicate(?:\s+of|.*?document(?:\s+id)?)[^0-9]{0,20}([0-9]+)",
                result,
                flags=re.IGNORECASE,
            )
            if duplicate_match:
                return ArchiveTask(
                    task_ref=task_ref,
                    state="duplicate",
                    source_external_id=_document_id(duplicate_match.group(1)),
                    error_code="paperless_duplicate",
                )
            return ArchiveTask(task_ref=task_ref, state="failed", error_code="paperless_task_failed")
        return ArchiveTask(task_ref=task_ref, state="pending")

    def grant_read_access(self, source_external_id: str) -> None:
        if self.read_user_id is None or self.read_user_id <= 0:
            raise RuntimeError("paperless_read_user_id_unavailable")
        document_id = _document_id(source_external_id)
        self.client.request(
            "PATCH",
            f"/api/documents/{document_id}/",
            json={
                "set_permissions": {
                    "view": {"users": [self.read_user_id], "groups": []},
                    "change": {"users": [], "groups": []},
                }
            },
        )

    def download_original(self, source_external_id: str) -> Iterator[bytes]:
        document_id = _document_id(source_external_id)
        with self.client.stream(
            "GET",
            f"/api/documents/{document_id}/download/",
            params={"original": "true"},
        ) as response:
            response.raise_for_status()
            self.client.validate_response(response)
            for chunk in response.iter_bytes(chunk_size=65536):
                if chunk:
                    yield chunk


class PaperlessReadAdapter:
    provider_name = "paperless"

    def __init__(self, client: PaperlessClient) -> None:
        self.client = client

    def search(self, *, query: str, limit: int) -> list[ArchiveSearchHit]:
        normalized = " ".join(str(query or "").split())[:200]
        if not normalized:
            return []
        bounded = max(1, min(int(limit), 20))
        response = self.client.request(
            "GET",
            "/api/documents/",
            params={"text": normalized, "page_size": bounded},
        )
        hits: list[ArchiveSearchHit] = []
        for row in _results(response.json())[:bounded]:
            external_id = row.get("id")
            if external_id is None:
                continue
            hits.append(
                ArchiveSearchHit(
                    source_external_id=_document_id(external_id),
                    title=_bounded_text(row.get("title"), 200),
                    snippet=_bounded_text(row.get("content"), 500),
                )
            )
        return hits

    def download_original(self, source_external_id: str) -> Iterator[bytes]:
        document_id = _document_id(source_external_id)
        with self.client.stream(
            "GET",
            f"/api/documents/{document_id}/download/",
            params={"original": "true"},
        ) as response:
            response.raise_for_status()
            self.client.validate_response(response)
            for chunk in response.iter_bytes(chunk_size=65536):
                if chunk:
                    yield chunk

    def list_origins(self, *, limit: int) -> tuple[list[ArchiveOrigin], bool]:
        bounded = max(1, min(int(limit), 100))
        response = self.client.request(
            "GET",
            "/api/documents/",
            params={"page_size": bounded, "ordering": "modified"},
        )
        value = response.json()
        rows = _results(value)
        origins: list[ArchiveOrigin] = []
        for row in rows[:bounded]:
            external_id = row.get("id")
            if external_id is None:
                continue
            filename = _bounded_text(
                row.get("original_file_name") or row.get("archived_file_name"),
                180,
            )
            extension = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
            media_type = {
                "pdf": "application/pdf",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
            }.get(extension, "application/octet-stream")
            origins.append(
                ArchiveOrigin(
                    external_id=_document_id(external_id),
                    external_version=_bounded_text(row.get("modified"), 100) or None,
                    title=_bounded_text(row.get("title"), 200),
                    original_filename=filename,
                    media_type=media_type,
                    modified_at=_bounded_text(row.get("modified"), 100) or None,
                )
            )
        complete = not isinstance(value, dict) or value.get("next") in {None, ""}
        return origins, complete
