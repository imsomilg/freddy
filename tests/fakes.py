from dataclasses import replace
from datetime import date, datetime
from typing import Any

from freddy.integrations.google.calendar import CalendarError, CalendarEvent, _check_range


class FakeCalendar:
    """Stands in for CalendarService and records every call."""

    def __init__(self, events: list[CalendarEvent] | None = None, fail: str | None = None) -> None:
        self.events = {e.id: e for e in events or []}
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    def _check(self) -> None:
        if self.fail:
            raise CalendarError(self.fail)

    async def list_events(
        self, start: datetime, end: datetime, query: str | None = None
    ) -> list[CalendarEvent]:
        self.calls.append(("list", (start, end, query)))
        self._check()
        return list(self.events.values())

    async def busy(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime | date, datetime | date]]:
        self.calls.append(("busy", (start, end)))
        self._check()
        return [(e.start, e.end) for e in self.events.values()]

    async def get_event(self, event_id: str) -> CalendarEvent:
        self.calls.append(("get", event_id))
        self._check()
        if event_id not in self.events:
            raise CalendarError("I couldn't find that event, sir.")
        return self.events[event_id]

    async def create_event(self, summary: str, start: Any, end: Any, **kw: Any) -> CalendarEvent:
        self.calls.append(("create", (summary, start, end, kw)))
        self._check()
        _check_range(start, end)
        event = CalendarEvent(
            id="new1",
            summary=summary,
            start=start,
            end=end,
            all_day=not isinstance(start, datetime),
            location=kw.get("location"),
            attendees=tuple(kw.get("attendees") or ()),
        )
        self.events[event.id] = event
        return event

    async def update_event(self, event_id: str, **kw: Any) -> CalendarEvent:
        self.calls.append(("update", (event_id, kw)))
        self._check()
        changes = {k: v for k, v in kw.items() if k in ("summary", "start", "end") and v}
        event = replace(self.events[event_id], **changes)
        self.events[event_id] = event
        return event

    async def delete_event(self, event_id: str, **kw: Any) -> None:
        self.calls.append(("delete", (event_id, kw)))
        self._check()
        self.events.pop(event_id, None)

    async def cancel_occurrence(self, event_id: str, **kw: Any) -> None:
        self.calls.append(("cancel", (event_id, kw)))
        self._check()
        self.events.pop(event_id, None)
