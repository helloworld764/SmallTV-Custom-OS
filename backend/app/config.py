"""Validated environment configuration, without global cached state."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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

    @field_validator("db_path", mode="before")
    @classmethod
    def validate_db_path(cls, value):
        if not str(value).strip() or str(value) == ":memory:":
            raise ValueError("db_path must be a persistent filesystem path")
        return value
