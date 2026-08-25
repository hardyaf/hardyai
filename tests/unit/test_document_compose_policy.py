from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_document_compose_profile_is_digest_pinned_segmented_and_least_privilege() -> None:
    compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker" / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    document_services = {
        "paperless-broker",
        "paperless-db",
        "paperless-webserver",
        "document-worker",
        "document-gateway",
    }
    assert all(services[name]["profiles"] == ["documents"] for name in document_services)
    for name in ("paperless-broker", "paperless-db", "paperless-webserver"):
        assert "@sha256:" in services[name]["image"]
    assert "ports" not in services["paperless-webserver"]
    assert "ports" not in services["document-gateway"]
    assert set(services["jarvis"]["networks"]) == {
        "default",
        "documents-control",
        "discord-ingress-control",
    }
    assert set(services["document-gateway"]["networks"]) == {
        "documents-control",
        "documents-edge",
    }
    assert set(services["document-worker"]["networks"]) == {
        "documents-edge",
        "documents-inference",
    }
    docling = services["docling-serve"]
    assert docling["profiles"] == ["documents-phase3"]
    assert "@sha256:" in docling["image"]
    assert "ports" not in docling
    assert docling["networks"] == ["documents-inference"]
    assert docling["read_only"] is True
    assert docling["cap_drop"] == ["ALL"]
    assert docling["environment"]["DOCLING_DEVICE"] == "cpu"
    assert docling["environment"]["DOCLING_SERVE_ENABLE_REMOTE_SERVICES"] == "false"
    assert docling["environment"]["DOCLING_SERVE_ALLOW_EXTERNAL_PLUGINS"] == "false"
    assert docling["environment"]["DOCLING_SERVE_ALLOWED_SOURCE_TYPES"] == "file"
    assert docling["environment"]["DOCLING_SERVE_ALLOWED_TARGET_TYPES"] == "inbody"
    assert services["paperless-db"]["networks"] == ["paperless-data"]
    assert all(compose["networks"][name]["internal"] for name in (
        "documents-control",
        "documents-edge",
        "paperless-data",
        "documents-inference",
    ))

    gateway_mounts = str(services["document-gateway"]["volumes"])
    worker_mounts = str(services["document-worker"]["volumes"])
    assert "paperless_read_token" in gateway_mounts
    assert "paperless_archive_token" not in gateway_mounts
    assert "paperless_archive_token" in worker_mounts
    assert "paperless_read_token" not in worker_mounts
    assert "/opt/jarvis/data" not in gateway_mounts
    assert services["document-gateway"]["environment"]["JARVIS_OPERATOR_API_KEY"] == ""
    for name in ("document-worker", "document-gateway"):
        assert services[name]["environment"]["OFFLINE_MODE"] == "true"
        assert services[name]["environment"]["DISCORD_ATTACHMENT_INGRESS_ENABLED"] == "false"
    assert services["paperless-webserver"]["environment"]["PAPERLESS_OCR_MODE"] == "auto"
    assert services["paperless-webserver"]["environment"]["PAPERLESS_CONSUMER_DELETE_DUPLICATES"] == "true"
    attachment_ingress = services["discord-attachment-ingress"]
    assert attachment_ingress["profiles"] == ["discord-attachments"]
    assert "ports" not in attachment_ingress
    assert attachment_ingress["read_only"] is True
    assert attachment_ingress["cap_drop"] == ["ALL"]
    assert set(attachment_ingress["networks"]) == {
        "discord-ingress-control",
        "documents-control",
        "discord-attachment-egress",
    }
    assert "/opt/jarvis/documents" not in str(attachment_ingress.get("volumes", []))
    assert "/opt/jarvis/data" not in str(attachment_ingress.get("volumes", []))
    assert services["ollama"]["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "nvidia-smi" in services["ollama"]["healthcheck"]["test"][1]
    # The image uses s6-overlay as PID 1; Docker's extra init process breaks startup.
    assert "init" not in services["paperless-webserver"]
    assert any(
        value.startswith("/run:exec,")
        for value in services["paperless-webserver"]["tmpfs"]
    )
