"""INAPROC RUP API collector. Run: python client_inaproc_complete.py --year 2026 --limit 100 --max-pages 2

Dependencies: pip install httpx pydantic tenacity
The x-secret / x-request-time pair is only sent when INAPROC_X_SECRET and
INAPROC_REQUEST_TIME are set in the environment (.env). Never hard-code them here.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

DEFAULT_TABLE_URL = "https://data.inaproc.id/dashboard-api/rup/table"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; INAPROCDataCollector/1.0)"
log = logging.getLogger(__name__)


class InaprocError(Exception):
    pass


class InaprocRefusedError(InaprocError):
    """Access denied, no automatic retry."""


class InaprocUnavailableError(InaprocError):
    """Temporary error, eligible for retry."""


class InaprocFormatError(InaprocError):
    """Unexpected response format."""


class RupRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    kode_rup: str = Field(min_length=1, max_length=40)
    tahun_anggaran: int
    nama_paket: str = ""
    nama_instansi: str = ""
    nama_satker: str = ""
    total_nilai: float | None = None
    cara_pengadaan: str | None = None
    cara_pengadaan_label: str | None = None
    jenis_pengadaan: str | None = None
    metode_pengadaan: str | None = None
    sumber_dana: str | None = None
    produk_dalam_negeri: str | None = None
    status_umk: str | None = None


class Pagination(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    next_cursor: str | None = Field(default=None, alias="nextCursor", max_length=2000)
    has_more: bool = Field(default=False, alias="hasMore")


class TablePage(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    rows: list[RupRow] = Field(alias="tableRows")
    total_rows: int | None = Field(default=None, alias="tableTotalRows", ge=0)
    pagination: Pagination = Field(default_factory=Pagination, alias="tablePagination")


def epoch_millis() -> str:
    return str(int(time.time() * 1000))


class InaprocClient:
    def __init__(
        self,
        *,
        table_url: str = DEFAULT_TABLE_URL,
        cursor_param: str = "cursor",
        time_header: str = "x-request-time",
        secret_header: str = "x-secret",
        x_secret: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 30.0,
        retry_attempts: int = 3,
        backoff_min_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
        clock: Callable[[], str] = epoch_millis,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = table_url
        self._cursor_param = cursor_param
        self._time_header = time_header
        self._secret_header = secret_header
        self._x_secret = x_secret or ""
        self._clock = clock
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Referer": "https://data.inaproc.id/rup",
            },
            follow_redirects=False,
        )
        self._retry_attempts = retry_attempts
        self._backoff_min = backoff_min_seconds
        self._backoff_max = backoff_max_seconds

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> InaprocClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_page(self, year: int, limit: int, cursor: str = "") -> TablePage:
        retrying = Retrying(
            retry=retry_if_exception_type(InaprocUnavailableError),
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(min=self._backoff_min, max=self._backoff_max),
            reraise=True,
        )
        return retrying(self._fetch_once, year, limit, cursor)

    def _fetch_once(self, year: int, limit: int, cursor: str) -> TablePage:
        params = {"tahun": str(year), "limit": str(limit)}
        if cursor:
            params[self._cursor_param] = cursor

        headers = {self._time_header: self._clock()}
        if self._x_secret:
            headers[self._secret_header] = self._x_secret

        try:
            resp = self._http.get(self._url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise InaprocUnavailableError(f"network error: {exc}") from exc

        if resp.status_code in (401, 403):
            raise InaprocRefusedError(f"Access refused (HTTP {resp.status_code}): {resp.text[:120]}")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise InaprocUnavailableError(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise InaprocFormatError(f"Unexpected HTTP {resp.status_code}")
        try:
            return TablePage.model_validate_json(resp.content)
        except ValidationError as exc:
            if not resp.content.strip():
                raise InaprocUnavailableError("empty body") from exc
            raise InaprocFormatError(f"unexpected JSON shape: {exc.error_count()} errors") from exc


def collect(client: InaprocClient, year: int, limit: int, max_pages: int, delay: float) -> list[dict]:
    records: list[dict] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    cursor = ""

    for page_no in range(1, max_pages + 1):
        page = client.fetch_page(year, limit, cursor)
        added = 0
        for row in page.rows:
            if row.kode_rup not in seen_ids:
                seen_ids.add(row.kode_rup)
                records.append(row.model_dump(mode="json"))
                added += 1
        print(f"Page {page_no}: {len(page.rows)} rows; {added} new; total {len(records)}")

        next_cursor = page.pagination.next_cursor
        if not page.pagination.has_more or not next_cursor:
            print("No more pages or no nextCursor supplied.")
            break
        if next_cursor in seen_cursors or next_cursor == cursor:
            print("Repeated cursor detected; stopping.")
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_no < max_pages:
            time.sleep(delay)
    return records


def save(records: list[dict], output: Path, year: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"rup_{year}.json"
    json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {json_path}")
    if not records:
        return
    csv_path = output / f"rup_{year}.csv"
    columns = list(dict.fromkeys(k for row in records for k in row))
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})
    print(f"Saved {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect authorized INAPROC RUP table pages")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=2, help="Max pages; increase after testing")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    if args.limit < 1 or args.max_pages < 1 or args.delay < 0:
        parser.error("limit and max-pages must be positive; delay must be >= 0")

    try:
        secret = os.getenv("INAPROC_X_SECRET", "")
        fixed_time = os.getenv("INAPROC_REQUEST_TIME", "")
        clock = (lambda: fixed_time) if fixed_time else epoch_millis
        with InaprocClient(x_secret=secret, clock=clock) as client:
            records = collect(client, args.year, args.limit, args.max_pages, args.delay)
        save(records, args.output, args.year)
    except InaprocError as exc:
        parser.exit(1, f"INAPROC error: {exc}\n")


if __name__ == "__main__":
    main()
