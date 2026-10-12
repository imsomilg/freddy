from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import User
from freddy.domain.goals import GoalService, format_goal_list
from freddy.integrations.google.calendar import CalendarError, CalendarEvent, CalendarService


def day_bounds(day: date, tz: ZoneInfo, days: int = 1) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time(), tz),
        datetime.combine(day + timedelta(days=days), time(), tz),
    )


def event_when(event: CalendarEvent, tz: ZoneInfo) -> str:
    if isinstance(event.start, datetime) and isinstance(event.end, datetime):
        start, end = event.start.astimezone(tz), event.end.astimezone(tz)
        return f"{start:%H:%M} to {end:%H:%M}"
    return "all day"


def format_event_list(events: list[CalendarEvent], tz: ZoneInfo) -> str:
    return "\n".join(f"• {event_when(e, tz)} {e.summary}" for e in events)


async def day_events(
    calendar: CalendarService, day: date, tz: ZoneInfo
) -> list[CalendarEvent] | str:
    """The day's events without the ones I declined, or a line saying why the calendar failed."""
    try:
        events = await calendar.list_events(*day_bounds(day, tz))
    except CalendarError as e:
        return str(e)
    return [e for e in events if not e.declined]


async def calendar_block(calendar: CalendarService | None, day: date, tz: ZoneInfo) -> str | None:
    """'On the calendar' section for check-ins and /today. None when the calendar isn't set up."""
    if calendar is None:
        return None
    events = await day_events(calendar, day, tz)
    if isinstance(events, str):
        return events
    if not events:
        return "Nothing on the calendar."
    return "On the calendar:\n" + format_event_list(events, tz)


async def render_day(
    session: AsyncSession, user: User, calendar: CalendarService | None, days_ahead: int = 0
) -> str:
    """Goals plus calendar for today or a later day. Backs 'what's on today' and /today."""
    goals = GoalService(session, user)
    today = goals.today()
    day = today + timedelta(days=days_ahead)
    items = await goals.for_day(day)
    label = {0: "Today", 1: "Tomorrow"}.get(days_ahead, f"{day:%A %d %b}")
    lines = [f"{label}, sir:", format_goal_list(items, today) if items else "No goals yet."]
    block = await calendar_block(calendar, day, goals.tz)
    if block:
        lines.append(block)
    return "\n".join(lines)
