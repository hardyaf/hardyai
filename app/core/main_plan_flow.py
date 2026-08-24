from __future__ import annotations

import re
from typing import Any

from app.core.agent_loop import MainAgentLoop
from app.core.executor import MainAgentExecutor
from app.core.session_store import SessionRecord


class MainPlanFlow:
    """Own bounded Main plan execution and token-session accounting."""

    def __init__(self, router_ports: Any) -> None:
        self._router = router_ports

    @staticmethod
    def _main_agent_token_session(session: SessionRecord) -> dict[str, Any]:
        raw = session.context_reference.get("main_agent_token_session")
        if isinstance(raw, dict):
            return dict(raw)
        return {"turn_summaries": [], "total_turns": 0}

    def _update_main_agent_token_session(
        self,
        *,
        session: SessionRecord,
        goal_text: str,
        execution: dict[str, Any],
    ) -> None:
        router = self._router
        if not router._main_agent_token_session_enabled:
            return

        current = router._main_agent_token_session(session)
        existing_summaries = current.get("turn_summaries")
        if not isinstance(existing_summaries, list):
            existing_summaries = []
        summaries = [str(item).strip() for item in existing_summaries if str(item).strip()]

        requested_count = int(execution.get("requested_count") or 0)
        success_count = int(execution.get("success_count") or 0)
        loop_state = str(execution.get("loop_state") or "").strip() or "UNKNOWN"
        status = str(execution.get("status") or "").strip() or "unknown"
        agent_loop = execution.get("agent_loop")
        terminal_message = ""
        context_budget: dict[str, Any] = {}
        if isinstance(agent_loop, dict):
            terminal_message = str(agent_loop.get("terminal_message") or "").strip()
            maybe_budget = agent_loop.get("context_budget")
            if isinstance(maybe_budget, dict):
                context_budget = maybe_budget

        summary = (
            f"goal={router._truncate_for_token_session(goal_text, 72)} | "
            f"status={status} | loop={loop_state} | steps={success_count}/{requested_count}"
        )
        if terminal_message:
            summary = f"{summary} | note={router._truncate_for_token_session(terminal_message, 72)}"
        summaries.append(summary)
        if len(summaries) > router._main_agent_token_session_max_turns:
            summaries = summaries[-router._main_agent_token_session_max_turns :]

        updated = {
            "turn_summaries": summaries,
            "total_turns": int(current.get("total_turns") or 0) + 1,
            "last_status": status,
            "last_loop_state": loop_state,
            "last_used_tokens_estimate": int(context_budget.get("used_tokens_estimate") or 0),
            "last_max_tokens_estimate": int(context_budget.get("max_tokens_estimate") or 0),
            "last_trimmed": bool(context_budget.get("trimmed")),
            "last_compaction": context_budget.get("compaction") if isinstance(context_budget, dict) else None,
        }
        context_reference = dict(session.context_reference)
        context_reference["main_agent_token_session"] = updated
        session.context_reference = context_reference
        session.touch()
        router._session_store.save(session)

    @staticmethod
    def _truncate_for_token_session(value: str, limit: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[: max(0, limit - 3)]}..."

    def _execute_main_plan(
        self,
        plan: dict[str, Any],
        session: SessionRecord,
        session_id: str,
        source_interface: str,
        requested_by_user_id: str,
        agent_id: str,
        goal_text: str = "",
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        router = self._router
        token_session = router._main_agent_token_session(session)
        loop = MainAgentLoop(
            planner=router._main_agent_planner,
            evaluator=router._main_agent_evaluator,
            context_budget=router._main_agent_context_budget,
            limits=router._main_agent_limits,
            event_hook=lambda event_type, payload: router._event_log.record(
                event_type=event_type,
                session_id=session_id,
                payload=payload,
            ),
        )
        executor = MainAgentExecutor(
            micro_jarvis=router._micro_jarvis,
            run_fast_command=lambda decision, planner_decision: router._execute_fast_command(
                decision=decision,
                source_interface=source_interface,
                requested_by_user_id=requested_by_user_id,
                agent_id=agent_id,
                request_id=(
                    f"{router._request_id_var.get()}:main-step:{planner_decision.step_number}"
                ),
                request_context=request_context,
            ),
        )
        execution = loop.run(
            goal_text=goal_text,
            plan=plan,
            agent_id=agent_id,
            execution_context={
                "session_id": session_id,
                "source_interface": source_interface,
                "requested_by_user_id": requested_by_user_id,
                "agent_id": agent_id,
                "token_session_turn_summaries": token_session.get("turn_summaries", []),
                **(request_context or {}),
            },
            executor=executor,
            content_policy_gate=router._main_agent_content_policy_gate,
        )

        router._update_main_agent_token_session(
            session=session,
            goal_text=goal_text,
            execution=execution,
        )

        for result in execution.get("results", []):
            if not isinstance(result, dict):
                continue
            classification = result.get("classification")
            intent_value = ""
            if isinstance(classification, dict):
                intent_value = str(classification.get("intent") or "").strip()
            router._event_log.record(
                event_type="main.plan.command.executed",
                session_id=session_id,
                payload={
                    "index": result.get("index"),
                    "command_text": result.get("command_text"),
                    "intent": intent_value,
                    "result_status": result.get("status"),
                },
            )

        agent_loop = execution.get("agent_loop")
        run_id = None
        if isinstance(agent_loop, dict):
            run_id = agent_loop.get("run_id")
        router._event_log.record(
            event_type="main.agent_loop.completed",
            session_id=session_id,
            payload={
                "run_id": run_id,
                "status": execution.get("status"),
                "loop_state": execution.get("loop_state"),
                "requested_count": execution.get("requested_count"),
                "success_count": execution.get("success_count"),
                "failed_count": execution.get("failed_count"),
            },
        )
        return execution
