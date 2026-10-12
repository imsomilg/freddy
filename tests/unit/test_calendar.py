import json
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMockSequence

from freddy import persona
from freddy.integrations.google import calendar
from freddy.integrations.google.calendar import CalendarError, CalendarService

TZ = ZoneInfo("Asia/Kolkata")
START = datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
END = START + timedelta(hours=1)
TIMED = {
    "id": "abc123",
    "summary": "Lunch with Raj",
    "start": {"dateTime": "2026-10-13T03:30:00Z"},
    "end": {"dateTime": "2026-10-13T04:30:00Z"},
    "attendees": [{"email": "raj@example.com"}, {"self": True}],
    "htmlLink": "https://www.google.com/calendar/event?eid=x",
}
ALL_DAY = {
    "id": "day1",
    "summary": "Holiday",
    "start": {"date": "2026-10-14"},
    "end": {"date": "2026-10-15"},
}


def error(status: int, reason: str) -> tuple[dict[str, str], str]:
    body = {"error": {"errors": [{"reason": reason, "message": reason}], "code": status}}
    return {"status": str(status)}, json.dumps(body)


def ok(body: Any = None) -> tuple[dict[str, str], str]:
    return {"status": "200"}, json.dumps(body) if body is not None else ""


class FakeAlert:
    def __init__(self) -> None:
        self.count = 0

    async def expired(self) -> None:
        self.count += 1


def service(
    responses: list[Any], alert: FakeAlert | None = None
) -> tuple[CalendarService, HttpMockSequence]:
    http = HttpMockSequence(responses)
    svc = CalendarService(
        Credentials(token="t"),
        "primary",
        TZ,
        alert=alert,  # type: ignore[arg-type]
        http_factory=lambda: http,
    )
    return svc, http


def query(http: HttpMockSequence, i: int = 0) -> dict[str, list[str]]:
    return parse_qs(urlparse(http.request_sequence[i][0]).query)


def sent_body(http: HttpMockSequence, i: int = 0) -> Any:
    return json.loads(http.request_sequence[i][2])


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)


async def test_list_pages_and_parses_timed_and_all_day() -> None:
    svc, http = service([ok({"items": [TIMED], "nextPageToken": "p2"}), ok({"items": [ALL_DAY]})])
    events = await svc.list_events(START, START + timedelta(days=2))

    q = query(http)
    assert q["timeMin"] == ["2026-10-13T09:00:00+05:30"]
    assert q["timeMax"] == ["2026-10-15T09:00:00+05:30"]
    assert q["singleEvents"] == ["true"]
    assert q["orderBy"] == ["startTime"]
    assert query(http, 1)["pageToken"] == ["p2"]

    lunch, holiday = events
    assert lunch.start == datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
    assert lunch.start.tzinfo == TZ  # type: ignore[union-attr]
    assert not lunch.all_day
    assert lunch.attendees == ("raj@example.com",)
    assert lunch.link == TIMED["htmlLink"]
    assert holiday.all_day
    assert (holiday.start, holiday.end) == (date(2026, 10, 14), date(2026, 10, 15))


async def test_busy_skips_available_and_declined() -> None:
    available = {**TIMED, "id": "free1", "transparency": "transparent"}
    declined = {
        **TIMED,
        "id": "no1",
        "attendees": [{"email": "me@example.com", "self": True, "responseStatus": "declined"}],
    }
    maybe = {
        **TIMED,
        "id": "maybe1",
        "attendees": [{"email": "me@example.com", "self": True, "responseStatus": "tentative"}],
    }
    svc, _ = service([ok({"items": [TIMED, available, declined, maybe, ALL_DAY]})])
    busy = await svc.busy(START, START + timedelta(days=2))

    lunch = (datetime(2026, 10, 13, 9, 0, tzinfo=TZ), datetime(2026, 10, 13, 10, 0, tzinfo=TZ))
    assert busy == [lunch, lunch, (date(2026, 10, 14), date(2026, 10, 15))]


async def test_list_rejects_naive_times() -> None:
    svc, _ = service([])
    with pytest.raises(ValueError, match="timezone"):
        await svc.list_events(datetime(2026, 10, 13), datetime(2026, 10, 14))  # noqa: DTZ001


async def test_create_timed_sends_no_emails_by_default() -> None:
    svc, http = service([ok(TIMED)])
    event = await svc.create_event("Lunch with Raj", START, END, attendees=["raj@example.com"])

    assert query(http)["sendUpdates"] == ["none"]
    body = sent_body(http)
    assert body["start"] == {"dateTime": "2026-10-13T09:00:00+05:30"}
    assert body["end"] == {"dateTime": "2026-10-13T10:00:00+05:30"}
    assert body["attendees"] == [{"email": "raj@example.com"}]
    assert len(body["id"]) == 32
    assert event.id == "abc123"


async def test_create_all_day_and_explicit_updates() -> None:
    svc, http = service([ok(ALL_DAY)])
    await svc.create_event("Holiday", date(2026, 10, 14), date(2026, 10, 15), send_updates="all")
    assert query(http)["sendUpdates"] == ["all"]
    body = sent_body(http)
    assert body["start"] == {"date": "2026-10-14"}
    assert body["end"] == {"date": "2026-10-15"}


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START, date(2026, 10, 14)),
        (END, START),
        (date(2026, 10, 14), date(2026, 10, 14)),
    ],
)
async def test_create_rejects_bad_ranges(start: Any, end: Any) -> None:
    svc, _ = service([])
    with pytest.raises(ValueError):
        await svc.create_event("x", start, end)


async def test_create_retry_that_already_landed_fetches_it() -> None:
    svc, http = service([error(409, "duplicate"), ok(TIMED)])
    event = await svc.create_event("Lunch with Raj", START, END)
    assert event.id == "abc123"
    sent_id = sent_body(http)["id"]
    assert http.request_sequence[1][1] == "GET"
    assert http.request_sequence[1][0].split("?")[0].endswith(f"/events/{sent_id}")


async def test_update_patches_only_given_fields() -> None:
    svc, http = service([ok(TIMED)])
    await svc.update_event("abc123", summary="Late lunch")
    assert http.request_sequence[0][1] == "PATCH"
    assert sent_body(http) == {"summary": "Late lunch"}
    assert query(http)["sendUpdates"] == ["none"]


async def test_update_switching_to_all_day_clears_datetime() -> None:
    svc, http = service([ok(ALL_DAY)])
    await svc.update_event("abc123", start=date(2026, 10, 14), end=date(2026, 10, 15))
    body = sent_body(http)
    assert body["start"] == {"date": "2026-10-14", "dateTime": None}
    assert body["end"] == {"date": "2026-10-15", "dateTime": None}


async def test_update_needs_both_ends_and_something_to_do() -> None:
    svc, _ = service([])
    with pytest.raises(ValueError):
        await svc.update_event("abc123", start=START)
    with pytest.raises(ValueError):
        await svc.update_event("abc123")


async def test_delete_sends_no_emails_and_already_gone_is_fine() -> None:
    svc, http = service([ok(), error(410, "deleted")])
    await svc.delete_event("abc123")
    await svc.delete_event("abc123")
    assert http.request_sequence[0][1] == "DELETE"
    assert query(http)["sendUpdates"] == ["none"]


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (error(404, "notFound"), "calendar_not_found"),
        (error(403, "forbiddenForNonOrganizer"), "calendar_organizer_only"),
        (error(403, "rateLimitExceeded"), "calendar_rate_limited"),
        (error(500, "backendError"), "calendar_down"),
    ],
)
async def test_http_errors_become_friendly(response: Any, kind: str) -> None:
    svc, _ = service([response] * (calendar.RETRIES + 1))
    with pytest.raises(CalendarError) as e:
        await svc.delete_event("abc123")
    assert any(str(e.value).startswith(line) for line in persona.LINES[kind])


async def test_rate_limits_and_server_errors_are_retried() -> None:
    svc, http = service([error(429, "rateLimitExceeded"), error(500, "backendError"), ok()])
    await svc.delete_event("abc123")
    assert len(http.request_sequence) == 3


async def test_invalid_grant_alerts_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    alert = FakeAlert()
    svc, _ = service([], alert)

    def boom() -> Any:
        raise RefreshError("invalid_grant: Token has been expired", {"error": "invalid_grant"})

    monkeypatch.setattr(svc, "_http", boom)
    with pytest.raises(CalendarError) as e:
        await svc.list_events(START, END)
    assert str(e.value) in persona.LINES["calendar_expired"]
    assert alert.count == 1


async def test_list_passes_search_and_reads_recurring_fields() -> None:
    instance = {**TIMED, "id": "abc123_20261013T033000Z", "recurringEventId": "abc123"}
    series = {**TIMED, "recurrence": ["RRULE:FREQ=WEEKLY"]}
    svc, http = service([ok({"items": [instance, series]})])
    one, parent = await svc.list_events(START, END, query="Raj")
    assert query(http)["q"] == ["Raj"]
    assert (one.recurring_event_id, one.series) == ("abc123", False)
    assert (parent.recurring_event_id, parent.series) == (None, True)


async def test_list_without_search_sends_no_q() -> None:
    svc, http = service([ok({"items": []})])
    await svc.list_events(START, END)
    assert "q" not in query(http)


async def test_get_event() -> None:
    svc, http = service([ok(TIMED)])
    event = await svc.get_event("abc123")
    assert event.summary == "Lunch with Raj"
    assert http.request_sequence[0][1] == "GET"
    assert http.request_sequence[0][0].split("?")[0].endswith("/events/abc123")


async def test_cancel_occurrence_patches_status() -> None:
    svc, http = service([ok({**TIMED, "status": "cancelled"})])
    await svc.cancel_occurrence("abc123_20261013T033000Z")
    assert http.request_sequence[0][1] == "PATCH"
    assert sent_body(http) == {"status": "cancelled"}
    assert query(http)["sendUpdates"] == ["none"]
