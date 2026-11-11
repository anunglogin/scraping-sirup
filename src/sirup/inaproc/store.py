"""Upsert collected data.inaproc.id rows into PostgreSQL (table inaproc_rup)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Self

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from sirup.inaproc.client import RupRow

log = logging.getLogger(__name__)

COLUMNS = (
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
_UPDATES = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "kode_rup")
UPSERT_SQL = (
    f"INSERT INTO inaproc_rup ({', '.join(COLUMNS)}, raw) "  # noqa: S608 - fixed column names
    f"VALUES ({', '.join(['%s'] * (len(COLUMNS) + 1))}) "
    f"ON CONFLICT (kode_rup) DO UPDATE SET {_UPDATES}, raw = EXCLUDED.raw, last_seen_at = now()"
)


def to_params(row: RupRow) -> tuple[Any, ...] | None:
    """DB parameters for one row, or None when kode_rup is not numeric."""
    data = row.model_dump()
    try:
        kode = int(row.kode_rup)
    except ValueError:
        return None
    values = [kode, *(data.get(c) for c in COLUMNS[1:])]
    return (*values, Jsonb(data))


class InaprocStore:
    def __init__(self, database_url: str) -> None:
        self._conn = psycopg.connect(database_url, autocommit=False)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._conn.close()

    def upsert(self, rows: Iterable[RupRow]) -> int:
        """Insert or update rows in one transaction; returns rows written."""
        params = []
        for row in rows:
            p = to_params(row)
            if p is None:
                log.warning("skipping row with non-numeric kode_rup %r", row.kode_rup[:40])
                continue
            params.append(p)
        if not params:
            return 0
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, params)
        return len(params)

    def count(self) -> int:
        row = self._conn.execute("SELECT count(*) FROM inaproc_rup").fetchone()
        self._conn.commit()
        return int(row[0]) if row else 0


def read_jsonl(path: Path) -> Iterator[RupRow]:
    """Rows from a collector JSONL file; invalid lines are logged and skipped."""
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                yield RupRow.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError):
                log.warning("skipping invalid line %d in %s", n, path.name)


def load_file(store: InaprocStore, path: Path, batch_size: int = 1000) -> int:
    total = 0
    batch: list[RupRow] = []
    for row in read_jsonl(path):
        batch.append(row)
        if len(batch) >= batch_size:
            total += store.upsert(batch)
            batch.clear()
    total += store.upsert(batch)
    return total
