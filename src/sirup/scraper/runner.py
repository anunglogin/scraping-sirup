"""One scrape run: page through SIRUP and store everything."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sirup.scraper.client import Page, SirupClient
from sirup.scraper.config import ScraperSettings
from sirup.scraper.models import validate_page
from sirup.scraper.store import Store

log = logging.getLogger(__name__)

MAX_INVALID_SHARE = 0.5


class PageSource(Protocol):
    def fetch_page(self, year: int, start: int, length: int) -> Page: ...


class TooManyInvalidRowsError(RuntimeError):
    """More than half of a page failed validation: SIRUP format probably changed."""


@dataclass(frozen=True)
class RunResult:
    run_id: int
    status: str
    pages: int
    rows_deactivated: int
    note: str | None


def make_client(settings: ScraperSettings) -> SirupClient:
    return SirupClient(
        base_url=settings.sirup_base_url,
        user_agent=settings.http_user_agent,
        timeout_seconds=settings.http_timeout_seconds,
        retry_attempts=settings.http_retry_attempts,
        backoff_min_seconds=settings.http_backoff_min_seconds,
        backoff_max_seconds=settings.http_backoff_max_seconds,
    )


def run_scrape(
    settings: ScraperSettings,
    source: PageSource,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> RunResult:
    """Run (or resume) the scrape for the configured budget year.

    Raises on failure after recording it in ``scrape_runs``.
    """
    year = settings.resolved_year()
    size = settings.scrape_page_size
    with Store.connect(settings.database_url) as store:
        run = store.open_run(year)
        start = run.checkpoint_start
        pages = 0
        complete = True
        try:
            while True:
                page = source.fetch_page(year, start, size)
                checked = validate_page(page.data)
                valid, invalid = checked.valid, checked.invalid
                if invalid:
                    log.warning(
                        "run %s: start=%s %s of %s rows invalid: %s",
                        run.id,
                        start,
                        invalid,
                        len(page.data),
                        checked.describe(),
                    )
                if page.data and invalid > MAX_INVALID_SHARE * len(page.data):
                    raise TooManyInvalidRowsError(
                        f"{invalid} of {len(page.data)} rows invalid at start={start}; "
                        f"SIRUP format probably changed; {checked.describe()}"
                    )
                next_start = start + len(page.data)
                result = store.save_page(
                    run,
                    valid,
                    invalid=invalid,
                    next_start=next_start,
                    records_total=page.records_total,
                )
                pages += 1
                log.info(
                    "run %s: start=%s got=%s invalid=%s inserted=%s changed=%s total=%s",
                    run.id,
                    start,
                    len(page.data),
                    invalid,
                    result.inserted,
                    result.changed,
                    page.records_total,
                )
                if not page.data or next_start >= page.records_total:
                    break
                if settings.scrape_max_pages is not None and pages >= settings.scrape_max_pages:
                    complete = False
                    log.info("run %s: SCRAPE_MAX_PAGES=%s reached", run.id, pages)
                    break
                start = next_start
                if settings.scrape_delay_seconds:
                    sleep(settings.scrape_delay_seconds)
            deactivated, note = store.finish_run(run, complete=complete)
        except Exception as exc:
            log.exception("run %s failed", run.id)
            try:
                store.fail_run(run, f"{type(exc).__name__}: {exc}")
            except Exception:
                log.exception("could not record failure of run %s", run.id)
            raise
    log.info("run %s finished: pages=%s deactivated=%s", run.id, pages, deactivated)
    return RunResult(
        run_id=run.id, status="success", pages=pages, rows_deactivated=deactivated, note=note
    )
