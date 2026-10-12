import asyncio
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httplib2
import structlog
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from freddy import persona
from freddy.integrations.google.auth import AuthAlert, is_invalid_grant

log = structlog.get_logger()

SendUpdates = Literal["all", "externalOnly", "none"]
When = datetime | date
HttpFactory = Callable[[], Any]

RETRIES = 3
RATE_LIMITS = ("rateLimitExceeded", "userRateLimitExceeded")


class CalendarError(Exception):
    """A calendar call failed. str(e) is safe to show me in Discord."""


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    summary: str
    start: When
    end: When
    all_day: bool
    location: str | None = None
    description: str | None = None
    attendees: tuple[str, ...] = ()
    link: str | None = None
    transparent: bool = False
    declined: bool = False
    recurring_event_id: str | None = None
    series: bool = False


def _parse_when(raw: dict[str, Any], tz: ZoneInfo) -> When:
    if "dateTime" in raw:
        return datetime.fromisoformat(raw["dateTime"]).astimezone(tz)
    return date.fromisoformat(raw["date"])


def _event(raw: dict[str, Any], tz: ZoneInfo) -> CalendarEvent:
    return CalendarEvent(
        id=raw["id"],
        summary=raw.get("summary", "(no title)"),
        start=_parse_when(raw["start"], tz),
        end=_parse_when(raw["end"], tz),
        all_day="date" in raw["start"],
        location=raw.get("location"),
        description=raw.get("description"),
        attendees=tuple(a["email"] for a in raw.get("attendees", ()) if "email" in a),
        link=raw.get("htmlLink"),
        transparent=raw.get("transparency") == "transparent",
        declined=any(
            a.get("self") and a.get("responseStatus") == "declined"
            for a in raw.get("attendees", ())
        ),
        recurring_event_id=raw.get("recurringEventId"),
        series="recurrence" in raw,
    )


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("calendar times need a timezone")
    return dt


def _when_body(when: When, clear_other: bool = False) -> dict[str, Any]:
    if isinstance(when, datetime):
        body: dict[str, Any] = {"dateTime": _aware(when).isoformat()}
        other = "date"
    else:
        body = {"date": when.isoformat()}
        other = "dateTime"
    # Patch merges nested objects, so switching timed <-> all-day has to null the old field.
    if clear_other:
        body[other] = None
    return body


def _check_range(start: When, end: When) -> None:
    if isinstance(start, datetime) != isinstance(end, datetime):
        raise ValueError("start and end must both be timed or both be all-day")
    if isinstance(start, datetime) and isinstance(end, datetime):
        if _aware(end) <= _aware(start):
            raise ValueError("end must be after start")
    elif end <= start:
        raise ValueError("end date must be after start date (it's exclusive)")


def _reason(e: HttpError) -> str | None:
    try:
        return str(json.loads(e.content)["error"]["errors"][0]["reason"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _friendly(e: HttpError) -> str:
    status = e.status_code
    if status == 404:
        return persona.line("calendar_not_found")
    if status == 400:
        return f"{persona.line('calendar_bad_request')} ({e.reason})"
    if status == 403 and _reason(e) == "forbiddenForNonOrganizer":
        return persona.line("calendar_organizer_only")
    if status == 429 or (status == 403 and _reason(e) in RATE_LIMITS):
        return persona.line("calendar_rate_limited")
    return f"{persona.line('calendar_down')} ({status})"


class CalendarService:
    """Async wrapper over Google Calendar v3 for one calendar."""

    def __init__(
        self,
        creds: Credentials,
        calendar_id: str,
        tz: ZoneInfo,
        alert: AuthAlert | None = None,
        http_factory: HttpFactory | None = None,
    ) -> None:
        self._calendar_id = calendar_id
        self._tz = tz
        self.alert = alert
        self._events: Any = build(
            "calendar", "v3", credentials=creds, cache_discovery=False, static_discovery=True
        ).events()
        # httplib2 isn't thread-safe, so every call gets its own Http.
        self._http = http_factory or (lambda: AuthorizedHttp(creds, http=httplib2.Http()))
        self._lock = asyncio.Lock()

    async def _run(self, action: str, fn: Callable[[Any], Any]) -> Any:
        async with self._lock:
            try:
                return await asyncio.to_thread(fn, self._http())
            except RefreshError as e:
                if is_invalid_grant(e) and self.alert:
                    await self.alert.expired()
                log.warning("calendar.refresh_failed", action=action, error=str(e))
                raise CalendarError(persona.line("calendar_expired")) from e
            except HttpError as e:
                log.warning(
                    "calendar.http_error", action=action, status=e.status_code, error=str(e)
                )
                raise CalendarError(_friendly(e)) from e
            except (TransportError, httplib2.HttpLib2Error, OSError) as e:
                log.warning("calendar.unreachable", action=action, error=str(e))
                raise CalendarError(persona.line("calendar_down")) from e

    async def list_events(
        self, start: datetime, end: datetime, query: str | None = None
    ) -> list[CalendarEvent]:
        """Events overlapping [start, end), recurring ones expanded, ordered by start.

        query is Google's free text search over title, description, location and guests.
        """
        _check_range(start, end)
        kwargs: dict[str, Any] = {
            "calendarId": self._calendar_id,
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 2500,
        }
        if query:
            kwargs["q"] = query

        def call(http: Any) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            request = self._events.list(**kwargs)
            while request is not None:
                resp = request.execute(http=http, num_retries=RETRIES)
                items.extend(resp.get("items", []))
                request = self._events.list_next(request, resp)
            return items

        return [_event(raw, self._tz) for raw in await self._run("list", call)]

    async def busy(self, start: datetime, end: datetime) -> list[tuple[When, When]]:
        """Blocks that take my time. Skips events marked Available and ones I declined.

        Freebusy can't tell all-day events from timed ones, so this reads events.list instead.
        """
        events = await self.list_events(start, end)
        return [(e.start, e.end) for e in events if not e.transparent and not e.declined]

    async def get_event(self, event_id: str) -> CalendarEvent:
        def call(http: Any) -> dict[str, Any]:
            result: dict[str, Any] = self._events.get(
                calendarId=self._calendar_id, eventId=event_id
            ).execute(http=http, num_retries=RETRIES)
            return result

        return _event(await self._run("get", call), self._tz)

    async def create_event(
        self,
        summary: str,
        start: When,
        end: When,
        *,
        location: str | None = None,
        description: str | None = None,
        attendees: Sequence[str] = (),
        send_updates: SendUpdates | None = None,
    ) -> CalendarEvent:
        """Timed events take aware datetimes, all-day events take dates (end exclusive)."""
        _check_range(start, end)
        # A client id makes retries safe: a retried insert that already landed gets a 409.
        event_id = uuid.uuid4().hex
        body: dict[str, Any] = {
            "id": event_id,
            "summary": summary,
            "start": _when_body(start),
            "end": _when_body(end),
        }
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]

        def call(http: Any) -> dict[str, Any]:
            try:
                result: dict[str, Any] = self._events.insert(
                    calendarId=self._calendar_id,
                    body=body,
                    sendUpdates=send_updates or "none",
                ).execute(http=http, num_retries=RETRIES)
                return result
            except HttpError as e:
                if e.status_code != 409 or _reason(e) != "duplicate":
                    raise
                existing: dict[str, Any] = self._events.get(
                    calendarId=self._calendar_id, eventId=event_id
                ).execute(http=http, num_retries=RETRIES)
                return existing

        return _event(await self._run("create", call), self._tz)

    async def update_event(
        self,
        event_id: str,
        *,
        summary: str | None = None,
        start: When | None = None,
        end: When | None = None,
        location: str | None = None,
        description: str | None = None,
        attendees: Sequence[str] | None = None,
        send_updates: SendUpdates | None = None,
    ) -> CalendarEvent:
        """Patches only what's passed. attendees replaces the whole list."""
        if (start is None) != (end is None):
            raise ValueError("pass start and end together")
        body: dict[str, Any] = {}
        if summary is not None:
            body["summary"] = summary
        if start is not None and end is not None:
            _check_range(start, end)
            body["start"] = _when_body(start, clear_other=True)
            body["end"] = _when_body(end, clear_other=True)
        if location is not None:
            body["location"] = location
        if description is not None:
            body["description"] = description
        if attendees is not None:
            body["attendees"] = [{"email": a} for a in attendees]
        if not body:
            raise ValueError("nothing to update")

        def call(http: Any) -> dict[str, Any]:
            result: dict[str, Any] = self._events.patch(
                calendarId=self._calendar_id,
                eventId=event_id,
                body=body,
                sendUpdates=send_updates or "none",
            ).execute(http=http, num_retries=RETRIES)
            return result

        return _event(await self._run("update", call), self._tz)

    async def delete_event(self, event_id: str, *, send_updates: SendUpdates | None = None) -> None:
        def call(http: Any) -> None:
            try:
                self._events.delete(
                    calendarId=self._calendar_id,
                    eventId=event_id,
                    sendUpdates=send_updates or "none",
                ).execute(http=http, num_retries=RETRIES)
            except HttpError as e:
                if e.status_code != 410:
                    raise
                log.info("calendar.already_deleted", event_id=event_id)

        await self._run("delete", call)

    async def cancel_occurrence(
        self, event_id: str, *, send_updates: SendUpdates | None = None
    ) -> None:
        """Cancels one instance of a recurring event, as Google's recurring events guide does."""

        def call(http: Any) -> None:
            self._events.patch(
                calendarId=self._calendar_id,
                eventId=event_id,
                body={"status": "cancelled"},
                sendUpdates=send_updates or "none",
            ).execute(http=http, num_retries=RETRIES)

        await self._run("cancel", call)
