import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy import persona
from freddy.assistant.history import ConversationStore
from freddy.assistant.service import UserLocks
from freddy.channels.base import Channel
from freddy.db.models import EventPing, GoalStatus, MessageSource, Reminder, ReminderKind, User
from freddy.domain.agenda import calendar_block
from freddy.domain.goals import GoalService, Progress, format_goal_list
from freddy.domain.timeutil import format_local, next_cron_run, utcnow
from freddy.integrations.google.calendar import CalendarService

log = structlog.get_logger()

LATE_AFTER = timedelta(minutes=5)
SKIP_CHECKIN_AFTER = timedelta(hours=3)


async def render_checkin(
    session: AsyncSession,
    user: User,
    kind: ReminderKind,
    calendar: CalendarService | None = None,
) -> str:
    goals = GoalService(session, user)
    today = goals.today()
    items = await goals.for_day()
    if kind == ReminderKind.CHECKIN_MORNING:
        hello = persona.line("morning")
        events = await calendar_block(calendar, today, goals.tz)
        if not items:
            if events is None:
                return f"{hello} Nothing on the list yet. What are we going for today?"
            return f"{hello} Nothing on the list yet.\n{events}\nWhat are we going for today?"
        parts = [f"{hello} Here's today:", format_goal_list(items, today)]
        if events is not None:
            parts.append(events)
        parts.append("Anything to add?")
        return "\n".join(parts)
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


def render_ping(ping: EventPing, tz: ZoneInfo, now: datetime) -> str:
    minutes = round((ping.start_at - now).total_seconds() / 60)
    when = f"in {minutes} min" if minutes > 0 else "starting now"
    start = ping.start_at.astimezone(tz)
    text = f"📅 {persona.line('event_ping')} {ping.summary} at {start:%H:%M} ({when})"
    if ping.location:
        text += f", {ping.location}"
    return text


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
        calendar: CalendarService | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.channel = channel
        self.locks = locks
        self.interval = interval
        self.calendar = calendar

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
            pings = list(
                await session.execute(
                    select(EventPing.id, User.discord_id)
                    .join(User, User.id == EventPing.user_id)
                    .where(EventPing.sent_at.is_(None), EventPing.ping_at <= now)
                    .order_by(EventPing.ping_at)
                    .limit(50)
                )
            )
        for reminder_id, discord_id in due:
            async with self.locks.get(discord_id):
                await self._fire(reminder_id)
        for ping_id, discord_id in pings:
            async with self.locks.get(discord_id):
                await self._ping(ping_id)

    async def _ping(self, ping_id: int) -> None:
        now = utcnow()
        async with self.sessionmaker() as session, session.begin():
            ping = await session.scalar(
                select(EventPing)
                .where(EventPing.id == ping_id, EventPing.sent_at.is_(None))
                .with_for_update(skip_locked=True)
            )
            if ping is None:
                return
            if ping.start_at <= now:
                # Already started, so the ping is useless. Sync never re-adds past events.
                await session.delete(ping)
                log.info("scheduler.ping_skipped", ping_id=ping.id)
                return
            user = await session.get(User, ping.user_id)
            assert user is not None
            text = render_ping(ping, ZoneInfo(user.timezone), now)
            await self.channel.notify(text)
            await ConversationStore(session, user).append_exchange(
                "(scheduled: event ping)", text, MessageSource.SCHEDULER
            )
            ping.sent_at = now
            log.info("scheduler.ping_sent", ping_id=ping.id)

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
        return await render_checkin(session, user, reminder.kind, self.calendar)

    @staticmethod
    def _label(reminder: Reminder) -> str:
        if reminder.kind == ReminderKind.CHECKIN_MORNING:
            return "morning check-in"
        if reminder.kind == ReminderKind.CHECKIN_EVENING:
            return "evening check-in"
        if reminder.kind == ReminderKind.WEEKLY_SUMMARY:
            return "weekly summary"
        return f"reminder #{reminder.id}"
