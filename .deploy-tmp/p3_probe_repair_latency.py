from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/jarvis")

from app.core.main_backend import OllamaMainRepairBackend
from app.core.main_repair_contract import normalize_repair_payload


case = next(
    item
    for item in json.loads(
        Path("/opt/jarvis/benchmarks/models/main_acceptance_cases.json").read_text(
            encoding="utf-8"
        )
    )
    if item.get("id") == "repair_authorized_list_add"
)
backend = OllamaMainRepairBackend(
    base_url="http://accelerator-admission:8040",
    model="gpt-oss:20b",
    timeout_seconds=180,
    num_ctx=32768,
    num_predict=1024,
    think=False,
)

for attempt in range(1, 6):
    started = time.perf_counter()
    observed = backend.repair_action(str(case["text"]), dict(case["context"]))
    normalized = normalize_repair_payload(observed)
    print(
        json.dumps(
            {
                "attempt": attempt,
                "seconds": round(time.perf_counter() - started, 4),
                "contract_valid": normalized is not None,
                "status": normalized.get("status") if normalized else None,
                "sequence_metrics": backend.status().get("last_sequence_metrics"),
            },
            sort_keys=True,
        )
    )
