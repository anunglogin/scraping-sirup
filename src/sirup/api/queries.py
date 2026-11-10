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
    pemilihan_bulan: tuple[int, ...] = ()
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
        if self.pemilihan_bulan:
            clauses.append(sql.SQL("id_bulan = ANY(%s)"))
            params.append(list(self.pemilihan_bulan))
        if self.pagu_min is not None:
            clauses.append(sql.SQL("pagu >= %s"))
            params.append(self.pagu_min)
        if self.pagu_max is not None:
            clauses.append(sql.SQL("pagu <= %s"))
            params.append(self.pagu_max)
        return sql.SQL(" AND ").join(clauses), params


def count_query(f: PaketFilter) -> tuple[sql.Composable, list[Any]]:
    where, params = f.where()
    return sql.SQL("SELECT count(*) FROM paket WHERE {}").format(where), params


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


DETAIL_QUERY = sql.SQL("SELECT {cols}, raw FROM paket WHERE id = %s").format(
    cols=sql.SQL(", ").join(map(sql.Identifier, LIST_COLUMNS))
)

RUNS_QUERY = sql.SQL(
    "SELECT id, tahun_anggaran, status, started_at, finished_at, records_total, rows_received, "
    "rows_inserted, rows_updated, rows_invalid, rows_deactivated, checkpoint_start, error, note "
    "FROM scrape_runs ORDER BY id DESC LIMIT %s"
)
