from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.principals import discord_adapter_principal
from app.runtime import turn_service
from app.schemas.api import AskRequest
from app.services.discord.bot import build_ask_request_payload


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 4)


async def _run(*, iterations: int, prompt: str) -> dict:
    rows: list[dict] = []
    run_id = uuid4().hex[:12]
    for iteration in range(iterations):
        payload = build_ask_request_payload(
            command_text=prompt,
            guild_id=100,
            channel_id=200,
            user_id=300,
            message_id=f"benchmark-{run_id}-{iteration + 1}",
            micro_command_explicit=False,
        )
        started = time.perf_counter()
        response = await turn_service.route(
            AskRequest.model_validate(payload),
            principal=discord_adapter_principal(),
        )
        elapsed = time.perf_counter() - started
        assistant = response.get("assistant") if isinstance(response, dict) else None
        assistant_text = assistant.get("text") if isinstance(assistant, dict) else None
        rows.append(
            {
                "iteration": iteration + 1,
                "seconds": round(elapsed, 4),
                "response_ok": bool(str(assistant_text or "").strip()),
                "route": str(response.get("route") or "")[:80]
                if isinstance(response, dict)
                else "",
            }
        )
    values = [float(row["seconds"]) for row in rows]
    return {
        "schema_version": 1,
        "iterations": len(rows),
        "mean_seconds": round(statistics.mean(values), 4),
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "all_responses_ok": all(row["response_ok"] for row in rows),
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the trusted unprefixed Discord conversation path."
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument(
        "--prompt",
        default="In one short sentence, what are the primary colors?",
    )
    parser.add_argument("--max-p95-seconds", type=float, default=10.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    report = asyncio.run(
        _run(
            iterations=max(1, min(int(args.iterations), 10)),
            prompt=str(args.prompt).strip(),
        )
    )
    report["accepted"] = bool(report["all_responses_ok"]) and bool(
        report["p95_seconds"] is not None
        and float(report["p95_seconds"]) <= float(args.max_p95_seconds)
    )
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
