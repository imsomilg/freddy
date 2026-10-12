import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy.db.models import EventPing, User
from freddy.domain.timeutil import utcnow
from freddy.integrations.google.calendar import CalendarError, CalendarEvent, CalendarService

log = structlog.get_logger()

WINDOW = timedelta(hours=24)
KEEP_SENT = timedelta(days=1)


def pingable(event: CalendarEvent, now: datetime) -> bool:
    return isinstance(event.start, datetime) and not event.declined and event.start > now


class CalendarSync:
    """Mirrors the next 24h of events into event_pings, so the scheduler can ping before each one.

    Google leaves cancelled events out of events.list, so an unsent ping whose (event_id, start)
    is missing from the latest pull was moved or cancelled and gets dropped.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        calendar: CalendarService,
        owner_discord_id: int,
        lead_minutes: int = 10,
        interval: float = 300,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.calendar = calendar
        self.owner_discord_id = owner_discord_id
        self.lead = timedelta(minutes=lead_minutes)
        self.interval = interval

    async def run(self) -> None:
        while True:
            try:
                await self.sync()
            except Exception:
                log.exception("calendar_sync.failed")
            await asyncio.sleep(self.interval)

    async def sync(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        try:
            events = await self.calendar.list_events(now, now + WINDOW)
        except CalendarError as e:
            log.warning("calendar_sync.skipped", error=str(e))
            return
        wanted: dict[tuple[str, datetime], CalendarEvent] = {}
        for event in events:
            if pingable(event, now):
                assert isinstance(event.start, datetime)
                wanted[(event.id, event.start.astimezone(UTC))] = event

        async with self.sessionmaker() as session, session.begin():
            user_id = await session.scalar(
                select(User.id).where(User.discord_id == self.owner_discord_id)
            )
            if user_id is None:
                return
            if wanted:
                stmt = insert(EventPing).values(
                    [
                        {
                            "user_id": user_id,
                            "event_id": event_id,
                            "start_at": start,
                            "summary": e.summary,
                            "location": e.location,
                            "ping_at": start - self.lead,
                        }
                        for (event_id, start), e in wanted.items()
                    ]
                )
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["event_id", "start_at"],
                        set_={
                            "summary": stmt.excluded.summary,
                            "location": stmt.excluded.location,
                            "ping_at": stmt.excluded.ping_at,
                        },
                    )
                )
            pending = await session.execute(
                select(EventPing.id, EventPing.event_id, EventPing.start_at).where(
                    EventPing.sent_at.is_(None),
                    EventPing.start_at > now,
                    EventPing.start_at < now + WINDOW,
                )
            )
            stale = [
                ping_id
                for ping_id, event_id, start in pending
                if (event_id, start.astimezone(UTC)) not in wanted
            ]
            if stale:
                await session.execute(delete(EventPing).where(EventPing.id.in_(stale)))
            await session.execute(delete(EventPing).where(EventPing.start_at < now - KEEP_SENT))
        log.info("calendar_sync.done", upcoming=len(wanted), dropped=len(stale))
