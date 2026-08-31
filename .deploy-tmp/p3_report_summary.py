from __future__ import annotations

import json
import sys
from pathlib import Path


report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = report["models"][0]
print(
    json.dumps(
        {
            "accepted": report["accepted"],
            "passed": model["passed"],
            "pass_rate": model["pass_rate"],
            "groups": model["groups"],
            "failures": [
                {
                    "case_id": row["case_id"],
                    "contract_valid": row["contract_valid"],
                    "observed_mode": row["observed_mode"],
                    "seconds": row["seconds"],
                }
                for row in model["results"]
                if not row["passed"]
            ],
            "slowest": [
                {
                    "case_id": row["case_id"],
                    "seconds": row["seconds"],
                    "benchmark_group": row["benchmark_group"],
                    "sequence_metrics": row.get("sequence_metrics"),
                }
                for row in sorted(
                    model["results"], key=lambda item: float(item["seconds"]), reverse=True
                )[:5]
            ],
        },
        sort_keys=True,
    )
)
