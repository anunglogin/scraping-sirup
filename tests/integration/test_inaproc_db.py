"""inaproc_rup upserts against a real PostgreSQL (TEST_DATABASE_URL)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import psycopg
import pytest

from sirup.inaproc.client import RupRow
from sirup.inaproc.store import InaprocStore, load_file

ROW = {
    "kode_rup": "68336359",
    "tahun_anggaran": 2026,
    "nama_paket": "Belanja Makanan dan Minuman Rapat",
    "nama_instansi": "KAB. HULU SUNGAI UTARA",
    "nama_satker": "BADAN KESBANGPOL",
    "total_nilai": 17550000.5,
    "metode_pengadaan": "E-Purchasing",
    "status_umk": "Ya",
    "kolom_baru": "kept in raw",
}


@pytest.fixture
def store(db_url: str) -> Iterator[InaprocStore]:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE inaproc_rup, paket, paket_raw")
    with InaprocStore(db_url) as s:
        yield s


def fetch(db_url: str) -> list[tuple]:
    with psycopg.connect(db_url) as conn:
        return conn.execute(
            "SELECT kode_rup, nama_paket, total_nilai, metode_pengadaan, raw->>'kolom_baru' "
            "FROM inaproc_rup ORDER BY kode_rup"
        ).fetchall()


def test_upsert_inserts_then_updates_without_duplicates(store, db_url):
    assert store.upsert([RupRow.model_validate(ROW)]) == 1
    changed = {**ROW, "nama_paket": "Nama baru", "total_nilai": 1}
    assert store.upsert([RupRow.model_validate(changed)]) == 1
    rows = fetch(db_url)
    assert len(rows) == 1
    kode, nama, nilai, metode, extra = rows[0]
    assert (kode, nama, float(nilai), metode, extra) == (
        68336359,
        "Nama baru",
        1.0,
        "E-Purchasing",
        "kept in raw",
    )


def test_non_numeric_kode_is_skipped(store, db_url):
    bad = RupRow.model_validate({**ROW, "kode_rup": "abc"})
    assert store.upsert([bad, RupRow.model_validate(ROW)]) == 1
    assert [r[0] for r in fetch(db_url)] == [68336359]


def test_load_file_skips_invalid_lines(store, db_url, tmp_path):
    path = tmp_path / "rup_2026.jsonl"
    other = {**ROW, "kode_rup": "42390663"}
    path.write_text(json.dumps(ROW) + "\nnot json\n\n" + json.dumps(other) + "\n", encoding="utf-8")
    assert load_file(store, path, batch_size=1) == 2
    assert store.count() == 2


def paket(db_url: str, pid: int):
    with psycopg.connect(db_url) as conn:
        return conn.execute(
            "SELECT nama_paket, pagu, metode, kldi, satuan_kerja, is_pdn, is_umk, sumber "
            "FROM paket WHERE id = %s",
            (pid,),
        ).fetchone()


def test_existing_sirup_package_only_gets_inaproc_tag(store, db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu) "
            "VALUES (68336359, 2026, 'Nama dari SIRUP', 5)"
        )
    store.upsert([RupRow.model_validate(ROW)])
    store.upsert([RupRow.model_validate(ROW)])  # tag is not added twice
    nama, pagu, *_, sumber = paket(db_url, 68336359)
    assert (nama, pagu, sumber) == ("Nama dari SIRUP", 5, ["sirup", "inaproc"])


def test_inaproc_only_package_is_added_and_refreshed(store, db_url):
    store.upsert([RupRow.model_validate({**ROW, "produk_dalam_negeri": "Tidak"})])
    assert paket(db_url, 68336359) == (
        "Belanja Makanan dan Minuman Rapat",
        17_550_001,  # 17,550,000.50 rounded
        "E-Purchasing",
        "KAB. HULU SUNGAI UTARA",
        "BADAN KESBANGPOL",
        False,
        True,
        ["inaproc"],
    )
    store.upsert(
        [RupRow.model_validate({**ROW, "nama_paket": "Nama baru", "metode_pengadaan": "-"})]
    )
    nama, _, metode, *_, sumber = paket(db_url, 68336359)
    assert (nama, metode, sumber) == ("Nama baru", None, ["inaproc"])
