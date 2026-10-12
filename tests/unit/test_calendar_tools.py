from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from freddy.assistant.agent import Agent
from freddy.assistant.tools import CALENDAR_TOOLS, TOOLS, ToolContext, ToolError, tools_for
from freddy.db.models import User
from freddy.domain.agenda import calendar_block
from freddy.integrations.google.calendar import CalendarEvent
from tests.fakes import FakeCalendar

TZ = ZoneInfo("Asia/Kolkata")
LUNCH = CalendarEvent(
    id="lunch1",
    summary="Lunch with Raj",
    start=datetime(2030, 1, 7, 13, 0, tzinfo=TZ),
    end=datetime(2030, 1, 7, 14, 0, tzinfo=TZ),
    all_day=False,
    location="Cafe",
    attendees=("raj@example.com",),
)
STANDUP = CalendarEvent(
    id="standup_20300107T033000Z",
    summary="Standup",
    start=datetime(2030, 1, 7, 9, 0, tzinfo=TZ),
    end=datetime(2030, 1, 7, 9, 15, tzinfo=TZ),
    all_day=False,
    recurring_event_id="standup",
)
SERIES = CalendarEvent(
    id="standup",
    summary="Standup",
    start=datetime(2030, 1, 7, 9, 0, tzinfo=TZ),
    end=datetime(2030, 1, 7, 9, 15, tzinfo=TZ),
    all_day=False,
    series=True,
)
TRIP = CalendarEvent(
    id="trip1",
    summary="Goa",
    start=date(2030, 1, 8),
    end=date(2030, 1, 11),
    all_day=True,
)
DECLINED = CalendarEvent(
    id="no1",
    summary="Boring sync",
    start=datetime(2030, 1, 7, 16, 0, tzinfo=TZ),
    end=datetime(2030, 1, 7, 17, 0, tzinfo=TZ),
    all_day=False,
    declined=True,
)


def ctx(cal: FakeCalendar | None) -> ToolContext:
    user = User(discord_id=1, timezone="Asia/Kolkata", morning_checkin=time(8))
    return ToolContext(None, user, cal)  # type: ignore[arg-type]


async def call(name: str, raw: dict[str, Any], cal: FakeCalendar | None) -> str:
    tool = {t.name: t for t in CALENDAR_TOOLS}[name]
    return await tool.call(raw, ctx(cal))


def test_calendar_tools_only_when_connected() -> None:
    names = {t.name for t in CALENDAR_TOOLS}
    assert names == {
        "list_events",
        "create_event",
        "update_event",
        "delete_event",
        "find_free_time",
    }
    assert tools_for(False) == TOOLS
    assert tools_for(True) == TOOLS + CALENDAR_TOOLS
    agent = Agent(None, model="m", tools=tools_for(True))  # type: ignore[arg-type]
    assert names <= {d["name"] for d in agent.tool_defs}
    assert not names & {d["name"] for d in Agent(None, model="m").tool_defs}  # type: ignore[arg-type]


async def test_no_calendar_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match="isn't connected"):
        await call("list_events", {}, None)


async def test_list_events_shows_ids_and_details() -> None:
    cal = FakeCalendar([STANDUP, LUNCH, TRIP])
    out = await call("list_events", {"day": "2030-01-07", "days": 7, "query": "Raj"}, cal)

    start, end, query = cal.calls[0][1]
    assert (start, end, query) == (
        datetime(2030, 1, 7, tzinfo=TZ),
        datetime(2030, 1, 14, tzinfo=TZ),
        "Raj",
    )
    assert out.splitlines() == [
        "events matching 'Raj' for 2030-01-07 to 2030-01-13:",
        "id standup_20300107T033000Z | Mon 07 Jan 09:00 to 09:15 | Standup | repeats",
        "id lunch1 | Mon 07 Jan 13:00 to 14:00 | Lunch with Raj | at Cafe"
        " | guests: raj@example.com",
        "id trip1 | Tue 08 Jan to Thu 10 Jan all day | Goa",
    ]


async def test_list_events_empty_and_errors() -> None:
    assert await call("list_events", {"day": "2030-01-07"}, FakeCalendar()) == (
        "no events for 2030-01-07"
    )
    with pytest.raises(ToolError, match="having a moment"):
        await call("list_events", {}, FakeCalendar(fail="Google Calendar is having a moment"))


async def test_create_timed_localizes_and_sends_no_emails() -> None:
    cal = FakeCalendar()
    out = await call(
        "create_event",
        {
            "summary": "Dentist",
            "start": "2030-01-07T15:00",
            "end": "2030-01-07T16:00",
            "attendees": ["doc@example.com"],
        },
        cal,
    )
    _, start, end, kw = cal.calls[0][1]
    assert start == datetime(2030, 1, 7, 15, 0, tzinfo=TZ)
    assert end == datetime(2030, 1, 7, 16, 0, tzinfo=TZ)
    assert kw["send_updates"] == "none"
    assert kw["attendees"] == ["doc@example.com"]
    assert out.startswith("created: id new1 | Mon 07 Jan 15:00 to 16:00 | Dentist")
    assert "emailed" not in out


async def test_create_with_notify_emails_guests() -> None:
    cal = FakeCalendar()
    out = await call(
        "create_event",
        {
            "summary": "Dinner",
            "start": "2030-01-07T20:00",
            "end": "2030-01-07T21:00",
            "attendees": ["raj@example.com"],
            "notify_attendees": True,
        },
        cal,
    )
    assert cal.calls[0][1][3]["send_updates"] == "all"
    assert out.endswith("guests emailed")


async def test_create_all_day_uses_exclusive_end() -> None:
    cal = FakeCalendar()
    await call("create_event", {"summary": "Goa", "all_day": "2030-01-08", "all_day_days": 3}, cal)
    assert cal.calls[0][1][1:3] == (date(2030, 1, 8), date(2030, 1, 11))


@pytest.mark.parametrize(
    "raw",
    [
        {"summary": "x"},
        {"summary": "x", "start": "2030-01-07T15:00"},
        {"summary": "x", "all_day": "2030-01-08", "start": "2030-01-07T15:00"},
        {"summary": "x", "start": "2030-01-07T16:00", "end": "2030-01-07T15:00"},
    ],
)
async def test_create_rejects_bad_times(raw: dict[str, Any]) -> None:
    with pytest.raises(ToolError):
        await call("create_event", raw, FakeCalendar())


async def test_update_moves_one_occurrence() -> None:
    cal = FakeCalendar([STANDUP])
    out = await call(
        "update_event",
        {"event_id": STANDUP.id, "start": "2030-01-07T10:00", "end": "2030-01-07T10:15"},
        cal,
    )
    assert [c[0] for c in cal.calls] == ["get", "update"]
    event_id, kw = cal.calls[1][1]
    assert event_id == STANDUP.id
    assert kw["start"] == datetime(2030, 1, 7, 10, 0, tzinfo=TZ)
    assert kw["send_updates"] == "none"
    assert out.startswith("updated: id standup_20300107T033000Z | Mon 07 Jan 10:00 to 10:15")


async def test_update_and_delete_refuse_whole_series() -> None:
    cal = FakeCalendar([SERIES])
    with pytest.raises(ToolError, match="whole repeating series"):
        await call("update_event", {"event_id": "standup", "summary": "Daily"}, cal)
    with pytest.raises(ToolError, match="whole repeating series"):
        await call("delete_event", {"event_id": "standup"}, cal)
    assert [c[0] for c in cal.calls] == ["get", "get"]


async def test_delete_plain_event() -> None:
    cal = FakeCalendar([LUNCH])
    out = await call("delete_event", {"event_id": "lunch1"}, cal)
    assert cal.calls[1] == ("delete", ("lunch1", {"send_updates": "none"}))
    assert out.startswith("deleted id lunch1")


async def test_delete_occurrence_cancels_it() -> None:
    cal = FakeCalendar([STANDUP])
    out = await call("delete_event", {"event_id": STANDUP.id, "notify_attendees": True}, cal)
    assert cal.calls[1] == ("cancel", (STANDUP.id, {"send_updates": "all"}))
    assert out.startswith("deleted this occurrence of id standup_20300107T033000Z")


async def test_delete_unknown_event() -> None:
    with pytest.raises(ToolError, match="couldn't find"):
        await call("delete_event", {"event_id": "nope"}, FakeCalendar())


async def test_find_free_time() -> None:
    cal = FakeCalendar([STANDUP, LUNCH, TRIP])
    out = await call("find_free_time", {"day": "2030-01-07", "minutes": 120}, cal)
    assert out.splitlines() == [
        "free slots of at least 120 min (08:00 to 22:00):",
        "Mon 07 Jan 09:15 to 13:00",
        "Mon 07 Jan 14:00 to 22:00",
    ]


async def test_find_free_time_custom_hours_and_past_days() -> None:
    cal = FakeCalendar([LUNCH])
    out = await call(
        "find_free_time",
        {"day": "2030-01-07", "minutes": 90, "wake": "12:00", "sleep": "15:00"},
        cal,
    )
    assert out == "no free 90 min slots between 12:00 and 15:00"
    assert await call("find_free_time", {"day": "2020-01-01", "minutes": 30}, cal) == (
        "that time has already passed"
    )


async def test_calendar_block() -> None:
    day = date(2030, 1, 7)
    assert await calendar_block(None, day, TZ) is None
    assert await calendar_block(FakeCalendar(), day, TZ) == "Nothing on the calendar."  # type: ignore[arg-type]
    assert await calendar_block(FakeCalendar(fail="down"), day, TZ) == "down"  # type: ignore[arg-type]
    block = await calendar_block(FakeCalendar([STANDUP, DECLINED, TRIP]), day, TZ)  # type: ignore[arg-type]
    assert block == "On the calendar:\n• 09:00 to 09:15 Standup\n• all day Goa"
