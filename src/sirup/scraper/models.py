"""Validation of rows coming from SIRUP (untrusted external input)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Bounds of the PostgreSQL column types; an out-of-range value would abort the
# whole page COPY, so such rows are rejected (counted invalid) here instead.
BIGINT_MAX = 9_223_372_036_854_775_807
INT_MIN, INT_MAX = -2_147_483_648, 2_147_483_647
SMALLINT_MIN, SMALLINT_MAX = -32_768, 32_767


def _strip_nul(value: Any) -> Any:
    """PostgreSQL text/jsonb cannot hold NUL characters; drop them recursively."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


class SirupRow(BaseModel):
    """One package row from the DataTables endpoint. Unknown fields are kept."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: int = Field(gt=0, le=BIGINT_MAX)
    paket: str
    pagu: int = Field(ge=0, le=BIGINT_MAX)
    id_jenis_pengadaan: int = Field(alias="idJenisPengadaan", ge=SMALLINT_MIN, le=SMALLINT_MAX)
    jenis_pengadaan: str = Field(alias="jenisPengadaan")
    id_metode: int | None = Field(default=None, alias="idMetode", ge=SMALLINT_MIN, le=SMALLINT_MAX)
    metode: str | None = None
    id_bulan: int | None = Field(default=None, alias="idBulan", ge=SMALLINT_MIN, le=SMALLINT_MAX)
    pemilihan: str | None = None
    id_kldi: str | None = Field(default=None, alias="idKldi")
    kldi: str | None = None
    id_satker: int | None = Field(default=None, alias="idSatker", ge=-BIGINT_MAX, le=BIGINT_MAX)
    satuan_kerja: str | None = Field(default=None, alias="satuanKerja")
    ids_lokasi: str | None = Field(default=None, alias="idsLokasi")
    lokasi: str | None = None
    sumber_dana: str | None = Field(default=None, alias="sumberDana")
    is_pdn: bool | None = Field(default=None, alias="isPDN")
    is_umk: bool | None = Field(default=None, alias="isUMK")
    pds: bool | None = None
    id_referensi: int | None = Field(default=None, ge=INT_MIN, le=INT_MAX)

    @field_validator("paket", mode="before")
    @classmethod
    def _strip_paket(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("pagu", mode="before")
    @classmethod
    def _round_pagu(cls, value: Any) -> Any:
        """SIRUP sometimes sends decimals; store whole rupiah (half-up)."""
        if isinstance(value, bool):
            raise ValueError("pagu must be a number")
        if isinstance(value, float | str | Decimal):
            try:
                dec = Decimal(str(value).strip())
            except InvalidOperation as exc:
                raise ValueError("pagu is not a number") from exc
            if not dec.is_finite():
                raise ValueError("pagu is not finite")
            return int(dec.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return value

    @field_validator("id_kldi", "ids_lokasi", mode="before")
    @classmethod
    def _text_ids(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


# Column order used by COPY in the store. Keep in sync with SirupRow.to_copy_row.
COPY_COLUMNS: tuple[str, ...] = (
    "id",
    "tahun_anggaran",
    "nama_paket",
    "pagu",
    "id_jenis_pengadaan",
    "jenis_pengadaan",
    "id_metode",
    "metode",
    "id_bulan",
    "pemilihan",
    "id_kldi",
    "kldi",
    "id_satker",
    "satuan_kerja",
    "ids_lokasi",
    "lokasi",
    "sumber_dana",
    "is_pdn",
    "is_umk",
    "pds",
    "id_referensi",
    "raw",
)


class ValidRow(BaseModel):
    row: SirupRow
    raw: dict[str, Any]

    def to_copy_values(self, tahun_anggaran: int) -> tuple[Any, ...]:
        r = self.row
        return (
            r.id,
            tahun_anggaran,
            r.paket,
            r.pagu,
            r.id_jenis_pengadaan,
            r.jenis_pengadaan,
            r.id_metode,
            r.metode,
            r.id_bulan,
            r.pemilihan,
            r.id_kldi,
            r.kldi,
            r.id_satker,
            r.satuan_kerja,
            r.ids_lokasi,
            r.lokasi,
            r.sumber_dana,
            r.is_pdn,
            r.is_umk,
            r.pds,
            r.id_referensi,
            self.raw,
        )


def parse_rows(items: list[Any]) -> tuple[list[ValidRow], int]:
    """Validate a page of rows. Returns (valid rows, number of invalid rows)."""
    valid: list[ValidRow] = []
    invalid = 0
    for item in items:
        if not isinstance(item, dict):
            invalid += 1
            continue
        clean = _strip_nul(item)
        try:
            row = SirupRow.model_validate(clean)
        except ValidationError:
            invalid += 1
            continue
        valid.append(ValidRow(row=row, raw=clean))
    return valid, invalid
