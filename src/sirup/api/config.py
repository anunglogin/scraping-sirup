"""API configuration, read from environment variables."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_KEY_LENGTH = 24


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    database_url: str = Field(description="postgresql:// URL of the READ-ONLY role")
    # Comma-separated. Kept as a plain string so pydantic-settings does not try
    # to JSON-decode it.
    api_keys: str
    db_pool_min_size: int = Field(default=1, ge=0, le=50)
    db_pool_max_size: int = Field(default=10, ge=1, le=100)
    db_statement_timeout_ms: int = Field(default=10_000, ge=100, le=600_000)

    @field_validator("api_keys")
    @classmethod
    def _check_keys(cls, value: str) -> str:
        keys = [k.strip() for k in value.split(",") if k.strip()]
        if not keys:
            raise ValueError("API_KEYS must contain at least one key")
        short = [k for k in keys if len(k) < MIN_KEY_LENGTH]
        if short:
            raise ValueError(
                f"every API key must be at least {MIN_KEY_LENGTH} characters "
                f"({len(short)} key(s) too short)"
            )
        return ",".join(keys)

    def key_list(self) -> list[str]:
        return self.api_keys.split(",")
