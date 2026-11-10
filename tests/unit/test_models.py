from __future__ import annotations

from sirup.scraper.models import COPY_COLUMNS, parse_rows
from tests.conftest import make_row


def test_sample_rows_from_sirup_parse(sample_rows):
    valid, invalid = parse_rows(sample_rows)
    assert invalid == 0
    assert [v.row.id for v in valid] == [60522826, 60522827, 60522828]
    first = valid[0].row
    assert first.paket == "Pelaksanaan Penatausahaan dan Pengujian/Verifikasi Keuangan SKPD"
    assert first.pagu == 4000000
    assert first.id_bulan == 1
    assert first.id_kldi == "D69"
    assert first.satuan_kerja == "SUKU DINAS PENDIDIKAN WILAYAH 1 KOTA - JAKUT"
    assert first.is_umk is True and first.pds is False


def test_raw_keeps_original_row_including_unknown_fields(sample_rows):
    valid, _ = parse_rows(sample_rows)
    assert valid[0].raw == sample_rows[0]
    assert valid[0].raw["idlokasi"] == 0  # not a column, still preserved


def test_copy_values_match_columns(sample_rows):
    valid, _ = parse_rows(sample_rows)
    values = valid[0].to_copy_values(2026)
    assert len(values) == len(COPY_COLUMNS)
    assert dict(zip(COPY_COLUMNS, values, strict=True))["tahun_anggaran"] == 2026


def test_decimal_pagu_rounded_half_up():
    rows = [make_row(1, pagu=1234.5), make_row(2, pagu="99.49"), make_row(3, pagu=7.0)]
    valid, invalid = parse_rows(rows)
    assert invalid == 0
    assert [v.row.pagu for v in valid] == [1235, 99, 7]


def test_invalid_rows_are_counted_not_fatal():
    missing_id = make_row(1)
    del missing_id["id"]
    rows = [
        missing_id,
        make_row(2, pagu="abc"),
        make_row(3, pagu=-5),
        {**make_row(4), "idMetode": 99999},  # does not fit SMALLINT
        {**make_row(5), "paket": None},
        "not a dict",
        make_row(6),
    ]
    valid, invalid = parse_rows(rows)
    assert invalid == 6
    assert [v.row.id for v in valid] == [1006]


def test_nul_characters_removed():
    valid, invalid = parse_rows([make_row(1, paket="Laptop\x00 kantor")])
    assert invalid == 0
    assert valid[0].row.paket == "Laptop kantor"
    assert "\x00" not in valid[0].raw["paket"]
