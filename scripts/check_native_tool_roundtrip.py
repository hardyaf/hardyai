from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


def _tool_call(message: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise RuntimeError("native_tool_call_missing")
    call = calls[0]
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        raise RuntimeError("native_tool_call_invalid")
    name = str(function.get("name") or "").strip()
    arguments = function.get("arguments")
    if name != "lookup_fixture_value" or not isinstance(arguments, dict):
        raise RuntimeError("native_tool_call_unexpected")
    return name, arguments, call


def run(*, base_url: str, key_path: Path, model: str, timeout_seconds: float) -> dict[str, Any]:
    key = key_path.read_text(encoding="utf-8").strip()
    headers = {
        "X-HardyAI-Accelerator-Key": key,
        "X-HardyAI-Accelerator-Lane": "main_conversation",
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_fixture_value",
                "description": "Return the value for one harmless acceptance fixture key.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key"],
                    "properties": {"key": {"type": "string"}},
                },
            },
        }
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "Use the supplied tool for fixture facts. After its result, answer briefly.",
        },
        {
            "role": "user",
            "content": "What value is stored under acceptance-key? Use the tool; do not guess.",
        },
    ]
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "think": "low",
        "options": {"num_ctx": 8192, "num_predict": 256},
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        first = client.post(f"{base_url.rstrip('/')}/api/chat", headers=headers, json=payload)
        first.raise_for_status()
        first_data = first.json()
        assistant = first_data.get("message")
        if not isinstance(assistant, dict):
            raise RuntimeError("native_assistant_message_missing")
        tool_name, arguments, _ = _tool_call(assistant)
        messages.extend(
            [
                assistant,
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": json.dumps(
                        {"key": arguments.get("key"), "value": 42},
                        separators=(",", ":"),
                    ),
                },
            ]
        )
        second = client.post(
            f"{base_url.rstrip('/')}/api/chat",
            headers=headers,
            json={**payload, "messages": messages},
        )
        if not second.is_success:
            raise RuntimeError(
                f"native_second_turn_rejected:{second.status_code}:{second.text[:1000]}:"
                f"assistant_keys={sorted(assistant)}:tool_call={json.dumps(assistant.get('tool_calls'), ensure_ascii=True)[:1000]}"
            )
        second_data = second.json()
    final_message = second_data.get("message")
    if not isinstance(final_message, dict):
        raise RuntimeError("native_final_message_missing")
    final_content = str(final_message.get("content") or "").strip()
    if "42" not in final_content:
        raise RuntimeError("native_tool_result_not_used")
    return {
        "status": "passed",
        "model": model,
        "first_tool": tool_name,
        "arguments": arguments,
        "final": final_content[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check one native Ollama tool round trip through admission.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8040")
    parser.add_argument("--key-path", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        key_path=args.key_path,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
