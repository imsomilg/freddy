from typing import Any

import pytest
from google.auth.exceptions import RefreshError

from freddy.config import Settings
from freddy.integrations.google.auth import SCOPES, credentials_from_settings, is_invalid_grant


def settings(**google: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        discord_token="x",
        discord_guild_id=1,
        discord_channel_id=2,
        owner_discord_id=3,
        database_url="postgresql+asyncpg://u:p@h/d",
        **google,
    )


FULL = {
    "google_client_id": "id.apps.googleusercontent.com",
    "google_client_secret": "secret",
    "google_refresh_token": "refresh",
}


@pytest.mark.parametrize("blank", list(FULL))
def test_calendar_off_unless_every_var_is_set(blank: str) -> None:
    assert credentials_from_settings(settings()) is None
    assert credentials_from_settings(settings(**{**FULL, blank: ""})) is None


def test_credentials_are_refreshable() -> None:
    creds = credentials_from_settings(settings(**FULL))
    assert creds is not None
    assert creds.refresh_token == "refresh"
    assert creds.client_id == FULL["google_client_id"]
    assert creds.client_secret == "secret"
    assert creds.token_uri == "https://oauth2.googleapis.com/token"
    assert creds.scopes == list(SCOPES)
    assert settings(**FULL).google_calendar_id == "primary"


def test_invalid_grant_detection() -> None:
    data = {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
    assert is_invalid_grant(RefreshError("invalid_grant: Token has been expired", data))
    assert not is_invalid_grant(RefreshError("invalid_client: nope", {"error": "invalid_client"}))
    assert not is_invalid_grant(RefreshError("some string"))
