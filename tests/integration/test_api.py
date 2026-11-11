"""API against a real PostgreSQL (TEST_DATABASE_URL)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sirup.api.config import ApiSettings
from sirup.api.main import create_app

KEY = "test-key-" + "x" * 30
KEY2 = "second-key-" + "y" * 30
H = {"X-API-Key": KEY}
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip

PAKET = [
    # id, nama, pagu, satker, bulan, active, tahun
    (1, "Pengadaan Laptop Kantor", 150_000_000, "DINAS PENDIDIKAN KOTA BANDUNG", 3, True, 2026),
    (2, "Belanja LAPTOP Sekolah", 900_000_000, "Dinas Pendidikan Provinsi", 4, True, 2026),
    (3, "Pembangunan Puskesmas", 2_500_000_000, "DINAS KESEHATAN KABUPATEN BOGOR", 4, True, 2026),
    (4, "Obat 100% generik", 50_000_000, "Puskesmas Cibinong", 5, True, 2026),
    (5, "Obat 100 persen herbal", 60_000_000, "Puskesmas Cibinong", 5, True, 2026),
    (6, "Paket_khusus alat", 70_000_000, "RSUD Kota", 6, True, 2026),
    (7, "Paket khusus bahan", 80_000_000, "RSUD Kota", 6, True, 2026),
    (8, "Laptop yang dibatalkan", 300_000_000, "DINAS PENDIDIKAN KOTA BANDUNG", 3, False, 2026),
    (9, "Laptop tahun lalu", 200_000_000, "DINAS PENDIDIKAN KOTA BANDUNG", 3, True, 2025),
]


@pytest.fixture
def seeded(db) -> None:
    with db.cursor() as cur:
        for i, (pid, nama, pagu, satker, bulan, active, tahun) in enumerate(PAKET):
            cur.execute(
                """
                INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu, id_jenis_pengadaan,
                    jenis_pengadaan, id_bulan, pemilihan, satuan_kerja, is_active,
                    first_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        now() - make_interval(hours => %s))
                """,
                (
                    pid,
                    tahun,
                    nama,
                    pagu,
                    None if pid == 7 else 1,  # SIRUP has packages without a type
                    None if pid == 7 else "Barang",
                    bulan,
                    # package 5 is planned for May 2025, still in tahun anggaran 2026
                    f"{MONTHS[bulan - 1]} {2025 if pid == 5 else 2026}",
                    satker,
                    active,
                    100 - i,
                ),
            )
            cur.execute(
                "INSERT INTO paket_raw (id, raw) VALUES (%s, %s)", (pid, json.dumps({"id": pid}))
            )
        cur.execute(
            "INSERT INTO scrape_runs (tahun_anggaran, status, records_total) "
            "VALUES (2026, 'success', 9), (2026, 'failed', NULL)"
        )


@pytest.fixture
def client(db_url) -> Iterator[TestClient]:
    app = create_app(ApiSettings(database_url=db_url, api_keys=f"{KEY},{KEY2}"))
    with TestClient(app) as c:
        yield c


def ids(resp) -> list[int]:
    assert resp.status_code == 200, resp.text
    return [p["id"] for p in resp.json()["data"]]


def search(client: TestClient, **params: Any):
    return client.get("/v1/paket", params={"tahun": 2026, **params}, headers=H)


# ------------------------------------------------------------------- auth
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": KEY + "x"}])
def test_requires_valid_key(client, seeded, headers):
    for path in ("/v1/paket", "/v1/paket/1", "/v1/scrape-runs"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 401
        assert "Kunci API" in resp.json()["detail"]


def test_any_configured_key_works(client, seeded):
    assert client.get("/v1/paket", headers={"X-API-Key": KEY2}).status_code == 200


def test_health_is_public(client, db):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_db_down():
    app = create_app(
        ApiSettings(database_url="postgresql://nobody:x@127.0.0.1:1/none", api_keys=KEY)
    )
    with TestClient(app) as c:
        resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Database tidak bisa dihubungi."}


# ---------------------------------------------------------------- filters
def test_filter_paket_case_insensitive(client, seeded):
    assert sorted(ids(search(client, paket="laptop"))) == [1, 2]
    assert sorted(ids(search(client, paket="  LAPTOP  "))) == [1, 2]


def test_filter_satuan_kerja(client, seeded):
    assert sorted(ids(search(client, satuan_kerja="pendidikan"))) == [1, 2]
    assert sorted(ids(search(client, satuan_kerja="puskesmas"))) == [4, 5]


def test_filter_pemilihan_month_and_year(client, seeded):
    assert sorted(ids(search(client, pemilihan="2026-04"))) == [2, 3]
    assert sorted(ids(search(client, pemilihan=["2026-03", "2026-04"]))) == [1, 2, 3]
    # same month, different year: kept apart (owner decision 11 Nov 2026)
    assert ids(search(client, pemilihan="2026-05")) == [4]
    assert ids(search(client, pemilihan="2025-05")) == [5]
    assert sorted(ids(search(client, pemilihan=["2025-05", "2026-05"]))) == [4, 5]
    assert ids(search(client, pemilihan=" 2026-06 ")) == [7, 6]


def test_pemilihan_tahun_returned(client, seeded):
    body = client.get("/v1/paket/5", headers=H).json()
    assert (body["pemilihan"], body["pemilihan_tahun"], body["id_bulan"]) == ("May 2025", 2025, 5)


def test_filter_pagu_range(client, seeded):
    assert sorted(ids(search(client, pagu_min=100_000_000))) == [1, 2, 3]
    assert sorted(ids(search(client, pagu_max=60_000_000))) == [4, 5]
    assert sorted(ids(search(client, pagu_min=60_000_000, pagu_max=150_000_000))) == [1, 5, 6, 7]
    assert sorted(ids(search(client, pagu_min=150_000_000, pagu_max=150_000_000))) == [1]


def test_filters_combined(client, seeded):
    resp = search(
        client,
        paket="laptop",
        satuan_kerja="pendidikan",
        pemilihan=["2026-03", "2026-04"],
        pagu_min=500_000_000,
    )
    assert ids(resp) == [2]
    assert ids(search(client, paket="laptop", pemilihan="2026-03")) == [1]
    assert ids(search(client, satuan_kerja="kesehatan", pagu_max=1)) == []


def test_like_wildcards_are_literal(client, seeded):
    assert ids(search(client, paket="100%")) == [4]
    assert ids(search(client, paket="Paket_khusus")) == [6]
    assert ids(search(client, paket="ket_kh")) == [6]


def test_default_aktif_hides_inactive(client, seeded):
    assert 8 not in ids(search(client, paket="laptop"))
    assert sorted(ids(search(client, paket="laptop", aktif=False))) == [1, 2, 8]


def test_tahun_filter(client, seeded):
    assert ids(client.get("/v1/paket", params={"tahun": 2025}, headers=H)) == [9]


def test_sort_orders(client, seeded):
    assert ids(search(client, sort="pagu_desc", size=3)) == [3, 2, 1]
    assert ids(search(client, sort="pagu_asc", size=3)) == [4, 5, 6]
    # first_seen_at was seeded decreasing with id, so newest = highest id
    assert ids(search(client, sort="terbaru", size=3)) == [7, 6, 5]


def test_pagination(client, seeded):
    p1 = search(client, sort="pagu_asc", size=3, page=1).json()
    p3 = search(client, sort="pagu_asc", size=3, page=3).json()
    assert p1["total"] == 7 and p1["page"] == 1 and p1["size"] == 3
    assert [p["id"] for p in p3["data"]] == [3]
    assert search(client, size=3, page=4).json()["data"] == []


def test_default_page_size_and_fields(client, seeded):
    body = search(client).json()
    assert body["size"] == 20 and body["page"] == 1 and body["total"] == 7
    assert "raw" not in body["data"][0]
    assert {"nama_paket", "pagu", "satuan_kerja", "id_bulan", "is_active", "sumber"} <= set(
        body["data"][0]
    )
    assert body["data"][0]["sumber"] == ["sirup"]


@pytest.mark.parametrize(
    "params",
    [
        {"pagu_min": 10, "pagu_max": 5},
        {"pemilihan": "2026-13"},
        {"pemilihan": "2026-00"},
        {"pemilihan": "1999-01"},
        {"pemilihan": "2101-01"},
        {"pemilihan": "2026-3"},
        {"pemilihan": "03-2026"},
        {"pemilihan": "2026/03"},
        {"pemilihan": "Maret 2026"},
        {"pemilihan": ["2026-01"] * 25},
        {"paket": "ab"},
        {"paket": "%%%"},
        {"paket": "___"},
        {"paket": "..."},
        {"paket": "a.."},
        {"paket": "e%%"},
        {"paket": "ab.cd"},
        {"paket": "\U0001f642\U0001f642\U0001f642"},
        {"paket": "abc\x00def"},
        {"paket": "lap\ttop kantor"},
        {"satuan_kerja": "..."},
        {"satuan_kerja": "di\x7fnas"},
        {"paket": "  ab  "},
        {"paket": "x" * 101},
        {"satuan_kerja": "ab"},
        {"pagu_min": -1},
        {"size": 101},
        {"size": 0},
        {"page": 0},
        {"page": 101, "size": 100},
        {"page": 501, "size": 20},
        {"sort": "id; DROP TABLE paket"},
        {"tahun": 1999},
        {"sumber": "lkpp"},
    ],
)
def test_validation_errors(client, seeded, params):
    resp = search(client, **params)
    assert resp.status_code == 422, resp.text
    assert isinstance(resp.json()["detail"], list)


def test_deep_paging_message(client, seeded):
    resp = search(client, page=101, size=100)
    assert "Persempit filter" in resp.json()["detail"][0]["msg"]


def test_largest_allowed_window(client, seeded):
    assert search(client, page=100, size=100).status_code == 200


# ----------------------------------------------------------- other routes
def test_detail_and_404(client, seeded):
    resp = client.get("/v1/paket/3", headers=H)
    assert resp.status_code == 200
    assert resp.json()["nama_paket"] == "Pembangunan Puskesmas"
    assert resp.json()["raw"] == {"id": 3}
    missing = client.get("/v1/paket/999999", headers=H)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Paket tidak ditemukan."}
    assert client.get("/v1/paket/abc", headers=H).status_code == 422


def test_scrape_runs(client, seeded):
    resp = client.get("/v1/scrape-runs", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert [r["status"] for r in body] == ["failed", "success"]
    assert body[1]["records_total"] == 9
    assert len(client.get("/v1/scrape-runs?limit=1", headers=H).json()) == 1
    assert client.get("/v1/scrape-runs?limit=101", headers=H).status_code == 422


# ----------------------------------------------------------------- docs
def test_openapi_documents_security_and_parameters(client):
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert schemes["KunciAPI"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Kunci API dari admin. Kirim di header `X-API-Key`.",
    }
    op = spec["paths"]["/v1/paket"]["get"]
    assert op["security"] == [{"KunciAPI": []}]
    params = {p["name"]: p for p in op["parameters"]}
    expected = {
        "paket", "satuan_kerja", "pemilihan", "pagu_min", "pagu_max",
        "sumber", "tahun", "aktif", "sort", "page", "size",
    }  # fmt: skip
    assert set(params) == expected
    for name, p in params.items():
        assert p.get("description"), f"{name} has no description"
        assert p.get("examples") or p["schema"].get("examples") or p.get("example"), name
    for code in ("200", "401", "422", "503"):
        assert code in op["responses"], code
    assert "404" in spec["paths"]["/v1/paket/{id}"]["get"]["responses"]
    assert "security" not in spec["paths"]["/health"]["get"]
    paket_schema = spec["components"]["schemas"]["Paket"]
    assert all(f.get("description") for f in paket_schema["properties"].values())
    assert "example" in paket_schema


def test_swagger_and_redoc_pages(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_package_without_jenis_is_served(client, seeded):
    body = client.get("/v1/paket/7", headers=H).json()
    assert body["id_jenis_pengadaan"] is None and body["jenis_pengadaan"] is None


def test_exact_total_below_cap(client, seeded):
    body = search(client, paket="laptop").json()
    assert body["total"] == 2 and body["total_terbatas"] is False


@pytest.fixture
def many(db) -> None:
    """10,050 packages in 2027 to exercise the 10,000 count cap."""
    db.execute(
        """
        INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu, id_bulan, satuan_kerja)
        SELECT 100000 + g, 2027,
               CASE WHEN g <= 30 THEN 'Langka ' ELSE 'Umum ' END || g,
               g * 1000, 1 + g % 12, 'Satker ' || (g % 7)
        FROM generate_series(1, 10050) AS g
        """
    )


def test_total_is_capped_at_10000(client, many):
    body = client.get("/v1/paket", params={"tahun": 2027, "size": 5}, headers=H).json()
    assert body["total"] == 10000 and body["total_terbatas"] is True
    assert [p["id"] for p in body["data"]] == [110050, 110049, 110048, 110047, 110046]
    narrow = client.get("/v1/paket", params={"tahun": 2027, "paket": "langka"}, headers=H)
    assert narrow.json()["total"] == 30 and narrow.json()["total_terbatas"] is False


def test_last_reachable_page_when_capped(client, many):
    params = {"tahun": 2027, "page": 100, "size": 100, "sort": "pagu_asc"}
    body = client.get("/v1/paket", params=params, headers=H).json()
    assert body["total_terbatas"] is True
    assert body["data"][0]["id"] == 100000 + 9901 and len(body["data"]) == 100


def test_openapi_documents_total_terbatas(client):
    props = client.get("/openapi.json").json()["components"]["schemas"]["PaketPage"]["properties"]
    assert "lebih dari 10.000" in props["total_terbatas"]["description"]


# ------------------------------------------------------- overload (F-01)
@pytest.mark.parametrize("bad", ["%%%", "a..", "\U0001f642\U0001f642\U0001f642", "abc\x00"])
def test_untrigrammable_keywords_rejected_fast(client, seeded, bad):
    started = time.perf_counter()
    resp = search(client, paket=bad)
    assert resp.status_code == 422
    assert time.perf_counter() - started < 1
    msg = resp.json()["detail"][0]["msg"]
    assert "3 huruf/angka" in msg or "karakter kontrol" in msg


def test_search_slots_full_returns_429_but_health_and_detail_work(db_url, seeded):
    settings = ApiSettings(
        database_url=db_url, api_keys=KEY, search_max_concurrent=1, search_queue_wait_seconds=0.2
    )
    app = create_app(settings)
    with TestClient(app) as c:
        slots = app.state.search_slots
        assert slots.acquire(timeout=1)  # simulate one long-running search
        try:
            resp = c.get("/v1/paket", params={"tahun": 2026}, headers=H)
            assert resp.status_code == 429
            assert resp.headers["Retry-After"] == "2"
            assert "Terlalu banyak" in resp.json()["detail"]
            assert c.get("/health").status_code == 200
            assert c.get("/v1/paket/1", headers=H).status_code == 200
        finally:
            slots.release()
        assert c.get("/v1/paket", params={"tahun": 2026}, headers=H).status_code == 200


def test_concurrent_slow_searches_do_not_starve_pool(db_url, seeded, monkeypatch):
    """Real threads + real slow SQL: excess searches get 429, /health stays up."""
    from psycopg import sql

    import sirup.api.main as main_mod

    def slow_count(f, cap):
        return sql.SQL("SELECT count(*) FROM (SELECT pg_sleep(0.8)) AS s"), []

    monkeypatch.setattr(main_mod, "count_query", slow_count)
    settings = ApiSettings(
        database_url=db_url,
        api_keys=KEY,
        db_pool_max_size=4,
        search_queue_wait_seconds=0.1,
    )  # 4 - 2 = 2 search slots
    app = create_app(settings)
    codes: list[int] = []
    with TestClient(app) as c:

        def one() -> None:
            codes.append(c.get("/v1/paket", params={"tahun": 2026}, headers=H).status_code)

        threads = [threading.Thread(target=one) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        health_started = time.perf_counter()
        health = c.get("/health")
        health_secs = time.perf_counter() - health_started
        detail = c.get("/v1/paket/3", headers=H)
        for t in threads:
            t.join()
    assert health.status_code == 200 and health_secs < 1
    assert detail.status_code == 200
    assert codes.count(200) >= 2 and codes.count(429) >= 1
    assert set(codes) <= {200, 429}


def test_statement_timeout_default_is_3s():
    s = ApiSettings(database_url="postgresql://x@y/z", api_keys="k" * 24)
    assert s.db_statement_timeout_ms == 3000
    assert s.search_slots() == 8


def test_filter_sumber_and_inaproc_only_detail(client, seeded, db):
    db.execute("UPDATE paket SET sumber = '{sirup,inaproc}' WHERE id = 3")
    db.execute(
        "INSERT INTO paket (id, tahun_anggaran, nama_paket, pagu, sumber) "
        "VALUES (10, 2026, 'Paket hanya INAPROC', 400000000, '{inaproc}')"
    )
    db.execute(
        "INSERT INTO inaproc_rup (kode_rup, tahun_anggaran, raw) VALUES (10, 2026, %s)",
        (json.dumps({"kode_rup": "10"}),),
    )
    assert ids(search(client, sumber="inaproc")) == [3, 10]
    assert 10 not in ids(search(client, sumber="sirup"))
    assert 3 in ids(search(client, sumber="sirup"))
    assert 10 in ids(search(client))
    detail = client.get("/v1/paket/10", headers=H).json()
    assert detail["sumber"] == ["inaproc"]
    assert detail["raw"] == {"kode_rup": "10"}
