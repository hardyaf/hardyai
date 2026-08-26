from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _post(url: str, *, payload: dict, headers: dict[str, str], timeout: float) -> tuple[dict, float]:
    body = json.dumps(payload, separators=(",", ":")).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json", **headers},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("coexistence_response_invalid")
    return value, time.perf_counter() - started


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 4)


def _route_discord_turn(*, text: str, iteration: int) -> tuple[dict, float]:
    from app.api.principals import discord_adapter_principal
    from app.runtime import turn_service
    from app.schemas.api import AskRequest
    from app.services.discord.bot import build_ask_request_payload

    payload = build_ask_request_payload(
        command_text=text,
        guild_id=100,
        channel_id=200,
        user_id=300,
        message_id=f"phase5-coexistence-{uuid4()}-{iteration}",
        micro_command_explicit=False,
    )

    async def _route() -> dict:
        return await turn_service.route(
            AskRequest.model_validate(payload),
            principal=discord_adapter_principal(),
        )

    started = time.perf_counter()
    response = asyncio.run(_route())
    return response, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark admitted VLM/live-conversation coexistence.")
    parser.add_argument(
        "--ask-mode",
        choices=("operator-http", "discord-direct"),
        default="operator-http",
    )
    parser.add_argument("--ask-url", default="http://jarvis:8000/ask")
    parser.add_argument("--operator-key-path")
    parser.add_argument("--vlm-url", default="http://accelerator-admission:8040/v1/document-vlm")
    parser.add_argument("--accelerator-key-path", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--vlm-head-start-seconds", type=float, default=1.0)
    parser.add_argument("--max-ask-p95-seconds", type=float, default=30.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    operator_key = ""
    if args.ask_mode == "operator-http":
        if not args.operator_key_path:
            parser.error("--operator-key-path is required for --ask-mode operator-http")
        operator_key = Path(args.operator_key_path).read_text(encoding="utf-8").strip()
    accelerator_key = Path(args.accelerator_key_path).read_text(encoding="utf-8").strip()
    fixture = Path(args.fixture).resolve()
    media_type = "image/jpeg" if fixture.suffix.casefold() in {".jpg", ".jpeg"} else "image/png"
    vlm_payload = {
        "filename": fixture.name,
        "media_type": media_type,
        "file_base64": base64.b64encode(fixture.read_bytes()).decode("ascii"),
    }
    rows = []
    for iteration in range(max(1, min(int(args.iterations), 10))):
        with ThreadPoolExecutor(max_workers=2) as executor:
            vlm_future = executor.submit(
                _post,
                args.vlm_url,
                payload=vlm_payload,
                headers={
                    "X-HardyAI-Accelerator-Key": accelerator_key,
                    "X-HardyAI-Accelerator-Lane": "document_vlm",
                },
                timeout=180.0,
            )
            time.sleep(max(0.1, min(float(args.vlm_head_start_seconds), 5.0)))
            try:
                if args.ask_mode == "discord-direct":
                    ask, ask_seconds = _route_discord_turn(
                        text="In one short sentence, what are the primary colors?",
                        iteration=iteration + 1,
                    )
                else:
                    ask, ask_seconds = _post(
                        args.ask_url,
                        payload={
                            "text": "In one short sentence, what are the primary colors?",
                            "request_id": f"phase5-coexistence-{uuid4()}",
                            "user_id": "phase5-coexistence-benchmark",
                            "source": "accelerator_benchmark",
                            "context": {"wake_on_message": True, "force_main_owner": True},
                        },
                        headers={"X-Jarvis-Operator-Key": operator_key},
                        timeout=180.0,
                    )
                assistant = ask.get("assistant") if isinstance(ask.get("assistant"), dict) else {}
                ask_ok = bool(str(assistant.get("text") or "").strip())
                route = str(ask.get("route") or "")[:80]
            except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as exc:
                ask_seconds = None
                ask_ok = False
                route = ""
                ask_error = f"ask_{type(exc).__name__}"
            else:
                ask_error = None
            try:
                vlm, vlm_seconds = vlm_future.result(timeout=190.0)
                vlm_ok = vlm.get("status") == "success" and isinstance(vlm.get("pages"), list)
            except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as exc:
                vlm_seconds = None
                vlm_ok = False
                vlm_error = f"vlm_{type(exc).__name__}"
            else:
                vlm_error = None
        rows.append(
            {
                "iteration": iteration + 1,
                "ask_seconds": round(ask_seconds, 4) if ask_seconds is not None else None,
                "ask_ok": ask_ok,
                "ask_route": route,
                "ask_error": ask_error,
                "vlm_seconds": round(vlm_seconds, 4) if vlm_seconds is not None else None,
                "vlm_ok": vlm_ok,
                "vlm_error": vlm_error,
            }
        )
    ask_values = [float(row["ask_seconds"]) for row in rows if row["ask_seconds"] is not None]
    vlm_values = [float(row["vlm_seconds"]) for row in rows if row["vlm_seconds"] is not None]
    ask_p95 = _percentile(ask_values, 0.95)
    report = {
        "schema_version": 1,
        "ask_mode": args.ask_mode,
        "iterations": len(rows),
        "ask_p50_seconds": _percentile(ask_values, 0.50),
        "ask_p95_seconds": ask_p95,
        "vlm_mean_seconds": round(statistics.mean(vlm_values), 4) if vlm_values else None,
        "accepted": bool(rows)
        and all(row["ask_ok"] and row["vlm_ok"] for row in rows)
        and ask_p95 is not None
        and ask_p95 <= float(args.max_ask_p95_seconds),
        "results": rows,
    }
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
