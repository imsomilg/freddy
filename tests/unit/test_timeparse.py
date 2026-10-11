from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from freddy.fastpath.timeparse import parse_clock, parse_recurring, parse_when

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 11, 16, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6pm", time(18, 0)),
        ("6:30 pm", time(18, 30)),
        ("7:30", time(7, 30)),
        ("19:00", time(19, 0)),
        ("12am", time(0, 0)),
        ("12pm", time(12, 0)),
        ("noon", time(12, 0)),
        ("15", time(15, 0)),
        ("7", None),
        ("25:00", None),
        ("13pm", None),
    ],
)
def test_parse_clock(text: str, expected: time | None) -> None:
    assert parse_clock(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("in 20 min", datetime(2026, 10, 11, 16, 20, tzinfo=TZ)),
        ("in 2 hours", datetime(2026, 10, 11, 18, 0, tzinfo=TZ)),
        ("in an hour", datetime(2026, 10, 11, 17, 0, tzinfo=TZ)),
        ("in half an hour", datetime(2026, 10, 11, 16, 30, tzinfo=TZ)),
        ("at 6pm", datetime(2026, 10, 11, 18, 0, tzinfo=TZ)),
        ("today at 18:30", datetime(2026, 10, 11, 18, 30, tzinfo=TZ)),
        ("tonight at 9", datetime(2026, 10, 11, 21, 0, tzinfo=TZ)),
        ("tomorrow at 9am", datetime(2026, 10, 12, 9, 0, tzinfo=TZ)),
        ("monday at 10am", datetime(2026, 10, 12, 10, 0, tzinfo=TZ)),
        ("on sunday at 5pm", datetime(2026, 10, 11, 17, 0, tzinfo=TZ)),
        ("sunday at 3pm", datetime(2026, 10, 18, 15, 0, tzinfo=TZ)),
        ("at 9am", None),
        ("tomorrow at 9", None),
        ("tomorrow", None),
        ("sometime soon", None),
        ("in 5 parsecs", None),
    ],
)
def test_parse_when(text: str, expected: datetime | None) -> None:
    assert parse_when(text, NOW, TZ) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("every day at 7:30", "30 7 * * *"),
        ("daily at 8am", "0 8 * * *"),
        ("every weekday at 7:30am", "30 7 * * 1-5"),
        ("every weekend at 10am", "0 10 * * 0,6"),
        ("every monday at 9am", "0 9 * * 1"),
        ("every mon and thu at 6pm", "0 18 * * 1,4"),
        ("every mondays, wednesdays and fridays at 07:00", "0 7 * * 1,3,5"),
        ("every morning at 7am", "0 7 * * *"),
        ("every morning at 7pm", None),
        ("every day at 7", None),
        ("every blursday at 7pm", None),
        ("in 20 min", None),
    ],
)
def test_parse_recurring(text: str, expected: str | None) -> None:
    assert parse_recurring(text) == expected
