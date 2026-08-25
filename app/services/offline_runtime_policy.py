from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse


def _local_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
        return address.is_private or address.is_loopback or address.is_link_local
    except ValueError:
        return "." not in hostname


def validate_offline_runtime(settings: Any, *, entrypoint: str) -> None:
    """Fail closed before startup when an offline process could reach a remote service."""

    if not bool(getattr(settings, "offline_mode", False)):
        return
    violations: list[str] = []
    for attribute in (
        "discord_enabled",
        "discord_attachment_ingress_enabled",
        "calendar_google_enabled",
        "calendar_inbox_enabled",
        "email_agent_enabled",
        "email_agent_sync_enabled",
        "plane_enabled",
        "web_research_enabled",
    ):
        if bool(getattr(settings, attribute, False)):
            violations.append(attribute)
    local_model_needed = any(
        bool(getattr(settings, attribute, False))
        for attribute in ("micro_model_enabled", "main_repair_model_enabled", "action_ticket_review_enabled")
    )
    if local_model_needed and not _local_url(str(getattr(settings, "local_model_url", ""))):
        violations.append("local_model_url")
    for enabled_attribute, provider_attribute in (
        ("micro_model_enabled", "micro_model_provider"),
        ("main_repair_model_enabled", "main_repair_model_provider"),
        ("action_ticket_review_enabled", "action_ticket_review_model_provider"),
    ):
        if bool(getattr(settings, enabled_attribute, False)) and str(
            getattr(settings, provider_attribute, "")
        ).strip().casefold() != "ollama":
            violations.append(provider_attribute)
    if bool(getattr(settings, "documents_enabled", False)) and not _local_url(
        str(getattr(settings, "paperless_base_url", ""))
    ):
        violations.append("paperless_base_url")
    if bool(getattr(settings, "documents_enabled", False)) and not bool(
        getattr(settings, "documents_local_only", False)
    ):
        violations.append("documents_local_only")
    if bool(getattr(settings, "documents_processing_enabled", False)) and bool(
        getattr(settings, "documents_docling_enabled", False)
    ) and not _local_url(str(getattr(settings, "docling_base_url", ""))):
        violations.append("docling_base_url")
    if bool(getattr(settings, "documents_enabled", False)) and not _local_url(
        str(getattr(settings, "document_gateway_base_url", ""))
    ):
        violations.append("document_gateway_base_url")
    if violations:
        joined = ", ".join(sorted(set(violations)))
        raise RuntimeError(f"OFFLINE_MODE rejected {entrypoint} configuration: {joined}")
