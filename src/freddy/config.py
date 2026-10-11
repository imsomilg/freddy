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

    anthropic_api_key: SecretStr
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str = "low"
    claude_max_tool_rounds: int = 8
    claude_max_tokens: int = 4096

    database_url: str

    timezone: str = "UTC"
    morning_checkin: time = time(8, 0)
    evening_checkin: time = time(21, 0)

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
