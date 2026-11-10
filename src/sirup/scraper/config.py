"""Scraper configuration, read from environment variables."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sirup.timeutil import current_year, parse_hhmm

DEFAULT_BASE_URL = "https://sirup.inaproc.id/sirup/caripaketctr/search"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; sirup-collector/0.1)"


class ScraperSettings(BaseSettings):
    # Blank strings in the environment count as "not set" so that e.g.
    # ``TAHUN_ANGGARAN=`` falls back to the default (None = current year).
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    database_url: str = Field(description="postgresql:// URL with write access")
    tahun_anggaran: int | None = Field(default=None, ge=2000, le=2100)
    scrape_page_size: int = Field(default=5000, ge=1, le=10000)
    scrape_delay_seconds: float = Field(default=2.0, ge=0, le=600)
    scrape_time: str = Field(default="02:00", description="Daily start time, HH:MM in WIB")
    scrape_max_pages: int | None = Field(default=None, ge=1)

    sirup_base_url: str = DEFAULT_BASE_URL
    http_user_agent: str = DEFAULT_USER_AGENT
    http_timeout_seconds: float = Field(default=60.0, gt=0)
    http_retry_attempts: int = Field(default=5, ge=1, le=20)
    http_backoff_min_seconds: float = Field(default=2.0, ge=0)
    http_backoff_max_seconds: float = Field(default=60.0, ge=0)

    # Schedule mode: after a failed run, try again (resuming from the checkpoint)
    # this many times, waiting this long in between.
    schedule_retry_count: int = Field(default=3, ge=0, le=10)
    schedule_retry_wait_minutes: float = Field(default=30.0, ge=0)

    @field_validator("scrape_time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        parse_hhmm(value)
        return value.strip()

    def resolved_year(self) -> int:
        return self.tahun_anggaran or current_year()
