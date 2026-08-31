from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/jarvis")

import httpx

from app.accelerator.client import accelerator_request_headers
from app.core.main_backend import OllamaMainConversationBackend
from app.core.ollama_observability import apply_ollama_think_mode
from app.core.tool_loop_types import ModelStep, ToolLoopContractError


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
    context = dict(case["context"])
    observed = backend.next_tool_step(
        str(case["text"]),
        list(context.pop("selected_tools", [])),
        list(context.pop("observations", [])),
        dict(context.pop("temporal_contexts", {})),
        context,
    )
    allowed = {
        str(item.get("tool_id") or "").strip().casefold()
        for item in case["context"]["selected_tools"]
    }
    try:
        ModelStep.from_mapping(observed or {}, allowed_tool_ids=allowed)
        error = None
    except ToolLoopContractError as exc:
        error = exc.code
    prompt = backend._build_tool_step_prompt(
        text=str(case["text"]),
        selected_tools=list(case["context"]["selected_tools"]),
        observations=list(case["context"].get("observations", [])),
        temporal_contexts=dict(case["context"].get("temporal_contexts", {})),
        context={},
    )
    prompt = (
        "Return one ordinary JSON object in the final response and no prose. "
        "This is a planning simulation: never use a native tool channel. "
        "Choose exactly one shape: "
        '{"mode":"respond","message":"complete answer"} OR '
        '{"mode":"clarify","tool_id":"allowed ID","arguments":{},'
        '"missing_fields":["field"],"question":"question"} OR '
        '{"mode":"call_tool","tool_id":"allowed ID","call_id":"correlation-id",'
        '"arguments":{},"provenance_claims":[]}. '
        "If an observation already answers the request, respond. Otherwise encode the next call as JSON. "
        f"Allowed operation descriptors: {json.dumps(case['context']['selected_tools'], separators=(',', ':'))}. "
        f"Prior untrusted observations: {json.dumps(case['context'].get('observations', []), separators=(',', ':'))}. "
        f"User request: {case['text']}"
    )
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
    print(
        json.dumps(
            {
                "case_id": case["id"],
                "observed": observed,
                "error": error,
                "raw_response_preview": str(raw.get("response") or "")[:1000],
                "response_chars": len(str(raw.get("response") or "")),
                "thinking_chars": len(str(raw.get("thinking") or "")),
                "response_keys": sorted(str(key) for key in raw),
                "eval_count": raw.get("eval_count"),
                "done_reason": raw.get("done_reason"),
            },
            sort_keys=True,
        )
    )
