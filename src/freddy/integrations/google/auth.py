import asyncio
from typing import Any

import structlog
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy import persona
from freddy.channels.base import Channel
from freddy.config import Settings
from freddy.db.models import AppState

log = structlog.get_logger()

SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
)
EXPIRED_KEY = "google_auth_expired"


def credentials_from_settings(settings: Settings) -> Credentials | None:
    """Builds refreshable user credentials, or None if the Google env vars aren't all set."""
    secret = settings.google_client_secret
    refresh = settings.google_refresh_token
    if not (
        settings.google_client_id
        and secret
        and secret.get_secret_value()
        and refresh
        and refresh.get_secret_value()
    ):
        return None
    creds: Credentials = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
        {
            "client_id": settings.google_client_id,
            "client_secret": secret.get_secret_value(),
            "refresh_token": refresh.get_secret_value(),
        },
        scopes=list(SCOPES),
    )
    return creds


def is_invalid_grant(exc: RefreshError) -> bool:
    return any(isinstance(a, dict) and a.get("error") == "invalid_grant" for a in exc.args)


class AuthAlert:
    """Tells me once in the channel when the refresh token dies, and resets when it works again."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], channel: Channel) -> None:
        self._sessionmaker = sessionmaker
        self._channel = channel
        self._lock = asyncio.Lock()

    async def expired(self) -> None:
        async with self._lock, self._sessionmaker() as session, session.begin():
            if await session.scalar(select(AppState.key).where(AppState.key == EXPIRED_KEY)):
                return
            await self._channel.notify(persona.line("calendar_expired"))
            session.add(AppState(key=EXPIRED_KEY, value="1"))
        log.warning("google.auth_expired")

    async def restored(self) -> None:
        async with self._lock, self._sessionmaker() as session, session.begin():
            result = await session.execute(delete(AppState).where(AppState.key == EXPIRED_KEY))
        if getattr(result, "rowcount", 0):
            log.info("google.auth_restored")


def _calendar_timezone(creds: Credentials, calendar_id: str) -> str:
    service: Any = build("calendar", "v3", credentials=creds, cache_discovery=False)
    resp = service.events().list(calendarId=calendar_id, maxResults=1).execute()
    return str(resp["timeZone"])


async def check_calendar(
    creds: Credentials, calendar_id: str, timezone: str, alert: AuthAlert
) -> None:
    try:
        cal_tz = await asyncio.to_thread(_calendar_timezone, creds, calendar_id)
    except RefreshError as e:
        if is_invalid_grant(e):
            await alert.expired()
        else:
            log.warning("google.refresh_failed", error=str(e))
        return
    except HttpError as e:
        log.warning("google.calendar_failed", status=e.status_code, error=str(e))
        return
    except Exception:
        log.exception("google.check_failed")
        return
    await alert.restored()
    log.info("google.calendar_ready", calendar_id=calendar_id, timezone=cal_tz)
    if cal_tz != timezone:
        log.warning("google.timezone_mismatch", calendar=cal_tz, freddy=timezone)
