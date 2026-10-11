from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import GoalStatus, User
from freddy.domain.goals import GoalNotFound, GoalService, format_goal_list
from freddy.domain.reminders import ReminderError, ReminderService
from freddy.domain.timeutil import describe_cron, format_local
from freddy.domain.users import ensure_checkins


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    session: AsyncSession
    user: User

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.user.timezone)


class AddGoalInput(BaseModel):
    title: str = Field(description="Short goal title, e.g. 'Read 20 pages'")
    day: date | None = Field(None, description="Local date YYYY-MM-DD. Omit for today.")


class ListGoalsInput(BaseModel):
    day: date | None = Field(
        None, description="Local date YYYY-MM-DD. Omit for today (includes unfinished older goals)."
    )


class GoalIdInput(BaseModel):
    goal_id: int


class UpdateGoalInput(BaseModel):
    goal_id: int
    title: str | None = None
    day: date | None = Field(None, description="Move the goal to this local date")
    status: Literal["open", "done", "dropped"] | None = None


class ProgressInput(BaseModel):
    days: int = Field(7, ge=1, le=90)


class SetReminderInput(BaseModel):
    text: str = Field(description="What to remind about")
    at: datetime | None = Field(
        None, description="One-shot: local date and time, ISO format like 2026-10-12T18:00"
    )
    cron: str | None = Field(
        None, description="Recurring: 5 field cron in local time, e.g. '30 7 * * 1-5'"
    )


class EmptyInput(BaseModel):
    pass


class ReminderIdInput(BaseModel):
    reminder_id: int


class CheckinTimesInput(BaseModel):
    morning: time | None = Field(None, description="Local time HH:MM")
    evening: time | None = Field(None, description="Local time HH:MM")


Handler = Callable[[Any, ToolContext], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler

    def definition(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}

    async def call(self, raw: dict[str, Any], ctx: ToolContext) -> str:
        try:
            args = self.input_model.model_validate(raw)
        except ValidationError as e:
            raise ToolError(f"invalid input: {e.errors(include_url=False)}") from e
        return await self.handler(args, ctx)


async def add_goal(args: AddGoalInput, ctx: ToolContext) -> str:
    goal = await GoalService(ctx.session, ctx.user).add(args.title, args.day)
    return f"added goal #{goal.id} '{goal.title}' for {goal.for_date.isoformat()}"


async def list_goals(args: ListGoalsInput, ctx: ToolContext) -> str:
    svc = GoalService(ctx.session, ctx.user)
    goals = await svc.for_day(args.day)
    day = args.day or svc.today()
    if not goals:
        return f"no goals for {day.isoformat()}"
    return f"goals for {day.isoformat()}:\n" + format_goal_list(goals, svc.today())


async def complete_goal(args: GoalIdInput, ctx: ToolContext) -> str:
    try:
        goal = await GoalService(ctx.session, ctx.user).complete(args.goal_id)
    except GoalNotFound as e:
        raise ToolError(f"no goal #{args.goal_id}") from e
    return f"completed goal #{goal.id} '{goal.title}'"


async def update_goal(args: UpdateGoalInput, ctx: ToolContext) -> str:
    try:
        goal = await GoalService(ctx.session, ctx.user).update(
            args.goal_id,
            title=args.title,
            for_date=args.day,
            status=GoalStatus(args.status) if args.status else None,
        )
    except GoalNotFound as e:
        raise ToolError(f"no goal #{args.goal_id}") from e
    return (
        f"goal #{goal.id} is now '{goal.title}', {goal.for_date.isoformat()}, {goal.status.value}"
    )


async def get_progress(args: ProgressInput, ctx: ToolContext) -> str:
    p = await GoalService(ctx.session, ctx.user).progress(args.days)
    days = "\n".join(f"{d:%a %d %b}: {done}/{total}" for d, done, total in p.per_day if total)
    return (
        f"last {p.days} days: {p.done}/{p.total} goals done, current streak {p.streak} days\n"
        f"{days or 'no goals in this period'}"
    )


async def set_reminder(args: SetReminderInput, ctx: ToolContext) -> str:
    svc = ReminderService(ctx.session, ctx.user)
    if bool(args.at) == bool(args.cron):
        raise ToolError("give exactly one of 'at' or 'cron'")
    try:
        if args.cron:
            r = await svc.add_recurring(args.text, args.cron)
            return f"reminder #{r.id} set: {describe_cron(args.cron)}, '{r.text}'"
        assert args.at is not None
        at = args.at if args.at.tzinfo else args.at.replace(tzinfo=ctx.tz)
        r = await svc.add_once(args.text, at)
    except ReminderError as e:
        raise ToolError(str(e)) from e
    return f"reminder #{r.id} set: {format_local(r.next_run_at, ctx.tz)}, '{r.text}'"


async def list_reminders(args: EmptyInput, ctx: ToolContext) -> str:
    svc = ReminderService(ctx.session, ctx.user)
    items = await svc.active()
    user = ctx.user
    checkins = (
        f"check-ins: morning {user.morning_checkin:%H:%M}, evening {user.evening_checkin:%H:%M}"
    )
    if not items:
        return f"no reminders\n{checkins}"
    return "\n".join(svc.describe(r) for r in items) + f"\n{checkins}"


async def cancel_reminder(args: ReminderIdInput, ctx: ToolContext) -> str:
    try:
        r = await ReminderService(ctx.session, ctx.user).cancel(args.reminder_id)
    except ReminderError as e:
        raise ToolError(str(e)) from e
    return f"cancelled reminder #{r.id} '{r.text}'"


async def set_checkin_times(args: CheckinTimesInput, ctx: ToolContext) -> str:
    if not args.morning and not args.evening:
        raise ToolError("give morning and/or evening")
    if args.morning:
        ctx.user.morning_checkin = args.morning.replace(second=0, microsecond=0)
    if args.evening:
        ctx.user.evening_checkin = args.evening.replace(second=0, microsecond=0)
    await ensure_checkins(ctx.session, ctx.user)
    return (
        f"check-ins now morning {ctx.user.morning_checkin:%H:%M}, "
        f"evening {ctx.user.evening_checkin:%H:%M}"
    )


TOOLS: list[Tool] = [
    Tool("add_goal", "Add a goal for today or a given date.", AddGoalInput, add_goal),
    Tool("list_goals", "List goals with ids and status.", ListGoalsInput, list_goals),
    Tool("complete_goal", "Mark a goal as done.", GoalIdInput, complete_goal),
    Tool(
        "update_goal",
        "Rename a goal, move it to another date, reopen it or drop it.",
        UpdateGoalInput,
        update_goal,
    ),
    Tool(
        "get_progress",
        "Goal completion stats and current streak.",
        ProgressInput,
        get_progress,
    ),
    Tool(
        "set_reminder",
        "Create a reminder. Use 'at' for one-shot or 'cron' for recurring, not both.",
        SetReminderInput,
        set_reminder,
    ),
    Tool(
        "list_reminders",
        "List active reminders with ids, plus check-in times.",
        EmptyInput,
        list_reminders,
    ),
    Tool("cancel_reminder", "Cancel a reminder by id.", ReminderIdInput, cancel_reminder),
    Tool(
        "set_checkin_times",
        "Change the daily morning and/or evening check-in time.",
        CheckinTimesInput,
        set_checkin_times,
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
