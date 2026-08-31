from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from app.core.main_backend import OllamaMainConversationBackend
from app.core.ollama_observability import apply_ollama_think_mode
from scripts.benchmark_main_models import load_cases


mode = sys.argv[1]
case_id = sys.argv[2] if len(sys.argv) > 2 else "p5a_lists_begin_adaptive_create_add"
all_cases = load_cases(Path("benchmarks/models/main_acceptance_cases.json"))
cases = [item for item in all_cases if item["id"].startswith("p5a_")] if case_id == "all" else [
    next(item for item in all_cases if item["id"] == case_id)
]
for case in cases:
    context = dict(case["context"])
    prompt = OllamaMainConversationBackend._build_tool_step_prompt(
        text=str(case["text"]),
        selected_tools=list(context.pop("selected_tools", [])),
        observations=list(context.pop("observations", [])),
        temporal_contexts=dict(context.pop("temporal_contexts", {})),
        context=context,
    )
    payload = {
        "model": "gpt-oss:20b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 32768, "num_predict": 2048},
    }
    if mode not in {"chat", "chatjson", "chattool"}:
        if mode != "omit":
            apply_ollama_think_mode(payload, False if mode == "false" else mode)
        raw = httpx.post("http://ollama:11434/api/generate", json=payload, timeout=180).json()
        print(json.dumps({"case_id": case["id"], **{key: raw.get(key) for key in ("response", "thinking", "done_reason", "eval_count")}}, indent=2))
        continue
    chat_payload = {
        "model": "gpt-oss:20b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_ctx": 32768, "num_predict": 2048},
    }
    if mode == "chatjson":
        chat_payload["format"] = "json"
    if mode == "chattool":
        chat_payload["tools"] = [OllamaMainConversationBackend._model_step_submission_tool()]
    chat = httpx.post("http://ollama:11434/api/chat", json=chat_payload, timeout=180).json()
    message = chat.get("message") or {}
    print(json.dumps({"case_id": case["id"], "content": message.get("content"), "thinking": message.get("thinking"), "tool_calls": message.get("tool_calls"), "done_reason": chat.get("done_reason"), "eval_count": chat.get("eval_count")}, indent=2))
