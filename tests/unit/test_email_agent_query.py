from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta

import pytest

from app.skills.domains.email_agent.query import (
    EmailQuery,
    EmailQueryError,
    exact_local_date_interval,
    rolling_days_interval,
    strict_local_datetime,
)


def _query(**overrides) -> EmailQuery:
    values = {
        "start": "2026-08-20T04:00:00Z",
        "end": "2026-08-25T04:00:00Z",
        "timezone_name": "America/New_York",
        "senders": ("sender@example.com",),
        "recipients": ("jarvis@example.com",),
        "source": "work",
        "category": "work_mail",
        "visibility": "active",
        "text": "budget review",
        "has_attachment": True,
        "order": "newest",
        "limit": 25,
    }
    values.update(overrides)
    return EmailQuery(**values)


def test_email_query_is_immutable_normalized_and_closed() -> None:
    query = EmailQuery.from_arguments(
        {
            "start": "2026-08-20T00:00:00-04:00",
            "end": "2026-08-25T00:00:00-04:00",
            "senders": ["Sender@Example.com"],
            "recipients": ["Jarvis@Example.com"],
            "source": "WORK",
            "category": "WORK_MAIL",
            "visibility": "all",
            "text": "  budget   review ",
            "has_attachment": True,
            "order": "oldest",
            "limit": 25,
        },
        timezone_name="America/New_York",
        allowed_sources=("work", "personal"),
        allowed_categories=("work_mail", "needs_review"),
    )

    assert query.start == datetime(2026, 8, 20, 4, tzinfo=UTC)
    assert query.end == datetime(2026, 8, 25, 4, tzinfo=UTC)
    assert query.senders == ("sender@example.com",)
    assert query.recipients == ("jarvis@example.com",)
    assert query.text_terms == ("budget", "review")
    assert query.to_arguments()["start"] == "2026-08-20T04:00:00Z"
    assert query.normalized(returned_count=3)["timezone"] == "America/New_York"
    assert query.normalized(returned_count=3)["returned_count"] == 3
    with pytest.raises(FrozenInstanceError):
        query.limit = 50
    with pytest.raises(EmailQueryError, match="arguments_shape"):
        EmailQuery.from_arguments(
            {
                "start": "2026-08-20T00:00:00-04:00",
                "end": "2026-08-25T00:00:00-04:00",
                "provider_query": "from:anyone",
            },
            timezone_name="America/New_York",
            allowed_sources=("work",),
            allowed_categories=("work_mail",),
        )


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"source": "unknown"}, "source_invalid"),
        ({"category": "unknown"}, "category_invalid"),
        ({"visibility": "whatever"}, "visibility_invalid"),
        ({"limit": 101}, "limit_invalid"),
        ({"senders": ["not-an-email"]}, "senders_invalid"),
        ({"start": "2026-08-25T04:00:00Z"}, "interval_reversed"),
    ],
)
def test_email_query_filters_fail_closed(arguments: dict, code: str) -> None:
    base = {
        "start": "2026-08-20T04:00:00Z",
        "end": "2026-08-25T04:00:00Z",
    }
    base.update(arguments)
    with pytest.raises(EmailQueryError, match=code):
        EmailQuery.from_arguments(
            base,
            timezone_name="America/New_York",
            allowed_sources=("work", "personal"),
            allowed_categories=("work_mail", "needs_review"),
        )


def test_exact_local_date_uses_exclusive_midnight_and_dst_lengths() -> None:
    spring_start, spring_end = exact_local_date_interval(
        date(2026, 3, 8),
        timezone_name="America/New_York",
    )
    fall_start, fall_end = exact_local_date_interval(
        date(2026, 11, 1),
        timezone_name="America/New_York",
    )
    year_start, year_end = exact_local_date_interval(
        date(2026, 12, 31),
        timezone_name="America/New_York",
    )

    assert (spring_end - spring_start).total_seconds() == 23 * 3600
    assert (fall_end - fall_start).total_seconds() == 25 * 3600
    assert year_start == datetime(2026, 12, 31, 5, tzinfo=UTC)
    assert year_end == datetime(2027, 1, 1, 5, tzinfo=UTC)


def test_rolling_three_days_uses_injected_clock_and_local_wall_time() -> None:
    start, end = rolling_days_interval(
        3,
        now=datetime(2026, 3, 10, 16, 30, tzinfo=UTC),
        timezone_name="America/New_York",
    )

    assert start == datetime(2026, 3, 7, 17, 30, tzinfo=UTC)
    assert end == datetime(2026, 3, 10, 16, 30, tzinfo=UTC)
    assert (end - start).total_seconds() == 71 * 3600


def test_local_time_ambiguity_and_nonexistence_require_explicit_resolution() -> None:
    with pytest.raises(EmailQueryError, match="nonexistent"):
        strict_local_datetime(
            datetime(2026, 3, 8, 2, 30),
            timezone_name="America/New_York",
        )
    with pytest.raises(EmailQueryError, match="ambiguous"):
        strict_local_datetime(
            datetime(2026, 11, 1, 1, 30),
            timezone_name="America/New_York",
        )

    first = strict_local_datetime(
        datetime(2026, 11, 1, 1, 30),
        timezone_name="America/New_York",
        fold=0,
    )
    second = strict_local_datetime(
        datetime(2026, 11, 1, 1, 30),
        timezone_name="America/New_York",
        fold=1,
    )
    assert second.astimezone(UTC) - first.astimezone(UTC) == timedelta(hours=1)


def test_future_interval_is_valid_but_reversed_interval_is_not() -> None:
    future = _query(
        start="2030-01-01T00:00:00Z",
        end="2030-01-02T00:00:00Z",
    )
    assert future.start.year == 2030
    with pytest.raises(EmailQueryError, match="interval_reversed"):
        _query(
            start="2030-01-02T00:00:00Z",
            end="2030-01-01T00:00:00Z",
        )
