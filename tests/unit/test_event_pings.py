from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from freddy.db.models import EventPing
from freddy.integrations.google.calendar import CalendarEvent
from freddy.scheduler.calendar_sync import pingable
from freddy.scheduler.loop import render_ping

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 13, 7, 20, tzinfo=UTC)
START = datetime(2026, 10, 13, 7, 30, tzinfo=UTC)


def ping(location: str | None = None) -> EventPing:
    return EventPing(event_id="e1", start_at=START, summary="Lunch with Raj", location=location)


def event(**kw: object) -> CalendarEvent:
    fields: dict[str, object] = {
        "id": "e1",
        "summary": "Lunch",
        "start": START,
        "end": START + timedelta(hours=1),
        "all_day": False,
    }
    fields.update(kw)
    return CalendarEvent(**fields)  # type: ignore[arg-type]


def test_ping_text() -> None:
    text = render_ping(ping(), TZ, NOW)
    assert text.startswith("📅 ")
    assert text.endswith("Lunch with Raj at 13:00 (in 10 min)")


def test_ping_rounds_tick_lag_and_adds_location() -> None:
    text = render_ping(ping("Cafe Mondegar"), TZ, NOW + timedelta(seconds=20))
    assert text.endswith("at 13:00 (in 10 min), Cafe Mondegar")


def test_ping_starting_now() -> None:
    assert render_ping(ping(), TZ, START - timedelta(seconds=20)).endswith("(starting now)")


def test_pingable() -> None:
    assert pingable(event(), NOW)
    assert not pingable(event(declined=True), NOW)
    assert not pingable(event(start=START.date(), end=date(2026, 10, 14), all_day=True), NOW)
    assert not pingable(event(), START)
    assert pingable(event(transparent=True), NOW)
