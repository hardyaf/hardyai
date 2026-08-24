from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path

if __package__:
    from scripts.configure_web_research import upsert_env_text
else:
    from configure_web_research import upsert_env_text


def configure(
    env_path: Path,
    *,
    enabled: bool,
    timezone_name: str,
    start_hour: int,
    end_hour: int,
    poll_seconds: int,
) -> None:
    if not 0 <= start_hour <= end_hour <= 23:
        raise ValueError("hours must satisfy 0 <= start <= end <= 23")
    if not 30 <= poll_seconds <= 3600:
        raise ValueError("poll seconds must be between 30 and 3600")

    candidate = env_path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"refusing to edit non-regular environment file: {candidate}")
    resolved = candidate.resolve()
    original = resolved.read_text(encoding="utf-8")
    updated = upsert_env_text(
        original,
        {
            "CALENDAR_INBOX_ENABLED": str(enabled).lower(),
            "CALENDAR_INBOX_TIMEZONE": timezone_name,
            "CALENDAR_INBOX_START_HOUR": str(start_hour),
            "CALENDAR_INBOX_END_HOUR": str(end_hour),
            "CALENDAR_INBOX_POLL_SECONDS": str(poll_seconds),
            "CALENDAR_INBOX_MAX_MESSAGES_PER_RUN": "100",
            "CALENDAR_INBOX_LOOKBACK_DAYS": "30",
            "CALENDAR_INBOX_ALLOWED_SENDER_EMAILS": "",
        },
    )

    mode = stat.S_IMODE(resolved.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.calendar-inbox-", dir=resolved.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, resolved)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically configure Jarvis calendar-inbox scheduling.")
    parser.add_argument("--env-file", default=".env", help="Jarvis environment file (default: .env)")
    parser.add_argument("--disable", action="store_true", help="Disable the scheduler instead of enabling it")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--start-hour", type=int, default=8)
    parser.add_argument("--end-hour", type=int, default=20)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    configure(
        Path(args.env_file),
        enabled=not args.disable,
        timezone_name=args.timezone,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        poll_seconds=args.poll_seconds,
    )
    state = "disabled" if args.disable else "enabled"
    print(f"Calendar inbox {state}; schedule is {args.start_hour:02d}:00-{args.end_hour:02d}:00 {args.timezone}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
