from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent

# Three real rows captured from SIRUP on 10 Nov 2026 (length=3, ordered by id).
SAMPLE_ROWS: list[dict[str, Any]] = [
    {
        "idBulan": 1,
        "pagu": 4000000,
        "satuanKerja": "SUKU DINAS PENDIDIKAN WILAYAH 1 KOTA - JAKUT",
        "isPDN": True,
        "idlokasi": 0,
        "idKldi": "D69",
        "metode": "Dikecualikan",
        "sumberDana": "APBD",
        "kldi": "Provinsi DKI Jakarta",
        "isUMK": True,
        "idsLokasi": "14751",
        "id_referensi": 334,
        "lokasi": "DKI Jakarta, Jakarta Utara (Kota)",
        "jenisPengadaan": "Barang",
        "idSatker": 144073,
        "pemilihan": "January 2026",
        "idMetode": 16,
        "id": 60522826,
        "idJenisPengadaan": 1,
        "paket": "Pelaksanaan Penatausahaan dan Pengujian/Verifikasi Keuangan SKPD\t",
        "pds": False,
    },
    {
        "idBulan": 12,
        "pagu": 6359532351,
        "satuanKerja": "SUKU DINAS PENDIDIKAN WILAYAH 1 KOTA - JAKUT",
        "isPDN": True,
        "idlokasi": 0,
        "idKldi": "D69",
        "metode": "Pengadaan Langsung",
        "sumberDana": "APBD",
        "kldi": "Provinsi DKI Jakarta",
        "isUMK": True,
        "idsLokasi": "14751",
        "id_referensi": 334,
        "lokasi": "DKI Jakarta, Jakarta Utara (Kota)",
        "jenisPengadaan": "Jasa Lainnya",
        "idSatker": 144073,
        "pemilihan": "December 2025",
        "idMetode": 8,
        "id": 60522827,
        "idJenisPengadaan": 4,
        "paket": "Penyediaan Pendidik dan Tenaga Kependidikan bagi Satuan Pendidikan "
        "Sekolah Menengah Atas\t",
        "pds": False,
    },
    {
        "idBulan": 12,
        "pagu": 5920943913,
        "satuanKerja": "SUKU DINAS PENDIDIKAN WILAYAH 1 KOTA - JAKUT",
        "isPDN": True,
        "idlokasi": 0,
        "idKldi": "D69",
        "metode": "Pengadaan Langsung",
        "sumberDana": "APBD",
        "kldi": "Provinsi DKI Jakarta",
        "isUMK": True,
        "idsLokasi": "14751",
        "id_referensi": 334,
        "lokasi": "DKI Jakarta, Jakarta Utara (Kota)",
        "jenisPengadaan": "Jasa Lainnya",
        "idSatker": 144073,
        "pemilihan": "December 2025",
        "idMetode": 8,
        "id": 60522828,
        "idJenisPengadaan": 4,
        "paket": "Penyediaan Pendidik dan Tenaga Kependidikan bagi Satuan Pendidikan "
        "Sekolah Menengah Kejuruan\t",
        "pds": False,
    },
]


def make_row(
    i: int,
    *,
    paket: str | None = None,
    pagu: Any = None,
    satker: str = "DINAS KESEHATAN KOTA CONTOH",
    bulan: int = 3,
) -> dict[str, Any]:
    row = copy.deepcopy(SAMPLE_ROWS[0])
    row.update(
        id=1000 + i,
        paket=paket if paket is not None else f"Pengadaan barang nomor {i}",
        pagu=pagu if pagu is not None else 1_000_000 * (i + 1),
        satuanKerja=satker,
        idBulan=bulan,
    )
    return row


@pytest.fixture
def sample_rows() -> list[dict[str, Any]]:
    return copy.deepcopy(SAMPLE_ROWS)


# --------------------------------------------------------------------- database
@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; skipping database tests")
    from alembic import command
    from alembic.config import Config

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    # Prove the downgrade works, then leave the schema at head.
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def db(db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE paket, scrape_runs RESTART IDENTITY")
        yield conn
