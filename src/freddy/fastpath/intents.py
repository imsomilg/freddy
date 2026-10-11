import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from freddy.fastpath.timeparse import DAYS, parse_recurring, parse_when


@dataclass(frozen=True)
class ListGoals:
    pass


@dataclass(frozen=True)
class AddGoal:
    title: str


@dataclass(frozen=True)
class CompleteGoal:
    goal_id: int | None = None
    query: str | None = None


@dataclass(frozen=True)
class AddReminderOnce:
    text: str
    at: datetime


@dataclass(frozen=True)
class AddReminderRecurring:
    text: str
    cron: str


@dataclass(frozen=True)
class ListReminders:
    pass


@dataclass(frozen=True)
class CancelReminder:
    reminder_id: int


Intent = (
    ListGoals
    | AddGoal
    | CompleteGoal
    | AddReminderOnce
    | AddReminderRecurring
    | ListReminders
    | CancelReminder
)

_LIST_GOALS = re.compile(
    r"^(?:goals|my goals|today|todays goals|today's goals|"
    r"(?:show|list)(?: me)?(?: my)? goals(?: for today)?|"
    r"what are my goals(?: for today| today)?|what(?:'s| is) on today)$"
)
_ADD_GOAL = re.compile(
    r"^(?:add(?: a)?(?: new)? goal|new goal)\s*:?\s+(?P<t>.+)$|^goal\s*:\s*(?P<t2>.+)$"
)
_DONE = re.compile(
    r"^(?:done|finished|completed|complete|did)(?: with)?\s+(?:#?(?P<id>\d+)|(?P<q>.+))$"
    r"|^mark\s+(?:#?(?P<id2>\d+)|(?P<q2>.+?))\s+(?:as\s+)?(?:done|complete|completed)$"
)
_LIST_REMINDERS = re.compile(
    r"^(?:reminders|my reminders|(?:show|list)(?: me)?(?: my)? reminders|what are my reminders)$"
)
_CANCEL_REMINDER = re.compile(r"^(?:cancel|delete|remove|stop)\s+reminder\s+#?(?P<id>\d+)$")
_REMIND_WHEN_FIRST = re.compile(r"^remind me\s+(?P<when>.+?)\s+to\s+(?P<what>.+)$")
_REMIND_WHAT_FIRST = re.compile(r"^remind me\s+to\s+(?P<rest>.+)$")
_WHEN_KEYWORDS = re.compile(r"\s(?=(?:in|at|on|tomorrow|tonight|every|each|daily)\b)")

_DATE_WORDS = {"tomorrow", "yesterday", "next", "tonight", *DAYS}
_MULTI = re.compile(r",|\band\b|&|\+")


def _normalise(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text.rstrip(".!?").strip()


def _reminder(what: str, when: str, now: datetime, tz: ZoneInfo) -> Intent | None:
    what = what.strip()
    if not what:
        return None
    cron = parse_recurring(when)
    if cron:
        return AddReminderRecurring(text=what, cron=cron)
    at = parse_when(when, now, tz)
    if at:
        return AddReminderOnce(text=what, at=at)
    return None


def parse(text: str, now: datetime, tz: ZoneInfo) -> Intent | None:
    original = _normalise(text)
    lower = original.lower()
    if not lower or "\n" in lower:
        return None

    if _LIST_GOALS.match(lower):
        return ListGoals()
    if _LIST_REMINDERS.match(lower):
        return ListReminders()

    if m := _CANCEL_REMINDER.match(lower):
        return CancelReminder(int(m["id"]))

    if m := _ADD_GOAL.match(lower):
        start = m.start("t") if m["t"] else m.start("t2")
        title = original[start:].strip()
        words = set(re.findall(r"[a-z']+", title.lower()))
        if not title or words & _DATE_WORDS or _MULTI.search(title.lower()):
            return None
        return AddGoal(title)

    if m := _DONE.match(lower):
        gid = m["id"] or m["id2"]
        if gid:
            return CompleteGoal(goal_id=int(gid))
        query = (m["q"] or m["q2"] or "").strip()
        if not query or _MULTI.search(query):
            return None
        return CompleteGoal(query=query)

    if m := _REMIND_WHEN_FIRST.match(lower):
        start = m.start("what")
        intent = _reminder(original[start:], m["when"], now, tz)
        if intent:
            return intent

    if m := _REMIND_WHAT_FIRST.match(lower):
        rest_start = m.start("rest")
        rest = lower[rest_start:]
        splits = [s.start() for s in _WHEN_KEYWORDS.finditer(rest)]
        for pos in reversed(splits):
            intent = _reminder(original[rest_start : rest_start + pos], rest[pos + 1 :], now, tz)
            if intent:
                return intent

    return None
