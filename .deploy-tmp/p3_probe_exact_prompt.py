from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/jarvis")

import httpx

from app.accelerator.client import accelerator_request_headers
from app.core.main_backend import OllamaMainConversationBackend
from app.core.ollama_observability import apply_ollama_think_mode


cases = json.loads(
    Path("/opt/jarvis/benchmarks/models/main_acceptance_cases.json").read_text(
        encoding="utf-8"
    )
)
backend = OllamaMainConversationBackend(
    base_url="http://accelerator-admission:8040",
    model="gpt-oss:20b",
    timeout_seconds=180,
    num_ctx=32768,
    num_predict=1024,
    think="low",
    turn_decision_think=False,
)

for case in cases:
    if case.get("kind") != "tool_step" or case.get("execution_enabled") is not True:
        continue
    prompt = backend._build_tool_step_prompt(
        text=str(case["text"]),
        selected_tools=list(case["context"]["selected_tools"]),
        observations=list(case["context"].get("observations", [])),
        temporal_contexts=dict(case["context"].get("temporal_contexts", {})),
        context={},
    )
    for attempt in range(1, 6):
        payload = {
            "model": "gpt-oss:20b",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": 32768,
                "num_predict": 1024,
            },
        }
        apply_ollama_think_mode(payload, False)
        response = httpx.post(
            "http://accelerator-admission:8040/api/generate",
            headers=accelerator_request_headers("main_conversation"),
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        raw = response.json()
        ordinary = str(raw.get("response") or "")
        print(
            json.dumps(
                {
                    "case_id": case["id"],
                    "attempt": attempt,
                    "prompt_chars": len(prompt),
                    "response_preview": ordinary[:500],
                    "response_chars": len(ordinary),
                    "thinking_chars": len(str(raw.get("thinking") or "")),
                    "eval_count": raw.get("eval_count"),
                    "done_reason": raw.get("done_reason"),
                },
                sort_keys=True,
            )
        )
