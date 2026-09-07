from __future__ import annotations

from scripts.canary_action_approval import create_canary, inspect_canary


def test_canary_creates_only_inert_unregistered_proposal_and_content_free_output(tmp_path) -> None:
    permissions = tmp_path / "discord_permissions.yaml"
    permissions.write_text(
        "\n".join(
            [
                "version: 1",
                "protected_destinations:",
                "  human_reviews:",
                '    guild_id: "111111111111111111"',
                '    channel_id: "222222222222222222"',
                '    approver_user_id: "333333333333333333"',
            ]
        ),
        encoding="utf-8",
    )
    database = tmp_path / "core.db"

    created = create_canary(
        database=database,
        permissions_path=permissions,
        source_interface="discord",
        external_user_id="external-user",
        user_id="operator",
        agent_id="jarvis",
        channel_scope="interactive-channel",
        session_id="canary-session",
    )
    inspected = inspect_canary(database=database, proposal_id=created["proposal_id"])

    assert created["status"] == "pending"
    assert inspected["status"] == "pending"
    assert inspected["destination_arguments_present"] is True
    assert set(created) == {
        "status",
        "proposal_id",
        "review_id",
        "operation_id",
        "notification_job_id",
    }
