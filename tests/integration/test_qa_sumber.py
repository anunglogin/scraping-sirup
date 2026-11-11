"""QA edge cases for TD-09 / TD-10 (paket.sumber, inaproc_rup). Needs TEST_DATABASE_URL."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from sirup.api.config import ApiSettings
from sirup.api.main import create_app
from sirup.inaproc.client import RupRow
from sirup.inaproc.store import InaprocStore
from sirup.scraper.runner import run_scrape
from tests.integration.test_scraper_db import FakeSource, rows, settings

ROOT = Path(__file__).resolve().parents[2]
KEY = "qa-key-" + "x" * 30
H = {"X-API-Key": KEY}
BASE = {
    "kode_rup": "70000001",
    "tahun_anggaran": 2026,
    "nama_paket": "Paket QA INAPROC",
    "nama_instansi": "KAB. UJI",
    "nama_satker": "DINAS UJI",
    "total_nilai": 1000000,
    "jenis_pengadaan": "Barang",
    "metode_pengadaan": "E-Purchasing",
    "sumber_dana": "APBD",
}


def rr(**kw) -> RupRow:
    return RupRow.model_validate({**BASE, **kw})


@pytest.fixture
def store(db_url: str) -> Iterator[InaprocStore]:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE inaproc_rup, paket, paket_raw, scrape_runs")
    with InaprocStore(db_url) as s:
        yield s


@pytest.fixture
def client(db_url, store) -> Iterator[TestClient]:
    with TestClient(create_app(ApiSettings(database_url=db_url, api_keys=KEY))) as c:
        yield c


def one(db_url: str, q: str, args=()):
    with psycopg.connect(db_url) as conn:
        return conn.execute(q, args).fetchone()


def test_td10_check_constraint_rejects_empty_and_unknown_tag(db_url, store):
    for bad in ("{}", "{lkpp}", "{sirup,lkpp}"):
        with (
            psycopg.connect(db_url, autocommit=True) as conn,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            conn.execute(
                "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu, sumber) "
                "VALUES (1, 2026, 'x', 1, %s::text[])",
                (bad,),
            )
    with (
        psycopg.connect(db_url, autocommit=True) as conn,
        pytest.raises(psycopg.errors.NotNullViolation),
    ):
        conn.execute(
            "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu, sumber) "
            "VALUES (1, 2026, 'x', 1, NULL)"
        )


def test_td10_default_sumber_is_sirup(db_url, store):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu) VALUES (1,2026,'x',1)"
        )
    assert one(db_url, "SELECT sumber FROM paket WHERE id=1")[0] == ["sirup"]


def test_td10_downgrade_0007_to_0006_and_back_keeps_sirup_drops_inaproc_only(db_url, store):
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    cfg.attributes["database_url"] = db_url
    cfg.attributes["configure_logger"] = False
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu, sumber) VALUES "
            "(1,2026,'s',1,'{sirup}'), (2,2026,'b',1,'{sirup,inaproc}'), (3,2026,'i',1,'{inaproc}')"
        )
    try:
        command.downgrade(cfg, "0006")
        cols = one(
            db_url,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='paket' AND column_name='sumber'",
        )[0]
        assert cols == 0
        assert one(db_url, "SELECT array_agg(id ORDER BY id) FROM paket")[0] == [1, 2]
    finally:
        command.upgrade(cfg, "head")
    assert one(db_url, "SELECT array_agg(sumber::text ORDER BY id) FROM paket")[0] == [
        "{sirup}",
        "{sirup}",
    ]


def test_td10_package_lifecycle_inaproc_only_then_sirup_then_gone_from_sirup(store, db_url):
    store.upsert([rr(kode_rup="1003", nama_paket="Versi INAPROC")])
    assert one(db_url, "SELECT sumber FROM paket WHERE id=1003")[0] == ["inaproc"]
    # SIRUP now lists it: content from SIRUP, both tags
    src = rows(5)
    run_scrape(settings(db_url), FakeSource(src))
    nama, sumber = one(db_url, "SELECT nama_paket, sumber FROM paket WHERE id=1003")
    assert nama == "Pengadaan barang nomor 3" and sumber == ["inaproc", "sirup"]
    # INAPROC recollect must not overwrite SIRUP content
    store.upsert([rr(kode_rup="1003", nama_paket="Versi INAPROC baru")])
    assert one(db_url, "SELECT nama_paket FROM paket WHERE id=1003")[0] == nama
    # SIRUP stops listing it: after enough complete runs it is deactivated, tags remain
    keep = [r for r in src if r["id"] != 1003]
    for _ in range(3):
        run_scrape(settings(db_url), FakeSource(keep))
    active, sumber = one(db_url, "SELECT is_active, sumber FROM paket WHERE id=1003")
    assert active is False and sumber == ["inaproc", "sirup"]


def test_td10_dash_values_become_null_and_null_total_is_zero_pagu(store, db_url):
    store.upsert([rr(jenis_pengadaan="-", metode_pengadaan="-", sumber_dana="-", total_nilai=None)])
    row = one(
        db_url,
        "SELECT jenis_pengadaan, metode, sumber_dana, pagu FROM paket WHERE id=70000001",
    )
    assert row == (None, None, None, 0)


def test_td10_concurrent_collectors_do_not_duplicate_tag_or_rows(store, db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu) VALUES (70000001,2026,'s',1)"
        )
    batch = [rr(kode_rup=str(70000001 + i)) for i in range(50)]
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with InaprocStore(db_url) as s:
                s.upsert(batch)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert one(db_url, "SELECT count(*) FROM inaproc_rup")[0] == 50
    assert one(db_url, "SELECT count(*) FROM paket")[0] == 50
    assert one(db_url, "SELECT sumber FROM paket WHERE id=70000001")[0] == ["sirup", "inaproc"]
    assert one(db_url, "SELECT max(cardinality(sumber)) FROM paket")[0] == 2


def test_td10_sumber_filter_combined_with_other_filters(client, store, db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO paket (id,tahun_anggaran,nama_paket,pagu,satuan_kerja,is_active,"
            "sumber) VALUES "
            "(1,2026,'Laptop sekolah',100,'DINAS A',true,'{sirup}'),"
            "(2,2026,'Laptop kantor',500,'DINAS A',true,'{sirup,inaproc}'),"
            "(3,2026,'Laptop desa',900,'DINAS B',true,'{inaproc}'),"
            "(4,2026,'Laptop batal',900,'DINAS B',false,'{inaproc}')"
        )

    def ids(**p) -> list[int]:
        r = client.get("/v1/paket", params={"tahun": 2026, **p}, headers=H)
        assert r.status_code == 200, r.text
        return sorted(x["id"] for x in r.json()["data"])

    assert ids(sumber="inaproc", paket="laptop") == [2, 3]
    assert ids(sumber="inaproc", pagu_min=600) == [3]
    assert ids(sumber="sirup", pagu_min=600) == []
    assert ids(sumber="inaproc", satuan_kerja="dinas b") == [3]
    assert ids(sumber="sirup", paket="laptop") == [1, 2]
    # INAPROC-only has no pemilihan: excluded when that filter is used (documented)
    assert ids(sumber="inaproc", pemilihan="2026-03") == []
    for bad in ("", "SIRUP", "sirup,inaproc", "{sirup}", "x' OR 1=1--"):
        assert client.get("/v1/paket", params={"sumber": bad}, headers=H).status_code == 422
    total = client.get("/v1/paket", params={"tahun": 2026}, headers=H).json()["total"]
    assert total == len(ids())


def test_td10_sumber_requires_api_key(client):
    assert client.get("/v1/paket", params={"sumber": "inaproc"}).status_code == 401
    assert client.get("/v1/paket/1").status_code == 401
