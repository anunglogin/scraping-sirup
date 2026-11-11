from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from pydantic import ValidationError

from sirup.api.config import ApiSettings
from sirup.scraper.config import ScraperSettings
from sirup.timeutil import JAKARTA, next_run_at, parse_hhmm, seconds_until

TWO_AM = time(2, 0)


def test_next_run_later_same_day():
    now = datetime(2026, 11, 10, 1, 30, tzinfo=JAKARTA)
    assert next_run_at(now, TWO_AM) == datetime(2026, 11, 10, 2, 0, tzinfo=JAKARTA)
    assert seconds_until(now, TWO_AM) == 30 * 60


def test_next_run_tomorrow_when_time_passed_or_equal():
    assert next_run_at(datetime(2026, 11, 10, 2, 0, tzinfo=JAKARTA), TWO_AM) == datetime(
        2026, 11, 11, 2, 0, tzinfo=JAKARTA
    )
    assert next_run_at(datetime(2026, 12, 31, 23, 0, tzinfo=JAKARTA), TWO_AM) == datetime(
        2027, 1, 1, 2, 0, tzinfo=JAKARTA
    )


def test_next_run_uses_jakarta_even_for_utc_input():
    # 18:30 UTC == 01:30 WIB next day
    now = datetime(2026, 11, 10, 18, 30, tzinfo=UTC)
    assert next_run_at(now, TWO_AM) == datetime(2026, 11, 11, 2, 0, tzinfo=JAKARTA)
    assert seconds_until(now, TWO_AM) == 30 * 60


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        next_run_at(datetime(2026, 1, 1), TWO_AM)


@pytest.mark.parametrize("bad", ["2", "24:00", "02:60", "aa:bb", "2:00:00", "٠٢:٠٠"])
def test_parse_hhmm_rejects(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)


def test_parse_hhmm_ok():
    assert parse_hhmm(" 02:05 ") == time(2, 5)


def test_blank_env_means_current_year(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@y/z")
    monkeypatch.setenv("TAHUN_ANGGARAN", "")
    monkeypatch.setenv("SCRAPE_MAX_PAGES", "")
    s = ScraperSettings()
    assert s.tahun_anggaran is None and s.scrape_max_pages is None
    assert s.resolved_year() == datetime.now(JAKARTA).year
    assert s.scrape_page_size == 5000 and s.scrape_delay_seconds == 2


def test_year_override_and_page_size_bounds(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@y/z")
    monkeypatch.setenv("TAHUN_ANGGARAN", "2025")
    assert ScraperSettings().resolved_year() == 2025
    monkeypatch.setenv("SCRAPE_PAGE_SIZE", "10001")
    with pytest.raises(ValidationError):
        ScraperSettings()
    monkeypatch.setenv("SCRAPE_PAGE_SIZE", "5000")
    monkeypatch.setenv("SCRAPE_TIME", "25:00")
    with pytest.raises(ValidationError):
        ScraperSettings()


def test_api_keys_must_be_long():
    with pytest.raises(ValidationError):
        ApiSettings(database_url="postgresql://x@y/z", api_keys="short-key")
    with pytest.raises(ValidationError):
        ApiSettings(database_url="postgresql://x@y/z", api_keys=" , ")
    s = ApiSettings(database_url="postgresql://x@y/z", api_keys=f" {'a' * 24} , {'b' * 30} ")
    assert s.key_list() == ["a" * 24, "b" * 30]


def test_config_errors_do_not_echo_secrets(monkeypatch):
    secret = "FAKEKEY-short-123,another-fake-key-also"
    with pytest.raises(ValidationError) as exc:
        ApiSettings(database_url="postgresql://u:topsecretpw@h/db", api_keys=secret)
    text = str(exc.value)
    assert "FAKEKEY" not in text and "another-fake" not in text
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:topsecretpw@h/db")
    monkeypatch.setenv("SCRAPE_PAGE_SIZE", "99999")
    with pytest.raises(ValidationError) as exc2:
        ScraperSettings()
    assert "topsecretpw" not in str(exc2.value) and "99999" not in str(exc2.value)
