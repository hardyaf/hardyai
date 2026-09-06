from __future__ import annotations

from datetime import UTC, datetime

from app.skills.domains.email_agent.catalog import EmailCatalogService
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage


def _permissions() -> EmailAgentPermissions:
    return EmailAgentPermissions.from_mapping(
        {
            "version": 2,
            "gmail_profile": "jarvis@example.com",
            "google_account_key": "house",
            "taxonomy_version": "shared-v1",
            "source_routes": [
                {
                    "route_key": "work",
                    "display_name": "Work",
                    "source_mailbox": "source@example.edu",
                    "destination_alias": "jarvis+work@example.com",
                },
                {
                    "route_key": "sports",
                    "display_name": "Family Sports",
                    "source_mailbox": "source@example.org",
                    "destination_alias": "jarvis+sports@example.com",
                },
            ],
            "categories": [
                {"key": "needs_review", "display_name": "Needs Review", "audience": "shared"}
            ],
            "managed_labels": [
                {"key": "done", "display_name": "Done", "gmail_label_name": "Jarvis/Done"},
                {"key": "ayso", "display_name": "AYSO", "gmail_label_name": "Jarvis/AYSO"},
            ],
            "access": [
                {
                    "user_id": "operator",
                    "discord_channel_id": "100",
                    "external_user_id": "42",
                    "agent_ids": ["jarvis"],
                    "audiences": ["shared"],
                    "enabled": True,
                }
            ],
        }
    )


def test_catalogs_are_opaque_safe_and_resolve_human_aliases(tmp_path):
    storage = EmailAgentSQLiteStorage(str(tmp_path / "email.db"))
    storage.upsert_message(
        record={
            "gmail_message_id": "m1",
            "gmail_thread_id": "t1",
            "rfc_message_id": None,
            "source_route_key": "work",
            "gmail_history_id": "1",
            "internal_date": int(datetime(2026, 8, 20, tzinfo=UTC).timestamp() * 1000),
            "sender_name": "Sender",
            "sender_email": "sender@example.net",
            "recipient_headers_json": "[]",
            "subject": "Subject",
            "snippet": "Snippet",
            "gmail_label_ids_json": "[]",
            "attachment_metadata_json": "[]",
            "canonical_body_hash": "hash",
            "list_id": None,
        },
        now="2026-08-20T00:00:00Z",
    )
    catalog = EmailCatalogService(permissions=_permissions(), storage=storage)

    mailboxes = catalog.mailboxes()
    labels = catalog.labels()
    work = catalog.resolve_mailboxes(["Work"])
    ayso = catalog.resolve_labels(["ayso"])

    assert mailboxes[0]["mailbox_ref"].startswith("mailbox_v1_")
    assert mailboxes[0]["message_count"] == 1
    assert "source_mailbox" not in str(mailboxes)
    assert "destination_alias" not in str(mailboxes)
    assert labels[0]["label_ref"].startswith("label_v1_")
    assert work.status == "ok"
    assert catalog.route_keys_for_refs(work.canonical_refs) == ("work",)
    assert ayso.status == "ok"
    assert catalog.label_keys_for_refs(ayso.canonical_refs) == ("ayso",)
    assert catalog.resolve_labels(["missing"]).status == "missing"
    storage.close()
