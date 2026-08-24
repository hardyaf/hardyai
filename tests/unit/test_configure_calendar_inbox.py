from __future__ import annotations

from pathlib import Path

import pytest

from scripts.configure_calendar_inbox import configure


def test_configure_calendar_inbox_preserves_unrelated_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_SECRET=keep-me\nCALENDAR_INBOX_ENABLED=false\n", encoding="utf-8")

    configure(
        env_path,
        enabled=True,
        timezone_name="America/New_York",
        start_hour=8,
        end_hour=20,
        poll_seconds=60,
    )

    result = env_path.read_text(encoding="utf-8")
    assert "EXISTING_SECRET=keep-me" in result
    assert result.count("CALENDAR_INBOX_ENABLED=true") == 1
    assert "CALENDAR_INBOX_TIMEZONE=America/New_York" in result
    assert "CALENDAR_INBOX_START_HOUR=8" in result
    assert "CALENDAR_INBOX_END_HOUR=20" in result
    assert "CALENDAR_INBOX_ALLOWED_SENDER_EMAILS=" in result


@pytest.mark.parametrize(
    ("start_hour", "end_hour", "poll_seconds"),
    [(-1, 20, 60), (8, 24, 60), (20, 8, 60), (8, 20, 29)],
)
def test_configure_calendar_inbox_rejects_invalid_bounds(
    tmp_path: Path,
    start_hour: int,
    end_hour: int,
    poll_seconds: int,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("UNCHANGED=yes\n", encoding="utf-8")

    with pytest.raises(ValueError):
        configure(
            env_path,
            enabled=True,
            timezone_name="America/New_York",
            start_hour=start_hour,
            end_hour=end_hour,
            poll_seconds=poll_seconds,
        )

    assert env_path.read_text(encoding="utf-8") == "UNCHANGED=yes\n"
