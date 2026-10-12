from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from freddy.assistant.service import UserLocks
from freddy.db.engine import make_sessionmaker
from freddy.db.models import EventPing, Message, User
from freddy.integrations.google.calendar import CalendarEvent
from freddy.scheduler.calendar_sync import CalendarSync
from freddy.scheduler.loop import Scheduler
from tests.fakes import FakeCalendar
from tests.integration.test_boot import FakeChannel

NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)


def event(event_id: str, start: datetime, **kw: object) -> CalendarEvent:
    fields: dict[str, object] = {
        "id": event_id,
        "summary": f"Event {event_id}",
        "start": start,
        "end": start + timedelta(hours=1),
        "all_day": False,
    }
    fields.update(kw)
    return CalendarEvent(**fields)  # type: ignore[arg-type]


@pytest.fixture
async def maker(
    engine: AsyncEngine, committed_owner: User
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    m = make_sessionmaker(engine)
    async with m() as s, s.begin():
        await s.execute(delete(EventPing))
    yield m
    async with m() as s, s.begin():
        await s.execute(delete(EventPing))


async def pings(maker: async_sessionmaker[AsyncSession]) -> list[EventPing]:
    async with maker() as s:
        return list(await s.scalars(select(EventPing).order_by(EventPing.start_at)))


def sync_for(
    maker: async_sessionmaker[AsyncSession], cal: FakeCalendar, lead: int = 10
) -> CalendarSync:
    return CalendarSync(maker, cal, 9001, lead)  # type: ignore[arg-type]


async def test_sync_adds_moves_and_drops(maker: async_sessionmaker[AsyncSession]) -> None:
    start = NOW + timedelta(minutes=30)
    cal = FakeCalendar(
        [
            event("a", start, location="Cafe"),
            event("b", NOW + timedelta(hours=2)),
            event("day", NOW.date(), end=NOW.date() + timedelta(days=1), all_day=True),
            event("nope", start, declined=True),
            event("started", NOW - timedelta(minutes=5)),
        ]
    )
    sync = sync_for(maker, cal)
    await sync.sync(NOW)

    rows = await pings(maker)
    assert [(p.event_id, p.ping_at) for p in rows] == [
        ("a", start - timedelta(minutes=10)),
        ("b", NOW + timedelta(hours=2) - timedelta(minutes=10)),
    ]
    assert rows[0].location == "Cafe"

    moved = start + timedelta(hours=1)
    cal.events["a"] = event("a", moved, summary="Renamed")
    del cal.events["b"]
    await sync.sync(NOW + timedelta(minutes=5))

    rows = await pings(maker)
    assert [(p.event_id, p.start_at, p.summary) for p in rows] == [("a", moved, "Renamed")]


async def test_sync_failure_keeps_pings(maker: async_sessionmaker[AsyncSession]) -> None:
    cal = FakeCalendar([event("a", NOW + timedelta(minutes=30))])
    await sync_for(maker, cal).sync(NOW)
    cal.fail = "down"
    await sync_for(maker, cal).sync(NOW)
    assert len(await pings(maker)) == 1


async def test_sync_follows_new_lead(maker: async_sessionmaker[AsyncSession]) -> None:
    start = NOW + timedelta(minutes=30)
    cal = FakeCalendar([event("a", start)])
    await sync_for(maker, cal, lead=10).sync(NOW)
    await sync_for(maker, cal, lead=15).sync(NOW)
    assert [p.ping_at for p in await pings(maker)] == [start - timedelta(minutes=15)]


async def test_scheduler_sends_once_and_skips_started(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    cal = FakeCalendar(
        [event("soon", now + timedelta(minutes=5)), event("later", now + timedelta(hours=3))]
    )
    await sync_for(maker, cal).sync(now)
    channel = FakeChannel()
    scheduler = Scheduler(maker, channel, UserLocks())

    await scheduler.tick()
    await scheduler.tick()

    assert len(channel.sent) == 1
    assert "Event soon at" in channel.sent[0]
    rows = {p.event_id: p for p in await pings(maker)}
    assert rows["soon"].sent_at is not None
    assert rows["later"].sent_at is None

    async with maker() as s:
        logged = list(await s.scalars(select(Message.content).where(Message.role == "user")))
    assert "(scheduled: event ping)" in logged

    async with maker() as s, s.begin():
        row = await s.get(EventPing, rows["later"].id)
        assert row is not None
        row.start_at = now - timedelta(minutes=1)
        row.ping_at = now - timedelta(minutes=11)
    await scheduler.tick()

    assert len(channel.sent) == 1
    assert [p.event_id for p in await pings(maker)] == ["soon"]
