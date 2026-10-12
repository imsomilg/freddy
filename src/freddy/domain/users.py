from datetime import time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import Reminder, ReminderKind, User
from freddy.domain.timeutil import daily_cron, next_cron_run, weekly_cron


async def ensure_owner(
    session: AsyncSession,
    discord_id: int,
    timezone: str,
    morning: time,
    evening: time,
    weekly_delay: int = 30,
) -> User:
    user = await session.scalar(select(User).where(User.discord_id == discord_id))
    if user is None:
        user = User(
            discord_id=discord_id,
            timezone=timezone,
            morning_checkin=morning,
            evening_checkin=evening,
            weekly_summary_delay=weekly_delay,
        )
        session.add(user)
        await session.flush()
    else:
        user.timezone = timezone
        user.weekly_summary_delay = weekly_delay
    await ensure_checkins(session, user)
    return user


async def ensure_checkins(session: AsyncSession, user: User) -> None:
    tz = ZoneInfo(user.timezone)
    wanted = {
        ReminderKind.CHECKIN_MORNING: daily_cron(user.morning_checkin),
        ReminderKind.CHECKIN_EVENING: daily_cron(user.evening_checkin),
        ReminderKind.WEEKLY_SUMMARY: weekly_cron(user.evening_checkin, user.weekly_summary_delay),
    }
    existing = {
        r.kind: r
        for r in await session.scalars(
            select(Reminder).where(Reminder.user_id == user.id, Reminder.kind.in_(wanted.keys()))
        )
    }
    for kind, cron in wanted.items():
        row = existing.get(kind)
        if row is None:
            session.add(
                Reminder(
                    user_id=user.id,
                    kind=kind,
                    text=kind.value,
                    cron=cron,
                    next_run_at=next_cron_run(cron, tz),
                )
            )
        elif row.cron != cron or not row.active:
            row.cron = cron
            row.active = True
            row.next_run_at = next_cron_run(cron, tz)
