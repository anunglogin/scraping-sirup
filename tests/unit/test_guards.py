from __future__ import annotations

from sirup.scraper.store import deactivation_block_reason


def reason(**kw):
    base = {"complete": True, "records_total": 1000, "valid_received": 1000, "previous_total": 1000}
    base.update(kw)
    return deactivation_block_reason(**base)


def test_safe_when_complete_and_stable():
    assert reason() is None
    assert reason(previous_total=None) is None
    assert reason(valid_received=990) is None  # exactly 99%
    assert reason(records_total=800, valid_received=800) is None  # exactly -20%


def test_partial_run_blocks():
    assert "partial" in reason(complete=False)


def test_zero_or_missing_total_blocks():
    assert "0 records" in reason(records_total=0, valid_received=0)
    assert "0 records" in reason(records_total=None, valid_received=0)


def test_too_few_valid_rows_blocks():
    assert "99%" in reason(valid_received=989)


def test_big_drop_blocks():
    assert "dropped" in reason(records_total=799, valid_received=799)
