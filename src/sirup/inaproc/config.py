"""Settings for the data.inaproc.id collector, read from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from sirup.timeutil import current_year

DEFAULT_TABLE_URL = "https://data.inaproc.id/dashboard-api/rup/table"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; sirup-collector/0.1)"


class InaprocSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INAPROC_", env_ignore_empty=True, extra="ignore", hide_input_in_errors=True
    )

    tahun: int | None = Field(default=None, ge=2000, le=2100)
    limit: int = Field(default=100, ge=1, le=100)
    # 0 = keep going until the API says hasMore=false.
    max_pages: int = Field(default=10, ge=0)
    delay_seconds: float = Field(default=2.0, ge=0.5, le=600)
    output_dir: Path = Path("output/inaproc")

    table_url: str = DEFAULT_TABLE_URL
    cursor_param: str = Field(default="cursor", min_length=1, max_length=40)
    time_header: str = Field(default="x-request-time", min_length=1, max_length=40)
    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = Field(default=30.0, gt=0)
    retry_attempts: int = Field(default=3, ge=1, le=10)
    # Write access to PostgreSQL; blank = write files only (no database).
    database_url: SecretStr | None = None
    # Access pair granted in writing by INAPROC/LKPP (see TD-08). Keep in .env only.
    x_secret: SecretStr | None = None
    request_time: str | None = Field(default=None, pattern=r"^\d{10,16}$")

    def resolved_year(self) -> int:
        return self.tahun or current_year()
