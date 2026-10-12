from typing import Any

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from freddy import persona
from freddy.db.engine import make_sessionmaker
from freddy.db.models import AppState
from freddy.integrations.google import auth
from freddy.integrations.google.auth import EXPIRED_KEY, AuthAlert, check_calendar
from tests.integration.test_boot import FakeChannel

CREDS = Credentials(token=None, refresh_token="r", client_id="c", client_secret="s")
EXPIRED = RefreshError(
    "invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"}
)


async def stored(engine: AsyncEngine) -> str | None:
    async with make_sessionmaker(engine)() as s:
        return await s.scalar(select(AppState.value).where(AppState.key == EXPIRED_KEY))


@pytest.fixture
async def alert(engine: AsyncEngine) -> tuple[AuthAlert, FakeChannel]:
    maker = make_sessionmaker(engine)
    async with maker() as s, s.begin():
        await s.execute(delete(AppState).where(AppState.key == EXPIRED_KEY))
    channel = FakeChannel()
    return AuthAlert(maker, channel), channel


def fake_calendar(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    def calendar_timezone(creds: Credentials, calendar_id: str) -> str:
        if isinstance(result, Exception):
            raise result
        return str(result)

    monkeypatch.setattr(auth, "_calendar_timezone", calendar_timezone)


async def test_invalid_grant_posts_once_until_it_works_again(
    engine: AsyncEngine, alert: tuple[AuthAlert, FakeChannel], monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_, channel = alert
    fake_calendar(monkeypatch, EXPIRED)
    await check_calendar(CREDS, "primary", "Asia/Kolkata", alert_)
    await check_calendar(CREDS, "primary", "Asia/Kolkata", alert_)
    assert len(channel.sent) == 1
    assert channel.sent[0] in persona.LINES["calendar_expired"]
    assert await stored(engine) == "1"

    fake_calendar(monkeypatch, "Asia/Kolkata")
    await check_calendar(CREDS, "primary", "Asia/Kolkata", alert_)
    assert await stored(engine) is None

    fake_calendar(monkeypatch, EXPIRED)
    await check_calendar(CREDS, "primary", "Asia/Kolkata", alert_)
    assert len(channel.sent) == 2


async def test_other_failures_stay_quiet(
    engine: AsyncEngine, alert: tuple[AuthAlert, FakeChannel], monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_, channel = alert
    for error in (RefreshError("invalid_client", {"error": "invalid_client"}), OSError("dns")):
        fake_calendar(monkeypatch, error)
        await check_calendar(CREDS, "primary", "UTC", alert_)
    assert channel.sent == []
    assert await stored(engine) is None
