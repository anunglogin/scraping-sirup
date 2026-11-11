"""HTTP client for the SIRUP DataTables search endpoint."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sirup.scraper.config import DEFAULT_BASE_URL, DEFAULT_USER_AGENT

log = logging.getLogger(__name__)


class SirupError(Exception):
    """Base class for errors talking to SIRUP."""


class SirupUnavailableError(SirupError):
    """Temporary problem (network, 429, 5xx, empty or broken body). Retried."""


class SirupRejectedError(SirupError):
    """SIRUP refused the request (4xx other than 429). Not retried."""


class SirupFormatError(SirupError):
    """The response JSON does not have the expected shape. Not retried."""


class Page(BaseModel):
    """A DataTables response page."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    records_total: int = Field(alias="recordsTotal", ge=0)
    records_filtered: int | None = Field(default=None, alias="recordsFiltered")
    data: list[Any]


def check_not_truncated(page: Page, start: int) -> None:
    """An empty page before recordsTotal is reached means SIRUP cut the data
    short; treating it as "done" would end the run early and hide live
    packages (review F-02). Raise the retryable error instead."""
    if not page.data and start < page.records_total:
        raise SirupUnavailableError(
            f"empty page at start={start} although recordsTotal={page.records_total}"
        )


def build_params(year: int, start: int, length: int) -> dict[str, str]:
    """Query parameters. The ``order`` triple is mandatory: without it SIRUP
    answers HTTP 200 with an empty body."""
    return {
        "tahunAnggaran": str(year),
        "jenisPengadaan": "",
        "draw": "1",
        "columns[11][data]": "id",
        "order[0][column]": "11",
        "order[0][dir]": "ASC",
        "start": str(start),
        "length": str(length),
        "search[value]": "",
    }


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    sleep = state.next_action.sleep if state.next_action else 0
    log.warning(
        "sirup request failed (attempt %d), retrying in %.1fs: %s",
        state.attempt_number,
        sleep,
        exc,
    )


class SirupClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 60.0,
        retry_attempts: int = 5,
        backoff_min_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
        http: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=False,
        )
        self._retry_attempts = retry_attempts
        self._backoff_min = backoff_min_seconds
        self._backoff_max = backoff_max_seconds

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> SirupClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_page(self, year: int, start: int, length: int) -> Page:
        retrying = Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=1, min=self._backoff_min, max=self._backoff_max),
            retry=retry_if_exception_type(SirupUnavailableError),
            before_sleep=_log_retry,
            reraise=True,
        )
        return retrying(self._fetch_once, year, start, length)

    def _fetch_once(self, year: int, start: int, length: int) -> Page:
        try:
            resp = self._http.get(self._base_url, params=build_params(year, start, length))
        except httpx.TransportError as exc:
            raise SirupUnavailableError(f"network error: {exc!r}") from exc

        status = resp.status_code
        if status == 429 or status >= 500:
            raise SirupUnavailableError(f"HTTP {status}")
        if status >= 300:
            raise SirupRejectedError(f"HTTP {status} (not retried)")
        if not resp.content.strip():
            raise SirupUnavailableError("empty response body")
        try:
            payload = json.loads(resp.content)
        except ValueError as exc:
            raise SirupUnavailableError("response is not valid JSON") from exc
        try:
            page = Page.model_validate(payload)
        except ValidationError as exc:
            raise SirupFormatError(
                f"unexpected response shape (SIRUP format changed?): {exc.error_count()} errors"
            ) from exc
        check_not_truncated(page, start)
        return page
