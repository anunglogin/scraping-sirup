from __future__ import annotations

from sirup.scraper.models import COPY_COLUMNS, parse_rows, validate_page
from tests.conftest import REAL_ROW_WITHOUT_JENIS, make_row


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
        make_row(3, pagu=10**19),  # does not fit BIGINT
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


def test_real_row_without_jenis_pengadaan_is_valid():
    no_type = {k: v for k, v in REAL_ROW_WITHOUT_JENIS.items() if k != "idJenisPengadaan"}
    valid, invalid = parse_rows([REAL_ROW_WITHOUT_JENIS, no_type])
    assert invalid == 0
    assert (valid[0].row.id_jenis_pengadaan, valid[0].row.jenis_pengadaan) == (6, None)
    assert (valid[1].row.id_jenis_pengadaan, valid[1].row.jenis_pengadaan) == (None, None)


def test_validate_page_summarises_reasons():
    bad_pagu = make_row(1, pagu="abc")
    no_id = make_row(2)
    del no_id["id"]
    result = validate_page([bad_pagu, no_id, make_row(3, pagu="x"), "junk", make_row(4)])
    assert result.invalid == 4 and len(result.valid) == 1
    assert result.reasons[("pagu", "value_error")] == 2
    assert result.reasons[("id", "missing")] == 1
    assert result.reasons[("<row>", "not_an_object")] == 1
    assert result.sample_ids == [1001, None, 1003]  # at most 3
    assert "pagu:value_error=2" in result.describe()


def test_negative_pagu_from_sirup_is_kept():
    # Real rows from run 4 (11 Nov 2026), e.g. id 68897096 "Belanja Barang dan Jasa BLUD".
    valid, invalid = parse_rows([make_row(1, pagu=-58598261), make_row(2, pagu="-1413150")])
    assert invalid == 0
    assert [v.row.pagu for v in valid] == [-58598261, -1413150]
