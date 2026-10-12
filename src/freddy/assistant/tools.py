from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import GoalStatus, User
from freddy.domain.agenda import day_bounds, event_when
from freddy.domain.freetime import SLEEP, WAKE, free_slots
from freddy.domain.goals import GoalNotFound, GoalService, format_goal_list
from freddy.domain.reminders import ReminderError, ReminderService
from freddy.domain.timeutil import describe_cron, format_local, local_today, utcnow
from freddy.domain.users import ensure_checkins
from freddy.integrations.google.calendar import (
    CalendarError,
    CalendarEvent,
    CalendarService,
    SendUpdates,
)


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    session: AsyncSession
    user: User
    calendar: CalendarService | None = None

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


class ListEventsInput(BaseModel):
    day: date | None = Field(None, description="First local date YYYY-MM-DD. Omit for today.")
    days: int = Field(1, ge=1, le=31, description="How many days to cover, starting at day")
    query: str | None = Field(
        None,
        description="Optional search over title, description, location and guests, e.g. 'Raj'",
    )


NOTIFY = Field(
    False,
    description="Email the guests about this. Only true after the owner said yes to emailing them.",
)


class CreateEventInput(BaseModel):
    summary: str = Field(description="Event title")
    start: datetime | None = Field(
        None, description="Timed event: local start, ISO like 2026-10-13T13:00"
    )
    end: datetime | None = Field(None, description="Timed event: local end")
    all_day: date | None = Field(
        None, description="All-day event: local date YYYY-MM-DD. Use instead of start/end."
    )
    all_day_days: int = Field(1, ge=1, le=31, description="All-day event length in days")
    location: str | None = None
    description: str | None = None
    attendees: list[str] = Field(default_factory=list, description="Guest emails")
    notify_attendees: bool = NOTIFY


class UpdateEventInput(BaseModel):
    event_id: str
    summary: str | None = None
    start: datetime | None = Field(
        None, description="New local start. Give start and end together."
    )
    end: datetime | None = Field(None, description="New local end")
    all_day: date | None = Field(
        None, description="Make it an all-day event on this local date, instead of start/end"
    )
    all_day_days: int = Field(1, ge=1, le=31, description="All-day event length in days")
    location: str | None = None
    description: str | None = None
    attendees: list[str] | None = Field(
        None, description="Guest emails. Replaces the whole guest list."
    )
    notify_attendees: bool = NOTIFY


class DeleteEventInput(BaseModel):
    event_id: str
    notify_attendees: bool = NOTIFY


class FreeTimeInput(BaseModel):
    day: date | None = Field(None, description="First local date YYYY-MM-DD. Omit for today.")
    days: int = Field(1, ge=1, le=14, description="How many days to search, starting at day")
    minutes: int = Field(description="Slot length in minutes", ge=5, le=720)
    wake: time | None = Field(None, description="Earliest local time HH:MM, default 08:00")
    sleep: time | None = Field(None, description="Latest local time HH:MM, default 22:00")


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


def _calendar(ctx: ToolContext) -> CalendarService:
    if ctx.calendar is None:
        raise ToolError("the calendar isn't connected")
    return ctx.calendar


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _span(
    start: datetime | None,
    end: datetime | None,
    all_day: date | None,
    all_day_days: int,
    tz: ZoneInfo,
) -> tuple[datetime, datetime] | tuple[date, date] | None:
    if all_day is not None:
        if start is not None or end is not None:
            raise ToolError("give either all_day or start and end, not both")
        return all_day, all_day + timedelta(days=all_day_days)
    if (start is None) != (end is None):
        raise ToolError("give start and end together")
    if start is None or end is None:
        return None
    return _local(start, tz), _local(end, tz)


def _updates(notify: bool) -> SendUpdates:
    return "all" if notify else "none"


def _event_span(e: CalendarEvent, tz: ZoneInfo) -> str:
    if isinstance(e.start, datetime):
        return f"{e.start.astimezone(tz):%a %d %b} {event_when(e, tz)}"
    last = e.end - timedelta(days=1)
    if not isinstance(e.end, datetime) and last > e.start:
        return f"{e.start:%a %d %b} to {last:%a %d %b} all day"
    return f"{e.start:%a %d %b} all day"


def describe_event(e: CalendarEvent, tz: ZoneInfo) -> str:
    parts = [f"id {e.id}", _event_span(e, tz), e.summary]
    if e.location:
        parts.append(f"at {e.location}")
    if e.attendees:
        parts.append("guests: " + ", ".join(e.attendees))
    if e.recurring_event_id:
        parts.append("repeats")
    if e.declined:
        parts.append("I declined")
    if e.transparent:
        parts.append("marked free")
    return " | ".join(parts)


async def _single(cal: CalendarService, event_id: str) -> CalendarEvent:
    event = await cal.get_event(event_id)
    if event.series:
        raise ToolError(
            "that id is a whole repeating series. I only change single occurrences, "
            "use an id from list_events, or change the series in Google Calendar"
        )
    return event


async def list_events(args: ListEventsInput, ctx: ToolContext) -> str:
    cal = _calendar(ctx)
    day = args.day or local_today(ctx.tz)
    try:
        events = await cal.list_events(*day_bounds(day, ctx.tz, args.days), query=args.query)
    except CalendarError as e:
        raise ToolError(str(e)) from e
    last = day + timedelta(days=args.days - 1)
    span = day.isoformat() if args.days == 1 else f"{day.isoformat()} to {last.isoformat()}"
    found = f" matching '{args.query}'" if args.query else ""
    if not events:
        return f"no events{found} for {span}"
    return f"events{found} for {span}:\n" + "\n".join(describe_event(e, ctx.tz) for e in events)


async def create_event(args: CreateEventInput, ctx: ToolContext) -> str:
    cal = _calendar(ctx)
    span = _span(args.start, args.end, args.all_day, args.all_day_days, ctx.tz)
    if span is None:
        raise ToolError("give start and end, or all_day")
    try:
        event = await cal.create_event(
            args.summary,
            *span,
            location=args.location,
            description=args.description,
            attendees=args.attendees,
            send_updates=_updates(args.notify_attendees),
        )
    except (CalendarError, ValueError) as e:
        raise ToolError(str(e)) from e
    emailed = ", guests emailed" if args.notify_attendees and args.attendees else ""
    return f"created: {describe_event(event, ctx.tz)}{emailed}"


async def update_event(args: UpdateEventInput, ctx: ToolContext) -> str:
    cal = _calendar(ctx)
    span = _span(args.start, args.end, args.all_day, args.all_day_days, ctx.tz)
    try:
        await _single(cal, args.event_id)
        event = await cal.update_event(
            args.event_id,
            summary=args.summary,
            start=span[0] if span else None,
            end=span[1] if span else None,
            location=args.location,
            description=args.description,
            attendees=args.attendees,
            send_updates=_updates(args.notify_attendees),
        )
    except (CalendarError, ValueError) as e:
        raise ToolError(str(e)) from e
    emailed = ", guests emailed" if args.notify_attendees and event.attendees else ""
    return f"updated: {describe_event(event, ctx.tz)}{emailed}"


async def delete_event(args: DeleteEventInput, ctx: ToolContext) -> str:
    cal = _calendar(ctx)
    updates = _updates(args.notify_attendees)
    try:
        event = await _single(cal, args.event_id)
        if event.recurring_event_id:
            await cal.cancel_occurrence(args.event_id, send_updates=updates)
        else:
            await cal.delete_event(args.event_id, send_updates=updates)
    except CalendarError as e:
        raise ToolError(str(e)) from e
    which = "this occurrence of " if event.recurring_event_id else ""
    emailed = ", guests emailed" if args.notify_attendees and event.attendees else ""
    return f"deleted {which}{describe_event(event, ctx.tz)}{emailed}"


async def find_free_time(args: FreeTimeInput, ctx: ToolContext) -> str:
    cal = _calendar(ctx)
    day = args.day or local_today(ctx.tz)
    start, end = day_bounds(day, ctx.tz, args.days)
    start = max(start, utcnow().astimezone(ctx.tz))
    if end <= start:
        return "that time has already passed"
    wake, sleep = args.wake or WAKE, args.sleep or SLEEP
    try:
        busy = await cal.busy(start, end)
        slots = free_slots(busy, start, end, timedelta(minutes=args.minutes), ctx.tz, wake, sleep)
    except (CalendarError, ValueError) as e:
        raise ToolError(str(e)) from e
    if not slots:
        return f"no free {args.minutes} min slots between {wake:%H:%M} and {sleep:%H:%M}"
    return f"free slots of at least {args.minutes} min ({wake:%H:%M} to {sleep:%H:%M}):\n" + (
        "\n".join(f"{s:%a %d %b} {s:%H:%M} to {e:%H:%M}" for s, e in slots)
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

CALENDAR_TOOLS: list[Tool] = [
    Tool(
        "list_events",
        "List calendar events with ids for a day or range, optionally searched by text. "
        "Repeating events show one entry per occurrence.",
        ListEventsInput,
        list_events,
    ),
    Tool(
        "create_event",
        "Create a calendar event, timed (start and end) or all-day.",
        CreateEventInput,
        create_event,
    ),
    Tool(
        "update_event",
        "Change a calendar event by id: title, time, location, notes or guests. "
        "For a repeating event this changes only that occurrence.",
        UpdateEventInput,
        update_event,
    ),
    Tool(
        "delete_event",
        "Delete a calendar event by id. For a repeating event this cancels only that occurrence.",
        DeleteEventInput,
        delete_event,
    ),
    Tool(
        "find_free_time",
        "Find open slots of a given length in the calendar, inside waking hours.",
        FreeTimeInput,
        find_free_time,
    ),
]


def tools_for(calendar: bool) -> list[Tool]:
    return TOOLS + CALENDAR_TOOLS if calendar else list(TOOLS)
