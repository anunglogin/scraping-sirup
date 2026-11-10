"""API against a real PostgreSQL (TEST_DATABASE_URL)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sirup.api.config import ApiSettings
from sirup.api.main import create_app

KEY = "test-key-" + "x" * 30
KEY2 = "second-key-" + "y" * 30
H = {"X-API-Key": KEY}

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
                    jenis_pengadaan, id_bulan, satuan_kerja, raw, is_active, first_seen_at)
                VALUES (%s, %s, %s, %s, 1, 'Barang', %s, %s, %s, %s,
                        now() - make_interval(hours => %s))
                """,
                (pid, tahun, nama, pagu, bulan, satker, json.dumps({"id": pid}), active, 100 - i),
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


def test_filter_pemilihan_bulan_repeatable(client, seeded):
    assert sorted(ids(search(client, pemilihan_bulan=4))) == [2, 3]
    assert sorted(ids(search(client, pemilihan_bulan=[3, 4]))) == [1, 2, 3]


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
        pemilihan_bulan=[3, 4],
        pagu_min=500_000_000,
    )
    assert ids(resp) == [2]
    assert ids(search(client, paket="laptop", pemilihan_bulan=3)) == [1]
    assert ids(search(client, satuan_kerja="kesehatan", pagu_max=1)) == []


def test_like_wildcards_are_literal(client, seeded):
    assert ids(search(client, paket="100%")) == [4]
    assert ids(search(client, paket="Paket_khusus")) == [6]
    assert ids(search(client, paket="%%%")) == []
    assert ids(search(client, paket="___")) == []
    assert ids(search(client, paket="\\\\\\")) == []


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
    assert {"nama_paket", "pagu", "satuan_kerja", "id_bulan", "is_active"} <= set(body["data"][0])


@pytest.mark.parametrize(
    "params",
    [
        {"pagu_min": 10, "pagu_max": 5},
        {"pemilihan_bulan": 13},
        {"pemilihan_bulan": 0},
        {"paket": "ab"},
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
        "paket", "satuan_kerja", "pemilihan_bulan", "pagu_min", "pagu_max",
        "tahun", "aktif", "sort", "page", "size",
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
