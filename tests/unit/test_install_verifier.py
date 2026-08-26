from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import scripts.verify_install as verify_install
from app.config import settings
from scripts.configure_web_research import upsert_env_text
from scripts.verify_install import (
    InstallChecks,
    _check_documents,
    _check_email_agent,
    _check_skill_artifacts,
    _check_web_research,
    canonical_model_name,
    configured_model_names,
    discord_policy_has_allow_scope,
    discord_policy_uses_example_ids,
    ollama_model_is_present,
)


def _provision_document_paths(tmp_path, monkeypatch) -> None:
    storage = tmp_path / "storage"
    for name in (
        "paperless/valkey",
        "paperless/postgres",
        "paperless/data",
        "paperless/media",
        "paperless/export",
        "jarvis",
        "jarvis/spool",
        "control",
        "backups",
        "restore-drills",
    ):
        (storage / name).mkdir(parents=True, exist_ok=True)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    values = {
        "paperless_db_password": "db-secret",
        "paperless_secret_key": "app-secret",
        "paperless_archive_token": "archive-token",
        "paperless_read_token": "read-token",
        "paperless_read_user_id": "7",
        "jarvis_operator_api_key": "operator-key",
    }
    for name, value in values.items():
        path = secrets / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setenv("DOCUMENTS_STORAGE_ROOT", str(storage.resolve()))
    monkeypatch.setenv("DOCUMENTS_SECRETS_ROOT", str(secrets.resolve()))
    monkeypatch.setattr(verify_install.platform, "system", lambda: "Windows")


def test_document_install_verifier_accepts_provisioned_local_contract(tmp_path, monkeypatch) -> None:
    _provision_document_paths(tmp_path, monkeypatch)
    checks = InstallChecks()
    profile = SimpleNamespace(
        documents_enabled=True,
        documents_local_only=True,
        offline_mode=True,
        operator_api_key="operator-key",
    )

    _check_documents(checks, profile, require_documents=True)

    assert not checks.failed, [result.detail for result in checks.results]
    assert any(result.name == "documents_tokens" and result.level == "PASS" for result in checks.results)
    assert any(result.name == "documents_images" and result.level == "PASS" for result in checks.results)


def test_document_install_verifier_uses_service_scoped_offline_contract(tmp_path, monkeypatch) -> None:
    _provision_document_paths(tmp_path, monkeypatch)
    checks = InstallChecks()
    profile = SimpleNamespace(
        documents_enabled=True,
        documents_local_only=True,
        offline_mode=False,
        operator_api_key="operator-key",
    )

    _check_documents(checks, profile, require_documents=True)

    assert not checks.failed, [result.detail for result in checks.results]
    assert any(result.name == "documents_offline" and result.level == "PASS" for result in checks.results)


def test_document_install_verifier_fails_closed_when_required_profile_is_disabled() -> None:
    checks = InstallChecks()

    _check_documents(
        checks,
        SimpleNamespace(documents_enabled=False),
        require_documents=True,
    )

    assert checks.failed
    assert checks.results == [verify_install.CheckResult("FAIL", "documents", "DOCUMENTS_ENABLED is false")]


def test_document_install_verifier_requires_safe_extraction_for_downstream_features(
    tmp_path, monkeypatch
) -> None:
    _provision_document_paths(tmp_path, monkeypatch)
    checks = InstallChecks()
    profile = SimpleNamespace(
        documents_enabled=True,
        documents_local_only=True,
        operator_api_key="operator-key",
        documents_safe_extraction_enabled=False,
        documents_note_proposals_enabled=True,
    )

    _check_documents(checks, profile, require_documents=True)

    assert any(
        result.name == "documents_feature_dependencies" and result.level == "FAIL"
        for result in checks.results
    )


def test_document_install_verifier_blocks_restricted_workflow_without_adapters(
    tmp_path, monkeypatch
) -> None:
    _provision_document_paths(tmp_path, monkeypatch)
    checks = InstallChecks()
    profile = SimpleNamespace(
        documents_enabled=True,
        documents_local_only=True,
        operator_api_key="operator-key",
        documents_restricted_workflow_enabled=True,
        documents_restricted_security_review_id="review-1",
        documents_restricted_recovery_attestation_path=str(tmp_path / "restore.txt"),
    )

    _check_documents(checks, profile, require_documents=True)

    assert any(
        result.name == "documents_restricted" and result.level == "FAIL"
        for result in checks.results
    )


def test_email_verifier_accepts_explicit_api_token_isolation(tmp_path) -> None:
    permissions_path = tmp_path / "email-agent.yaml"
    permissions_path.write_text(
        """
version: 1
gmail_profile: jarvis.house@example.com
google_account_key: jarvis_personal
taxonomy_version: test-v1
source_routes:
  - route_key: jordan
    source_mailbox: jordan@example.com
    destination_alias: jarvis.house+operator@example.com
categories:
  - key: needs_review
    display_name: Needs Review
    gmail_label_name: Jarvis/Needs Review
  - key: spam
    display_name: Spam
    gmail_label_name: Jarvis/Spam
access:
  - user_id: jordan
    discord_channel_id: '222222222222222222'
    external_user_id: '42'
    audiences: [shared]
    agent_ids: [jarvis]
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    permissions_path.chmod(0o600)
    profile = SimpleNamespace(
        email_agent_enabled=True,
        email_agent_permissions_path=str(permissions_path),
        email_agent_label_shadow_enabled=True,
        email_agent_attachment_extraction_enabled=False,
        email_agent_allow_historical_backfill=False,
        email_agent_label_writes_enabled=True,
        email_agent_allow_remote_model=False,
        email_agent_sync_enabled=True,
        email_agent_spam_writes_enabled=True,
        email_agent_label_token_path=str(tmp_path / "not-mounted.json"),
        email_agent_spam_token_path=str(tmp_path / "not-mounted.json"),
        email_agent_worker_token_isolated=True,
    )
    checks = InstallChecks()

    _check_email_agent(checks, profile)

    assert not checks.failed
    assert any(
        result.name == "email_spam_worker_isolation" and result.level == "PASS"
        for result in checks.results
    )


def test_pytest_runtime_is_forced_to_disposable_database() -> None:
    database_path = Path(settings.database_path)

    assert settings.app_env == "test"
    assert database_path.parent.name == "pytest_runtime"
    assert database_path.name.startswith("jarvis_pytest_")
    assert settings.discord_enabled is False
    assert settings.micro_model_enabled is False
    assert settings.main_repair_model_enabled is False
    assert settings.calendar_google_enabled is False
    assert settings.skill_artifact_auto_compile_enabled is False


def test_ollama_model_matching_normalizes_latest_tag() -> None:
    assert canonical_model_name("gemma3") == "gemma3:latest"
    assert ollama_model_is_present("gemma3", ["gemma3:latest"])
    assert ollama_model_is_present("qwen2.5:3b", ["QWEN2.5:3B"])
    assert not ollama_model_is_present("qwen2.5:7b", ["qwen2.5:3b"])


def test_configured_models_are_enabled_and_deduplicated() -> None:
    profile = SimpleNamespace(
        micro_model_enabled=True,
        micro_model_name="qwen2.5:3b",
        main_repair_model_enabled=True,
        main_repair_model_name="qwen2.5:3b",
    )

    assert configured_model_names(profile) == ["qwen2.5:3b"]


def test_model_probe_allows_reasoning_model_to_reach_visible_output(monkeypatch) -> None:
    payloads: list[dict] = []

    def fake_request_json(url, *, timeout_seconds, method="GET", payload=None):
        if url.endswith("/api/tags"):
            return {"models": [{"name": "gpt-oss:20b"}]}
        payloads.append(payload)
        return {"done": True, "response": "OK"}

    monkeypatch.setattr(verify_install, "_request_json", fake_request_json)
    checks = InstallChecks()
    profile = SimpleNamespace(
        micro_model_enabled=False,
        micro_model_name="qwen2.5:3b",
        micro_model_provider="ollama",
        main_repair_model_enabled=True,
        main_repair_model_name="gpt-oss:20b",
        main_repair_model_provider="ollama",
        local_model_url="http://127.0.0.1:11434",
    )

    verify_install._check_local_models(
        checks,
        profile,
        require_models=True,
        probe_models=True,
        timeout_seconds=300,
    )

    assert not checks.failed
    assert payloads[0]["options"]["num_predict"] == verify_install.OLLAMA_PROBE_NUM_PREDICT
    assert verify_install.OLLAMA_PROBE_NUM_PREDICT >= 64


def test_live_smoke_accepts_model_backed_main_repair(monkeypatch) -> None:
    captured_headers: list[dict[str, str]] = []

    def fake_request_json(
        url,
        *,
        timeout_seconds,
        method="GET",
        payload=None,
        request_headers=None,
    ):
        if url.endswith("/health"):
            return {"status": "ok"}
        assert url.endswith("/ask")
        captured_headers.append(dict(request_headers or {}))
        return {
            "route": "main_jarvis_repair",
            "classification": {"repair_source": "backend"},
            "result": {"repair_source": "backend"},
            "assistant": {"text": "I am Jarvis."},
        }

    monkeypatch.setattr(verify_install, "_request_json", fake_request_json)
    monkeypatch.setattr(
        verify_install,
        "_request_text",
        lambda url, *, timeout_seconds: "<title>Jarvis House Dashboard</title>",
    )
    checks = InstallChecks()
    profile = SimpleNamespace(
        micro_model_enabled=True,
        micro_model_name="qwen2.5:3b",
        main_repair_model_enabled=True,
        main_repair_model_name="gpt-oss:20b",
        operator_api_key="test-operator-key",
    )

    verify_install._check_live_api(
        checks,
        profile,
        api_url="http://127.0.0.1:8000",
        smoke_turn=True,
        timeout_seconds=180,
    )

    assert not checks.failed
    assert captured_headers == [{"X-Jarvis-Operator-Key": "test-operator-key"}]
    assert any(
        result.name == "jarvis_model_path" and result.level == "PASS"
        for result in checks.results
    )


def test_live_smoke_refuses_operator_key_over_plain_remote_http(monkeypatch) -> None:
    def fake_request_json(url, *, timeout_seconds, method="GET", payload=None):
        assert url.endswith("/health")
        return {"status": "ok"}

    monkeypatch.setattr(verify_install, "_request_json", fake_request_json)
    monkeypatch.setattr(
        verify_install,
        "_request_text",
        lambda url, *, timeout_seconds: "<title>Jarvis House Dashboard</title>",
    )
    checks = InstallChecks()
    profile = SimpleNamespace(
        micro_model_enabled=True,
        micro_model_name="qwen2.5:3b",
        main_repair_model_enabled=True,
        main_repair_model_name="gpt-oss:20b",
        operator_api_key="test-operator-key",
    )

    verify_install._check_live_api(
        checks,
        profile,
        api_url="http://192.0.2.10:8000",
        smoke_turn=True,
        timeout_seconds=180,
    )

    assert checks.failed
    assert any(
        result.name == "jarvis_smoke_turn"
        and "refusing to send the operator key" in result.detail
        for result in checks.results
    )


def test_discord_policy_requires_positive_allow_scope() -> None:
    assert not discord_policy_has_allow_scope(
        {
            "defaults": {
                "allow_direct_messages": False,
                "allowed_guild_ids": [],
                "denied_user_ids": [123],
            },
            "guilds": [],
        }
    )
    assert discord_policy_has_allow_scope(
        {
            "defaults": {"allowed_guild_ids": [123]},
            "guilds": [],
        }
    )
    assert discord_policy_has_allow_scope(
        {
            "defaults": {},
            "guilds": [{"guild_id": 123, "allowed_channel_ids": [456]}],
        }
    )
    assert not discord_policy_has_allow_scope(
        {
            "defaults": {"allowed_guild_ids": ["not-a-snowflake", 0, -1]},
            "guilds": [{"guild_id": "replace-me", "allowed_channel_ids": [456]}],
        }
    )


def test_discord_policy_detects_unedited_example_ids() -> None:
    assert discord_policy_uses_example_ids(
        {"guilds": [{"guild_id": 111111111111111111, "allowed_channel_ids": [456]}]}
    )
    assert not discord_policy_uses_example_ids(
        {"guilds": [{"guild_id": 333333333333333333, "allowed_channel_ids": [456]}]}
    )


def test_checked_in_skill_artifacts_match_metadata_and_referenced_sources() -> None:
    checks = InstallChecks()

    _check_skill_artifacts(checks)

    assert not checks.failed, [result.detail for result in checks.results]


def test_web_research_verifier_probes_searxng_json(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request_json(url, *, timeout_seconds, method="GET", payload=None):
        calls.append(url)
        return {"results": [{"title": "Result"}]}

    monkeypatch.setattr(verify_install, "_request_json", fake_request_json)
    checks = InstallChecks()
    profile = SimpleNamespace(
        web_research_enabled=True,
        web_research_provider="searxng",
        web_research_base_url="http://127.0.0.1:8080",
        web_research_timeout_seconds=10,
        web_research_safe_search=1,
        web_research_children_enabled=False,
        micro_model_enabled=False,
        main_repair_model_enabled=True,
    )

    _check_web_research(checks, profile)

    assert not checks.failed
    assert calls and "/search?" in calls[0]
    assert "format=json" in calls[0]


def test_web_research_verifier_rejects_missing_model_lane() -> None:
    checks = InstallChecks()
    profile = SimpleNamespace(
        web_research_enabled=True,
        web_research_provider="searxng",
        micro_model_enabled=False,
        main_repair_model_enabled=False,
    )

    _check_web_research(checks, profile)

    assert checks.failed


def test_web_research_env_upsert_preserves_other_secrets() -> None:
    original = "DISCORD_BOT_TOKEN=keep-me\nWEB_RESEARCH_ENABLED=false\n"

    updated = upsert_env_text(
        original,
        {
            "WEB_RESEARCH_ENABLED": "true",
            "SEARXNG_SECRET_KEY": "generated-secret",
        },
    )

    assert "DISCORD_BOT_TOKEN=keep-me" in updated
    assert "WEB_RESEARCH_ENABLED=true" in updated
    assert "SEARXNG_SECRET_KEY=generated-secret" in updated
