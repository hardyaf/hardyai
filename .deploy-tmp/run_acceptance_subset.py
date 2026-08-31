from __future__ import annotations

import json
from pathlib import Path

from scripts.benchmark_main_models import load_cases, run_model


ids = {
    "p3_emit_bounded_tool_call",
    "p4_email_multi_filter_combination",
    "p4_email_no_match_response",
    "p5a_lists_begin_adaptive_create_add",
    "p5a_lists_replan_after_missing_target",
    "p5a_lists_add_three_after_create",
    "p5a_lists_add_two_without_punctuation",
    "p5a_lists_add_one_same_schema",
    "p5a_lists_add_semicolon_items",
}
cases = [
    case
    for case in load_cases(Path("benchmarks/models/main_acceptance_cases.json"))
    if case["id"] in ids
]
result = run_model(
    model="gpt-oss:20b",
    cases=cases,
    base_url="http://ollama:11434",
    timeout_seconds=180,
    num_ctx=32768,
)
print(
    json.dumps(
        {
            "passed": result["passed"],
            "case_count": result["case_count"],
            "failed_token_loops": result["failed_token_loops"],
            "results": [
                {
                    "case_id": row["case_id"],
                    "passed": row["passed"],
                    "contract_error_code": row["contract_error_code"],
                    "observed_mode": row["observed_mode"],
                    "observed_tool_id": row["observed_tool_id"],
                    "seconds": row["seconds"],
                }
                for row in result["results"]
            ],
        },
        indent=2,
        sort_keys=True,
    )
)
