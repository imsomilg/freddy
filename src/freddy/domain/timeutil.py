from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from croniter import croniter

DAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_today(tz: ZoneInfo, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(tz).date()


def valid_cron(expr: str) -> bool:
    return len(expr.split()) == 5 and croniter.is_valid(expr)


def next_cron_run(expr: str, tz: ZoneInfo, after: datetime | None = None) -> datetime:
    base = (after or utcnow()).astimezone(tz)
    nxt: datetime = croniter(expr, base).get_next(datetime)
    return nxt.astimezone(UTC)


def daily_cron(t: time) -> str:
    return f"{t.minute} {t.hour} * * *"


def weekly_cron(t: time, delay_minutes: int = 0, dow: int = 0) -> str:
    minutes = t.hour * 60 + t.minute + delay_minutes
    day = (dow + minutes // 1440) % 7
    minutes %= 1440
    return f"{minutes % 60} {minutes // 60} * * {day}"


def describe_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    if not (minute.isdigit() and hour.isdigit() and dom == "*" and month == "*"):
        return f"cron `{expr}`"
    at = f"{int(hour):02d}:{int(minute):02d}"
    if dow == "*":
        return f"every day {at}"
    if dow == "1-5":
        return f"weekdays {at}"
    if dow in ("0,6", "6,0"):
        return f"weekends {at}"
    days = dow.split(",")
    if all(d.isdigit() and 0 <= int(d) <= 7 for d in days):
        names = ", ".join(DAY_NAMES[int(d) % 7].capitalize() for d in days)
        return f"every {names} {at}"
    return f"cron `{expr}`"


def format_local(dt: datetime, tz: ZoneInfo, now: datetime | None = None) -> str:
    local = dt.astimezone(tz)
    today = local_today(tz, now)
    delta = (local.date() - today).days
    if delta == 0:
        day = "today"
    elif delta == 1:
        day = "tomorrow"
    elif 1 < delta < 7:
        day = local.strftime("%a")
    else:
        day = local.strftime("%a %d %b")
    return f"{day} {local:%H:%M}"
