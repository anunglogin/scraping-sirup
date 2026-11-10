"""PostgreSQL persistence for the scraper: upserts, checkpoints, run log."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Self

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from sirup.scraper.models import COPY_COLUMNS, ValidRow

log = logging.getLogger(__name__)

# Arbitrary constant identifying "the scraper" in pg_advisory_lock.
ADVISORY_LOCK_KEY = 7_412_026_001

RESUME_WINDOW = "24 hours"
MIN_COMPLETENESS = 0.99
MAX_TOTAL_DROP = 0.20

# ``raw`` lives in its own table (paket_raw, migration 0003); the rest in paket.
_PAKET_COLUMNS = [c for c in COPY_COLUMNS if c != "raw"]
_UPDATE_COLUMNS = [c for c in _PAKET_COLUMNS if c != "id"]


class ScrapeAlreadyRunningError(RuntimeError):
    """Another process holds the scraper lock."""


@dataclass
class Run:
    id: int
    tahun_anggaran: int
    started_at: datetime
    checkpoint_start: int
    resumed: bool


@dataclass(frozen=True)
class PageResult:
    inserted: int
    changed: int


def deactivation_block_reason(
    *,
    complete: bool,
    records_total: int | None,
    valid_received: int,
    previous_total: int | None,
) -> str | None:
    """Return why missing packages must NOT be marked inactive, or None if safe.

    Marking packages inactive based on an incomplete picture would wrongly hide
    live tenders, so every doubt blocks it.
    """
    if not complete:
        return "partial run (page limit reached); deactivation skipped"
    if not records_total:
        return "SIRUP reported 0 records; deactivation skipped"
    if valid_received < MIN_COMPLETENESS * records_total:
        return (
            f"received {valid_received} valid rows, below 99% of recordsTotal "
            f"{records_total}; deactivation skipped"
        )
    if previous_total and records_total < (1 - MAX_TOTAL_DROP) * previous_total:
        return (
            f"recordsTotal dropped from {previous_total} to {records_total} (>20%); "
            "deactivation skipped"
        )
    return None


class Store:
    """One autocommit connection holding the session advisory lock.

    Use as a context manager. Raises ScrapeAlreadyRunningError when another
    scraper process is active.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, database_url: str) -> Self:
        conn = psycopg.connect(database_url, autocommit=True, application_name="sirup-scraper")
        try:
            got = conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()
            if not got or not got[0]:
                raise ScrapeAlreadyRunningError("another scrape run is in progress")
            conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS staging_paket "
                "(LIKE paket INCLUDING DEFAULTS, raw jsonb NOT NULL) ON COMMIT DELETE ROWS"
            )
        except BaseException:
            conn.close()
            raise
        return cls(conn)

    def close(self) -> None:
        # Release the lock explicitly: the server frees a closed session's locks
        # asynchronously, so a run started right after could otherwise be refused.
        try:
            if not self.conn.closed:
                self.conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        except psycopg.Error:
            pass  # the lock is released with the session anyway
        finally:
            self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ runs
    def open_run(self, year: int) -> Run:
        """Resume a recent interrupted run for ``year`` or start a new one."""
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, status, checkpoint_start, started_at,
                       started_at > now() - %s::interval AS recent
                FROM scrape_runs WHERE tahun_anggaran = %s
                ORDER BY id DESC LIMIT 1 FOR UPDATE
                """,
                (RESUME_WINDOW, year),
            )
            last = cur.fetchone()
            if (
                last
                and last["status"] in ("running", "failed")
                and last["checkpoint_start"] > 0
                and last["recent"]
            ):
                cur.execute(
                    "UPDATE scrape_runs SET status = 'running', finished_at = NULL, "
                    "error = NULL WHERE id = %s",
                    (last["id"],),
                )
                log.info("resuming run %s from start=%s", last["id"], last["checkpoint_start"])
                return Run(
                    id=last["id"],
                    tahun_anggaran=year,
                    started_at=last["started_at"],
                    checkpoint_start=last["checkpoint_start"],
                    resumed=True,
                )
            # Any 'running' row left behind by a killed process is stale now
            # (we hold the lock), so close it before starting over.
            cur.execute(
                "UPDATE scrape_runs SET status = 'failed', finished_at = now(), "
                "error = coalesce(error, 'abandoned: process stopped, not resumable') "
                "WHERE status = 'running'"
            )
            cur.execute(
                "INSERT INTO scrape_runs (tahun_anggaran, status) VALUES (%s, 'running') "
                "RETURNING id, started_at",
                (year,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("could not create scrape_runs row")
            log.info("started run %s for tahun_anggaran=%s", row["id"], year)
            return Run(
                id=row["id"],
                tahun_anggaran=year,
                started_at=row["started_at"],
                checkpoint_start=0,
                resumed=False,
            )

    def save_page(
        self,
        run: Run,
        rows: list[ValidRow],
        *,
        invalid: int,
        next_start: int,
        records_total: int,
    ) -> PageResult:
        """Upsert one page and advance the checkpoint atomically."""
        inserted = changed = 0
        with self.conn.transaction(), self.conn.cursor() as cur:
            if rows:
                copy_sql = sql.SQL("COPY staging_paket ({}) FROM STDIN").format(
                    sql.SQL(", ").join(map(sql.Identifier, COPY_COLUMNS))
                )
                with cur.copy(copy_sql) as copy:
                    for r in rows:
                        values = list(r.to_copy_values(run.tahun_anggaran))
                        values[-1] = Jsonb(values[-1])
                        copy.write_row(values)
                cols = sql.SQL(", ").join(map(sql.Identifier, _PAKET_COLUMNS))
                updates = sql.SQL(", ").join(
                    sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
                    for c in _UPDATE_COLUMNS
                )
                # 1) upsert the original rows; RETURNING lists ids whose raw row is
                #    new or different. 2) upsert paket; updated_at moves only for
                #    those ids, so it means "content changed".
                upsert = sql.SQL(
                    """
                    WITH src AS MATERIALIZED (
                        SELECT DISTINCT ON (id) * FROM staging_paket ORDER BY id
                    ),
                    changed_raw AS (
                        INSERT INTO paket_raw (id, raw)
                        SELECT id, raw FROM src
                        ON CONFLICT (id) DO UPDATE SET raw = EXCLUDED.raw
                            WHERE paket_raw.raw IS DISTINCT FROM EXCLUDED.raw
                        RETURNING id
                    )
                    INSERT INTO paket ({cols})
                    SELECT {cols} FROM src
                    ON CONFLICT (id) DO UPDATE SET {updates},
                        is_active = true,
                        last_seen_at = now(),
                        updated_at = CASE WHEN EXCLUDED.id IN (SELECT id FROM changed_raw)
                                          THEN now() ELSE paket.updated_at END
                    RETURNING (xmax = 0) AS inserted, (updated_at = now()) AS touched
                    """
                ).format(cols=cols, updates=updates)
                cur.execute(upsert)
                for was_inserted, touched in cur.fetchall():
                    if was_inserted:
                        inserted += 1
                    elif touched:
                        changed += 1
            cur.execute(
                """
                UPDATE scrape_runs SET
                    records_total = %s,
                    rows_received = rows_received + %s,
                    rows_invalid = rows_invalid + %s,
                    rows_inserted = rows_inserted + %s,
                    rows_updated = rows_updated + %s,
                    checkpoint_start = %s
                WHERE id = %s
                """,
                (
                    records_total,
                    len(rows) + invalid,
                    invalid,
                    inserted,
                    changed,
                    next_start,
                    run.id,
                ),
            )
        run.checkpoint_start = next_start
        return PageResult(inserted=inserted, changed=changed)

    def finish_run(self, run: Run, *, complete: bool) -> tuple[int, str | None]:
        """Mark the run successful; deactivate vanished packages when safe.

        Returns (rows_deactivated, note).
        """
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT records_total, rows_received - rows_invalid AS valid "
                "FROM scrape_runs WHERE id = %s",
                (run.id,),
            )
            cur_run = cur.fetchone()
            if cur_run is None:
                raise RuntimeError(f"scrape run {run.id} disappeared")
            cur.execute(
                "SELECT records_total FROM scrape_runs WHERE tahun_anggaran = %s "
                "AND status = 'success' AND id <> %s ORDER BY id DESC LIMIT 1",
                (run.tahun_anggaran, run.id),
            )
            prev = cur.fetchone()
            note = deactivation_block_reason(
                complete=complete,
                records_total=cur_run["records_total"],
                valid_received=cur_run["valid"] or 0,
                previous_total=prev["records_total"] if prev else None,
            )
            deactivated = 0
            if note is None:
                cur.execute(
                    "UPDATE paket SET is_active = false WHERE tahun_anggaran = %s "
                    "AND is_active AND last_seen_at < %s",
                    (run.tahun_anggaran, run.started_at),
                )
                deactivated = cur.rowcount
            cur.execute(
                "UPDATE scrape_runs SET status = 'success', finished_at = now(), "
                "rows_deactivated = %s, note = %s WHERE id = %s",
                (deactivated, note, run.id),
            )
        if note:
            log.warning("run %s: %s", run.id, note)
        return deactivated, note

    def fail_run(self, run: Run, error: str) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE scrape_runs SET status = 'failed', finished_at = now(), error = %s "
                "WHERE id = %s",
                (error[:2000], run.id),
            )
