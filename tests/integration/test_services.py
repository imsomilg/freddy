from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import GoalStatus, Reminder, ReminderKind, User
from freddy.domain.goals import GoalService
from freddy.domain.reminders import ReminderError, ReminderService
from freddy.domain.timeutil import utcnow
from freddy.fastpath.router import try_fastpath


async def test_owner_gets_checkins(session: AsyncSession, user: User) -> None:
    kinds = set(await session.scalars(select(Reminder.kind).where(Reminder.user_id == user.id)))
    assert kinds == {ReminderKind.CHECKIN_MORNING, ReminderKind.CHECKIN_EVENING}


async def test_goal_lifecycle(session: AsyncSession, user: User) -> None:
    goals = GoalService(session, user)
    gym = await goals.add("Hit the gym")
    await goals.add("Read 20 pages")
    yesterday = await goals.add("Call the bank", goals.today() - timedelta(days=1))

    today = await goals.for_day()
    assert [g.id for g in today] == [yesterday.id, gym.id, today[2].id]

    await goals.complete(gym.id)
    assert {g.id for g in await goals.open_today()} == {yesterday.id, today[2].id}

    progress = await goals.progress(7)
    assert progress.done == 1
    assert progress.streak == 1


async def test_fuzzy_match(session: AsyncSession, user: User) -> None:
    goals = GoalService(session, user)
    gym = await goals.add("Hit the gym")
    await goals.add("Read 20 pages")
    hits = await goals.match_open("gym")
    assert [g.id for g in hits] == [gym.id]
    assert await goals.match_open("taxes") == []


async def test_reminders(session: AsyncSession, user: User) -> None:
    svc = ReminderService(session, user)
    once = await svc.add_once("stretch", utcnow() + timedelta(minutes=5))
    rec = await svc.add_recurring("vitamins", "30 7 * * 1-5")
    assert {r.id for r in await svc.active()} == {once.id, rec.id}

    with pytest.raises(ReminderError):
        await svc.add_once("too late", utcnow() - timedelta(hours=1))
    with pytest.raises(ReminderError):
        await svc.add_recurring("bad", "every day")

    await svc.cancel(once.id)
    assert [r.id for r in await svc.active()] == [rec.id]


async def test_fastpath_end_to_end(session: AsyncSession, user: User) -> None:
    now = utcnow()
    added = await try_fastpath("add goal: hit the gym", session, user, now)
    assert added is not None and added.reply.startswith("✅ Goal added")

    done = await try_fastpath("done gym", session, user, now)
    assert done is not None and "Done" in done.reply
    goals = await GoalService(session, user).for_day()
    assert goals[0].status == GoalStatus.DONE

    rem = await try_fastpath("remind me in 10 min to stretch", session, user, now)
    assert rem is not None and rem.reply.startswith("✅ Reminder set")

    assert await try_fastpath("done taxes", session, user, now) is None
