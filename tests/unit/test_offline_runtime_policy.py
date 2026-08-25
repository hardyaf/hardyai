from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.offline_runtime_policy import validate_offline_runtime


def test_offline_runtime_rejects_remote_capabilities() -> None:
    configuration = SimpleNamespace(
        offline_mode=True,
        discord_enabled=True,
        discord_attachment_ingress_enabled=True,
        calendar_google_enabled=False,
        calendar_inbox_enabled=False,
        email_agent_enabled=False,
        email_agent_sync_enabled=False,
        plane_enabled=False,
        web_research_enabled=False,
        micro_model_enabled=True,
        micro_model_provider="openai",
        main_repair_model_enabled=False,
        main_repair_model_provider="ollama",
        action_ticket_review_enabled=False,
        action_ticket_review_model_provider="ollama",
        local_model_url="https://models.example.com",
        documents_enabled=True,
        documents_local_only=True,
        paperless_base_url="https://paperless.example.com",
        documents_processing_enabled=True,
        documents_docling_enabled=True,
        docling_base_url="https://docling.example.com",
        document_gateway_base_url="https://gateway.example.com",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_offline_runtime(configuration, entrypoint="test")
    assert "discord_enabled" in str(exc.value)
    assert "discord_attachment_ingress_enabled" in str(exc.value)
    assert "local_model_url" in str(exc.value)
    assert "paperless_base_url" in str(exc.value)
    assert "docling_base_url" in str(exc.value)
    assert "document_gateway_base_url" in str(exc.value)


def test_offline_runtime_accepts_local_only_services() -> None:
    configuration = SimpleNamespace(
        offline_mode=True,
        discord_enabled=False,
        discord_attachment_ingress_enabled=False,
        calendar_google_enabled=False,
        calendar_inbox_enabled=False,
        email_agent_enabled=False,
        email_agent_sync_enabled=False,
        plane_enabled=False,
        web_research_enabled=False,
        micro_model_enabled=True,
        micro_model_provider="ollama",
        main_repair_model_enabled=True,
        main_repair_model_provider="ollama",
        action_ticket_review_enabled=False,
        action_ticket_review_model_provider="ollama",
        local_model_url="http://ollama:11434",
        documents_enabled=True,
        documents_local_only=True,
        paperless_base_url="http://paperless-webserver:8000",
        documents_processing_enabled=True,
        documents_docling_enabled=True,
        docling_base_url="http://docling-serve:5001",
        document_gateway_base_url="http://documents-gateway:8010",
    )

    validate_offline_runtime(configuration, entrypoint="test")
