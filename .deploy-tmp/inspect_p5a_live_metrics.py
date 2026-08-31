from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


database_path = Path(os.environ["DATABASE_PATH"])
with sqlite3.connect(database_path) as connection:
    rows = connection.execute(
        """
        SELECT timestamp, payload_json
        FROM events
        WHERE event_type = 'model.ollama_call'
          AND json_extract(payload_json, '$.lane') = 'main_conversation'
        ORDER BY id DESC
        LIMIT 12
        """
    ).fetchall()

allowed = {
    "model",
    "outcome",
    "requested_num_ctx",
    "requested_num_predict",
    "attempt",
    "adaptive_retry",
    "token_budget_exhausted",
    "failed_loop",
    "escalation_reason",
    "escalated_to_num_predict",
    "prompt_chars",
    "estimated_prompt_tokens",
    "estimated_input_exceeds_context",
    "prompt_eval_count",
    "eval_count",
    "context_utilization_ratio",
    "done_reason",
    "total_duration_ms",
    "load_duration_ms",
    "prompt_eval_duration_ms",
    "eval_duration_ms",
    "error_type",
}
result = []
for timestamp, payload_json in reversed(rows):
    payload = json.loads(payload_json)
    result.append(
        {
            "timestamp": timestamp,
            **{key: payload.get(key) for key in sorted(allowed)},
        }
    )
print(json.dumps(result, indent=2, sort_keys=True))
