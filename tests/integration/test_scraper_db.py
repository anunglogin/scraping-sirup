"""Scraper + store against a real PostgreSQL (TEST_DATABASE_URL)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from sirup.scraper.client import Page, SirupUnavailableError
from sirup.scraper.config import ScraperSettings
from sirup.scraper.runner import TooManyInvalidRowsError, run_scrape
from sirup.scraper.store import ScrapeAlreadyRunningError, Store
from tests.conftest import REAL_ROW_WITHOUT_JENIS, make_row

YEAR = 2026


class FakeSource:
    """Pages through an in-memory list exactly like SIRUP does."""

    def __init__(
        self,
        rows: list[Any],
        *,
        total: int | None = None,
        fail_on_call: int | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.fail_on_call = fail_on_call
        self.exc = exc or SirupUnavailableError("simulated outage")
        self.starts: list[int] = []

    def fetch_page(self, year: int, start: int, length: int) -> Page:
        assert year == YEAR
        self.starts.append(start)
        if self.fail_on_call is not None and len(self.starts) == self.fail_on_call:
            raise self.exc
        return Page(recordsTotal=self.total, data=self.rows[start : start + length])


def settings(db_url: str, **kw: Any) -> ScraperSettings:
    base = {
        "database_url": db_url,
        "tahun_anggaran": YEAR,
        "scrape_page_size": 10,
        "scrape_delay_seconds": 0,
        "scrape_max_pages": None,
    }
    base.update(kw)
    return ScraperSettings(**base)


def rows(n: int) -> list[dict[str, Any]]:
    return [make_row(i) for i in range(n)]


def runs(db: psycopg.Connection) -> list[dict[str, Any]]:
    with db.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM scrape_runs ORDER BY id")
        return cur.fetchall()


def count(db: psycopg.Connection, where: str = "true") -> int:
    return db.execute(f"SELECT count(*) FROM paket WHERE {where}").fetchone()[0]  # noqa: S608


def test_full_scrape_stores_everything_and_logs_run(db, db_url):  # AC-01, AC-11
    source = FakeSource(rows(25))
    result = run_scrape(settings(db_url), source, sleep=lambda _: None)
    assert source.starts == [0, 10, 20]
    assert result.status == "success" and result.pages == 3
    assert count(db) == 25
    [run] = runs(db)
    assert run["status"] == "success"
    assert run["finished_at"] is not None and run["finished_at"] >= run["started_at"]
    assert run["records_total"] == 25
    assert run["rows_received"] == 25
    assert run["rows_inserted"] == 25
    assert run["rows_updated"] == 0
    assert run["rows_invalid"] == 0
    assert run["checkpoint_start"] == 25
    assert run["error"] is None and run["note"] is None
    row = db.execute(
        "SELECT p.tahun_anggaran, p.nama_paket, r.raw->>'paket', p.is_active "
        "FROM paket p JOIN paket_raw r USING (id) WHERE id = 1003"
    ).fetchone()
    assert row == (YEAR, "Pengadaan barang nomor 3", "Pengadaan barang nomor 3", True)


def test_delay_between_pages(db, db_url):  # AC-05
    sleeps: list[float] = []
    run_scrape(settings(db_url, scrape_delay_seconds=2), FakeSource(rows(25)), sleep=sleeps.append)
    assert sleeps == [2, 2]


def test_rerun_creates_no_duplicates(db, db_url):  # AC-02
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))
    run_scrape(settings(db_url), FakeSource(data))
    assert count(db) == 25
    assert db.execute("SELECT count(DISTINCT id) FROM paket").fetchone()[0] == 25
    first, second = runs(db)
    assert second["id"] != first["id"]
    assert (second["rows_inserted"], second["rows_updated"]) == (0, 0)


def test_duplicate_ids_within_a_page_are_collapsed(db, db_url):
    data = rows(5)
    data.append(dict(data[0]))
    run_scrape(settings(db_url), FakeSource(data))
    assert count(db) == 5


def test_changed_pagu_updates_updated_at_only_for_that_row(db, db_url):
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))
    before = dict(db.execute("SELECT id, updated_at FROM paket").fetchall())
    first_seen = db.execute("SELECT first_seen_at FROM paket WHERE id = 1005").fetchone()[0]
    data[5] = {**data[5], "pagu": 123}
    run_scrape(settings(db_url), FakeSource(data))
    after = dict(db.execute("SELECT id, updated_at FROM paket").fetchall())
    changed = [k for k in after if after[k] != before[k]]
    assert changed == [1005]
    assert db.execute(
        "SELECT p.pagu, r.raw->>'pagu', p.first_seen_at "
        "FROM paket p JOIN paket_raw r USING (id) WHERE id = 1005"
    ).fetchone() == (123, "123", first_seen)
    assert runs(db)[-1]["rows_updated"] == 1


def test_failure_mid_run_then_resume_from_checkpoint(db, db_url):  # AC-04
    data = rows(25)
    with pytest.raises(SirupUnavailableError):
        run_scrape(settings(db_url), FakeSource(data, fail_on_call=3))
    [failed] = runs(db)
    assert failed["status"] == "failed"
    assert failed["checkpoint_start"] == 20
    assert "SirupUnavailableError" in failed["error"]
    assert count(db) == 20

    source = FakeSource(data)
    run_scrape(settings(db_url), source)
    assert source.starts == [20]  # did not start from zero
    [resumed] = runs(db)
    assert resumed["id"] == failed["id"]
    assert resumed["started_at"] == failed["started_at"]
    assert resumed["status"] == "success" and resumed["error"] is None
    assert resumed["rows_received"] == 25 and resumed["rows_inserted"] == 25
    assert count(db) == 25


def test_killed_process_leaves_resumable_run(db, db_url):  # AC-04 (kill / SIGTERM)
    data = rows(25)
    with pytest.raises(KeyboardInterrupt):
        run_scrape(settings(db_url), FakeSource(data, fail_on_call=2, exc=KeyboardInterrupt()))
    [stuck] = runs(db)
    assert stuck["status"] == "running" and stuck["checkpoint_start"] == 10
    source = FakeSource(data)
    run_scrape(settings(db_url), source)
    assert source.starts == [10, 20]
    [done] = runs(db)
    assert done["id"] == stuck["id"] and done["status"] == "success"


def test_old_failed_run_is_not_resumed(db, db_url):
    data = rows(25)
    with pytest.raises(SirupUnavailableError):
        run_scrape(settings(db_url), FakeSource(data, fail_on_call=2))
    db.execute("UPDATE scrape_runs SET started_at = now() - interval '25 hours'")
    source = FakeSource(data)
    run_scrape(settings(db_url), source)
    assert source.starts == [0, 10, 20]
    old, new = runs(db)
    assert old["status"] == "failed" and new["status"] == "success"


def state(db, pid: int) -> tuple[bool, int]:
    return db.execute("SELECT is_active, missing_runs FROM paket WHERE id = %s", (pid,)).fetchone()


def test_missing_package_deactivated_after_two_complete_runs(db, db_url):  # F-05
    data = rows(25)
    without = data[:7] + data[8:]
    run_scrape(settings(db_url), FakeSource(data))
    run_scrape(settings(db_url), FakeSource(without))
    assert state(db, 1007) == (True, 1)  # one miss is not enough
    last = runs(db)[-1]
    assert (last["rows_missing"], last["rows_deactivated"]) == (1, 0)
    run_scrape(settings(db_url), FakeSource(without))
    assert state(db, 1007) == (False, 2)
    last = runs(db)[-1]
    assert (last["rows_missing"], last["rows_deactivated"]) == (1, 1)
    assert count(db, "is_active") == 24
    run_scrape(settings(db_url), FakeSource(data))  # comes back
    assert state(db, 1007) == (True, 0)
    assert count(db, "is_active") == 25


def test_misses_must_be_consecutive(db, db_url):
    data = rows(25)
    without = data[:7] + data[8:]
    run_scrape(settings(db_url), FakeSource(data))
    run_scrape(settings(db_url), FakeSource(without))
    run_scrape(settings(db_url), FakeSource(data))  # seen again: counter resets
    run_scrape(settings(db_url), FakeSource(without))
    assert state(db, 1007) == (True, 1)


def test_guarded_run_does_not_count_a_miss(db, db_url):
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))
    run_scrape(settings(db_url, scrape_max_pages=1), FakeSource(data[:24]))  # partial
    assert count(db, "missing_runs > 0") == 0
    assert runs(db)[-1]["rows_missing"] == 0


def test_guard_big_drop_skips_deactivation(db, db_url):
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))
    run_scrape(settings(db_url), FakeSource(data[:15]))  # -40%
    last = runs(db)[-1]
    assert last["status"] == "success" and last["rows_deactivated"] == 0
    assert "dropped" in last["note"]
    assert count(db, "is_active") == 25


def test_guard_partial_run_skips_deactivation(db, db_url):
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))
    source = FakeSource(data[:24])
    run_scrape(settings(db_url, scrape_max_pages=1), source)
    assert source.starts == [0]
    last = runs(db)[-1]
    assert last["rows_deactivated"] == 0 and "partial" in last["note"]
    assert count(db, "is_active") == 25


def test_guard_zero_total_skips_deactivation(db, db_url):
    run_scrape(settings(db_url), FakeSource(rows(25)))
    run_scrape(settings(db_url), FakeSource([]))
    last = runs(db)[-1]
    assert last["records_total"] == 0 and "0 records" in last["note"]
    assert count(db, "is_active") == 25


def test_guard_too_many_invalid_rows_overall_skips_deactivation(db, db_url):
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))
    damaged = [dict(r) for r in data[:24]]
    for i in (0, 10, 20):  # 3 invalid rows: < 50% per page, but < 99% overall
        damaged[i]["pagu"] = "rusak"
    run_scrape(settings(db_url), FakeSource(damaged))
    last = runs(db)[-1]
    assert last["rows_invalid"] == 3 and last["rows_received"] == 24
    assert "99%" in last["note"] and last["rows_deactivated"] == 0


def test_more_than_half_invalid_fails_the_run(db, db_url):
    data = rows(10)
    for r in data[:6]:
        r["pagu"] = "format baru"
    with pytest.raises(TooManyInvalidRowsError):
        run_scrape(settings(db_url), FakeSource(data))
    [run] = runs(db)
    assert run["status"] == "failed" and "format" in run["error"]
    assert count(db) == 0


def test_advisory_lock_blocks_second_store(db, db_url):
    with Store.connect(db_url):
        with pytest.raises(ScrapeAlreadyRunningError):
            Store.connect(db_url)
        with pytest.raises(ScrapeAlreadyRunningError):
            run_scrape(settings(db_url), FakeSource(rows(3)))
    with Store.connect(db_url):  # released after close
        pass
    assert runs(db) == []


def test_page_limit_that_covers_everything_counts_as_complete(db, db_url):
    sleeps: list[float] = []
    run_scrape(
        settings(db_url, scrape_max_pages=3, scrape_delay_seconds=1),
        FakeSource(rows(25)),
        sleep=sleeps.append,
    )
    [run] = runs(db)
    assert run["note"] is None and sleeps == [1, 1]


def test_page_limit_does_not_sleep_after_last_page(db, db_url):
    sleeps: list[float] = []
    run_scrape(
        settings(db_url, scrape_max_pages=2, scrape_delay_seconds=1),
        FakeSource(rows(25)),
        sleep=sleeps.append,
    )
    assert sleeps == [1]
    assert "partial" in runs(db)[0]["note"]


def test_real_rows_without_jenis_pengadaan_are_stored(db, db_url):  # AC-01 regression
    no_name = dict(REAL_ROW_WITHOUT_JENIS)  # real SIRUP row: idJenisPengadaan 6, no name
    no_type = {k: v for k, v in REAL_ROW_WITHOUT_JENIS.items() if k != "idJenisPengadaan"}
    no_type["id"] = no_name["id"] + 1
    run_scrape(settings(db_url), FakeSource([*rows(3), no_name, no_type]))
    [run] = runs(db)
    assert run["rows_invalid"] == 0 and run["rows_inserted"] == 5
    stored = db.execute(
        "SELECT id, id_jenis_pengadaan, jenis_pengadaan, satuan_kerja FROM paket "
        "WHERE id >= 61900988 ORDER BY id"
    ).fetchall()
    assert stored == [
        (61900988, 6, None, "KANTOR BAHASA KEPULAUAN RIAU"),
        (61900989, None, None, "KANTOR BAHASA KEPULAUAN RIAU"),
    ]
    raw = db.execute("SELECT raw FROM paket_raw WHERE id = 61900988").fetchone()[0]
    assert raw == REAL_ROW_WITHOUT_JENIS


def test_invalid_rows_are_logged_with_reason_and_ids(db, db_url, caplog):
    data = rows(10)
    data[2]["pagu"] = "rusak"
    del data[4]["id"]
    with caplog.at_level("WARNING", logger="sirup.scraper.runner"):
        run_scrape(settings(db_url), FakeSource(data))
    [msg] = [r.getMessage() for r in caplog.records if "invalid" in r.getMessage()]
    assert "2 of 10 rows invalid" in msg
    assert "pagu:value_error=1" in msg and "id:missing=1" in msg
    assert "sample_ids [1002, None]" in msg


class TruncatingSource(FakeSource):
    """Answers an empty page from ``empty_from`` on, while still claiming the full total."""

    def __init__(self, rows: list[Any], empty_from: int) -> None:
        super().__init__(rows)
        self.empty_from = empty_from

    def fetch_page(self, year: int, start: int, length: int) -> Page:
        self.starts.append(start)
        data = [] if start >= self.empty_from else self.rows[start : start + length]
        return Page(recordsTotal=self.total, data=data)


@pytest.mark.parametrize(("empty_from", "checkpoint"), [(10, 10), (20, 20)])  # mid, last page
def test_empty_page_before_total_fails_run_and_keeps_checkpoint(db, db_url, empty_from, checkpoint):
    data = rows(25)
    run_scrape(settings(db_url), FakeSource(data))  # all 25 active
    with pytest.raises(SirupUnavailableError, match="empty page"):
        run_scrape(settings(db_url), TruncatingSource(data, empty_from))
    last = runs(db)[-1]
    assert last["status"] == "failed" and "empty page" in last["error"]
    assert last["checkpoint_start"] == checkpoint and last["rows_deactivated"] == 0
    assert count(db, "is_active") == 25
    source = FakeSource(data)  # SIRUP recovers: resume, not restart
    run_scrape(settings(db_url), source)
    assert source.starts[0] == checkpoint
    assert runs(db)[-1]["status"] == "success"


def test_pemilihan_tahun_parsed_by_database(db, db_url):  # D6
    data = rows(5)
    data[0]["pemilihan"] = "December 2025"
    data[1]["pemilihan"] = "march 2027"
    data[2]["pemilihan"] = "Desember 2026"  # not an English month name
    data[3]["pemilihan"] = None
    run_scrape(settings(db_url), FakeSource(data))
    got = dict(db.execute("SELECT id, pemilihan_tahun FROM paket").fetchall())
    assert got == {1000: 2025, 1001: 2027, 1002: None, 1003: None, 1004: 2026}
    data[0]["pemilihan"] = "January 2026"  # SIRUP revises the plan: column follows
    run_scrape(settings(db_url), FakeSource(data))
    assert db.execute("SELECT pemilihan_tahun FROM paket WHERE id = 1000").fetchone()[0] == 2026
