from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from freddy.assistant.commands import SlashCommands
from freddy.assistant.service import UserLocks
from freddy.db.engine import make_sessionmaker
from freddy.db.models import Goal, GoalStatus, Message, Reminder, User


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
    assert (await c.goals(9001)).startswith("No goals for today")
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
    assert await slash(engine).done(9001, 999999) == "I don't see goal #999999."


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
    assert reply.startswith("Couldn't read `whenever`")


async def test_checkin(engine: AsyncEngine, committed_owner: User) -> None:
    c = slash(engine)
    assert (await c.checkin(9001, "morning")).startswith("Morning!")
    assert (await c.checkin(9001, "evening")).startswith("Evening!")


async def test_unknown_user(engine: AsyncEngine) -> None:
    assert await slash(engine).goals(1) == "I don't know you yet."
    assert await slash(engine).open_goals(1, "") == []
