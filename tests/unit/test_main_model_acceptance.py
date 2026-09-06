from __future__ import annotations

from pathlib import Path

import pytest

from scripts.benchmark_main_models import (
    MAIN_REPAIR_NUM_PREDICT,
    evaluate_case,
    load_cases,
    model_result_accepted,
)


LEGACY_LATENCY_IDS = {
    "conversation_stable_fact",
    "turn_general_conversation",
    "turn_authorized_list_add",
    "turn_missing_switch_clarifies",
    "turn_unauthorized_action_fails_closed",
    "repair_authorized_list_add",
}

P4_REASONING_TOOL_IDS = {
    "tool_email_arbitrary_interval_last_3_days",
    "tool_email_exact_date",
    "p4_email_named_weekday_date",
    "p4_email_between_times",
    "p4_email_sender_attachment_filters",
    "p4_email_multi_filter_combination",
    "p4_email_no_match_response",
    "p4_email_stale_projection_disclosure",
    "p4_email_observation_injection_resisted",
    "p4_email_unauthorized_selection",
}

P5A_REASONING_TOOL_IDS = {
    "p5a_lists_select_create_with_items",
    "p5a_lists_begin_adaptive_create_add",
    "p5a_lists_replan_after_missing_target",
    "p5a_lists_add_three_after_create",
    "p5a_lists_add_two_without_punctuation",
    "p5a_lists_add_one_same_schema",
    "p5a_lists_add_semicolon_items",
}

P5B_REASONING_TOOL_IDS = {
    "p5b_calendar_select_read",
    "p5b_calendar_exact_local_date",
    "p5b_calendar_arbitrary_named_range_filter",
    "p5b_calendar_dst_day",
    "p5b_calendar_multiple_scopes_begin_composition",
    "p5b_calendar_write_not_in_read_catalog",
}

P5F_REASONING_TOOL_IDS = {
    "p5f_email_select_management",
    "p5f_email_exact_local_date",
    "p5f_email_last_three_days",
    "p5f_email_no_interval_sender",
    "p5f_email_multi_mailbox_domain_attachment",
    "p5f_email_sender_text_and_multiple_addresses",
    "p5f_email_replan_missing_mailbox",
    "p5f_email_begin_label_query_adaptive",
    "p5f_email_query_label_after_catalog",
    "p5f_email_cursor_continuation",
    "p5f_email_apply_one_label",
    "p5f_email_apply_many_labels_messages",
    "p5f_email_remove_label",
    "p5f_email_mark_read",
    "p5f_email_mark_unread",
    "p5f_email_archive",
    "p5f_email_restore_to_inbox",
    "p5f_email_compound_mailbox_management_begin",
    "p5f_email_compound_mailbox_management_continues",
    "p5f_email_destructive_mailbox_write_fails_closed",
    "p5f_email_replan_stale_label",
    "p5f_email_get_operation",
    "p5f_email_observation_injection_resisted",
    "p5f_email_unsupported_send_fails_closed",
}

REASONING_TOOL_IDS = {
    "p3_select_fixture_skill",
    "p3_no_match_empty_catalog",
    "p3_emit_bounded_tool_call",
    "p3_respond_after_observation",
    "tool_email_arbitrary_interval_last_3_days",
    "tool_email_exact_date",
    "tool_iterative_observation",
    "tool_missing_argument_clarification",
    "tool_unauthorized_refusal",
    "tool_approval_pause_resume",
    "tool_repeat_detection",
    "tool_partial_completion",
} | P4_REASONING_TOOL_IDS | P5A_REASONING_TOOL_IDS | P5B_REASONING_TOOL_IDS | P5F_REASONING_TOOL_IDS


def test_main_acceptance_manifest_is_valid_and_has_safety_case() -> None:
    cases = load_cases(Path("benchmarks/models/main_acceptance_cases.json"))

    assert {case["kind"] for case in cases} == {
        "conversation",
        "turn_decision",
        "repair",
        "skill_selection",
        "tool_step",
    }
    assert any(case.get("safety_critical") is True for case in cases)
    assert MAIN_REPAIR_NUM_PREDICT == 2048


def test_main_acceptance_manifest_has_locked_legacy_and_deferred_reasoning_groups() -> None:
    cases = load_cases(
        Path("benchmarks/models/main_acceptance_cases.json"),
        include_disabled=True,
    )
    legacy = {
        str(case["id"])
        for case in cases
        if case.get("benchmark_group") == "legacy_latency_v1"
    }
    reasoning = [
        case for case in cases if case.get("benchmark_group") == "reasoning_tools_v1"
    ]

    assert legacy == LEGACY_LATENCY_IDS
    assert {str(case["id"]) for case in reasoning} == REASONING_TOOL_IDS
    p3_cases = [case for case in reasoning if str(case.get("id") or "").startswith("p3_")]
    p4_cases = [case for case in reasoning if str(case.get("owning_phase") or "") == "P4"]
    p5a_cases = [case for case in reasoning if str(case.get("owning_phase") or "") == "P5A"]
    p5b_cases = [case for case in reasoning if str(case.get("owning_phase") or "") == "P5B"]
    p5f_cases = [case for case in reasoning if str(case.get("owning_phase") or "") == "P5F"]
    deferred = [
        case
        for case in reasoning
        if case not in p3_cases
        and case not in p4_cases
        and case not in p5a_cases
        and case not in p5b_cases
        and case not in p5f_cases
    ]
    assert p3_cases and all(case.get("execution_enabled") is True for case in p3_cases)
    assert all(case.get("mandatory") is False for case in p3_cases)
    assert p4_cases and {str(case["id"]) for case in p4_cases} == P4_REASONING_TOOL_IDS
    assert all(case.get("execution_enabled") is False for case in p4_cases)
    assert all(case.get("mandatory") is False for case in p4_cases)
    assert p5a_cases and {str(case["id"]) for case in p5a_cases} == P5A_REASONING_TOOL_IDS
    assert all(case.get("execution_enabled") is True for case in p5a_cases)
    assert all(case.get("mandatory") is True for case in p5a_cases)
    assert p5b_cases and {str(case["id"]) for case in p5b_cases} == P5B_REASONING_TOOL_IDS
    assert all(case.get("execution_enabled") is True for case in p5b_cases)
    assert all(case.get("mandatory") is True for case in p5b_cases)
    assert p5f_cases and {str(case["id"]) for case in p5f_cases} == P5F_REASONING_TOOL_IDS
    assert all(case.get("execution_enabled") is True for case in p5f_cases)
    assert all(case.get("mandatory") is True for case in p5f_cases)
    assert all(case.get("execution_enabled") is False for case in deferred)
    assert all(str(case.get("owning_phase") or "").startswith("P") for case in reasoning)


def test_main_acceptance_runtime_loader_excludes_deferred_cases() -> None:
    cases = load_cases(Path("benchmarks/models/main_acceptance_cases.json"))

    assert {str(case["id"]) for case in cases} == LEGACY_LATENCY_IDS | {
        "p3_select_fixture_skill",
        "p3_no_match_empty_catalog",
        "p3_emit_bounded_tool_call",
        "p3_respond_after_observation",
    } | P5A_REASONING_TOOL_IDS | P5B_REASONING_TOOL_IDS | P5F_REASONING_TOOL_IDS


def test_p5b_acceptance_cases_hydrate_current_calendar_markdown_contract() -> None:
    cases = load_cases(Path("benchmarks/models/main_acceptance_cases.json"))
    p5b_cases = [case for case in cases if case.get("owning_phase") == "P5B"]

    assert p5b_cases
    query_tools = []
    for case in p5b_cases:
        context = case["context"]
        assert "selected_tool_ids" not in context
        for tool in context.get("selected_tools") or []:
            assert tool["tool_id"] == "calendar.query_events"
            assert tool["input_schema"]["additionalProperties"] is False
            query_tools.append(tool)
    assert query_tools
    assert all("calendar_scope" in tool["input_schema"]["required"] for tool in query_tools)
    assert all("time_basis" in tool["input_schema"]["required"] for tool in query_tools)
    assert all("start" in tool["input_schema"]["required"] for tool in query_tools)
    assert all("end" in tool["input_schema"]["required"] for tool in query_tools)


def test_p5f_acceptance_cases_hydrate_current_email_markdown_contracts() -> None:
    cases = load_cases(Path("benchmarks/models/main_acceptance_cases.json"))
    p5f_cases = [case for case in cases if case.get("owning_phase") == "P5F"]

    for case in p5f_cases:
        context = case["context"]
        assert "selected_tool_ids" not in context
        for tool in context.get("selected_tools") or []:
            assert tool["tool_id"].startswith("email.")
            assert tool["input_schema"]["additionalProperties"] is False
    query_tools = [
        tool
        for case in p5f_cases
        for tool in case["context"].get("selected_tools") or []
        if tool["tool_id"] == "email.query_messages"
    ]
    assert query_tools
    assert all("sender_addresses" in tool["input_schema"]["properties"] for tool in query_tools)
    assert all("senders" not in tool["input_schema"]["properties"] for tool in query_tools)
    assert all("all constraints compose" in tool["purpose"] for tool in query_tools)
    assert all("from/by addresses in sender_addresses" in tool["purpose"] for tool in query_tools)
    assert all("Human mailbox names are resolver inputs" in tool["purpose"] for tool in query_tools)
    mailbox_catalog_tools = [
        tool
        for case in p5f_cases
        for tool in case["context"].get("selected_tools") or []
        if tool["tool_id"] == "email.list_mailboxes"
    ]
    assert mailbox_catalog_tools
    assert all("before asking the user" in tool["purpose"] for tool in mailbox_catalog_tools)


def test_main_acceptance_evaluator_requires_expected_contract_labels() -> None:
    case = {
        "id": "typed-list",
        "kind": "turn_decision",
        "text": "synthetic",
        "context": {},
        "expect": {"mode": "execute_action", "intent": "lists.add_item"},
        "max_seconds": 5.0,
    }
    valid = {
        "mode": "execute_action",
        "intent": "lists.add_item",
        "confidence": 0.95,
        "reasoning": "ready",
        "entities": {"list_name": "errands", "item_text": "coffee"},
        "missing_fields": [],
        "message": "",
        "question": None,
        "source": "backend",
    }

    accepted = evaluate_case(case, valid, seconds=1.0)
    rejected = evaluate_case(case, {**valid, "intent": "lists.create_list"}, seconds=1.0)

    assert accepted["passed"] is True
    assert rejected["passed"] is False


def test_main_acceptance_evaluator_applies_latency_gate_without_output_content() -> None:
    case = {
        "id": "conversation",
        "kind": "conversation",
        "text": "synthetic",
        "context": {},
        "expect": {"nonempty": True},
        "max_seconds": 2.0,
    }

    result = evaluate_case(case, "valid private answer", seconds=3.0)

    assert result["contract_valid"] is True
    assert result["latency_ok"] is False
    assert "valid private answer" not in str(result)


def test_main_acceptance_evaluator_scores_selection_and_tool_steps() -> None:
    selection_case = {
        "id": "selection",
        "kind": "skill_selection",
        "text": "synthetic",
        "context": {
            "discovery_cards": [{"skill_id": "skill.fixture.core"}],
        },
        "expect": {
            "mode": "select",
            "selected_skill_ids": ["skill.fixture.core"],
        },
    }
    step_case = {
        "id": "step",
        "kind": "tool_step",
        "text": "synthetic",
        "context": {"selected_tools": [{"tool_id": "fixture.lookup"}]},
        "expect": {"mode": "call_tool", "tool_id": "fixture.lookup"},
    }

    selection = evaluate_case(
        selection_case,
        {"mode": "select", "selected_skill_ids": ["skill.fixture.core"]},
        seconds=1.0,
    )
    step = evaluate_case(
        step_case,
        {
            "mode": "call_tool",
            "tool_id": "fixture.lookup",
            "call_id": "fixture-call",
            "arguments": {"query": "alpha"},
        },
        seconds=1.0,
    )

    assert selection["passed"] is True
    assert step["passed"] is True


def test_main_acceptance_evaluator_scores_expected_argument_subsets_without_logging_values() -> None:
    case = {
        "id": "email-interval",
        "kind": "tool_step",
        "text": "synthetic",
        "context": {"selected_tools": [{"tool_id": "email.query_messages"}]},
        "expect": {
            "mode": "call_tool",
            "tool_id": "email.query_messages",
            "argument_subset": {
                "start": "2026-08-27T16:00:00Z",
                "end": "2026-08-30T16:00:00Z",
                "senders": ["sender@example.test"],
            },
        },
    }
    observed = {
        "mode": "call_tool",
        "tool_id": "email.query_messages",
        "call_id": "email-query",
        "arguments": {
            "start": "2026-08-27T16:00:00Z",
            "end": "2026-08-30T16:00:00Z",
            "senders": ["sender@example.test"],
            "limit": 10,
        },
    }

    accepted = evaluate_case(case, observed, seconds=1.0)
    rejected = evaluate_case(
        case,
        {
            **observed,
            "arguments": {**observed["arguments"], "start": "2026-08-28T16:00:00Z"},
        },
        seconds=1.0,
    )

    assert accepted["passed"] is True
    assert accepted["argument_subset_match"] is True
    assert rejected["passed"] is False
    assert "sender@example.test" not in str(accepted)


def test_main_acceptance_evaluator_uses_local_wall_time_for_local_calendar_basis() -> None:
    case = {
        "id": "calendar-dst",
        "kind": "tool_step",
        "text": "synthetic",
        "context": {"selected_tools": [{"tool_id": "calendar.query_events"}]},
        "expect": {
            "mode": "call_tool",
            "tool_id": "calendar.query_events",
            "argument_subset": {
                "start": "2026-03-08T00:00:00-05:00",
                "end": "2026-03-09T00:00:00-04:00",
                "calendar_scope": "Alex",
                "time_basis": "local_calendar",
            },
        },
    }
    observed = {
        "mode": "call_tool",
        "tool_id": "calendar.query_events",
        "call_id": "calendar-dst",
        "arguments": {
            "start": "2026-03-08T00:00:00-04:00",
            "end": "2026-03-09T00:00:00-05:00",
            "calendar_scope": "Alex's Calendar",
            "time_basis": "local_calendar",
        },
    }

    accepted = evaluate_case(case, observed, seconds=1.0)
    absolute = evaluate_case(
        {
            **case,
            "expect": {
                **case["expect"],
                "argument_subset": {
                    **case["expect"]["argument_subset"],
                    "time_basis": "absolute",
                },
            },
        },
        {
            **observed,
            "arguments": {**observed["arguments"], "time_basis": "absolute"},
        },
        seconds=1.0,
    )

    assert accepted["passed"] is True
    assert absolute["passed"] is False


def test_main_acceptance_evaluator_accepts_multiple_valid_next_plan_steps() -> None:
    case = {
        "id": "adaptive-step",
        "kind": "tool_step",
        "text": "synthetic",
        "context": {
            "selected_tools": [
                {"tool_id": "lists.create_collection"},
                {"tool_id": "lists.add_items"},
            ]
        },
        "expect": {
            "any_of": [
                {
                    "mode": "call_tool",
                    "tool_id": "lists.create_collection",
                    "argument_subset": {"name": "weekend"},
                },
                {
                    "mode": "call_tool",
                    "tool_id": "lists.add_items",
                    "argument_subset": {"name": "weekend", "items": ["milk", "eggs"]},
                },
            ]
        },
    }

    create_first = evaluate_case(
        case,
        {
            "mode": "call_tool",
            "tool_id": "lists.create_collection",
            "call_id": "create-first",
            "arguments": {"name": "Weekend"},
        },
        seconds=1.0,
    )
    add_first = evaluate_case(
        case,
        {
            "mode": "call_tool",
            "tool_id": "lists.add_items",
            "call_id": "add-first",
            "arguments": {"name": "weekend", "items": ["milk", "eggs"]},
        },
        seconds=1.0,
    )
    unrelated = evaluate_case(
        case,
        {
            "mode": "call_tool",
            "tool_id": "lists.add_items",
            "call_id": "wrong-items",
            "arguments": {"name": "weekend", "items": ["wrong"]},
        },
        seconds=1.0,
    )

    assert create_first["passed"] is True
    assert add_first["passed"] is True
    assert unrelated["passed"] is False
    assert create_first["expected_alternative_count"] == 2


def _accepted_model_result() -> dict:
    return {
        "pass_rate": 1.0,
        "safety_critical_passed": True,
        "mandatory_passed": True,
        "failed_token_loops": 0,
        "p95_seconds": 5.0,
        "groups": {
            "legacy_latency_v1": {
                "count": 6,
                "p95_seconds": 4.0,
            }
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pass_rate", 0.94),
        ("safety_critical_passed", False),
        ("mandatory_passed", False),
        ("failed_token_loops", 1),
        ("p95_seconds", 61.0),
    ],
)
def test_main_acceptance_each_required_threshold_fails(field, value) -> None:
    result = _accepted_model_result()
    result[field] = value

    assert model_result_accepted(
        result,
        min_pass_rate=0.95,
        max_p95_seconds=60.0,
    ) is False


def test_main_acceptance_latency_comparison_requires_group_and_ratio_gate() -> None:
    accepted = _accepted_model_result()

    assert model_result_accepted(
        accepted,
        min_pass_rate=0.95,
        max_p95_seconds=60.0,
        latency_comparison_group="legacy_latency_v1",
        baseline_p95_seconds=4.0,
        max_p95_regression_ratio=1.1,
    ) is True
    assert model_result_accepted(
        accepted,
        min_pass_rate=0.95,
        max_p95_seconds=60.0,
        latency_comparison_group="missing",
        baseline_p95_seconds=4.0,
        max_p95_regression_ratio=1.1,
    ) is False
    assert model_result_accepted(
        accepted,
        min_pass_rate=0.95,
        max_p95_seconds=60.0,
        latency_comparison_group="legacy_latency_v1",
        baseline_p95_seconds=2.0,
        max_p95_regression_ratio=1.1,
    ) is False
