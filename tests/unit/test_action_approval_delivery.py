from __future__ import annotations

import pytest

from app.services.discord.approval_delivery import ApprovalDelivery, ApprovalDeliveryError
from app.services.discord.bot import (
    load_discord_permissions_policy,
    resolve_discord_protected_destination,
)


class FakeReviews:
    def __init__(self, proposal):
        self.proposal = dict(proposal)
        self.bindings = []

    def get_action_proposal(self, proposal_id):
        return dict(self.proposal) if proposal_id == self.proposal["proposal_id"] else None

    def mark_action_notification_delivered(self, **kwargs):
        self.bindings.append(dict(kwargs))
        self.proposal.update(
            {
                "notification_guild_id": kwargs["guild_id"],
                "notification_channel_id": kwargs["channel_id"],
                "notification_message_id": kwargs["message_id"],
            }
        )
        return dict(self.proposal)

    def mark_action_outcome_delivered(self, **kwargs):
        self.bindings.append(dict(kwargs))
        self.proposal.update(
            {
                "outcome_guild_id": kwargs["guild_id"],
                "outcome_channel_id": kwargs["channel_id"],
                "outcome_message_id": kwargs["message_id"],
            }
        )
        return dict(self.proposal)


class FakeGateway:
    def __init__(self):
        self.messages = []

    def send(self, **kwargs):
        self.messages.append(dict(kwargs))
        return "message-1"


def _permissions(tmp_path, *, approver="333333333333333333"):
    path = tmp_path / "discord_permissions.yaml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "defaults:",
                "  allowed_guild_ids: []",
                "protected_destinations:",
                "  human_reviews:",
                '    guild_id: "111111111111111111"',
                '    channel_id: "222222222222222222"',
                f'    approver_user_id: "{approver}"',
            ]
        ),
        encoding="utf-8",
    )
    return path


def _proposal():
    return {
        "proposal_id": "proposal-1",
        "review_id": "review-1",
        "operation_id": "operation-1",
        "authorization_binding": "authorization-1",
        "batch_manifest_hash": None,
        "transfer_binding_hash": None,
        "destination_purpose": "human_reviews",
        "approver_principal": "discord_user:333333333333333333",
        "safe_action_summary": "Change one synthetic target.",
        "requester_user_id": "requester-1",
        "risk_summary": "One reversible local write.",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "state": "pending",
        "notification_message_id": None,
        "notification_guild_id": None,
        "notification_channel_id": None,
        "outcome_message_id": None,
        "terminal_reason_code": None,
        "destination_arguments": {"private_value": "must-not-appear"},
    }


def _job():
    return {
        "payload": {
            "proposal_id": "proposal-1",
            "review_id": "review-1",
            "operation_id": "operation-1",
            "authorization_binding": "authorization-1",
            "batch_manifest_hash": None,
            "transfer_binding_hash": None,
            "destination_purpose": "human_reviews",
        }
    }


def test_symbolic_destination_resolves_only_from_permissions(tmp_path) -> None:
    policy = load_discord_permissions_policy(str(_permissions(tmp_path)))
    destination = resolve_discord_protected_destination(policy, purpose="human_reviews")
    assert destination == {
        "purpose": "human_reviews",
        "guild_id": "111111111111111111",
        "channel_id": "222222222222222222",
        "approver_principal": "discord_user:333333333333333333",
    }
    assert resolve_discord_protected_destination(policy, purpose="unknown") is None


def test_delivery_card_is_bounded_content_minimized_and_idempotent(tmp_path) -> None:
    reviews = FakeReviews(_proposal())
    gateway = FakeGateway()
    delivery = ApprovalDelivery(
        reviews=reviews,
        permissions_path=str(_permissions(tmp_path)),
        gateway=gateway,
    )

    result = delivery.deliver(_job())
    repeated = delivery.deliver(_job())

    assert result == {"status": "delivered", "message_id": "message-1"}
    assert repeated == {"status": "already_delivered", "message_id": "message-1"}
    assert len(gateway.messages) == 1
    card = gateway.messages[0]["content"]
    assert "approve review-1" in card
    assert "reject review-1 [reason]" in card
    assert "must-not-appear" not in card
    assert reviews.bindings[0]["destination_purpose"] == "human_reviews"


def test_delivery_rejects_protected_binding_change(tmp_path) -> None:
    reviews = FakeReviews(_proposal())
    delivery = ApprovalDelivery(
        reviews=reviews,
        permissions_path=str(_permissions(tmp_path, approver="444444444444444444")),
        gateway=FakeGateway(),
    )
    with pytest.raises(ApprovalDeliveryError, match="approval_destination_binding_changed"):
        delivery.deliver(_job())


def test_outcome_delivery_is_content_minimized_and_idempotent(tmp_path) -> None:
    proposal = _proposal()
    proposal.update(
        {
            "state": "executed",
            "notification_guild_id": "111111111111111111",
            "notification_channel_id": "222222222222222222",
            "terminal_reason_code": "approval_execution_committed",
            "action_receipt_ref": "receipt-1",
        }
    )
    reviews = FakeReviews(proposal)
    gateway = FakeGateway()
    delivery = ApprovalDelivery(
        reviews=reviews,
        permissions_path=str(_permissions(tmp_path)),
        gateway=gateway,
    )
    job = {
        "payload": {
            "proposal_id": "proposal-1",
            "review_id": "review-1",
            "operation_id": "operation-1",
            "authorization_binding": "authorization-1",
            "state": "executed",
            "destination_purpose": "human_reviews",
        }
    }

    result = delivery.deliver_outcome(job)
    repeated = delivery.deliver_outcome(job)

    assert result == {"status": "delivered", "message_id": "message-1"}
    assert repeated == {"status": "already_delivered", "message_id": "message-1"}
    assert len(gateway.messages) == 1
    assert "Result: completed" in gateway.messages[0]["content"]
    assert "Receipt: receipt-1" in gateway.messages[0]["content"]
    assert "must-not-appear" not in gateway.messages[0]["content"]
