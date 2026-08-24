from __future__ import annotations

import stat
import os

import yaml

from app.skills.domains.email_agent.config import EmailAgentPermissions
from scripts.configure_email_agent import build_permissions, configure, configure_discord_access


def _template() -> dict:
    return {
        "version": 1,
        "gmail_profile": "jarvis.house@example.com",
        "google_account_key": "replace-me",
        "taxonomy_version": "shared-v1",
        "source_routes": [
            {
                "route_key": "work",
                "source_mailbox": "person@example.edu",
                "destination_alias": "jarvis.house+work@example.com",
            }
        ],
        "categories": [
            {
                "key": "work",
                "display_name": "Work",
                "audience": "shared",
                "gmail_label_name": "Jarvis/Work",
            },
            {
                "key": "spam",
                "display_name": "Spam",
                "audience": "shared",
                "gmail_label_name": None,
            },
            {
                "key": "needs_review",
                "display_name": "Needs Review",
                "audience": "shared",
                "gmail_label_name": "Jarvis/Needs Review",
            },
        ],
        "access": [
            {
                "user_id": "operator",
                "discord_channel_id": "222222222222222222",
                "external_user_id": "333333333333333333",
                "agent_ids": ["jarvis"],
                "audiences": ["shared"],
                "enabled": True,
            }
        ],
        "classification_rules": [
            {"category_key": "work", "content_contains": ["project"]}
        ],
    }


def test_build_permissions_uses_explicit_synthetic_template():
    raw = build_permissions(
        template=_template(),
        google_account_key="house",
    )

    assert {item["audience"] for item in raw["categories"]} == {"shared"}
    assert len(EmailAgentPermissions.from_mapping(raw).source_routes) == 1
    assert any(item["key"] == "spam" and item["audience"] == "shared" for item in raw["categories"])
    assert raw["classification_rules"] == [
        {"category_key": "work", "content_contains": ["project"]}
    ]


def test_configure_writes_protected_permissions_and_fail_closed_flags(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\n", encoding="utf-8")
    permissions_file = tmp_path / "email_agent_permissions.yaml"
    template_file = tmp_path / "email_agent_template.yaml"
    template_file.write_text(yaml.safe_dump(_template()), encoding="utf-8")

    configure(
        env_path=env_file,
        permissions_path=permissions_file,
        permissions_template_path=template_file,
        google_account_key="house",
        enable_sync=True,
    )

    env_text = env_file.read_text(encoding="utf-8")
    loaded = yaml.safe_load(permissions_file.read_text(encoding="utf-8"))
    assert "EMAIL_AGENT_LABEL_WRITES_ENABLED=false" in env_text
    assert "EMAIL_AGENT_ALLOW_HISTORICAL_BACKFILL=false" in env_text
    assert "EMAIL_AGENT_ALLOW_REMOTE_MODEL=false" in env_text
    assert "EMAIL_AGENT_SPAM_WRITES_ENABLED=false" in env_text
    assert "EMAIL_AGENT_SPAM_TOKEN_PATH=secrets/email-spam-worker/token.json" in env_text
    assert loaded["gmail_profile"] == "jarvis.house@example.com"
    if os.name != "nt":
        assert stat.S_IMODE(permissions_file.stat().st_mode) & 0o077 == 0


def test_configure_discord_access_adds_exact_idempotent_skill_scope(tmp_path):
    policy = tmp_path / "discord_permissions.yaml"
    policy.write_text(
        "version: 1\nguilds:\n  - guild_id: 100\n    allowed_channel_ids: [200]\n",
        encoding="utf-8",
    )

    for _ in range(2):
        configure_discord_access(
            permissions_path=policy,
            guild_id="100",
            channel_id="201",
            external_user_id="42",
        )

    loaded = yaml.safe_load(policy.read_text(encoding="utf-8"))
    rows = loaded["guilds"][0]["skill_channel_access"]
    assert rows == [
        {
            "skill_id": "skill.email.agent",
            "channel_id": "201",
            "allowed_user_ids": ["42"],
            "audiences": ["shared"],
        }
    ]
