from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.reviews.repository import HumanReviewRepository  # noqa: E402
from app.reviews.service import HumanReviewService  # noqa: E402
from app.services.discord.approval_delivery import (  # noqa: E402
    load_action_approval_binding,
)
from app.skills.tool_contracts import ToolCallEnvelope, ToolDescriptor  # noqa: E402


def _descriptor() -> ToolDescriptor:
    """Return the inert harness-only descriptor; never register or compile it."""

    return ToolDescriptor.from_mapping(
        {
            "tool_id": "canary.no_effect",
            "skill_id": "skill.canary.no_effect",
            "contract_version": 1,
            "purpose": "Exercise approval lifecycle without a domain or provider effect.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "canary": {"type": "string", "enum": ["no_effect"], "maxLength": 9}
                },
                "required": ["canary"],
                "additionalProperties": False,
                "minProperties": 1,
                "maxProperties": 1,
            },
            "observation_schema": {
                "type": "object",
                "properties": {"executed": {"type": "boolean", "const": False}},
                "required": ["executed"],
                "additionalProperties": False,
                "minProperties": 1,
                "maxProperties": 1,
            },
            "effect": "local_write",
            "approval_rule": "always",
            "approval_conditions": [],
            "sensitivity": "normal",
            "persistence": "standard",
            "idempotency": "required",
            "effect_cardinality": "single",
            "transferable_observation_fields": [],
            "runtime_dependencies": ["action_approval"],
            "timeout_seconds": 5,
            "max_result_items": 1,
            "max_observation_chars": 200,
            "legacy_intents": [],
            "interactive": False,
        }
    )


def create_canary(
    *,
    database: Path,
    permissions_path: Path,
    source_interface: str,
    external_user_id: str,
    user_id: str,
    agent_id: str,
    channel_scope: str,
    session_id: str,
) -> dict[str, str]:
    binding = load_action_approval_binding(str(permissions_path))
    if binding is None:
        raise ValueError("protected_approval_binding_unavailable")
    descriptor = _descriptor()
    request_id = f"approval-canary-{uuid4()}"
    envelope = ToolCallEnvelope.create(
        root_request_id=request_id,
        call_ordinal=1,
        session_id=session_id,
        principal_kind="operator_canary",
        principal_subject=external_user_id,
        external_user_id=external_user_id,
        user_id=user_id,
        agent_id=agent_id,
        source_interface=source_interface,
        channel_scope=channel_scope,
        skill_id=descriptor.skill_id,
        descriptor=descriptor,
        authorization_snapshot_ref="authz_canary_" + uuid4().hex,
        validated_arguments={"canary": "no_effect"},
    )
    repository = HumanReviewRepository(str(database))
    try:
        created = HumanReviewService(repository).create_action_proposal(
            envelope=envelope,
            descriptor=descriptor,
            resource_version="canary-harness-v1",
            approver_principal=str(binding["approver_principal"]),
            expires_at=(datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            safe_action_summary="Run the inert action-approval canary.",
            risk_summary="No domain or provider executor exists for this canary.",
        )
        proposal = created["proposal"]
        job = created["notification_job"]
        return {
            "status": str(proposal["state"]),
            "proposal_id": str(proposal["proposal_id"]),
            "review_id": str(proposal["review_id"]),
            "operation_id": str(proposal["operation_id"]),
            "notification_job_id": str(job["job_id"]),
        }
    finally:
        repository.close()


def inspect_canary(*, database: Path, proposal_id: str) -> dict[str, str | bool | None]:
    repository = HumanReviewRepository(str(database))
    try:
        proposal = repository.get_action_proposal(proposal_id)
        if proposal is None or proposal.get("tool_id") != "canary.no_effect":
            raise KeyError("canary_proposal_not_found")
        return {
            "status": str(proposal["state"]),
            "proposal_id": str(proposal["proposal_id"]),
            "review_id": str(proposal["review_id"]),
            "operation_id": str(proposal["operation_id"]),
            "destination_arguments_present": proposal.get("destination_arguments") is not None,
            "terminal_reason_code": (
                str(proposal["terminal_reason_code"])
                if proposal.get("terminal_reason_code") is not None
                else None
            ),
        }
    finally:
        repository.close()


def _required(value: str, *, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 255 or "\n" in normalized:
        raise ValueError(f"{name}_invalid")
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or inspect the inert approval canary.")
    parser.add_argument("--database", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--permissions", required=True, type=Path)
    create.add_argument("--source", default="discord")
    create.add_argument("--external-user-id", required=True)
    create.add_argument("--user-id", required=True)
    create.add_argument("--agent-id", required=True)
    create.add_argument("--channel-scope", required=True)
    create.add_argument("--session-id", default="operator-action-approval-canary")
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--proposal-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            from app.config import settings

            if not settings.action_approval_worker_enabled:
                raise ValueError("action_approval_worker_disabled")
            result = create_canary(
                database=args.database,
                permissions_path=args.permissions,
                source_interface=_required(args.source, name="source"),
                external_user_id=_required(args.external_user_id, name="external_user_id"),
                user_id=_required(args.user_id, name="user_id"),
                agent_id=_required(args.agent_id, name="agent_id"),
                channel_scope=_required(args.channel_scope, name="channel_scope"),
                session_id=_required(args.session_id, name="session_id"),
            )
        else:
            result = inspect_canary(
                database=args.database,
                proposal_id=_required(args.proposal_id, name="proposal_id"),
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (KeyError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "error", "reason": str(exc)[:160]},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
