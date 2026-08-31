from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.accelerator.client import accelerator_request_headers  # noqa: E402
from app.core.main_backend import OllamaMainConversationBackend, OllamaMainRepairBackend  # noqa: E402
from app.core.main_repair_contract import normalize_repair_payload  # noqa: E402
from app.core.main_turn_contract import normalize_main_turn_decision  # noqa: E402
from app.core.tool_loop_types import ModelStep, SkillSelection, ToolLoopContractError  # noqa: E402


ALLOWED_KINDS = frozenset(
    {"conversation", "turn_decision", "repair", "skill_selection", "tool_step"}
)
MAIN_REPAIR_NUM_PREDICT = 1024


def _contains_expected_arguments(observed: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(observed, dict) and all(
            key in observed and _contains_expected_arguments(observed[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(observed, (list, tuple)) and len(observed) == len(expected) and all(
            _contains_expected_arguments(item, expected[index])
            for index, item in enumerate(observed)
        )
    if isinstance(observed, str) and isinstance(expected, str):
        try:
            observed_datetime = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            expected_datetime = datetime.fromisoformat(expected.replace("Z", "+00:00"))
        except ValueError:
            return " ".join(observed.split()).casefold() == " ".join(expected.split()).casefold()
        if (
            observed_datetime.tzinfo is not None
            and observed_datetime.utcoffset() is not None
            and expected_datetime.tzinfo is not None
            and expected_datetime.utcoffset() is not None
        ):
            return observed_datetime.astimezone(UTC) == expected_datetime.astimezone(UTC)
    return observed == expected


def load_cases(path: Path, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError("main_acceptance_cases_required")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("main_acceptance_case_invalid")
        case_id = str(raw.get("id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        text = str(raw.get("text") or "").strip()
        context = raw.get("context")
        expected = raw.get("expect")
        benchmark_group = str(raw.get("benchmark_group") or "").strip()
        if (
            not case_id
            or case_id in seen
            or kind not in ALLOWED_KINDS
            or not text
            or not isinstance(context, dict)
            or not isinstance(expected, dict)
            or not benchmark_group
        ):
            raise ValueError("main_acceptance_case_invalid")
        seen.add(case_id)
        if raw.get("execution_enabled") is False and not include_disabled:
            continue
        cases.append(dict(raw))
    if not cases:
        raise ValueError("main_acceptance_cases_required")
    return cases


def evaluate_case(
    case: dict[str, Any],
    observed: str | dict[str, Any] | None,
    *,
    seconds: float,
) -> dict[str, Any]:
    kind = str(case["kind"])
    expected = dict(case["expect"])
    max_seconds = max(0.1, float(case.get("max_seconds", 60.0)))
    observed_mode = None
    observed_status = None
    observed_intent = None
    observed_tool_id = None
    observed_arguments: dict[str, Any] | None = None
    observed_skill_ids: list[str] | None = None
    contract_valid = False
    contract_error_code = None
    if kind == "conversation":
        contract_valid = isinstance(observed, str) and bool(observed.strip())
    elif kind == "turn_decision":
        normalized = normalize_main_turn_decision(observed if isinstance(observed, dict) else None)
        contract_valid = normalized is not None
        contract_error_code = None if contract_valid else "turn_decision_invalid"
        if normalized is not None:
            observed_mode = normalized["mode"]
            observed_intent = normalized["intent"]
    elif kind == "repair":
        normalized = normalize_repair_payload(observed if isinstance(observed, dict) else None)
        contract_valid = normalized is not None
        contract_error_code = None if contract_valid else "repair_payload_invalid"
        if normalized is not None:
            observed_status = normalized["status"]
            observed_intent = normalized["intent"]
    elif kind == "skill_selection":
        allowed_skill_ids = {
            str(item.get("skill_id") or "").strip().casefold()
            for item in case.get("context", {}).get("discovery_cards") or []
            if isinstance(item, dict)
        }
        try:
            normalized_selection = SkillSelection.from_mapping(
                observed if isinstance(observed, dict) else {},
                allowed_skill_ids=allowed_skill_ids,
                max_selected_skills=3,
            )
        except ToolLoopContractError as exc:
            normalized_selection = None
            contract_error_code = str(exc)
        contract_valid = normalized_selection is not None
        if normalized_selection is not None:
            observed_mode = normalized_selection.mode
            observed_skill_ids = list(normalized_selection.selected_skill_ids)
    else:
        allowed_tool_ids = {
            str(item.get("tool_id") or "").strip().casefold()
            for item in case.get("context", {}).get("selected_tools") or []
            if isinstance(item, dict)
        }
        try:
            normalized_step = ModelStep.from_mapping(
                observed if isinstance(observed, dict) else {},
                allowed_tool_ids=allowed_tool_ids,
            )
        except ToolLoopContractError as exc:
            normalized_step = None
            contract_error_code = str(exc)
        contract_valid = normalized_step is not None
        if normalized_step is not None:
            observed_mode = normalized_step.mode
            observed_tool_id = normalized_step.tool_id
            observed_arguments = (
                dict(normalized_step.arguments)
                if normalized_step.arguments is not None
                else None
            )

    alternatives_raw = expected.get("any_of")
    alternatives = (
        [dict(item) for item in alternatives_raw if isinstance(item, dict)]
        if isinstance(alternatives_raw, list)
        else [expected]
    )
    alternative_results: list[tuple[bool, bool | None]] = []
    for alternative in alternatives:
        label_match = contract_valid
        if "mode" in alternative:
            label_match = label_match and observed_mode == alternative.get("mode")
        if "status" in alternative:
            label_match = label_match and observed_status == alternative.get("status")
        if "intent" in alternative:
            label_match = label_match and observed_intent == alternative.get("intent")
        if "tool_id" in alternative:
            label_match = label_match and observed_tool_id == alternative.get("tool_id")
        if "selected_skill_ids" in alternative:
            label_match = label_match and observed_skill_ids == alternative.get("selected_skill_ids")
        argument_subset = alternative.get("argument_subset")
        subset_match = (
            _contains_expected_arguments(observed_arguments, argument_subset)
            if isinstance(argument_subset, dict)
            else None
        )
        if subset_match is not None:
            label_match = label_match and subset_match
        alternative_results.append((bool(label_match), subset_match))
    label_match = any(match for match, _ in alternative_results)
    subset_results = [subset for _, subset in alternative_results if subset is not None]
    argument_subset_match = any(subset_results) if subset_results else None
    latency_ok = seconds <= max_seconds
    return {
        "passed": bool(label_match and latency_ok),
        "contract_valid": contract_valid,
        "contract_error_code": contract_error_code,
        "latency_ok": latency_ok,
        "expected_mode": expected.get("mode"),
        "expected_status": expected.get("status"),
        "expected_intent": expected.get("intent"),
        "observed_mode": observed_mode,
        "observed_status": observed_status,
        "observed_intent": observed_intent,
        "expected_tool_id": expected.get("tool_id"),
        "observed_tool_id": observed_tool_id,
        "expected_selected_skill_ids": expected.get("selected_skill_ids"),
        "observed_selected_skill_ids": observed_skill_ids,
        "argument_subset_match": argument_subset_match,
        "expected_alternative_count": len(alternatives),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 4)


def _model_metadata(base_url: str, model: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        import httpx

        response = httpx.get(
            f"{base_url.rstrip('/')}/api/tags",
            headers=accelerator_request_headers("runtime_health"),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        models = response.json().get("models")
    except Exception:
        return {"name": model, "digest": None, "size": None}
    if not isinstance(models, list):
        return {"name": model, "digest": None, "size": None}
    for row in models:
        if not isinstance(row, dict):
            continue
        if str(row.get("name") or row.get("model") or "").strip() == model:
            return {
                "name": model,
                "digest": str(row.get("digest") or "").strip() or None,
                "size": row.get("size") if isinstance(row.get("size"), int) else None,
                "modified_at": str(row.get("modified_at") or "").strip() or None,
            }
    return {"name": model, "digest": None, "size": None}


def run_model(
    *,
    model: str,
    cases: list[dict[str, Any]],
    base_url: str,
    timeout_seconds: float,
    num_ctx: int,
) -> dict[str, Any]:
    conversation = OllamaMainConversationBackend(
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        num_ctx=num_ctx,
        num_predict=1024,
        think="low",
        turn_decision_think=False,
    )
    repair = OllamaMainRepairBackend(
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        num_ctx=num_ctx,
        num_predict=MAIN_REPAIR_NUM_PREDICT,
        think=False,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        kind = str(case["kind"])
        started = time.perf_counter()
        error_type = None
        try:
            if kind == "conversation":
                observed = conversation.respond(str(case["text"]), dict(case["context"]))
                status = conversation.status()
            elif kind == "turn_decision":
                observed = conversation.decide_turn(str(case["text"]), dict(case["context"]))
                status = conversation.status()
            elif kind == "skill_selection":
                case_context = dict(case["context"])
                discovery_cards = list(case_context.pop("discovery_cards", []))
                allowed_skill_ids = {
                    str(item.get("skill_id") or "").strip().casefold()
                    for item in discovery_cards
                    if isinstance(item, dict)
                }
                observed = None
                if not allowed_skill_ids:
                    observed = {
                        "mode": "no_match",
                        "selected_skill_ids": [],
                        "reason_code": "no_relevant_skill",
                    }
                    status = {}
                else:
                    for schema_attempt in range(2):
                        attempt_context = {
                            **case_context,
                            "schema_correction": schema_attempt > 0,
                        }
                        observed = conversation.select_skills(
                            str(case["text"]),
                            discovery_cards,
                            attempt_context,
                        )
                        try:
                            SkillSelection.from_mapping(
                                observed if isinstance(observed, dict) else {},
                                allowed_skill_ids=allowed_skill_ids,
                                max_selected_skills=3,
                            )
                        except ToolLoopContractError:
                            continue
                        break
                    status = conversation.status()
            elif kind == "tool_step":
                case_context = dict(case["context"])
                selected_tools = list(case_context.pop("selected_tools", []))
                observations = list(case_context.pop("observations", []))
                temporal_contexts = dict(case_context.pop("temporal_contexts", {}))
                allowed_tool_ids = {
                    str(item.get("tool_id") or "").strip().casefold()
                    for item in selected_tools
                    if isinstance(item, dict)
                }
                observed = None
                for schema_attempt in range(2):
                    attempt_context = {
                        **case_context,
                        "schema_correction": schema_attempt > 0,
                    }
                    observed = conversation.next_tool_step(
                        str(case["text"]),
                        selected_tools,
                        observations,
                        temporal_contexts,
                        attempt_context,
                    )
                    try:
                        ModelStep.from_mapping(
                            observed if isinstance(observed, dict) else {},
                            allowed_tool_ids=allowed_tool_ids,
                        )
                    except ToolLoopContractError:
                        continue
                    break
                status = conversation.status()
            else:
                observed = repair.repair_action(str(case["text"]), dict(case["context"]))
                status = repair.status()
        except Exception as exc:
            observed = None
            status = {}
            error_type = type(exc).__name__
        seconds = time.perf_counter() - started
        evaluation = evaluate_case(case, observed, seconds=seconds)
        sequence = status.get("last_sequence_metrics") if isinstance(status, dict) else None
        rows.append(
            {
                "case_id": str(case["id"]),
                "kind": kind,
                "benchmark_group": str(case["benchmark_group"]),
                "mandatory": bool(case.get("mandatory", False)),
                "safety_critical": bool(case.get("safety_critical", False)),
                "seconds": round(seconds, 4),
                "error_type": error_type,
                **evaluation,
                "sequence_metrics": sequence if isinstance(sequence, dict) else None,
            }
        )
    durations = [float(row["seconds"]) for row in rows]
    passed = sum(1 for row in rows if row["passed"])
    failed_loops = sum(
        1
        for row in rows
        if isinstance(row.get("sequence_metrics"), dict)
        and row["sequence_metrics"].get("failed_loop") is True
    )
    groups: dict[str, dict[str, Any]] = {}
    for group_name in sorted({str(row["benchmark_group"]) for row in rows}):
        group_rows = [row for row in rows if row["benchmark_group"] == group_name]
        group_durations = [float(row["seconds"]) for row in group_rows]
        group_passed = sum(1 for row in group_rows if row["passed"])
        groups[group_name] = {
            "count": len(group_rows),
            "passed": group_passed,
            "pass_rate": round(group_passed / len(group_rows), 4) if group_rows else 0.0,
            "p50_seconds": _percentile(group_durations, 0.50),
            "p95_seconds": _percentile(group_durations, 0.95),
        }
    return {
        "model": _model_metadata(base_url, model, timeout_seconds),
        "case_count": len(rows),
        "passed": passed,
        "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        "safety_critical_passed": all(
            row["passed"] for row in rows if row["safety_critical"]
        ),
        "mandatory_passed": all(row["passed"] for row in rows if row["mandatory"]),
        "failed_token_loops": failed_loops,
        "p50_seconds": _percentile(durations, 0.50),
        "p95_seconds": _percentile(durations, 0.95),
        "groups": groups,
        "results": rows,
    }


def model_result_accepted(
    result: dict[str, Any],
    *,
    min_pass_rate: float,
    max_p95_seconds: float,
    latency_comparison_group: str | None = None,
    baseline_p95_seconds: float | None = None,
    max_p95_regression_ratio: float | None = None,
) -> bool:
    accepted = bool(
        float(result.get("pass_rate") or 0.0) >= float(min_pass_rate)
        and result.get("safety_critical_passed") is True
        and result.get("mandatory_passed") is True
        and int(result.get("failed_token_loops") or 0) == 0
        and result.get("p95_seconds") is not None
        and float(result["p95_seconds"]) <= float(max_p95_seconds)
    )
    if latency_comparison_group is None:
        return accepted
    groups = result.get("groups")
    group = groups.get(latency_comparison_group) if isinstance(groups, dict) else None
    if not isinstance(group, dict) or int(group.get("count") or 0) < 1:
        return False
    if baseline_p95_seconds is None or max_p95_regression_ratio is None:
        return False
    group_p95 = group.get("p95_seconds")
    if group_p95 is None:
        return False
    return accepted and float(group_p95) <= (
        float(baseline_p95_seconds) * float(max_p95_regression_ratio)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run content-free Main model acceptance cases.")
    parser.add_argument("--base-url", default="http://accelerator-admission:8040")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument(
        "--cases",
        default=str(REPO_ROOT / "benchmarks" / "models" / "main_acceptance_cases.json"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--min-pass-rate", type=float, default=0.95)
    parser.add_argument("--max-p95-seconds", type=float, default=60.0)
    parser.add_argument("--latency-comparison-group")
    parser.add_argument("--baseline-p95-seconds", type=float)
    parser.add_argument("--max-p95-regression-ratio", type=float)
    parser.add_argument("--output")
    args = parser.parse_args()

    models = [str(item).strip() for item in (args.models or ["gpt-oss:20b", "qwen3.8:27b"])]
    if not models or any(not model for model in models):
        parser.error("at least one non-empty --model is required")
    cases = load_cases(Path(args.cases).resolve())
    comparison_values = (
        args.latency_comparison_group,
        args.baseline_p95_seconds,
        args.max_p95_regression_ratio,
    )
    if any(value is not None for value in comparison_values) and not all(
        value is not None for value in comparison_values
    ):
        parser.error("all latency comparison inputs are required together")
    if args.baseline_p95_seconds is not None and args.baseline_p95_seconds <= 0:
        parser.error("--baseline-p95-seconds must be positive")
    if args.max_p95_regression_ratio is not None and args.max_p95_regression_ratio <= 0:
        parser.error("--max-p95-regression-ratio must be positive")
    results = [
        run_model(
            model=model,
            cases=cases,
            base_url=str(args.base_url).rstrip("/"),
            timeout_seconds=max(1.0, min(float(args.timeout_seconds), 900.0)),
            num_ctx=max(512, int(args.num_ctx)),
        )
        for model in models
    ]
    accepted = all(
        model_result_accepted(
            result,
            min_pass_rate=float(args.min_pass_rate),
            max_p95_seconds=float(args.max_p95_seconds),
            latency_comparison_group=(
                str(args.latency_comparison_group).strip()
                if args.latency_comparison_group is not None
                else None
            ),
            baseline_p95_seconds=args.baseline_p95_seconds,
            max_p95_regression_ratio=args.max_p95_regression_ratio,
        )
        for result in results
    )
    report = {
        "schema_version": 1,
        "content_free": True,
        "num_ctx": max(512, int(args.num_ctx)),
        "accepted": accepted,
        "models": results,
    }
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
