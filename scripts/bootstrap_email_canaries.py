from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import a bounded set of forwarding canaries sent just before first activation."
    )
    parser.add_argument("--lookback-minutes", type=int, default=180)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Re-run the bounded alias search after a completed bootstrap.",
    )
    args = parser.parse_args()

    from app.runtime import email_agent_service

    if email_agent_service is None:
        raise RuntimeError("EMAIL_AGENT_ENABLED=true is required.")
    result = email_agent_service.bootstrap_recent_canaries(
        lookback_minutes=args.lookback_minutes,
        expected_count=args.expected_count,
        allow_recheck=args.recheck,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
