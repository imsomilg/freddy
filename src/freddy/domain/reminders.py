from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import Reminder, ReminderKind, User
from freddy.domain.timeutil import describe_cron, format_local, next_cron_run, utcnow, valid_cron


class ReminderError(Exception):
    pass


class ReminderService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user
        self.tz = ZoneInfo(user.timezone)

    async def add_once(self, text: str, at: datetime) -> Reminder:
        if at.tzinfo is None:
            at = at.replace(tzinfo=self.tz)
        if at <= utcnow() - timedelta(seconds=30):
            raise ReminderError("that time is already in the past")
        reminder = Reminder(user_id=self.user.id, text=text.strip(), next_run_at=at)
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def add_recurring(self, text: str, cron: str) -> Reminder:
        if not valid_cron(cron):
            raise ReminderError(f"invalid cron expression: {cron}")
        reminder = Reminder(
            user_id=self.user.id,
            text=text.strip(),
            cron=cron,
            next_run_at=next_cron_run(cron, self.tz),
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def active(self) -> list[Reminder]:
        stmt = (
            select(Reminder)
            .where(
                Reminder.user_id == self.user.id,
                Reminder.active.is_(True),
                Reminder.kind == ReminderKind.REMINDER,
            )
            .order_by(Reminder.next_run_at)
        )
        return list(await self.session.scalars(stmt))

    async def cancel(self, reminder_id: int) -> Reminder:
        reminder = await self.session.get(Reminder, reminder_id)
        if (
            reminder is None
            or reminder.user_id != self.user.id
            or reminder.kind != ReminderKind.REMINDER
            or not reminder.active
        ):
            raise ReminderError(f"no active reminder #{reminder_id}")
        reminder.active = False
        return reminder

    def describe(self, reminder: Reminder) -> str:
        when = (
            describe_cron(reminder.cron)
            if reminder.cron
            else format_local(reminder.next_run_at, self.tz)
        )
        return f"#{reminder.id} {when}, {reminder.text}"
