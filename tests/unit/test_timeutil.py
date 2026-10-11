from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from freddy.channels.base import chunk
from freddy.domain.timeutil import describe_cron, next_cron_run, valid_cron

TZ = ZoneInfo("America/New_York")


def test_next_cron_run_uses_local_time() -> None:
    after = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    nxt = next_cron_run("30 7 * * *", TZ, after=after)
    assert nxt.astimezone(TZ).hour == 7
    assert nxt.astimezone(TZ).minute == 30
    assert nxt > after


def test_next_cron_run_across_dst() -> None:
    after = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    nxt = next_cron_run("0 8 * * *", TZ, after=after)
    assert nxt.astimezone(TZ).hour == 8


def test_valid_cron() -> None:
    assert valid_cron("30 7 * * 1-5")
    assert not valid_cron("30 7 * *")
    assert not valid_cron("nope")


def test_describe_cron() -> None:
    assert describe_cron("30 7 * * 1-5") == "weekdays 07:30"
    assert describe_cron("0 9 * * *") == "every day 09:00"
    assert describe_cron("0 18 * * 1,4") == "every Mon, Thu 18:00"
    assert describe_cron("*/5 * * * *") == "cron `*/5 * * * *`"


def test_chunk() -> None:
    text = "\n".join(["x" * 50] * 100)
    parts = chunk(text, 2000)
    assert all(len(p) <= 2000 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
