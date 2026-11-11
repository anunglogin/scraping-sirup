"""Walk the RUP table page by page (cursor) and write the rows to disk.

Output in ``output_dir``:
- ``rup_<year>.jsonl``  one row per line, appended
- ``rup_<year>.csv``    same rows, the fixed columns below
- ``state_<year>.json`` cursor of the next page, so a rerun continues

With a ``store`` the same rows are also upserted into table inaproc_rup, before
the cursor is saved, so a crash never skips rows in the database.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sirup.inaproc.client import InaprocClient
from sirup.inaproc.config import InaprocSettings
from sirup.inaproc.store import InaprocStore

log = logging.getLogger(__name__)

CSV_COLUMNS = (
    "kode_rup",
    "tahun_anggaran",
    "nama_paket",
    "nama_instansi",
    "nama_satker",
    "total_nilai",
    "cara_pengadaan",
    "cara_pengadaan_label",
    "jenis_pengadaan",
    "metode_pengadaan",
    "sumber_dana",
    "produk_dalam_negeri",
    "status_umk",
)


@dataclass
class RunResult:
    pages: int = 0
    rows: int = 0
    db_rows: int = 0
    finished: bool = False
    total_rows: int | None = None


def _safe_csv(value: object) -> object:
    # Stop spreadsheet formula injection (cells opened in Excel).
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _load_cursor(state_path: Path) -> str:
    if not state_path.exists():
        return ""
    data = json.loads(state_path.read_text(encoding="utf-8"))
    return str(data.get("next_cursor") or "")


def _save_cursor(state_path: Path, cursor: str, finished: bool) -> None:
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"next_cursor": cursor, "finished": finished}), encoding="utf-8")
    tmp.replace(state_path)


def run_collect(
    settings: InaprocSettings,
    client: InaprocClient,
    *,
    sleep: Callable[[float], None] = time.sleep,
    store: InaprocStore | None = None,
) -> RunResult:
    year = settings.resolved_year()
    out = settings.output_dir
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / f"rup_{year}.jsonl"
    csv_path = out / f"rup_{year}.csv"
    state_path = out / f"state_{year}.json"

    cursor = _load_cursor(state_path)
    if cursor:
        log.info("resuming %d from saved cursor", year)
    new_csv = not csv_path.exists()
    result = RunResult()

    with (
        jsonl_path.open("a", encoding="utf-8") as jf,
        csv_path.open("a", encoding="utf-8-sig", newline="") as cf,
    ):
        writer = csv.DictWriter(cf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if new_csv:
            writer.writeheader()
        while True:
            page = client.fetch_page(year, settings.limit, cursor)
            for row in page.rows:
                data = row.model_dump()
                jf.write(json.dumps(data, ensure_ascii=False) + "\n")
                writer.writerow({k: _safe_csv(data.get(k)) for k in CSV_COLUMNS})
            jf.flush()
            cf.flush()
            if store is not None:
                result.db_rows += store.upsert(page.rows)
            result.pages += 1
            result.rows += len(page.rows)
            result.total_rows = page.total_rows

            nxt = page.pagination.next_cursor or ""
            done = not page.pagination.has_more or not nxt or not page.rows
            _save_cursor(state_path, "" if done else nxt, done)
            log.info("page %d: %d rows (so far %d)", result.pages, len(page.rows), result.rows)
            if done:
                result.finished = True
                break
            if settings.max_pages and result.pages >= settings.max_pages:
                break
            cursor = nxt
            sleep(settings.delay_seconds)
    return result
