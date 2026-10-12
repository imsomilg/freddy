from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from freddy import persona
from freddy.assistant.commands import UNKNOWN_USER, SlashCommands
from freddy.assistant.service import UserLocks
from freddy.db.engine import make_sessionmaker
from freddy.db.models import Goal, GoalStatus, Message, Reminder, User
from freddy.domain.agenda import day_bounds
from freddy.domain.timeutil import local_today
from freddy.integrations.google.calendar import CalendarEvent
from tests.fakes import FakeCalendar


def slash(engine: AsyncEngine) -> SlashCommands:
    return SlashCommands(make_sessionmaker(engine), UserLocks())


async def add_goal(engine: AsyncEngine, owner: User, title: str) -> int:
    from freddy.domain.goals import GoalService

    async with make_sessionmaker(engine)() as s, s.begin():
        user = await s.get(User, owner.id)
        assert user is not None
        return (await GoalService(s, user).add(title)).id


async def test_goals_and_done(engine: AsyncEngine, committed_owner: User) -> None:
    c = slash(engine)
    assert await c.goals(9001) in persona.LINES["no_goals"]
    gid = await add_goal(engine, committed_owner, "stretch")
    await add_goal(engine, committed_owner, "read 20 pages")

    assert await c.open_goals(9001, "") == [(gid, "stretch"), (gid + 1, "read 20 pages")]
    assert await c.open_goals(9001, "STR") == [(gid, "stretch")]

    reply = await c.done(9001, gid)
    assert reply.startswith("✅ Done") and "1 left today" in reply
    async with make_sessionmaker(engine)() as s:
        goal = await s.get(Goal, gid)
        assert goal is not None and goal.status == GoalStatus.DONE
        tools = list(await s.scalars(select(Message.tool_names).where(Message.role == "user")))
    assert ["slash:/goals"] in tools and ["slash:/done"] in tools


async def test_done_unknown_goal(engine: AsyncEngine, committed_owner: User) -> None:
    assert await slash(engine).done(9001, 999999) == "I can't find goal #999999, sir."


async def test_remind_parses_and_lists(engine: AsyncEngine, committed_owner: User) -> None:
    c = slash(engine)
    assert (await c.remind(9001, "in 20 min", "stand up")).startswith("✅ Reminder set")
    assert (await c.remind(9001, "every weekday at 7:30", "meds")).startswith("✅ Reminder set")
    listed = await c.reminders(9001)
    assert "stand up" in listed and "meds" in listed
    async with make_sessionmaker(engine)() as s:
        texts = set(
            await s.scalars(select(Reminder.text).where(Reminder.user_id == committed_owner.id))
        )
    assert {"stand up", "meds"} <= texts


async def test_remind_bad_time(engine: AsyncEngine, committed_owner: User) -> None:
    reply = await slash(engine).remind(9001, "whenever", "stand up")
    assert "couldn't read `whenever`" in reply


async def test_checkin(engine: AsyncEngine, committed_owner: User) -> None:
    c = slash(engine)
    assert (await c.checkin(9001, "morning")).startswith(persona.LINES["morning"])
    assert (await c.checkin(9001, "evening")).startswith(persona.LINES["evening"])


async def test_unknown_user(engine: AsyncEngine) -> None:
    assert await slash(engine).goals(1) == UNKNOWN_USER
    assert await slash(engine).open_goals(1, "") == []


def standup(owner: User) -> CalendarEvent:
    tz = ZoneInfo(owner.timezone)
    start = day_bounds(local_today(tz), tz)[0] + timedelta(hours=9, minutes=30)
    return CalendarEvent(
        id="s1", summary="Standup", start=start, end=start + timedelta(minutes=15), all_day=False
    )


async def test_today_shows_goals_and_events(engine: AsyncEngine, committed_owner: User) -> None:
    cal = FakeCalendar([standup(committed_owner)])
    c = SlashCommands(make_sessionmaker(engine), UserLocks(), cal)  # type: ignore[arg-type]
    await add_goal(engine, committed_owner, "stretch")
    reply = await c.today(9001)
    lines = reply.splitlines()
    assert lines[0] == "Today, sir:"
    assert lines[1].endswith("stretch")
    assert lines[2:] == ["On the calendar:", "• 09:30 to 09:45 Standup"]


async def test_morning_checkin_lists_events(engine: AsyncEngine, committed_owner: User) -> None:
    cal = FakeCalendar([standup(committed_owner)])
    c = SlashCommands(make_sessionmaker(engine), UserLocks(), cal)  # type: ignore[arg-type]
    empty = await c.checkin(9001, "morning")
    assert "Nothing on the list yet.\nOn the calendar:\n• 09:30 to 09:45 Standup\n" in empty

    await add_goal(engine, committed_owner, "stretch")
    reply = (await c.checkin(9001, "morning")).splitlines()
    assert reply[1].endswith("stretch")
    assert reply[2:] == ["On the calendar:", "• 09:30 to 09:45 Standup", "Anything to add?"]


async def test_morning_checkin_when_calendar_fails(
    engine: AsyncEngine, committed_owner: User
) -> None:
    cal = FakeCalendar(fail="The calendar isn't answering, sir.")
    c = SlashCommands(make_sessionmaker(engine), UserLocks(), cal)  # type: ignore[arg-type]
    reply = await c.checkin(9001, "morning")
    assert "The calendar isn't answering, sir." in reply
