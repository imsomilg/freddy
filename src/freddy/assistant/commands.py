from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy.assistant.history import ConversationStore
from freddy.assistant.service import UserLocks, stamp
from freddy.db.models import MessageSource, ReminderKind, User
from freddy.domain.goals import GoalService
from freddy.domain.timeutil import utcnow
from freddy.fastpath.intents import (
    CompleteGoal,
    ListGoals,
    ListReminders,
    reminder_intent,
)
from freddy.fastpath.router import run
from freddy.scheduler.loop import render_checkin

log = structlog.get_logger()

UNKNOWN_USER = "I don't know you yet."
BROKEN = "Something broke on my side. Check the logs."
REMIND_HELP = (
    "Couldn't read `{when}` as a time. Try `in 20 min`, `at 6pm`, `tomorrow at 9:30`, "
    "`friday at 18:00` or `every weekday at 7:30`."
)

Render = Callable[[AsyncSession, User, datetime, ZoneInfo], Awaitable[str | None]]


class SlashCommands:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], locks: UserLocks) -> None:
        self.sessionmaker = sessionmaker
        self.locks = locks

    async def goals(self, discord_id: int) -> str:
        async def render(s: AsyncSession, u: User, now: datetime, tz: ZoneInfo) -> str | None:
            return await run(ListGoals(), s, u)

        return await self._run(discord_id, "/goals", render)

    async def done(self, discord_id: int, goal_id: int) -> str:
        async def render(s: AsyncSession, u: User, now: datetime, tz: ZoneInfo) -> str | None:
            return await run(CompleteGoal(goal_id=goal_id), s, u)

        reply = await self._run(discord_id, f"/done {goal_id}", render)
        return reply or f"I don't see goal #{goal_id}."

    async def remind(self, discord_id: int, when: str, what: str) -> str:
        async def render(s: AsyncSession, u: User, now: datetime, tz: ZoneInfo) -> str | None:
            intent = reminder_intent(what, when, now, tz)
            return await run(intent, s, u) if intent else None

        reply = await self._run(discord_id, f"/remind when:{when} what:{what}", render)
        return reply or REMIND_HELP.format(when=when.strip())

    async def reminders(self, discord_id: int) -> str:
        async def render(s: AsyncSession, u: User, now: datetime, tz: ZoneInfo) -> str | None:
            return await run(ListReminders(), s, u)

        return await self._run(discord_id, "/reminders", render)

    async def checkin(
        self, discord_id: int, which: Literal["morning", "evening"] | None = None
    ) -> str:
        async def render(s: AsyncSession, u: User, now: datetime, tz: ZoneInfo) -> str | None:
            kind = checkin_kind(u, now.astimezone(tz), which)
            return await render_checkin(s, u, kind)

        return await self._run(discord_id, f"/checkin {which or ''}".strip(), render)

    async def open_goals(self, discord_id: int, current: str) -> list[tuple[int, str]]:
        async with self.sessionmaker() as session:
            user = await _user(session, discord_id)
            if user is None:
                return []
            goals = GoalService(session, user)
            items = await goals.open_today()
            if current.strip():
                q = current.strip().lower().lstrip("#")
                items = [g for g in items if q in g.title.lower() or q == str(g.id)]
            return [(g.id, g.title) for g in items]

    async def _run(self, discord_id: int, label: str, render: Render) -> str:
        now = utcnow()
        async with self.locks.get(discord_id):
            try:
                async with self.sessionmaker() as session, session.begin():
                    user = await _user(session, discord_id)
                    if user is None:
                        return UNKNOWN_USER
                    tz = ZoneInfo(user.timezone)
                    reply = await render(session, user, now, tz)
                    if reply is None:
                        return ""
                    await ConversationStore(session, user).append_exchange(
                        stamp(label, now, tz),
                        reply,
                        MessageSource.FASTPATH,
                        tool_names=[f"slash:{label.split()[0]}"],
                    )
                    log.info("slash.hit", command=label.split()[0])
                    return reply
            except Exception:
                log.exception("slash.failed", command=label.split()[0])
                return BROKEN


def checkin_kind(
    user: User, local_now: datetime, which: Literal["morning", "evening"] | None
) -> ReminderKind:
    if which == "morning":
        return ReminderKind.CHECKIN_MORNING
    if which == "evening":
        return ReminderKind.CHECKIN_EVENING
    morning = datetime.combine(local_now.date(), user.morning_checkin, local_now.tzinfo)
    evening = datetime.combine(local_now.date(), user.evening_checkin, local_now.tzinfo)
    midpoint = (
        morning + (evening - morning) / 2 if evening > morning else morning + timedelta(hours=6)
    )
    return ReminderKind.CHECKIN_MORNING if local_now < midpoint else ReminderKind.CHECKIN_EVENING


async def _user(session: AsyncSession, discord_id: int) -> User | None:
    return await session.scalar(select(User).where(User.discord_id == discord_id))
