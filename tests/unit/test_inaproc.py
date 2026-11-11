from __future__ import annotations

import csv
import json

import httpx
import pytest
import respx

from sirup.inaproc.client import (
    InaprocClient,
    InaprocFormatError,
    InaprocRefusedError,
)
from sirup.inaproc.config import DEFAULT_TABLE_URL, InaprocSettings
from sirup.inaproc.runner import run_collect

ROW_A = {
    "status_umk": "Ya",
    "cara_pengadaan_label": "Pengadaan Penyedia",
    "nama_instansi": "KAB. HULU SUNGAI UTARA",
    "produk_dalam_negeri": "Ya",
    "sumber_dana": "APBD",
    "kode_rup": "68336359",
    "tahun_anggaran": 2026,
    "total_nilai": 17550000,
    "cara_pengadaan": "Penyedia",
    "jenis_pengadaan": "Jasa Lainnya",
    "metode_pengadaan": "E-Purchasing",
    "nama_satker": "BADAN KESATUAN BANGSA DAN POLITIK - 8.01.0.00.0.00.01.0000",
    "nama_paket": "Belanja Makanan dan Minuman Rapat",
}
ROW_B = {**ROW_A, "kode_rup": "42390663", "nama_paket": '=HYPERLINK("x")'}


def body(rows, cursor="", has_more=False):
    return {
        "filters": {"tahun": "2026"},
        "tableRows": rows,
        "tableTotalRows": 4768992,
        "tablePagination": {"nextCursor": cursor, "hasMore": has_more, "limit": 20},
    }


def make_client(attempts: int = 3) -> InaprocClient:
    return InaprocClient(
        retry_attempts=attempts,
        backoff_min_seconds=0,
        backoff_max_seconds=0,
        clock=lambda: "1762862400000",
    )


@respx.mock
def test_sends_year_limit_and_time_header_only():
    route = respx.get(DEFAULT_TABLE_URL).mock(return_value=httpx.Response(200, json=body([ROW_A])))
    page = make_client().fetch_page(2026, 20)
    assert page.rows[0].kode_rup == "68336359"
    assert page.total_rows == 4768992
    req = route.calls.last.request
    assert dict(req.url.params) == {"tahun": "2026", "limit": "20"}
    assert req.headers["x-request-time"] == "1762862400000"
    assert "x-secret" not in req.headers
    assert "cookie" not in req.headers


@respx.mock
def test_cursor_is_sent_on_next_page():
    route = respx.get(DEFAULT_TABLE_URL).mock(return_value=httpx.Response(200, json=body([])))
    make_client().fetch_page(2026, 20, cursor="WzkyMjMz")
    assert route.calls.last.request.url.params["cursor"] == "WzkyMjMz"


@respx.mock
def test_403_stops_without_retry():
    route = respx.get(DEFAULT_TABLE_URL).mock(
        return_value=httpx.Response(403, json={"error": "Forbidden."})
    )
    with pytest.raises(InaprocRefusedError):
        make_client().fetch_page(2026, 20)
    assert route.call_count == 1


@respx.mock
def test_429_and_5xx_are_retried():
    route = respx.get(DEFAULT_TABLE_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(502),
            httpx.Response(200, json=body([ROW_A])),
        ]
    )
    make_client().fetch_page(2026, 20)
    assert route.call_count == 3


@respx.mock
def test_wrong_shape_is_format_error():
    respx.get(DEFAULT_TABLE_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    with pytest.raises(InaprocFormatError):
        make_client().fetch_page(2026, 20)


@respx.mock
def test_run_follows_cursor_writes_files_and_finishes(tmp_path):
    route = respx.get(DEFAULT_TABLE_URL).mock(
        side_effect=[
            httpx.Response(200, json=body([ROW_A], cursor="C1", has_more=True)),
            httpx.Response(200, json=body([ROW_B], cursor="", has_more=False)),
        ]
    )
    settings = InaprocSettings(tahun=2026, max_pages=0, output_dir=tmp_path)
    result = run_collect(settings, make_client(), sleep=lambda _: None)

    assert result.pages == 2 and result.rows == 2 and result.finished
    assert route.calls[1].request.url.params["cursor"] == "C1"
    lines = (tmp_path / "rup_2026.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["kode_rup"] for x in lines] == ["68336359", "42390663"]
    with (tmp_path / "rup_2026.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[1]["nama_paket"].startswith("'=")  # formula injection neutralised
    state = json.loads((tmp_path / "state_2026.json").read_text(encoding="utf-8"))
    assert state == {"next_cursor": "", "finished": True}


@respx.mock
def test_max_pages_stops_and_rerun_resumes(tmp_path):
    route = respx.get(DEFAULT_TABLE_URL).mock(
        side_effect=[
            httpx.Response(200, json=body([ROW_A], cursor="C1", has_more=True)),
            httpx.Response(200, json=body([ROW_B], cursor="", has_more=False)),
        ]
    )
    settings = InaprocSettings(tahun=2026, max_pages=1, output_dir=tmp_path)
    first = run_collect(settings, make_client(), sleep=lambda _: None)
    assert first.pages == 1 and not first.finished

    second = run_collect(settings, make_client(), sleep=lambda _: None)
    assert second.finished
    assert route.calls[1].request.url.params["cursor"] == "C1"
    with (tmp_path / "rup_2026.csv").open(encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 2  # header written once


@respx.mock
def test_sends_secret_only_when_configured():
    route = respx.get(DEFAULT_TABLE_URL).mock(return_value=httpx.Response(200, json=body([ROW_A])))
    client = InaprocClient(x_secret="dGVzdA==", clock=lambda: "1700000000000")
    client.fetch_page(2026, 100)
    req = route.calls.last.request
    assert req.headers["x-secret"] == "dGVzdA=="
    assert req.headers["x-request-time"] == "1700000000000"


def test_secret_is_read_from_env_and_hidden(monkeypatch):
    monkeypatch.setenv("INAPROC_X_SECRET", "dGVzdA==")
    monkeypatch.setenv("INAPROC_REQUEST_TIME", "1700000000000")
    settings = InaprocSettings()
    assert settings.x_secret.get_secret_value() == "dGVzdA=="
    assert "dGVzdA==" not in repr(settings)
