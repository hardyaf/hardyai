from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api.operator_auth as operator_auth
from app.api.document_app import create_document_app
from app.composition.documents import DocumentGatewayContainer
from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.ports import ArchiveSearchHit
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.storage import DocumentRepository


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class FakeEnqueuer:
    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        return "11111111-1111-4111-8111-111111111111"


class FakeArchiveReader:
    provider_name = "paperless"

    def search(self, *, query: str, limit: int):
        return [ArchiveSearchHit(source_external_id="42", title="Archived bill", snippet="Total due")]

    def download_original(self, source_external_id: str):
        assert source_external_id == "42"
        yield PDF


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        documents_enabled=True,
        documents_max_upload_bytes=1024,
        documents_max_request_overhead_bytes=1024,
        documents_body_timeout_seconds=5,
        documents_global_concurrency=2,
        documents_per_principal_concurrency=1,
        document_job_socket_path="missing.sock",
        app_env="test",
        offline_mode=False,
    )


def test_gateway_streams_upload_and_enforces_transport_auth_and_source_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        operator_auth,
        "settings",
        SimpleNamespace(operator_api_key="secret", app_env="test", operator_session_ttl_seconds=3600),
    )
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    ingestion = DocumentIngestionService(
        repository=repository,
        spool=spool,
        enqueuer=FakeEnqueuer(),
    )
    container = DocumentGatewayContainer(
        settings=_settings(),
        repository=repository,
        spool=spool,
        ingestion=ingestion,
        archive_reader=FakeArchiveReader(),
    )

    with TestClient(create_document_app(container)) as client:
        unauthorized = client.post(
            "/documents",
            files={"document": ("bill.pdf", PDF, "application/pdf")},
        )
        assert unauthorized.status_code == 401

        uploaded = client.post(
            "/documents",
            headers={"x-jarvis-operator-key": "secret"},
            data={"title": "Electric bill"},
            files={"document": ("bill.pdf", PDF, "application/pdf")},
        )
        assert uploaded.status_code == 202
        assert uploaded.json()["state"] == "queued"
        assert uploaded.json()["enqueue_confirmed"] is True
        assert uploaded.headers["cache-control"] == "no-store"
        assert uploaded.headers["x-content-type-options"] == "nosniff"
        document_id = uploaded.json()["document_id"]

        session = client.post(
            "/operator/session",
            headers={"x-jarvis-operator-key": "secret"},
        )
        assert session.status_code == 200
        csrf_rejected = client.post(
            "/documents",
            files={"document": ("bill.pdf", PDF, "application/pdf")},
        )
        assert csrf_rejected.status_code == 403
        csrf_accepted = client.post(
            "/documents",
            headers={"x-csrf-token": session.json()["csrf_token"]},
            files={"document": ("bill.pdf", PDF, "application/pdf")},
        )
        assert csrf_accepted.status_code == 200
        assert csrf_accepted.json()["duplicate"] is True

        status = client.get(
            f"/documents/{document_id}",
            headers={"x-jarvis-operator-key": "secret"},
        )
        assert status.status_code == 200
        assert status.json()["title"] == "Electric bill"

        repository.mark_archiving(document_id=document_id, task_ref="task-1")
        repository.mark_ready(
            document_id=document_id,
            provider="paperless",
            external_id="42",
            verified_sha256=uploaded.json()["sha256"],
        )
        search = client.get(
            "/documents/search?query=electric",
            headers={"x-jarvis-operator-key": "secret"},
        )
        assert search.json()["results"][0]["document_id"] == document_id
        source = client.get(
            f"/documents/{document_id}/source",
            headers={"x-jarvis-operator-key": "secret"},
        )
        assert source.content == PDF
        assert source.headers["x-document-sha256"] == uploaded.json()["sha256"]

        cleartext_remote = client.get(
            f"/documents/{document_id}",
            headers={"host": "lan.example", "x-jarvis-operator-key": "secret"},
        )
        assert cleartext_remote.status_code == 400

        oversized = client.post(
            "/documents",
            headers={"x-jarvis-operator-key": "secret"},
            files={"document": ("large.pdf", b"%PDF-" + b"x" * 3000, "application/pdf")},
        )
        assert oversized.status_code == 413


def test_gateway_persists_opaque_ingress_receipt_before_duplicate_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        operator_auth,
        "settings",
        SimpleNamespace(operator_api_key="secret", app_env="test", operator_session_ttl_seconds=3600),
    )
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(str(tmp_path / "spool"), max_bytes=1024, quota_bytes=4096)
    ingestion = DocumentIngestionService(repository=repository, spool=spool, enqueuer=FakeEnqueuer())
    container = DocumentGatewayContainer(
        settings=_settings(),
        repository=repository,
        spool=spool,
        ingestion=ingestion,
    )
    external_id = "a" * 64
    ingress_headers = {
        "x-jarvis-operator-key": "secret",
        "x-jarvis-ingress-source": "discord",
        "x-jarvis-ingress-external-id": external_id,
    }

    with TestClient(create_document_app(container)) as client:
        uploaded = client.post(
            "/documents",
            headers=ingress_headers,
            files={"document": ("receipt.pdf", PDF, "application/pdf")},
        )
        assert uploaded.status_code == 202
        document_id = uploaded.json()["document_id"]

        receipt = client.get(
            f"/documents/ingress-receipts/discord/{external_id}",
            headers={"x-jarvis-operator-key": "secret"},
        )
        assert receipt.status_code == 200
        assert receipt.json()["document_id"] == document_id
        assert receipt.json()["duplicate"] is True

        retried = client.post(
            "/documents",
            headers=ingress_headers,
            files={"document": ("ignored.pdf", b"not-a-pdf", "application/pdf")},
        )
        assert retried.status_code == 200
        assert retried.json()["document_id"] == document_id
        assert retried.json()["duplicate"] is True
        row = repository._conn.execute(
            "SELECT ingress_source, external_id, document_id FROM document_ingress_receipts"
        ).fetchone()
        assert tuple(row) == ("discord", external_id, document_id)
