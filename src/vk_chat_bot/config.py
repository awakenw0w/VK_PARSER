from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str = Field(min_length=20)
    vk_user_access_token: str = Field(min_length=8)
    vk_api_version: str = "5.199"
    admin_telegram_ids: frozenset[int] = frozenset()
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    vk_requests_per_second: float = Field(default=3.0, gt=0, le=20)
    log_level: str = "INFO"

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> frozenset[int]:
        if value in (None, ""):
            return frozenset()
        if isinstance(value, int):
            return frozenset({value})
        if isinstance(value, str):
            return frozenset(int(part.strip()) for part in value.split(",") if part.strip())
        return frozenset(int(item) for item in value)  # type: ignore[arg-type]

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Unsupported LOG_LEVEL")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
