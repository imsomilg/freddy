import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy import persona
from freddy.assistant.history import ConversationStore
from freddy.assistant.service import UserLocks
from freddy.channels.base import Channel
from freddy.db.models import GoalStatus, MessageSource, Reminder, ReminderKind, User
from freddy.domain.goals import GoalService, Progress, format_goal_list
from freddy.domain.timeutil import format_local, next_cron_run, utcnow

log = structlog.get_logger()

LATE_AFTER = timedelta(minutes=5)
SKIP_CHECKIN_AFTER = timedelta(hours=3)


async def render_checkin(session: AsyncSession, user: User, kind: ReminderKind) -> str:
    goals = GoalService(session, user)
    today = goals.today()
    items = await goals.for_day()
    if kind == ReminderKind.CHECKIN_MORNING:
        hello = persona.line("morning")
        if not items:
            return f"{hello} Nothing on the list yet. What are we going for today?"
        return f"{hello} Here's today:\n{format_goal_list(items, today)}\nAnything to add?"
    hello = persona.line("evening")
    done = [g for g in items if g.status == GoalStatus.DONE]
    open_ = [g for g in items if g.status == GoalStatus.OPEN]
    if not items:
        return f"{hello} No goals today. Shall we set some for tomorrow?"
    if not open_:
        return (
            f"{hello} You knocked out all {len(done)} goals today 🎉 "
            "A performance worthy of applause. Anything for tomorrow?"
        )
    parts = [hello]
    if done:
        parts.append(f"Done:\n{format_goal_list(done, today)}")
    parts.append(f"Still open:\n{format_goal_list(open_, today)}")
    parts.append("What got done, sir?")
    return "\n".join(parts)


def render_weekly(progress: Progress) -> str | None:
    if progress.total == 0:
        return None
    pct = round(100 * progress.done / progress.total)
    lines = [f"Your weekly report, sir. {progress.done} of {progress.total} goals done ({pct}%)."]
    best = max(progress.per_day, key=lambda d: (d[1], d[0]))
    if best[1] > 0:
        lines.append(f"Best day: {best[0]:%A} with {best[1]} done.")
    if progress.streak > 1:
        tail = " Remarkable, sir." if progress.streak >= 5 else ""
        lines.append(f"Streak: {progress.streak} days 🔥{tail}")
    lines.append(" · ".join(f"{d:%a} {done}/{total}" for d, done, total in progress.per_day))
    if progress.done == progress.total:
        lines.append(persona.line("week_sweep"))
    elif pct >= 60:
        lines.append(persona.line("week_strong"))
    else:
        lines.append(persona.line("week_quiet"))
    return "\n".join(lines)


class Scheduler:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        channel: Channel,
        locks: UserLocks,
        interval: float = 30,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.channel = channel
        self.locks = locks
        self.interval = interval

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler.tick_failed")
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        if not self.channel.ready:
            return
        now = utcnow()
        async with self.sessionmaker() as session:
            due = list(
                await session.execute(
                    select(Reminder.id, User.discord_id)
                    .join(User, User.id == Reminder.user_id)
                    .where(Reminder.active.is_(True), Reminder.next_run_at <= now)
                    .order_by(Reminder.next_run_at)
                    .limit(50)
                )
            )
        for reminder_id, discord_id in due:
            async with self.locks.get(discord_id):
                await self._fire(reminder_id)

    async def _fire(self, reminder_id: int) -> None:
        now = utcnow()
        async with self.sessionmaker() as session, session.begin():
            reminder = await session.scalar(
                select(Reminder)
                .where(
                    Reminder.id == reminder_id,
                    Reminder.active.is_(True),
                    Reminder.next_run_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
            if reminder is None:
                return
            user = await session.get(User, reminder.user_id)
            assert user is not None
            tz = ZoneInfo(user.timezone)
            late = now - reminder.next_run_at

            text = await self._render(session, user, reminder, late, tz)
            if text is not None:
                await self.channel.notify(text)
                await ConversationStore(session, user).append_exchange(
                    f"(scheduled: {self._label(reminder)})", text, MessageSource.SCHEDULER
                )
                log.info("scheduler.sent", reminder_id=reminder.id, kind=reminder.kind.value)
            else:
                log.info("scheduler.skipped", reminder_id=reminder.id, late=str(late))

            reminder.last_sent_at = now
            if reminder.cron:
                reminder.next_run_at = next_cron_run(reminder.cron, tz, after=now)
            else:
                reminder.active = False

    async def _render(
        self,
        session: AsyncSession,
        user: User,
        reminder: Reminder,
        late: timedelta,
        tz: ZoneInfo,
    ) -> str | None:
        if reminder.kind == ReminderKind.REMINDER:
            text = f"⏰ {persona.line('reminder')} {reminder.text}"
            if late > LATE_AFTER:
                text += f" (my apologies, this was due {format_local(reminder.next_run_at, tz)})"
            return text
        if late > SKIP_CHECKIN_AFTER:
            return None
        if reminder.kind == ReminderKind.WEEKLY_SUMMARY:
            return render_weekly(await GoalService(session, user).progress(7))
        return await render_checkin(session, user, reminder.kind)

    @staticmethod
    def _label(reminder: Reminder) -> str:
        if reminder.kind == ReminderKind.CHECKIN_MORNING:
            return "morning check-in"
        if reminder.kind == ReminderKind.CHECKIN_EVENING:
            return "evening check-in"
        if reminder.kind == ReminderKind.WEEKLY_SUMMARY:
            return "weekly summary"
        return f"reminder #{reminder.id}"
