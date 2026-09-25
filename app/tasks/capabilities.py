from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from app.reviews.types import ActionProposalState
from app.skills.authorized_executor import AuthorizedSkillExecutor, PreparedToolCall
from app.skills.tool_contracts import ToolDescriptor, thaw_json
from app.tasks.repository import TaskConflictError, TaskRepository


_LOGICAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")


class TaskCapabilityBridge:
    """Task-facing discovery and invocation over the existing authorized executor."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        skill_registry: Any,
        authorized_executor: AuthorizedSkillExecutor,
        available_runtime_dependencies: tuple[str, ...] = (),
        human_review_service: Any | None = None,
        human_review_repository: Any | None = None,
    ) -> None:
        self._repository = repository
        self._skill_registry = skill_registry
        self._authorized_executor = authorized_executor
        self._available_runtime_dependencies = tuple(available_runtime_dependencies)
        self._human_review_service = human_review_service
        self._human_review_repository = human_review_repository

    def reconcile_approval_effects(self, *, task_id: str) -> int:
        """Project terminal approval outcomes into task-owned effect receipts."""

        if self._human_review_repository is None:
            return 0
        reconciled = 0
        terminal_failures = {
            ActionProposalState.REJECTED.value,
            ActionProposalState.EXPIRED.value,
            ActionProposalState.CANCELED.value,
            ActionProposalState.DENIED.value,
            ActionProposalState.FAILED_TERMINAL.value,
        }
        for effect in self._repository.list_effects(task_id=task_id):
            if str(effect.get("state") or "") != "waiting_approval":
                continue
            prior = effect.get("result") if isinstance(effect.get("result"), dict) else {}
            proposal_id = str(prior.get("proposal_id") or "")
            if not proposal_id:
                continue
            proposal = self._human_review_repository.get_action_proposal(proposal_id)
            proposal_state = str((proposal or {}).get("state") or "")
            if proposal_state == ActionProposalState.EXECUTED.value:
                state = "committed"
                result = {
                    "status": "ok",
                    "approved_execution": True,
                    "proposal_id": proposal_id,
                    "receipt_ref": str((proposal or {}).get("action_receipt_ref") or ""),
                }
            elif proposal_state in terminal_failures:
                state = "failed"
                result = {
                    "status": "policy_denied",
                    "message": "The exact action was not approved or could not be executed.",
                    "proposal_id": proposal_id,
                    "proposal_state": proposal_state,
                    "reason_code": str((proposal or {}).get("terminal_reason_code") or ""),
                }
            else:
                continue
            self._repository.finish_effect(
                task_id=task_id,
                logical_operation_id=str(effect["logical_operation_id"]),
                state=state,
                result=result,
            )
            reconciled += 1
        return reconciled

    def _context(self, *, task: dict[str, Any], agent_id: str) -> dict[str, Any]:
        return {
            "source_interface": "task_workspace",
            "source": "task_workspace",
            "requested_by_user_id": str(task["owner_id"]),
            "user_id": str(task["owner_id"]),
            "agent_id": agent_id,
            "session_id": str(task.get("session_id") or f"task:{task['task_id']}"),
            "session_channel": f"task:{task['task_id']}",
            "principal_kind": "operator",
            "principal_subject": str(task["owner_id"]),
            "external_user_id": str(task["owner_id"]),
            "identity_bound": True,
            "available_runtime_dependencies": list(self._available_runtime_dependencies),
        }

    def discover(self, *, task: dict[str, Any], agent_id: str, query: str = "") -> dict[str, Any]:
        context = self._context(task=task, agent_id=agent_id)
        cards = self._authorized_executor.discovery_cards(
            user_id=str(task["owner_id"]),
            agent_id=agent_id,
            source_interface="task_workspace",
            request_context=context,
            max_skills=64,
        )
        needle = str(query or "").strip().casefold()
        results: list[dict[str, Any]] = []
        for card in cards:
            skill_id = str(card.get("skill_id") or "")
            tools = self._authorized_executor.effective_tools([skill_id], context)
            item = {**card, "tools": [str(tool.get("tool_id") or "") for tool in tools]}
            searchable = " ".join(
                [skill_id, str(card.get("title") or ""), str(card.get("purpose") or ""), *item["tools"]]
            ).casefold()
            if needle and needle not in searchable:
                continue
            results.append(item)
        existing_ids = {str(item.get("skill_id") or "") for item in results}
        for shipped in self._skill_registry.list_skills(active_only=True):
            skill_id = str(shipped.get("skill_id") or "").strip().casefold()
            if not skill_id or skill_id in existing_ids:
                continue
            users = {
                str(item or "").strip().casefold()
                for item in shipped.get("skill_user", [])
            } if isinstance(shipped.get("skill_user"), list) else {
                str(shipped.get("skill_user") or "all").strip().casefold()
            }
            agents = {
                str(item or "").strip().casefold()
                for item in shipped.get("skill_agents", [])
            }
            if users and "all" not in users and str(task["owner_id"]).casefold() not in users:
                continue
            if agents and "all" not in agents and agent_id.casefold() not in agents:
                continue
            markdown = self._skill_registry.load_skill_markdown(shipped)
            if not markdown.strip():
                continue
            title = str(shipped.get("skill_name") or skill_id)
            searchable = f"{skill_id} {title} {markdown[:1000]}".casefold()
            if needle and needle not in searchable:
                continue
            results.append(
                {
                    "skill_id": skill_id,
                    "title": title[:120],
                    "purpose": markdown.strip()[:600],
                    "safe_tags": ["instruction-only", "shipped"],
                    "availability": "available",
                    "tools": [],
                }
            )
            existing_ids.add(skill_id)
        for learned in self._repository.list_user_skills(
            owner_id=str(task["owner_id"]), active_only=True
        ):
            item = {
                "skill_id": learned["skill_id"],
                "title": learned["title"],
                "purpose": str(learned["instructions_markdown"])[:600],
                "safe_tags": ["instruction-only", "user-authored"],
                "availability": "available",
                "tools": [],
                "revision": learned["revision"],
            }
            searchable = " ".join(
                [str(item["skill_id"]), str(item["title"]), str(item["purpose"])]
            ).casefold()
            if needle and needle not in searchable:
                continue
            if not any(existing.get("skill_id") == item["skill_id"] for existing in results):
                results.append(item)
        return {"status": "ok", "skills": results[:64]}

    def load_skill(self, *, task: dict[str, Any], agent_id: str, skill_id: str) -> dict[str, Any]:
        normalized = str(skill_id or "").strip().casefold()
        learned = self._repository.get_user_skill(
            owner_id=str(task["owner_id"]), skill_id=normalized
        )
        base_id = str((learned or {}).get("base_skill_id") or normalized).strip().casefold()
        base = next(
            (
                item
                for item in self._skill_registry.list_skills(active_only=True)
                if str(item.get("skill_id") or "").strip().casefold() == base_id
            ),
            None,
        )
        base_markdown = self._skill_registry.load_skill_markdown(base) if base else ""
        if learned is None and not base_markdown:
            return {"status": "not_found", "message": "Skill was not found."}
        content_parts = [base_markdown.strip()] if base_markdown.strip() else []
        if learned is not None:
            content_parts.append(
                "# User instruction revision\n\n" + str(learned["instructions_markdown"]).strip()
            )
        content = "\n\n".join(content_parts)[:48_000]
        context = self._context(task=task, agent_id=agent_id)
        tools = self._authorized_executor.effective_tools([base_id], context) if base else []
        return {
            "status": "ok",
            "skill_id": normalized,
            "base_skill_id": base_id if base else None,
            "revision": learned.get("revision") if learned else None,
            "instructions": content,
            "tools": tools,
        }

    def describe(
        self,
        *,
        task: dict[str, Any],
        agent_id: str,
        tool_id: str,
    ) -> dict[str, Any]:
        resolved = self._skill_registry.resolve_tool(
            tool_id=str(tool_id or "").strip().casefold(),
            user_id=str(task["owner_id"]),
            agent_id=agent_id,
        )
        if not isinstance(resolved, tuple) or len(resolved) != 2:
            return {"status": "not_found", "message": "Capability was not found."}
        skill, descriptor = resolved
        context = self._context(task=task, agent_id=agent_id)
        tools = self._authorized_executor.effective_tools(
            [str(skill.get("skill_id") or "")], context
        )
        projection = next(
            (item for item in tools if item.get("tool_id") == descriptor.tool_id), None
        )
        if projection is None:
            return {
                "status": "policy_denied",
                "message": "Capability is not enabled in this task context.",
            }
        return {
            "status": "ok",
            "tool": {**projection, "contract_version": descriptor.contract_version},
        }

    @staticmethod
    def _conditional_approval_required(
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
    ) -> bool:
        if descriptor.approval_rule == "always":
            return True
        if descriptor.approval_rule != "conditional":
            return False
        for condition in descriptor.approval_conditions:
            if condition == "external_recipients_present":
                for key in ("attendees", "guests", "invitees", "invitee_emails", "recipients"):
                    value = arguments.get(key)
                    if isinstance(value, (list, tuple)) and value:
                        return True
                    if isinstance(value, str) and value.strip():
                        return True
        return False

    def _approval_result(
        self,
        *,
        prepared: PreparedToolCall,
        existing: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        if existing is not None and str(existing.get("state")) == "waiting_approval":
            prior = existing.get("result") if isinstance(existing.get("result"), dict) else {}
            proposal_id = str(prior.get("proposal_id") or "")
            proposal = (
                self._human_review_repository.get_action_proposal(proposal_id)
                if self._human_review_repository is not None and proposal_id
                else None
            )
            state = str((proposal or {}).get("state") or "")
            if state == ActionProposalState.EXECUTED.value:
                return "approved_executed", proposal
            if state in {
                ActionProposalState.REJECTED.value,
                ActionProposalState.EXPIRED.value,
                ActionProposalState.CANCELED.value,
                ActionProposalState.DENIED.value,
                ActionProposalState.FAILED_TERMINAL.value,
            }:
                return "rejected", proposal
            return "waiting", proposal
        if self._human_review_service is None:
            return "unavailable", None
        try:
            created = self._human_review_service.create_action_proposal(
                envelope=prepared.envelope,
                descriptor=prepared.descriptor,
                resource_version=prepared.resource_version,
                approver_principal=prepared.envelope.principal_subject,
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                destination_purpose="task_workspace",
            )
        except Exception:
            return "unavailable", None
        return "created", created

    def call(
        self,
        *,
        task: dict[str, Any],
        agent_id: str,
        tool_id: str,
        contract_version: int,
        arguments: dict[str, Any],
        logical_operation_id: str | None,
        call_ordinal: int,
        expected_steering_revision: int,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        context = self._context(task=task, agent_id=agent_id)
        logical_id = str(logical_operation_id or "").strip()
        request_identity = logical_id or f"read:{call_ordinal}"
        if logical_id and _LOGICAL_ID.fullmatch(logical_id) is None:
            return {"status": "error", "error_code": "logical_operation_id_invalid"}
        prepared = self._authorized_executor.prepare_tool_call(
            tool_id=str(tool_id or "").strip().casefold(),
            contract_version=int(contract_version),
            arguments=arguments,
            source_interface="task_workspace",
            requested_by_user_id=str(task["owner_id"]),
            agent_id=agent_id,
            request_context=context,
            request_id=f"task:{task['task_id']}:{request_identity}",
            call_ordinal=1 if logical_id else max(1, int(call_ordinal)),
        )
        if isinstance(prepared, dict):
            return prepared
        descriptor = prepared.descriptor
        if descriptor.approval_rule == "denied":
            return {
                "status": "policy_denied",
                "denial_reason": "tool_approval_policy_denied",
                "message": "This capability is prohibited by policy.",
            }
        effectful = descriptor.effect != "read"
        if effectful and not logical_id:
            return {
                "status": "error",
                "error_code": "logical_operation_id_required",
                "message": "Effectful capability calls require a stable logical operation ID.",
            }
        try:
            self._repository.consume_usage(
                task_id=str(task["task_id"]),
                expected_steering_revision=expected_steering_revision,
                capability_calls=1,
            )
        except TaskConflictError as exc:
            return {"status": "interrupted", "error_code": str(exc)}

        existing: dict[str, Any] | None = None
        if effectful:
            existing, _ = self._repository.reserve_effect(
                task_id=str(task["task_id"]),
                logical_operation_id=logical_id,
                provider_operation_id=prepared.envelope.operation_id,
                tool_id=descriptor.tool_id,
                arguments_hash=prepared.envelope.arguments_hash,
                run_id=run_id,
            )
            if str(existing.get("state")) in {"committed", "no_effect"}:
                replay = dict(existing.get("result") or {})
                replay["task_idempotent_replay"] = True
                return replay

        if effectful and self._conditional_approval_required(
            descriptor, thaw_json(prepared.envelope.arguments)
        ):
            approval_state, approval = self._approval_result(
                prepared=prepared,
                existing=existing,
            )
            if approval_state == "approved_executed":
                result = {
                    "status": "ok",
                    "approved_execution": True,
                    "proposal_id": str((approval or {}).get("proposal_id") or ""),
                    "receipt_ref": str((approval or {}).get("action_receipt_ref") or ""),
                }
                self._repository.finish_effect(
                    task_id=str(task["task_id"]),
                    logical_operation_id=logical_id,
                    state="committed",
                    result=result,
                )
                return result
            elif approval_state == "rejected":
                result = {
                    "status": "policy_denied",
                    "message": "The exact action was not approved.",
                    "proposal_id": str((approval or {}).get("proposal_id") or ""),
                }
                self._repository.finish_effect(
                    task_id=str(task["task_id"]),
                    logical_operation_id=logical_id,
                    state="failed",
                    result=result,
                )
                return result
            elif approval_state in {"created", "waiting"}:
                proposal = (
                    approval.get("proposal")
                    if approval_state == "created" and isinstance(approval, dict)
                    else approval
                )
                review = (
                    approval.get("review")
                    if approval_state == "created" and isinstance(approval, dict)
                    else None
                )
                result = {
                    "status": "waiting_for_approval",
                    "message": "The exact action is waiting for local operator approval.",
                    "proposal_id": str((proposal or {}).get("proposal_id") or ""),
                    "proposal_hash": str((proposal or {}).get("proposal_hash") or ""),
                    "review_id": str((review or {}).get("review_id") or ""),
                }
                self._repository.finish_effect(
                    task_id=str(task["task_id"]),
                    logical_operation_id=logical_id,
                    state="waiting_approval",
                    result=result,
                )
                return result
            else:
                result = {
                    "status": "policy_denied",
                    "message": "This action requires approval, but approval is unavailable.",
                    "denial_reason": "action_approval_unavailable",
                }
                self._repository.finish_effect(
                    task_id=str(task["task_id"]),
                    logical_operation_id=logical_id,
                    state="failed",
                    result=result,
                )
                return result

        result = self._authorized_executor.execute_prepared_tool(prepared)
        if effectful:
            status = str(result.get("status") or "")
            if status == "ok":
                state = "committed" if result.get("committed_effect") is not False else "no_effect"
            elif status in {"retryable_error", "uncertain"}:
                state = "uncertain"
            else:
                state = "failed"
            self._repository.finish_effect(
                task_id=str(task["task_id"]),
                logical_operation_id=logical_id,
                state=state,
                result=result,
            )
        else:
            self._repository.append_event(
                task_id=str(task["task_id"]),
                event_type="task.capability_read",
                actor="task_worker",
                payload={"tool_id": descriptor.tool_id, "status": result.get("status")},
            )
        return result
