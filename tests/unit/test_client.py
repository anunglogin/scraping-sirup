from __future__ import annotations

import httpx
import pytest
import respx

from sirup.scraper.client import (
    SirupClient,
    SirupFormatError,
    SirupRejectedError,
    SirupUnavailableError,
)
from sirup.scraper.config import DEFAULT_BASE_URL, DEFAULT_USER_AGENT

OK_BODY = {"recordsTotal": 2, "recordsFiltered": 2, "draw": 1, "data": [{"id": 1}, {"id": 2}]}


def make_client(attempts: int = 3) -> SirupClient:
    return SirupClient(retry_attempts=attempts, backoff_min_seconds=0, backoff_max_seconds=0)


@respx.mock
def test_sends_mandatory_order_params_and_user_agent():
    route = respx.get(DEFAULT_BASE_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    page = make_client().fetch_page(2026, 5000, 5000)
    assert page.records_total == 2 and len(page.data) == 2
    req = route.calls.last.request
    params = dict(req.url.params)
    assert params["columns[11][data]"] == "id"
    assert params["order[0][column]"] == "11"
    assert params["order[0][dir]"] == "ASC"
    assert params["tahunAnggaran"] == "2026"
    assert params["jenisPengadaan"] == ""
    assert params["start"] == "5000" and params["length"] == "5000"
    assert params["search[value]"] == "" and params["draw"] == "1"
    assert req.headers["User-Agent"] == DEFAULT_USER_AGENT


@respx.mock
def test_empty_body_is_retried():
    route = respx.get(DEFAULT_BASE_URL).mock(
        side_effect=[httpx.Response(200, content=b""), httpx.Response(200, json=OK_BODY)]
    )
    page = make_client().fetch_page(2026, 0, 10)
    assert page.records_total == 2
    assert route.call_count == 2


@respx.mock
def test_invalid_json_and_429_are_retried():
    route = respx.get(DEFAULT_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, content=b"<html>oops"),
            httpx.Response(429),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    make_client().fetch_page(2026, 0, 10)
    assert route.call_count == 3


@respx.mock
def test_transport_error_is_retried():
    route = respx.get(DEFAULT_BASE_URL).mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=OK_BODY)]
    )
    make_client().fetch_page(2026, 0, 10)
    assert route.call_count == 2


@respx.mock
def test_5xx_retried_then_gives_up():
    route = respx.get(DEFAULT_BASE_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(SirupUnavailableError):
        make_client(attempts=4).fetch_page(2026, 0, 10)
    assert route.call_count == 4


@respx.mock
def test_403_not_retried():
    route = respx.get(DEFAULT_BASE_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(SirupRejectedError):
        make_client().fetch_page(2026, 0, 10)
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    "body",
    [{"data": []}, {"recordsTotal": "many", "data": []}, {"recordsTotal": 1, "data": {}}, [1, 2]],
)
def test_wrong_shape_raises_format_error_without_retry(body):
    route = respx.get(DEFAULT_BASE_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(SirupFormatError):
        make_client().fetch_page(2026, 0, 10)
    assert route.call_count == 1


@respx.mock
def test_empty_page_before_records_total_is_retried_then_fails():
    body = {"recordsTotal": 10000, "recordsFiltered": 10000, "draw": 1, "data": []}
    route = respx.get(DEFAULT_BASE_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(SirupUnavailableError, match="empty page at start=5000"):
        make_client(attempts=3).fetch_page(2026, 5000, 5000)
    assert route.call_count == 3


@respx.mock
def test_empty_page_at_or_after_end_is_normal():
    body = {"recordsTotal": 5000, "recordsFiltered": 5000, "draw": 1, "data": []}
    route = respx.get(DEFAULT_BASE_URL).mock(return_value=httpx.Response(200, json=body))
    assert make_client().fetch_page(2026, 5000, 5000).data == []
    assert route.call_count == 1
