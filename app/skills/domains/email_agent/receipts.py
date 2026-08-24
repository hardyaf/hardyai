from __future__ import annotations

from typing import Any


def build_operation_receipt(
    *,
    intent: str,
    entities: dict[str, Any],
    context: dict[str, Any],
    result: dict[str, Any],
    services: dict[str, Any],
) -> dict[str, Any] | None:
    del entities
    del services
    if intent not in {
        "email.mark_reviewed",
        "email.snooze",
        "email.dismiss",
        "email.correct_category",
        "email.mark_needs_reply",
        "email.mark_complete",
        "email.mark_spam",
    }:
        return None
    result_status = str(result.get("status") or "").casefold()
    if result_status not in {"ok", "queued"}:
        return None
    operation_id = str(result.get("operation_id") or "").strip()
    message_id = str(result.get("gmail_message_id") or "").strip()
    if not operation_id or not message_id:
        return None
    request_id = str(context.get("request_id") or operation_id)
    is_spam_write = intent == "email.mark_spam"
    is_complete_write = intent == "email.mark_complete"
    is_provider_write = is_spam_write or is_complete_write
    return {
        "operation_id": operation_id,
        "idempotency_key": (
            f"email-spam:{request_id}:{message_id}"
            if is_spam_write
            else f"email-complete:{request_id}:{message_id}"
            if is_complete_write
            else f"email-local:{request_id}:{intent}:{message_id}"
        ),
        "capability": "email",
        "action": intent,
        "resource_key": message_id,
        "status": "verified" if is_provider_write and result_status == "ok" else (
            "queued" if is_provider_write else "committed"
        ),
        "expected_effect": (
            {"gmail_labels_present": ["SPAM"], "gmail_labels_absent": ["INBOX"]}
            if is_spam_write
            else {"gmail_labels_absent": ["UNREAD"], "jarvis_disposition": "complete"}
            if is_complete_write
            else {"local_only": True}
        ),
        "validator_name": "gmail_mailbox_label_readback" if is_provider_write else "email_local_state",
        "validator_version": "v1",
        "resource_locator": {
            "gmail_message_id": message_id,
            "provider": "gmail" if is_provider_write else "jarvis_sqlite",
        },
    }
