import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

DAYS = {
    "sunday": 0, "sun": 0,
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
}  # fmt: skip

_TIME = re.compile(r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?$")
_UNITS = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "d": 1440, "day": 1440, "days": 1440,
}  # fmt: skip
_IN = re.compile(r"^in\s+(?P<n>\d+|an?|half an)\s*(?P<unit>[a-z]+)$")


def parse_clock(text: str) -> time | None:
    text = text.strip().lower()
    if text == "noon":
        return time(12, 0)
    if text == "midnight":
        return time(0, 0)
    m = _TIME.match(text)
    if not m:
        return None
    hour, minute = int(m["h"]), int(m["m"] or 0)
    ampm = (m["ampm"] or "").replace(".", "")
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif m["m"] is None and hour <= 12:
        return None
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _at(day: datetime, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day.date(), t, tzinfo=tz)


def parse_when(text: str, now: datetime, tz: ZoneInfo) -> datetime | None:
    text = re.sub(r"\s+", " ", text.strip().lower())
    local_now = now.astimezone(tz)

    m = _IN.match(text)
    if m:
        unit = _UNITS.get(m["unit"])
        if unit is None:
            return None
        n = m["n"]
        if n == "half an":
            minutes = 30 if unit == 60 else None
        else:
            minutes = (1 if n in ("a", "an") else int(n)) * unit
        if not minutes or minutes > 60 * 24 * 365:
            return None
        return now + timedelta(minutes=minutes)

    day_part, sep, clock = text.rpartition(" at ")
    if not sep:
        if text.startswith("at "):
            day_part, clock = "today", text[3:]
        elif text.startswith("tonight at "):
            day_part, clock = "tonight", text[len("tonight at ") :]
        else:
            return None
    day_part = day_part.strip()
    t = parse_clock(clock)
    if t is None and day_part == "tonight" and clock.isdigit() and 1 <= int(clock) <= 11:
        t = time(int(clock) + 12, 0)
    if t is None:
        return None

    if day_part in ("", "today"):
        result = _at(local_now, t, tz)
    elif day_part == "tonight":
        if t.hour < 12:
            t = time(t.hour + 12, t.minute)
        result = _at(local_now, t, tz)
    elif day_part == "tomorrow":
        result = _at(local_now + timedelta(days=1), t, tz)
    else:
        name = day_part.removeprefix("on ").removeprefix("this ").strip()
        if name not in DAYS:
            return None
        target = DAYS[name]
        current = (local_now.weekday() + 1) % 7
        ahead = (target - current) % 7
        result = _at(local_now + timedelta(days=ahead), t, tz)
        if result <= local_now:
            result += timedelta(days=7)

    if result <= local_now:
        return None
    return result


_EVERY = re.compile(r"^(?:every|each)\s+(?P<days>.+?)\s+at\s+(?P<clock>.+)$")
_DAILY = re.compile(r"^(?:daily|every day|everyday)\s+at\s+(?P<clock>.+)$")


def parse_recurring(text: str) -> str | None:
    text = re.sub(r"\s+", " ", text.strip().lower())
    m = _DAILY.match(text)
    if m:
        t = parse_clock(m["clock"])
        return f"{t.minute} {t.hour} * * *" if t else None
    m = _EVERY.match(text)
    if not m:
        return None
    t = parse_clock(m["clock"])
    if t is None:
        return None
    days = m["days"].strip()
    if days in ("day", "morning", "evening", "night"):
        dow = "*"
    elif days in ("weekday", "weekdays"):
        dow = "1-5"
    elif days in ("weekend", "weekends"):
        dow = "0,6"
    else:
        names = [d.strip().removesuffix("s") for d in re.split(r",|\band\b", days) if d.strip()]
        nums = []
        for name in names:
            num = DAYS.get(name, DAYS.get(name + "s"))
            if num is None:
                return None
            nums.append(num)
        dow = ",".join(str(n) for n in sorted(set(nums)))
    if days == "morning" and t.hour >= 12:
        return None
    if days in ("evening", "night") and t.hour < 12:
        return None
    return f"{t.minute} {t.hour} * * {dow}"
