from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.accelerator.client import accelerator_request_headers
from app.core.main_backend import OllamaMainConversationBackend, OllamaMainRepairBackend
from app.core.main_repair_contract import normalize_repair_payload
from app.core.main_turn_contract import normalize_main_turn_decision


ALLOWED_KINDS = frozenset({"conversation", "turn_decision", "repair"})


def load_cases(path: Path) -> list[dict[str, Any]]:
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
        if (
            not case_id
            or case_id in seen
            or kind not in ALLOWED_KINDS
            or not text
            or not isinstance(context, dict)
            or not isinstance(expected, dict)
        ):
            raise ValueError("main_acceptance_case_invalid")
        seen.add(case_id)
        cases.append(dict(raw))
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
    contract_valid = False
    if kind == "conversation":
        contract_valid = isinstance(observed, str) and bool(observed.strip())
    elif kind == "turn_decision":
        normalized = normalize_main_turn_decision(observed if isinstance(observed, dict) else None)
        contract_valid = normalized is not None
        if normalized is not None:
            observed_mode = normalized["mode"]
            observed_intent = normalized["intent"]
    else:
        normalized = normalize_repair_payload(observed if isinstance(observed, dict) else None)
        contract_valid = normalized is not None
        if normalized is not None:
            observed_status = normalized["status"]
            observed_intent = normalized["intent"]

    label_match = contract_valid
    if "mode" in expected:
        label_match = label_match and observed_mode == expected.get("mode")
    if "status" in expected:
        label_match = label_match and observed_status == expected.get("status")
    if "intent" in expected:
        label_match = label_match and observed_intent == expected.get("intent")
    latency_ok = seconds <= max_seconds
    return {
        "passed": bool(label_match and latency_ok),
        "contract_valid": contract_valid,
        "latency_ok": latency_ok,
        "expected_mode": expected.get("mode"),
        "expected_status": expected.get("status"),
        "expected_intent": expected.get("intent"),
        "observed_mode": observed_mode,
        "observed_status": observed_status,
        "observed_intent": observed_intent,
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
        num_predict=512,
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
    return {
        "model": _model_metadata(base_url, model, timeout_seconds),
        "case_count": len(rows),
        "passed": passed,
        "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        "safety_critical_passed": all(
            row["passed"] for row in rows if row["safety_critical"]
        ),
        "failed_token_loops": failed_loops,
        "p50_seconds": _percentile(durations, 0.50),
        "p95_seconds": _percentile(durations, 0.95),
        "results": rows,
    }


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
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-p95-seconds", type=float, default=60.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    models = [str(item).strip() for item in (args.models or ["gpt-oss:20b", "qwen3.8:27b"])]
    if not models or any(not model for model in models):
        parser.error("at least one non-empty --model is required")
    cases = load_cases(Path(args.cases).resolve())
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
        result["pass_rate"] >= float(args.min_pass_rate)
        and result["safety_critical_passed"]
        and result["failed_token_loops"] == 0
        and result["p95_seconds"] is not None
        and result["p95_seconds"] <= float(args.max_p95_seconds)
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
