"""SQL for the read API. Every user value goes through bound parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from psycopg import sql

SortKey = Literal["pagu_desc", "pagu_asc", "terbaru"]

# Whitelisted ORDER BY clauses; ties broken by id so paging is stable.
ORDER_BY: dict[str, sql.Composable] = {
    "pagu_desc": sql.SQL("pagu DESC, id DESC"),
    "pagu_asc": sql.SQL("pagu ASC, id ASC"),
    "terbaru": sql.SQL("first_seen_at DESC, id DESC"),
}

LIST_COLUMNS = (
    "id",
    "tahun_anggaran",
    "nama_paket",
    "pagu",
    "id_jenis_pengadaan",
    "jenis_pengadaan",
    "id_metode",
    "metode",
    "id_bulan",
    "pemilihan",
    "pemilihan_tahun",
    "id_kldi",
    "kldi",
    "id_satker",
    "satuan_kerja",
    "ids_lokasi",
    "lokasi",
    "sumber_dana",
    "is_pdn",
    "is_umk",
    "pds",
    "id_referensi",
    "is_active",
    "first_seen_at",
    "last_seen_at",
    "updated_at",
)


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user text is matched literally (ESCAPE '\\')."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class PaketFilter:
    tahun: int
    aktif: bool
    paket: str | None = None
    satuan_kerja: str | None = None
    # (year, month) pairs, e.g. ((2026, 3), (2026, 4))
    pemilihan: tuple[tuple[int, int], ...] = ()
    pagu_min: int | None = None
    pagu_max: int | None = None

    def where(self) -> tuple[sql.Composable, list[Any]]:
        clauses: list[sql.Composable] = [sql.SQL("tahun_anggaran = %s")]
        params: list[Any] = [self.tahun]
        if self.aktif:
            clauses.append(sql.SQL("is_active"))
        if self.paket:
            clauses.append(sql.SQL("nama_paket ILIKE %s ESCAPE '\\'"))
            params.append(f"%{escape_like(self.paket)}%")
        if self.satuan_kerja:
            clauses.append(sql.SQL("satuan_kerja ILIKE %s ESCAPE '\\'"))
            params.append(f"%{escape_like(self.satuan_kerja)}%")
        if self.pemilihan:
            pairs = sql.SQL(", ").join(sql.SQL("(%s, %s)") for _ in self.pemilihan)
            clauses.append(sql.SQL("(pemilihan_tahun, id_bulan) IN ({})").format(pairs))
            for year, month in self.pemilihan:
                params.extend((year, month))
        if self.pagu_min is not None:
            clauses.append(sql.SQL("pagu >= %s"))
            params.append(self.pagu_min)
        if self.pagu_max is not None:
            clauses.append(sql.SQL("pagu <= %s"))
            params.append(self.pagu_max)
        return sql.SQL(" AND ").join(clauses), params


def count_query(f: PaketFilter, cap: int) -> tuple[sql.Composable, list[Any]]:
    """Count matches, but stop after ``cap`` rows.

    An exact count of a broad filter means scanning millions of rows (seconds);
    since only the first 10,000 results can be paged anyway, counting further
    adds nothing. The caller treats ``cap`` as "more than cap - 1".
    """
    where, params = f.where()
    query = sql.SQL("SELECT count(*) FROM (SELECT 1 FROM paket WHERE {} LIMIT %s) AS capped")
    return query.format(where), [*params, cap]


def list_query(
    f: PaketFilter, sort: SortKey, limit: int, offset: int
) -> tuple[sql.Composable, list[Any]]:
    where, params = f.where()
    query = sql.SQL("SELECT {cols} FROM paket WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s")
    composed = query.format(
        cols=sql.SQL(", ").join(map(sql.Identifier, LIST_COLUMNS)),
        where=where,
        order=ORDER_BY[sort],
    )
    return composed, [*params, limit, offset]


DETAIL_QUERY = sql.SQL(
    "SELECT {cols}, coalesce(r.raw, '{{}}'::jsonb) AS raw "
    "FROM paket p LEFT JOIN paket_raw r ON r.id = p.id WHERE p.id = %s"
).format(cols=sql.SQL(", ").join(sql.Identifier("p", c) for c in LIST_COLUMNS))

RUNS_QUERY = sql.SQL(
    "SELECT id, tahun_anggaran, status, started_at, finished_at, records_total, rows_received, "
    "rows_inserted, rows_updated, rows_invalid, rows_missing, rows_deactivated, "
    "checkpoint_start, error, note "
    "FROM scrape_runs ORDER BY id DESC LIMIT %s"
)
