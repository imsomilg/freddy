from datetime import time
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: SecretStr
    discord_guild_id: int
    discord_channel_id: int
    owner_discord_id: int

    anthropic_api_key: SecretStr | None = None
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str = "low"
    claude_max_tool_rounds: int = 8
    claude_max_tokens: int = 4096

    bridge_token: SecretStr | None = None
    mcp_token: SecretStr | None = None
    bridge_timeout: float = 150

    database_url: str

    timezone: str = "UTC"
    morning_checkin: time = time(8, 0)
    evening_checkin: time = time(21, 0)
    weekly_summary_delay: int = 30

    source_commit: str | None = None
    github_repo: str | None = None
    github_token: SecretStr | None = None

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_refresh_token: SecretStr | None = None
    google_calendar_id: str = "primary"
    event_ping_minutes: int = 10

    log_level: str = "INFO"
    log_json: bool = False
    health_port: int = 8080

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, v: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v.removeprefix(prefix)
        return v

    @field_validator("source_commit", "github_repo", "google_client_id")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        ZoneInfo(v)
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
