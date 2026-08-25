from __future__ import annotations

from app.core.persistence_policy import (
    most_restrictive_persistence_policy,
    persistence_policy,
    persistence_policy_for_intent,
)
from app.skills.domains.documents.query_service import DocumentQueryService


class Gateway:
    def ready(self) -> bool:
        return True

    def find(self, *, query: str, limit: int):
        return {
            "results": [
                {
                    "document_id": "doc-private-1",
                    "title": "Secret Financial Statement",
                    "snippet": "account balance 12345",
                    "sensitivity": "financial",
                    "page_number": 2,
                    "block_id": "b7",
                }
            ]
        }

    def status(self, document_id: str):
        return {
            "document_id": document_id,
            "title": "Secret Financial Statement",
            "sensitivity": "financial",
            "processing_state": "complete",
            "source_available": True,
        }

    def evidence(self, **kwargs):
        return {"evidence": [{"literal_text": "account balance 12345", "block_id": "b7"}]}

    def reprocess(self, *, document_id: str, idempotency_key: str):
        return {"document_id": document_id, "run_id": "run-2", "enqueue_confirmed": True}

    def propose_metadata(self, **kwargs):
        raise AssertionError("unused")

    def bind_metadata_review(self, **kwargs):
        raise AssertionError("unused")

    def source_path(self, document_id: str) -> str:
        return f"/documents/{document_id}/source"


def test_document_reads_require_operator_context_and_declare_restricted_persistence() -> None:
    service = DocumentQueryService(gateway=Gateway())
    denied = service.execute(
        intent="documents.find",
        entities={"query": "balance"},
        context={"principal_kind": "user", "source": "discord"},
    )
    assert denied["status"] == "denied"
    assert denied["_persistence_policy"] == "restricted_read"

    result = service.execute(
        intent="documents.find",
        entities={"query": "balance"},
        context={"principal_kind": "operator", "source": "web"},
    )
    assert result["status"] == "ok"
    assert result["_persistence_policy"] == "restricted_read"
    assert result["documents"][0]["snippet"] == "account balance 12345"
    context_entity = result["document_context_entities"][0]
    assert context_entity["entity_id"] == "doc-private-1"
    assert context_entity["display_name"] == "Document doc-priv"
    assert "Secret Financial" not in str(context_entity)
    assert "12345" not in str(context_entity)

    policy = persistence_policy(result["_persistence_policy"])
    assert policy.record_entity_context
    assert not policy.record_recent_turns
    assert not policy.record_conversation_history
    assert not policy.record_memory
    assert not policy.capture_ticket

    fallback_policy = most_restrictive_persistence_policy(
        persistence_policy_for_intent("documents.find"),
        "standard",
    )
    assert fallback_policy.name.value == "restricted_read"
    assert not fallback_policy.record_memory
    assert not fallback_policy.capture_ticket


def test_document_reprocess_is_main_operator_only_and_idempotency_bound_to_request() -> None:
    service = DocumentQueryService(gateway=Gateway())
    result = service.execute(
        intent="documents.reprocess",
        entities={"document_id": "doc-private-1"},
        context={"principal_kind": "operator", "source": "dashboard", "request_id": "request-9"},
    )
    assert result["status"] == "queued"
    assert result["run_id"] == "run-2"
    assert result["_persistence_policy"] == "restricted_read"


def test_discord_reads_only_the_recent_scoped_attachment_id() -> None:
    service = DocumentQueryService(gateway=Gateway())
    context = {
        "principal_kind": "discord_adapter",
        "source": "discord",
        "document_attachment_ids": ["doc-private-1"],
    }
    allowed = service.execute(
        intent="documents.get",
        entities={"document_id": "doc-private-1"},
        context=context,
    )
    assert allowed["status"] == "ok"
    assert allowed["evidence"]["evidence"][0]["literal_text"] == "account balance 12345"

    wrong_id = service.execute(
        intent="documents.get",
        entities={"document_id": "doc-private-2"},
        context=context,
    )
    search = service.execute(
        intent="documents.find",
        entities={"query": "balance"},
        context=context,
    )
    assert wrong_id["status"] == "denied"
    assert search["status"] == "denied"
