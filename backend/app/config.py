"""Validated environment configuration, without global cached state."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMALLTV_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"
    db_path: Path = Path("data/smalltv.sqlite3")
    cookie_secure: bool = False  # Local HTTP only; enable for future HTTPS hosting.
    session_ttl_seconds: int = Field(default=43200, ge=300, le=86400)
    agent_stale_seconds: int = Field(default=30, ge=10, le=3600)
    agent_offline_seconds: int = Field(default=90, ge=15, le=86400)
    device_stale_seconds: int = Field(default=30, ge=10, le=3600)
    device_offline_seconds: int = Field(default=90, ge=15, le=86400)
    weather_cache_seconds: int = Field(default=900, ge=300, le=1800)
    weather_timeout_seconds: float = Field(default=4.0, gt=0, le=15)
    telegram_bot_token: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_agent_timeouts(self):
        if self.agent_offline_seconds <= self.agent_stale_seconds:
            raise ValueError("agent_offline_seconds must exceed agent_stale_seconds")
        if self.device_offline_seconds <= self.device_stale_seconds:
            raise ValueError("device_offline_seconds must exceed device_stale_seconds")
        return self

    @field_validator("db_path", mode="before")
    @classmethod
    def validate_db_path(cls, value):
        if not str(value).strip() or str(value) == ":memory:":
            raise ValueError("db_path must be a persistent filesystem path")
        return value
