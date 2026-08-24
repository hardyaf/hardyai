from __future__ import annotations

import copy

import pytest

from app.skills.domains.email_agent.config import EmailAgentPermissions


def permissions_mapping() -> dict:
    return {
        "version": 1,
        "gmail_profile": "jarvis.house@example.com",
        "google_account_key": "house",
        "taxonomy_version": "shared-v1",
        "source_routes": [
            {
                "route_key": "work",
                "source_mailbox": "work.sender@example.edu",
                "destination_alias": "jarvis.house+work@example.com",
            },
            {
                "route_key": "personal",
                "source_mailbox": "personal.sender@example.com",
                "destination_alias": "jarvis.house+personal@example.com",
            },
        ],
        "categories": [
            {"key": "work_mail", "display_name": "Work Mail", "audience": "shared"},
            {"key": "needs_review", "display_name": "Needs Review", "audience": "shared"},
        ],
        "access": [
            {
                "user_id": "jordan",
                "discord_channel_id": "222222222222222222",
                "external_user_id": "42",
                "agent_ids": ["jarvis"],
                "audiences": ["shared"],
                "enabled": True,
            }
        ],
        "classification_rules": [
            {"category_key": "work_mail", "source_route_keys": ["work"]}
        ],
    }


def test_permissions_require_exact_identity_bound_discord_scope():
    permissions = EmailAgentPermissions.from_mapping(permissions_mapping())
    context = {
        "source_interface": "discord",
        "identity_bound": True,
        "requested_by_user_id": "jordan",
        "discord_channel_id": "222222222222222222",
        "external_user_id": "42",
        "agent_id": "jarvis",
    }

    assert permissions.authorize(context) is not None
    assert permissions.authorize({**context, "discord_channel_id": "999"}) is None
    assert permissions.authorize({**context, "identity_bound": False}) is None
    assert permissions.authorize({**context, "source_interface": "web"}) is None


def test_permissions_route_only_one_trusted_delivery_alias():
    permissions = EmailAgentPermissions.from_mapping(permissions_mapping())

    assert permissions.route_for_delivery_addresses(("jarvis.house+work@example.com",)).route_key == "work"
    assert permissions.route_for_delivery_addresses(("unknown@example.com",)) is None
    assert permissions.route_for_delivery_addresses(permissions.destination_aliases) is None


def test_permissions_build_bounded_managed_gmail_label_allowlist():
    permissions = EmailAgentPermissions.from_mapping(permissions_mapping())

    assert permissions.managed_gmail_labels == {
        "work_mail": "Jarvis/Work Mail",
        "needs_review": "Jarvis/Needs Review",
    }

    unsafe = copy.deepcopy(permissions_mapping())
    unsafe["categories"][0]["gmail_label_name"] = "Personal/Work Mail"
    with pytest.raises(ValueError, match="Jarvis/"):
        EmailAgentPermissions.from_mapping(unsafe)


def test_permissions_reject_duplicate_alias_and_private_category():
    duplicate = copy.deepcopy(permissions_mapping())
    duplicate["source_routes"][1]["destination_alias"] = "jarvis.house+work@example.com"
    with pytest.raises(ValueError, match="Duplicate email route"):
        EmailAgentPermissions.from_mapping(duplicate)

    private = copy.deepcopy(permissions_mapping())
    private["categories"][0]["audience"] = "jordan"
    with pytest.raises(ValueError, match="audience=shared"):
        EmailAgentPermissions.from_mapping(private)
