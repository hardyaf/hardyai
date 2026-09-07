from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.reviews.repository import HumanReviewRepository
from app.services.discord.bot import (
    load_discord_permissions_policy,
    resolve_discord_protected_destination,
)


class ApprovalDeliveryError(RuntimeError):
    pass


class ApprovalMessageGateway(Protocol):
    def send(self, *, channel_id: str, content: str) -> str: ...


@dataclass(frozen=True, slots=True)
class DiscordRestApprovalGateway:
    bot_token: str
    timeout_seconds: float = 10.0
    api_base_url: str = "https://discord.com/api/v10"

    def send(self, *, channel_id: str, content: str) -> str:
        token = str(self.bot_token or "").strip()
        if not token:
            raise ApprovalDeliveryError("discord_bot_token_unavailable")
        body = json.dumps(
            {"content": content, "allowed_mentions": {"parse": []}},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self.api_base_url.rstrip('/')}/channels/{channel_id}/messages",
            data=body,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "HardyAI-ActionApproval/1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(1.0, min(float(self.timeout_seconds), 30.0))) as response:
                if int(getattr(response, "status", 0)) not in {200, 201}:
                    raise ApprovalDeliveryError("discord_delivery_status_invalid")
                payload = json.loads(response.read(65_536).decode("utf-8"))
        except HTTPError as exc:
            raise ApprovalDeliveryError(f"discord_delivery_http_{int(exc.code)}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ApprovalDeliveryError("discord_delivery_retryable") from exc
        message_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
        if not message_id:
            raise ApprovalDeliveryError("discord_delivery_receipt_missing")
        return message_id


def load_action_approval_binding(permissions_path: str) -> dict[str, str] | None:
    destination = resolve_discord_protected_destination(
        load_discord_permissions_policy(permissions_path),
        purpose="human_reviews",
    )
    if not isinstance(destination, dict) or not destination.get("approver_principal"):
        return None
    return destination


class ApprovalDelivery:
    """Render and send one bounded, content-minimized Human Review card."""

    def __init__(
        self,
        *,
        reviews: HumanReviewRepository,
        permissions_path: str,
        gateway: ApprovalMessageGateway,
    ) -> None:
        self._reviews = reviews
        self._permissions_path = str(permissions_path)
        self._gateway = gateway

    @staticmethod
    def approval_card(proposal: dict[str, Any]) -> str:
        review_id = str(proposal.get("review_id") or "").strip()
        summary = " ".join(str(proposal.get("safe_action_summary") or "").split())[:500]
        requester = " ".join(str(proposal.get("requester_user_id") or "").split())[:120]
        risk = " ".join(str(proposal.get("risk_summary") or "").split())[:240]
        expiry = str(proposal.get("expires_at") or "").strip()[:80]
        if not all((review_id, summary, requester, risk, expiry)):
            raise ApprovalDeliveryError("approval_card_binding_incomplete")
        card = (
            "Action approval required\n"
            f"Review: {review_id}\n"
            f"Action: {summary}\n"
            f"Requester: {requester}\n"
            f"Risk: {risk}\n"
            f"Expires: {expiry}\n"
            f"Approve: approve {review_id}\n"
            f"Reject: reject {review_id} [reason]"
        )
        if len(card) > 1_900:
            raise ApprovalDeliveryError("approval_card_too_large")
        return card

    def deliver(self, job: dict[str, Any]) -> dict[str, str]:
        payload = job.get("payload")
        if not isinstance(payload, dict):
            raise ApprovalDeliveryError("approval_job_payload_invalid")
        proposal_id = str(payload.get("proposal_id") or "").strip()
        proposal = self._reviews.get_action_proposal(proposal_id)
        if proposal is None:
            raise ApprovalDeliveryError("approval_proposal_missing")
        if (
            str(proposal.get("review_id") or "") != str(payload.get("review_id") or "")
            or str(proposal.get("operation_id") or "") != str(payload.get("operation_id") or "")
            or str(proposal.get("authorization_binding") or "")
            != str(payload.get("authorization_binding") or "")
            or proposal.get("batch_manifest_hash") != payload.get("batch_manifest_hash")
            or proposal.get("transfer_binding_hash") != payload.get("transfer_binding_hash")
            or str(proposal.get("destination_purpose") or "")
            != str(payload.get("destination_purpose") or "")
        ):
            raise ApprovalDeliveryError("approval_job_binding_mismatch")
        existing_message = str(proposal.get("notification_message_id") or "").strip()
        if existing_message:
            return {"status": "already_delivered", "message_id": existing_message}
        if str(proposal.get("state") or "") != "pending":
            raise ApprovalDeliveryError("approval_proposal_not_pending")
        destination = load_action_approval_binding(self._permissions_path)
        if destination is None:
            raise ApprovalDeliveryError("approval_destination_unavailable")
        if (
            destination.get("purpose") != proposal.get("destination_purpose")
            or destination.get("approver_principal") != proposal.get("approver_principal")
        ):
            raise ApprovalDeliveryError("approval_destination_binding_changed")
        message_id = self._gateway.send(
            channel_id=str(destination["channel_id"]),
            content=self.approval_card(proposal),
        )
        self._reviews.mark_action_notification_delivered(
            proposal_id=proposal_id,
            destination_purpose=str(destination["purpose"]),
            guild_id=str(destination["guild_id"]),
            channel_id=str(destination["channel_id"]),
            message_id=message_id,
        )
        return {"status": "delivered", "message_id": message_id}

    @staticmethod
    def outcome_card(proposal: dict[str, Any]) -> str:
        review_id = str(proposal.get("review_id") or "").strip()
        state = str(proposal.get("state") or "").strip()
        labels = {
            "executed": "completed",
            "denied": "denied during reauthorization",
            "failed_terminal": "failed and will not be retried automatically",
        }
        reason = " ".join(str(proposal.get("terminal_reason_code") or "").split())[:120]
        receipt = " ".join(str(proposal.get("action_receipt_ref") or "").split())[:240]
        if not review_id or state not in labels or not reason or (state == "executed" and not receipt):
            raise ApprovalDeliveryError("approval_outcome_card_binding_incomplete")
        card = (
            "Action approval resolved\n"
            f"Review: {review_id}\n"
            f"Result: {labels[state]}\n"
            f"Reason: {reason}"
        )
        if receipt:
            card += f"\nReceipt: {receipt}"
        if len(card) > 800:
            raise ApprovalDeliveryError("approval_outcome_card_too_large")
        return card

    def deliver_outcome(self, job: dict[str, Any]) -> dict[str, str]:
        payload = job.get("payload")
        if not isinstance(payload, dict):
            raise ApprovalDeliveryError("approval_outcome_job_payload_invalid")
        proposal_id = str(payload.get("proposal_id") or "").strip()
        proposal = self._reviews.get_action_proposal(proposal_id)
        if proposal is None:
            raise ApprovalDeliveryError("approval_outcome_proposal_missing")
        if (
            str(proposal.get("review_id") or "") != str(payload.get("review_id") or "")
            or str(proposal.get("operation_id") or "") != str(payload.get("operation_id") or "")
            or str(proposal.get("authorization_binding") or "")
            != str(payload.get("authorization_binding") or "")
            or str(proposal.get("state") or "") != str(payload.get("state") or "")
            or str(proposal.get("destination_purpose") or "")
            != str(payload.get("destination_purpose") or "")
        ):
            raise ApprovalDeliveryError("approval_outcome_job_binding_mismatch")
        existing_message = str(proposal.get("outcome_message_id") or "").strip()
        if existing_message:
            return {"status": "already_delivered", "message_id": existing_message}
        destination = load_action_approval_binding(self._permissions_path)
        if destination is None:
            raise ApprovalDeliveryError("approval_outcome_destination_unavailable")
        if (
            destination.get("purpose") != proposal.get("destination_purpose")
            or destination.get("approver_principal") != proposal.get("approver_principal")
            or destination.get("guild_id") != proposal.get("notification_guild_id")
            or destination.get("channel_id") != proposal.get("notification_channel_id")
        ):
            raise ApprovalDeliveryError("approval_outcome_destination_binding_changed")
        message_id = self._gateway.send(
            channel_id=str(destination["channel_id"]),
            content=self.outcome_card(proposal),
        )
        self._reviews.mark_action_outcome_delivered(
            proposal_id=proposal_id,
            destination_purpose=str(destination["purpose"]),
            guild_id=str(destination["guild_id"]),
            channel_id=str(destination["channel_id"]),
            message_id=message_id,
        )
        return {"status": "delivered", "message_id": message_id}
