from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WAKE = time(8)
SLEEP = time(22)

Span = tuple[datetime, datetime]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("free time needs timezone aware datetimes")
    return dt.astimezone(UTC)


def merge(busy: Iterable[tuple[datetime | date, datetime | date]]) -> list[Span]:
    """Timed blocks in UTC, sorted, overlapping or touching ones merged. Drops all-day blocks."""
    spans = sorted(
        (_utc(s), _utc(e))
        for s, e in busy
        if isinstance(s, datetime) and isinstance(e, datetime) and _utc(e) > _utc(s)
    )
    merged: list[Span] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def free_slots(
    busy: Iterable[tuple[datetime | date, datetime | date]],
    start: datetime,
    end: datetime,
    duration: timedelta,
    tz: ZoneInfo,
    wake: time = WAKE,
    sleep: time = SLEEP,
) -> list[Span]:
    """Open slots of at least `duration` inside waking hours between start and end, in tz."""
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    if wake >= sleep:
        raise ValueError("wake must be before sleep")
    if _utc(end) <= _utc(start):
        raise ValueError("end must be after start")

    blocks = merge(busy)
    slots: list[Span] = []
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last:
        # Math in UTC: same-tzinfo datetimes subtract by wall clock and ignore DST.
        cursor = max(_utc(datetime.combine(day, wake, tzinfo=tz)), _utc(start))
        stop = min(_utc(datetime.combine(day, sleep, tzinfo=tz)), _utc(end))
        for b_start, b_end in blocks:
            if b_end <= cursor:
                continue
            if b_start >= stop:
                break
            if b_start - cursor >= duration:
                slots.append((cursor, b_start))
            cursor = max(cursor, b_end)
        if stop - cursor >= duration:
            slots.append((cursor, stop))
        day += timedelta(days=1)
    return [(s.astimezone(tz), e.astimezone(tz)) for s, e in slots]
