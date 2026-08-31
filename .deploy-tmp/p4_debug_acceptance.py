from __future__ import annotations

import json
from pathlib import Path

from app.core.main_backend import OllamaMainConversationBackend
from scripts.benchmark_main_models import load_cases


FAILED = {
    "tool_email_arbitrary_interval_last_3_days",
    "tool_email_exact_date",
}


backend = OllamaMainConversationBackend(
    base_url="http://accelerator-admission:8040",
    model="gpt-oss:20b",
    timeout_seconds=180,
    num_ctx=32768,
    num_predict=1024,
    think="low",
    turn_decision_think=False,
)
for case in load_cases(Path("benchmarks/models/main_acceptance_cases.json")):
    if case["id"] not in FAILED:
        continue
    context = dict(case["context"])
    if case["kind"] == "tool_step":
        observed = backend.next_tool_step(
            case["text"],
            list(context.pop("selected_tools", [])),
            list(context.pop("observations", [])),
            dict(context.pop("temporal_contexts", {})),
            context,
        )
    else:
        observed = backend.select_skills(
            case["text"],
            list(context.pop("discovery_cards", [])),
            context,
        )
    print(json.dumps({"case_id": case["id"], "observed": observed}, sort_keys=True))
