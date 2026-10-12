from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from freddy.domain.freetime import free_slots, merge

TZ = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)
FULL_DAY = (time(0), time(23, 59))


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def utc(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    return [(s.astimezone(UTC), e.astimezone(UTC)) for s, e in spans]


DAY = (at(14, 0), at(15, 0))

CASES: list[tuple[str, list[Any], tuple[datetime, datetime], timedelta, list[Any]]] = [
    ("empty day", [], DAY, HOUR, [(at(14, 8), at(14, 22))]),
    (
        "one block",
        [(at(14, 12), at(14, 13))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 12)), (at(14, 13), at(14, 22))],
    ),
    (
        "overlapping blocks merge",
        [(at(14, 10), at(14, 12)), (at(14, 11), at(14, 13))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 10)), (at(14, 13), at(14, 22))],
    ),
    (
        "touching blocks merge",
        [(at(14, 10), at(14, 11)), (at(14, 11), at(14, 12))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 10)), (at(14, 12), at(14, 22))],
    ),
    (
        "unsorted input",
        [(at(14, 15), at(14, 16)), (at(14, 9), at(14, 10))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 9)), (at(14, 10), at(14, 15)), (at(14, 16), at(14, 22))],
    ),
    (
        "gap too short",
        [(at(14, 9), at(14, 10)), (at(14, 10, 30), at(14, 22))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 9))],
    ),
    (
        "exact fit",
        [(at(14, 9), at(14, 21))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 9)), (at(14, 21), at(14, 22))],
    ),
    (
        "blocks outside waking hours",
        [(at(14, 6), at(14, 9)), (at(14, 21), at(14, 23))],
        DAY,
        HOUR,
        [(at(14, 9), at(14, 21))],
    ),
    (
        "block across midnight",
        [(at(14, 21), at(15, 9))],
        (at(14, 0), at(16, 0)),
        HOUR,
        [(at(14, 8), at(14, 21)), (at(15, 9), at(15, 22))],
    ),
    (
        "all-day event ignored",
        [(date(2026, 10, 14), date(2026, 10, 15))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 22))],
    ),
    (
        "busy in another timezone",
        [(datetime(2026, 10, 14, 16, tzinfo=UTC), datetime(2026, 10, 14, 17, tzinfo=UTC))],
        DAY,
        HOUR,
        [(at(14, 8), at(14, 12)), (at(14, 13), at(14, 22))],
    ),
    ("start mid day", [], (at(14, 15, 7), at(15, 0)), HOUR, [(at(14, 15, 7), at(14, 22))]),
    ("end mid day", [], (at(14, 0), at(14, 12)), HOUR, [(at(14, 8), at(14, 12))]),
    ("start after sleep", [], (at(14, 22, 30), at(15, 0)), HOUR, []),
    ("nothing long enough", [], DAY, timedelta(hours=15), []),
]


@pytest.mark.parametrize(
    ("busy", "window", "duration", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_free_slots(
    busy: list[Any], window: tuple[datetime, datetime], duration: timedelta, expected: list[Any]
) -> None:
    assert utc(free_slots(busy, *window, duration, TZ)) == utc(expected)


def test_slots_come_back_in_local_time() -> None:
    (slot,) = free_slots([], *DAY, HOUR, TZ)
    assert slot[0].tzinfo == TZ
    assert (slot[0].hour, slot[1].hour) == (8, 22)


DST_CASES = [
    ("fall back day is 24h59m", 1, 11, timedelta(hours=24, minutes=30), True),
    ("spring forward day is 22h59m", 8, 3, timedelta(hours=23), False),
    ("spring forward day fits 22h", 8, 3, timedelta(hours=22), True),
]


@pytest.mark.parametrize(
    ("day", "month", "duration", "fits"), [c[1:] for c in DST_CASES], ids=[c[0] for c in DST_CASES]
)
def test_dst_days_use_real_durations(day: int, month: int, duration: timedelta, fits: bool) -> None:
    start = datetime(2026, month, day, tzinfo=TZ)
    slots = free_slots([], start, start + timedelta(days=1), duration, TZ, *FULL_DAY)
    assert bool(slots) is fits


def test_block_in_repeated_hour_on_fall_back() -> None:
    busy = [(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), datetime(2026, 11, 1, 7, 30, tzinfo=UTC))]
    start = datetime(2026, 11, 1, tzinfo=TZ)
    slots = free_slots(busy, start, start + timedelta(days=1), timedelta(minutes=1), TZ, *FULL_DAY)
    assert utc(slots) == [
        (datetime(2026, 11, 1, 4, tzinfo=UTC), datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
        (datetime(2026, 11, 1, 7, 30, tzinfo=UTC), datetime(2026, 11, 2, 4, 59, tzinfo=UTC)),
    ]


def test_waking_hours_on_dst_days() -> None:
    for month, day in ((3, 8), (11, 1)):
        start = datetime(2026, month, day, tzinfo=TZ)
        (slot,) = free_slots([], start, start + timedelta(days=1), HOUR, TZ)
        assert (slot[0].hour, slot[1].hour) == (8, 22)
        assert slot[1].astimezone(UTC) - slot[0].astimezone(UTC) == timedelta(hours=14)


def test_merge_drops_empty_and_all_day_blocks() -> None:
    assert (
        merge(
            [
                (at(14, 9), at(14, 9)),
                (at(14, 10), at(14, 9)),
                (date(2026, 10, 14), date(2026, 10, 15)),
            ]
        )
        == []
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"duration": timedelta(0)}, "duration"),
        ({"wake": time(22), "sleep": time(8)}, "wake"),
        ({"end": at(14, 0)}, "end"),
        ({"start": datetime(2026, 10, 14)}, "timezone"),  # noqa: DTZ001
    ],
)
def test_rejects_bad_input(kwargs: dict[str, Any], match: str) -> None:
    args: dict[str, Any] = {
        "busy": [],
        "start": at(14, 0),
        "end": at(15, 0),
        "duration": HOUR,
        "tz": TZ,
    }
    with pytest.raises(ValueError, match=match):
        free_slots(**(args | kwargs))
