from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import User
from freddy.domain.goals import GoalNotFound, GoalService, format_goal, format_goal_list
from freddy.domain.reminders import ReminderError, ReminderService
from freddy.domain.timeutil import describe_cron, format_local
from freddy.fastpath.intents import (
    AddGoal,
    AddReminderOnce,
    AddReminderRecurring,
    CancelReminder,
    CompleteGoal,
    Intent,
    ListGoals,
    ListReminders,
    parse,
)


@dataclass
class FastPathResult:
    intent: str
    reply: str


async def run(intent: Intent, session: AsyncSession, user: User) -> str | None:
    tz = ZoneInfo(user.timezone)
    goals = GoalService(session, user)
    reminders = ReminderService(session, user)
    match intent:
        case ListGoals():
            items = await goals.for_day()
            if not items:
                return "No goals for today yet. Tell me what you're going for."
            return "Today:\n" + format_goal_list(items, goals.today())
        case AddGoal(title=title):
            goal = await goals.add(title)
            return f"✅ Goal added: #{goal.id} {goal.title}"
        case CompleteGoal(goal_id=goal_id, query=query):
            if goal_id is not None:
                try:
                    goal = await goals.complete(goal_id)
                except GoalNotFound:
                    return None
            else:
                hits = await goals.match_open(query or "")
                if len(hits) != 1:
                    return None
                goal = await goals.complete(hits[0].id)
            left = len(await goals.open_today())
            tail = " That's everything for today 🎉" if left == 0 else f" {left} left today."
            return f"✅ Done: {format_goal(goal, goals.today())[2:]}.{tail}"
        case AddReminderOnce(text=text, at=at):
            try:
                r = await reminders.add_once(text, at)
            except ReminderError:
                return None
            return f"✅ Reminder set: {format_local(r.next_run_at, tz)}, {r.text}"
        case AddReminderRecurring(text=text, cron=cron):
            try:
                r = await reminders.add_recurring(text, cron)
            except ReminderError:
                return None
            return f"✅ Reminder set: {describe_cron(cron)}, {r.text}"
        case ListReminders():
            active = await reminders.active()
            if not active:
                return "No reminders set."
            return "Reminders:\n" + "\n".join(reminders.describe(r) for r in active)
        case CancelReminder(reminder_id=rid):
            try:
                r = await reminders.cancel(rid)
            except ReminderError:
                return f"I don't see an active reminder #{rid}."
            return f"🗑️ Cancelled: {r.text}"
    return None


async def try_fastpath(
    text: str, session: AsyncSession, user: User, now: datetime
) -> FastPathResult | None:
    intent = parse(text, now, ZoneInfo(user.timezone))
    if intent is None:
        return None
    reply = await run(intent, session, user)
    if reply is None:
        return None
    return FastPathResult(intent=type(intent).__name__, reply=reply)
