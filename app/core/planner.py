from __future__ import annotations

import re
from typing import Any

from app.core.agent_loop_types import AgentLoopActionType, PlannerActionCandidate, PlannerDecision


class MainAgentPlanner:
    """Selects exactly one next action per loop step."""

    def __init__(self, *, auto_approve_actions: bool = True) -> None:
        self._auto_approve_actions = auto_approve_actions

    def next_decision(
        self,
        *,
        step_number: int,
        plan: dict[str, Any],
    ) -> PlannerDecision:
        commands = plan.get("commands")
        if not isinstance(commands, list):
            return PlannerDecision(
                step_number=step_number,
                action_type=AgentLoopActionType.FAIL,
                rationale="plan_missing_commands",
                subgoal="Validate that the plan includes executable commands.",
                preconditions=["commands_list_present"],
                expected_outcome="Planner can identify at least one command to process.",
                uncertainty=0.15,
                fallback_action="request_user_input",
                metadata={"reason": "Plan has no executable commands list."},
            )

        index = step_number - 1
        if index >= len(commands):
            return PlannerDecision(
                step_number=step_number,
                action_type=AgentLoopActionType.COMPLETE,
                rationale="all_plan_commands_processed",
                subgoal="Confirm all planned commands are complete.",
                preconditions=["all_commands_processed"],
                expected_outcome="Loop transitions to completed state.",
                uncertainty=0.05,
                fallback_action=None,
            )

        raw_command = commands[index]
        command_text = ""
        target: str | None = None
        metadata: dict[str, Any] = {}
        if isinstance(raw_command, dict):
            command_text = str(raw_command.get("command_text") or "").strip()
            raw_target = raw_command.get("target")
            if raw_target is not None:
                target = str(raw_target).strip() or None
            metadata = {str(key): value for key, value in raw_command.items()}
        elif isinstance(raw_command, str):
            command_text = raw_command.strip()
        else:
            return PlannerDecision(
                step_number=step_number,
                action_type=AgentLoopActionType.FAIL,
                rationale="unsupported_command_shape",
                subgoal="Normalize command payload shape before execution.",
                preconditions=["supported_command_shape"],
                expected_outcome="Planner obtains a string or dict command entry.",
                uncertainty=0.2,
                fallback_action="request_user_input",
                metadata={"reason": f"Unsupported command shape `{type(raw_command).__name__}`."},
            )

        if not command_text:
            return PlannerDecision(
                step_number=step_number,
                action_type=AgentLoopActionType.FAIL,
                rationale="empty_command_text",
                subgoal="Validate command text before tool execution.",
                preconditions=["non_empty_command_text"],
                expected_outcome="Planner has command text to execute.",
                uncertainty=0.1,
                fallback_action="request_user_input",
                metadata={"reason": "Empty command in plan."},
            )

        if bool(metadata.get("requires_approval")) and self._auto_approve_actions:
            metadata = dict(metadata)
            metadata["approval_mode"] = "auto_approved"

        uncertainty = self._uncertainty(metadata=metadata, command_text=command_text)
        subgoal = self._subgoal(metadata=metadata, command_text=command_text)
        preconditions = self._preconditions(metadata=metadata, target=target, command_text=command_text)
        expected_outcome = self._expected_outcome(metadata=metadata, command_text=command_text)
        fallback_action = str(metadata.get("fallback_action") or "request_user_input")
        precondition_evaluation = self._evaluate_preconditions(
            preconditions=preconditions,
            metadata=metadata,
            command_text=command_text,
            target=target,
        )
        unmet_preconditions = precondition_evaluation["unmet"]

        candidates = self._build_action_candidates(
            command_text=command_text,
            metadata=metadata,
            uncertainty=uncertainty,
            unmet_preconditions=unmet_preconditions,
        )
        selected = self._select_best_candidate(candidates)
        selected_action = selected.action_type
        if selected_action == AgentLoopActionType.REQUEST_APPROVAL and self._auto_approve_actions:
            selected_action = AgentLoopActionType.EXECUTE_COMMAND
        selected_expected_outcome = expected_outcome
        if selected_action == AgentLoopActionType.REQUEST_APPROVAL:
            selected_expected_outcome = "User approval is captured before any state-changing action."
        elif selected_action == AgentLoopActionType.REQUEST_USER_INPUT:
            if unmet_preconditions:
                selected_expected_outcome = "Resolve unmet preconditions before execution."
            else:
                selected_expected_outcome = "User provides missing details required for execution."

        enriched_metadata = dict(metadata)
        enriched_metadata["candidate_actions"] = [candidate.to_dict() for candidate in candidates]
        enriched_metadata["selected_candidate_action"] = selected_action.value
        enriched_metadata["selected_candidate_score"] = selected.score
        enriched_metadata["precondition_evaluation"] = precondition_evaluation
        enriched_metadata["unmet_preconditions"] = unmet_preconditions
        if unmet_preconditions:
            enriched_metadata.setdefault("precondition_strategy", "clarify_before_execute")
            enriched_metadata.setdefault("precondition_probe", unmet_preconditions)

        return PlannerDecision(
            step_number=step_number,
            action_type=selected_action,
            rationale=self._decision_rationale(action_type=selected_action, metadata=enriched_metadata),
            command_text=command_text,
            target=target,
            subgoal=subgoal,
            preconditions=preconditions,
            expected_outcome=selected_expected_outcome,
            uncertainty=uncertainty,
            fallback_action=fallback_action,
            candidate_actions=candidates,
            selected_candidate_score=selected.score,
            metadata=enriched_metadata,
        )

    def materialize_plan(self, *, plan: dict[str, Any]) -> dict[str, Any]:
        if bool(plan.get("_planner_materialized")):
            return plan
        commands = plan.get("commands")
        if not isinstance(commands, list):
            return plan

        materialized_commands: list[dict[str, Any]] = []
        injected_verification_count = 0
        injected_verifications: list[dict[str, Any]] = []
        for index, raw_command in enumerate(commands):
            normalized = self._normalize_command_entry(raw_command)
            if normalized is None:
                materialized_commands.append({"command_text": "", "unsupported_command_shape": str(type(raw_command))})
                continue
            command_text = normalized["command_text"]
            target = normalized["target"]
            metadata = dict(normalized["metadata"])

            base_entry: dict[str, Any] = dict(metadata)
            base_entry["command_text"] = command_text
            if target:
                base_entry.setdefault("target", target)
            materialized_commands.append(base_entry)

            inject, reason = self._should_inject_verification(command_text=command_text, metadata=base_entry)
            if not inject:
                continue
            verify_command = self._infer_verification_command(
                command_text=command_text,
                target=target,
                metadata=base_entry,
            )
            if not verify_command:
                continue
            injected_verification_count += 1
            verification_entry = {
                "command_text": verify_command,
                "target": target,
                "is_verification": True,
                "skip_auto_verify": True,
                "generated_by": "planner.verify_injection",
                "verification_for_index": index,
                "verification_for_command": command_text,
                "verification_reason": reason,
                "confidence": 0.92,
                "side_effect_risk": 0.18,
                "expected_outcome": "Verification read confirms the previous write action.",
            }
            materialized_commands.append(verification_entry)
            injected_verifications.append(
                {
                    "original_index": index,
                    "original_command_text": command_text,
                    "verification_command_text": verify_command,
                    "verification_reason": reason,
                    "materialized_index": len(materialized_commands) - 1,
                }
            )

        materialized_plan = dict(plan)
        materialized_plan["commands"] = materialized_commands
        materialized_plan["_planner_materialized"] = True
        materialized_plan["_planner_materialization"] = {
            "original_command_count": len(commands),
            "materialized_command_count": len(materialized_commands),
            "injected_verification_count": injected_verification_count,
            "injected_verifications": injected_verifications,
        }
        return materialized_plan

    @staticmethod
    def _subgoal(*, metadata: dict[str, Any], command_text: str) -> str:
        candidate = str(metadata.get("subgoal") or "").strip()
        if candidate:
            return candidate
        return f"Execute command `{command_text}` and advance the user goal."

    @staticmethod
    def _preconditions(*, metadata: dict[str, Any], target: str | None, command_text: str) -> list[str]:
        raw = metadata.get("preconditions")
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
            if values:
                return values
        conditions = ["non_empty_command_text"]
        if target:
            conditions.append("target_context_available")
        if bool(metadata.get("requires_approval")):
            conditions.append("approval_granted_or_auto_approved")
        if bool(metadata.get("requires_user_input")):
            conditions.append("required_user_input_provided")
        if MainAgentPlanner._has_deictic_reference(command_text=command_text):
            conditions.append("resolved_reference")
        return conditions

    @staticmethod
    def _expected_outcome(*, metadata: dict[str, Any], command_text: str) -> str:
        candidate = str(metadata.get("expected_outcome") or "").strip()
        if candidate:
            return candidate
        normalized = re.sub(r"\s+", " ", command_text).strip()
        if not normalized:
            return "Tool action succeeds and produces a normalized result payload."
        return f"`{normalized}` executes successfully and returns a stable tool result."

    @staticmethod
    def _uncertainty(*, metadata: dict[str, Any], command_text: str, forced_floor: float = 0.0) -> float:
        raw = metadata.get("uncertainty")
        if isinstance(raw, (int, float)):
            return max(float(forced_floor), min(1.0, max(0.0, float(raw))))
        base = 0.22
        lowered = command_text.lower()
        if any(token in lowered for token in ["maybe", "sometime", "soon", "it", "that", "this"]):
            base = 0.4
        if len(command_text.split()) <= 3:
            base = max(base, 0.35)
        return max(float(forced_floor), min(1.0, base))

    def _build_action_candidates(
        self,
        *,
        command_text: str,
        metadata: dict[str, Any],
        uncertainty: float,
        unmet_preconditions: list[str],
    ) -> list[PlannerActionCandidate]:
        requires_approval = bool(metadata.get("requires_approval")) and not self._auto_approve_actions
        requires_user_input = bool(metadata.get("requires_user_input"))
        missing_fields = self._missing_fields(metadata)
        unmet_preconditions_ratio = min(1.0, len(unmet_preconditions) / 3.0)
        missing_ratio = min(1.0, (len(missing_fields) + len(unmet_preconditions)) / 3.0)
        confidence = self._confidence(metadata=metadata, uncertainty=uncertainty)
        side_effect_risk = self._side_effect_risk(command_text=command_text, metadata=metadata)
        command_short_ambiguity = 0.1 if len(command_text.split()) <= 3 else 0.0

        execute_score = (
            confidence * 0.45
            + (1.0 - missing_ratio) * 0.2
            + (1.0 - side_effect_risk) * 0.1
            + 0.85 * 0.25
        )
        if missing_fields:
            execute_score -= min(0.35, 0.12 * len(missing_fields))
        if unmet_preconditions:
            execute_score -= min(0.5, 0.17 * len(unmet_preconditions))
        if requires_user_input:
            execute_score -= 0.35
        if requires_approval:
            execute_score -= 0.25
        if metadata.get("approval_mode") == "auto_approved":
            execute_score += 0.05

        user_input_score = (
            (1.0 - confidence) * 0.25
            + missing_ratio * 0.45
            + side_effect_risk * 0.1
            + 0.42 * 0.2
            + command_short_ambiguity
        )
        if requires_user_input:
            user_input_score += 0.28
        if unmet_preconditions:
            user_input_score += min(0.42, 0.15 * len(unmet_preconditions))
        if not missing_fields and not unmet_preconditions and not requires_user_input:
            user_input_score -= 0.2

        include_approval = bool(metadata.get("requires_approval")) or side_effect_risk >= 0.5
        approval_score = (
            side_effect_risk * 0.45
            + (1.0 - confidence) * 0.15
            + 0.15
        )
        if requires_approval:
            approval_score += 0.3
        else:
            approval_score -= 0.1
        if self._auto_approve_actions:
            approval_score -= 0.25

        fail_score = (
            max(0.0, 0.15 - confidence * 0.2)
            + missing_ratio * 0.1
            + command_short_ambiguity * 0.4
        )
        include_fail = confidence < 0.35 or len(missing_fields) >= 3 or len(unmet_preconditions) >= 3

        candidates: list[PlannerActionCandidate] = [
            PlannerActionCandidate(
                action_type=AgentLoopActionType.EXECUTE_COMMAND,
                score=self._clamp_score(execute_score),
                rationale="candidate_execute_command",
                signals={
                    "confidence": confidence,
                    "missing_ratio": missing_ratio,
                    "unmet_preconditions_ratio": unmet_preconditions_ratio,
                    "side_effect_risk": side_effect_risk,
                    "progress_gain": 0.85,
                },
            ),
            PlannerActionCandidate(
                action_type=AgentLoopActionType.REQUEST_USER_INPUT,
                score=self._clamp_score(user_input_score),
                rationale="candidate_request_user_input",
                signals={
                    "confidence": confidence,
                    "missing_ratio": missing_ratio,
                    "unmet_preconditions_ratio": unmet_preconditions_ratio,
                    "side_effect_risk": side_effect_risk,
                    "progress_gain": 0.42,
                },
            ),
        ]
        if include_approval:
            candidates.append(
                PlannerActionCandidate(
                    action_type=AgentLoopActionType.REQUEST_APPROVAL,
                    score=self._clamp_score(approval_score),
                    rationale="candidate_request_approval",
                    signals={
                        "confidence": confidence,
                        "missing_ratio": missing_ratio,
                        "unmet_preconditions_ratio": unmet_preconditions_ratio,
                        "side_effect_risk": side_effect_risk,
                        "progress_gain": 0.35,
                    },
                )
            )
        if include_fail and len(candidates) < 4:
            candidates.append(
                PlannerActionCandidate(
                    action_type=AgentLoopActionType.FAIL,
                    score=self._clamp_score(fail_score),
                    rationale="candidate_fail_safe",
                    signals={
                        "confidence": confidence,
                        "missing_ratio": missing_ratio,
                        "unmet_preconditions_ratio": unmet_preconditions_ratio,
                        "side_effect_risk": side_effect_risk,
                        "progress_gain": 0.05,
                    },
                )
            )

        candidates = self._apply_candidate_score_overrides(candidates=candidates, metadata=metadata)
        return candidates[:4]

    @staticmethod
    def _apply_candidate_score_overrides(
        *,
        candidates: list[PlannerActionCandidate],
        metadata: dict[str, Any],
    ) -> list[PlannerActionCandidate]:
        overrides = metadata.get("candidate_score_overrides")
        if not isinstance(overrides, dict):
            return candidates
        adjusted: list[PlannerActionCandidate] = []
        for candidate in candidates:
            override = overrides.get(candidate.action_type.value)
            if not isinstance(override, (int, float)):
                override = overrides.get(candidate.action_type.name)
            score = float(candidate.score)
            if isinstance(override, (int, float)):
                score = max(0.0, min(1.0, float(override)))
            adjusted.append(
                PlannerActionCandidate(
                    action_type=candidate.action_type,
                    score=score,
                    rationale=candidate.rationale,
                    signals=dict(candidate.signals),
                )
            )
        return adjusted

    @staticmethod
    def _select_best_candidate(candidates: list[PlannerActionCandidate]) -> PlannerActionCandidate:
        if not candidates:
            return PlannerActionCandidate(
                action_type=AgentLoopActionType.FAIL,
                score=0.0,
                rationale="candidate_none_available",
                signals={},
            )
        priority = {
            AgentLoopActionType.EXECUTE_COMMAND: 0,
            AgentLoopActionType.REQUEST_USER_INPUT: 1,
            AgentLoopActionType.REQUEST_APPROVAL: 2,
            AgentLoopActionType.FAIL: 3,
            AgentLoopActionType.COMPLETE: 4,
        }
        ordered = sorted(
            candidates,
            key=lambda item: (-float(item.score), int(priority.get(item.action_type, 99))),
        )
        return ordered[0]

    @staticmethod
    def _decision_rationale(*, action_type: AgentLoopActionType, metadata: dict[str, Any]) -> str:
        unmet_preconditions = metadata.get("unmet_preconditions")
        has_unmet_preconditions = isinstance(unmet_preconditions, list) and len(unmet_preconditions) > 0
        if action_type == AgentLoopActionType.EXECUTE_COMMAND:
            if metadata.get("approval_mode") == "auto_approved":
                return "execute_next_plan_command_auto_approved"
            if has_unmet_preconditions:
                return "execute_with_unmet_preconditions_candidate_best"
            return "execute_next_plan_command_candidate_best"
        if action_type == AgentLoopActionType.REQUEST_USER_INPUT:
            if has_unmet_preconditions:
                return "preconditions_unmet_request_user_input"
            if bool(metadata.get("requires_user_input")):
                return "command_requires_user_input"
            return "candidate_prefer_user_input"
        if action_type == AgentLoopActionType.REQUEST_APPROVAL:
            return "command_requires_approval"
        if action_type == AgentLoopActionType.COMPLETE:
            return "all_plan_commands_processed"
        return "candidate_fail_safe_stop"

    @staticmethod
    def _missing_fields(metadata: dict[str, Any]) -> list[str]:
        raw = metadata.get("missing_fields")
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    @staticmethod
    def _confidence(*, metadata: dict[str, Any], uncertainty: float) -> float:
        raw = metadata.get("confidence")
        if isinstance(raw, (int, float)):
            return max(0.0, min(1.0, float(raw)))
        return max(0.0, min(1.0, 1.0 - uncertainty))

    @staticmethod
    def _side_effect_risk(*, command_text: str, metadata: dict[str, Any]) -> float:
        raw = metadata.get("side_effect_risk")
        if isinstance(raw, (int, float)):
            return max(0.0, min(1.0, float(raw)))
        lowered = command_text.strip().lower()
        if not lowered:
            return 0.5
        low_risk_verbs = ["show", "list", "view", "get", "read", "what", "which"]
        if any(lowered.startswith(prefix) for prefix in low_risk_verbs):
            return 0.2
        high_risk_verbs = [
            "delete",
            "remove",
            "clear",
            "erase",
            "create",
            "make",
            "add",
            "set",
            "turn",
            "schedule",
            "invite",
            "send",
        ]
        if any(lowered.startswith(prefix) for prefix in high_risk_verbs):
            return 0.72
        return 0.45

    @staticmethod
    def _clamp_score(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _normalize_command_entry(raw_command: Any) -> dict[str, Any] | None:
        command_text = ""
        target: str | None = None
        metadata: dict[str, Any] = {}
        if isinstance(raw_command, dict):
            metadata = {str(key): value for key, value in raw_command.items()}
            command_text = str(metadata.get("command_text") or "").strip()
            if metadata.get("target") is not None:
                target = str(metadata.get("target") or "").strip() or None
        elif isinstance(raw_command, str):
            command_text = raw_command.strip()
        else:
            return None
        return {"command_text": command_text, "target": target, "metadata": metadata}

    @staticmethod
    def _evaluate_preconditions(
        *,
        preconditions: list[str],
        metadata: dict[str, Any],
        command_text: str,
        target: str | None,
    ) -> dict[str, Any]:
        checks = metadata.get("precondition_checks")
        check_overrides = checks if isinstance(checks, dict) else {}
        status: dict[str, bool] = {}
        missing_fields = MainAgentPlanner._missing_fields(metadata)

        for precondition in preconditions:
            if precondition in check_overrides and isinstance(check_overrides.get(precondition), bool):
                status[precondition] = bool(check_overrides[precondition])
                continue

            if precondition == "non_empty_command_text":
                status[precondition] = bool(command_text.strip())
            elif precondition == "target_context_available":
                status[precondition] = bool(target or metadata.get("resolved_target_context"))
            elif precondition == "approval_granted_or_auto_approved":
                status[precondition] = bool(
                    metadata.get("approval_mode") == "auto_approved"
                    or metadata.get("approval_granted") is True
                    or not bool(metadata.get("requires_approval"))
                )
            elif precondition == "required_user_input_provided":
                status[precondition] = bool(
                    metadata.get("user_input_provided") is True
                    or (not bool(metadata.get("requires_user_input")) and not missing_fields)
                )
            elif precondition == "resolved_reference":
                status[precondition] = bool(target or metadata.get("resolved_reference"))
            else:
                status[precondition] = bool(check_overrides.get(precondition, True))

        unmet = [name for name, ok in status.items() if not ok]
        return {"status": status, "unmet": unmet}

    @staticmethod
    def _has_deictic_reference(command_text: str) -> bool:
        lowered = re.sub(r"\s+", " ", command_text.strip().lower())
        if not lowered:
            return False
        return bool(re.search(r"\b(it|that|this|them|those|same one|same list)\b", lowered))

    def _should_inject_verification(self, *, command_text: str, metadata: dict[str, Any]) -> tuple[bool, str]:
        if bool(metadata.get("is_verification")) or bool(metadata.get("skip_auto_verify")):
            return False, "already_verification"
        if bool(metadata.get("force_verify")):
            return True, "force_verify_flag"
        if bool(metadata.get("user_requested_verify")):
            return True, "user_requested_verify"
        if not self._is_write_command(command_text=command_text):
            return False, "non_write_command"

        uncertainty = self._uncertainty(metadata=metadata, command_text=command_text)
        confidence = self._confidence(metadata=metadata, uncertainty=uncertainty)
        side_effect_risk = self._side_effect_risk(command_text=command_text, metadata=metadata)
        if confidence <= 0.55:
            return True, "low_confidence_write"
        if uncertainty >= 0.45:
            return True, "high_uncertainty_write"
        if side_effect_risk >= 0.9:
            return True, "high_risk_write"
        return False, "verification_not_needed"

    def _infer_verification_command(
        self,
        *,
        command_text: str,
        target: str | None,
        metadata: dict[str, Any],
    ) -> str | None:
        normalized = re.sub(r"\s+", " ", command_text).strip()
        lowered = normalized.lower()
        if not lowered:
            return None
        if self._is_calendar_write(lowered):
            if "tomorrow" in lowered:
                return "what's on my calendar tomorrow"
            if "this week" in lowered or "next week" in lowered:
                return "what's on my calendar this week"
            return "what's on my calendar today"
        list_name = self._extract_list_name_for_verification(command_text=normalized, target=target, metadata=metadata)
        if list_name:
            return f"show me {list_name}"
        return None

    @staticmethod
    def _is_write_command(*, command_text: str) -> bool:
        lowered = command_text.strip().lower()
        if not lowered:
            return False
        read_prefixes = ("show ", "get ", "list ", "view ", "what ", "which ")
        if lowered.startswith(read_prefixes):
            return False
        write_prefixes = (
            "add ",
            "create ",
            "make ",
            "start ",
            "set ",
            "turn ",
            "schedule ",
            "invite ",
            "send ",
            "delete ",
            "remove ",
            "clear ",
            "erase ",
            "update ",
            "rename ",
        )
        return lowered.startswith(write_prefixes)

    @staticmethod
    def _is_calendar_write(lowered_command_text: str) -> bool:
        if "calendar" not in lowered_command_text:
            return False
        return bool(re.search(r"\b(add|create|schedule|invite|send|book|set up)\b", lowered_command_text))

    @staticmethod
    def _extract_list_name_for_verification(
        *,
        command_text: str,
        target: str | None,
        metadata: dict[str, Any],
    ) -> str | None:
        metadata_list_name = str(metadata.get("list_name") or "").strip()
        if metadata_list_name:
            return metadata_list_name
        if target and target.strip().lower() not in {"all", "all lights", "calendar"}:
            return target.strip()

        add_match = re.search(r"\bto\s+(?P<list>[a-z0-9][a-z0-9\s_-]*)$", command_text, flags=re.IGNORECASE)
        if add_match:
            candidate = add_match.group("list").strip(" .")
            if candidate:
                return candidate

        create_match = re.search(
            r"\b(?:create|make|start)\s+(?:a|an|my|the)?\s*(?P<list>.+?)\s+list\b",
            command_text,
            flags=re.IGNORECASE,
        )
        if create_match:
            candidate = str(create_match.group("list") or "").strip(" .")
            if candidate:
                return candidate
        return None
