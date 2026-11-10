"""Response models (they also drive the Swagger / ReDoc documentation)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_PAKET_EXAMPLE: dict[str, Any] = {
    "id": 60522827,
    "tahun_anggaran": 2026,
    "nama_paket": "Penyediaan Pendidik dan Tenaga Kependidikan bagi Satuan Pendidikan SMA",
    "pagu": 6359532351,
    "id_jenis_pengadaan": 4,
    "jenis_pengadaan": "Jasa Lainnya",
    "id_metode": 8,
    "metode": "Pengadaan Langsung",
    "id_bulan": 12,
    "pemilihan": "December 2025",
    "id_kldi": "D69",
    "kldi": "Provinsi DKI Jakarta",
    "id_satker": 144073,
    "satuan_kerja": "SUKU DINAS PENDIDIKAN WILAYAH 1 KOTA - JAKUT",
    "ids_lokasi": "14751",
    "lokasi": "DKI Jakarta, Jakarta Utara (Kota)",
    "sumber_dana": "APBD",
    "is_pdn": True,
    "is_umk": True,
    "pds": False,
    "id_referensi": 334,
    "is_active": True,
    "first_seen_at": "2026-11-10T02:00:05+07:00",
    "last_seen_at": "2026-11-11T02:03:41+07:00",
    "updated_at": "2026-11-10T02:00:05+07:00",
}


class Paket(BaseModel):
    """Satu paket pengadaan dari SIRUP."""

    model_config = ConfigDict(json_schema_extra={"example": _PAKET_EXAMPLE})

    id: int = Field(description="ID paket di SIRUP (unik).")
    tahun_anggaran: int = Field(description="Tahun anggaran paket.")
    nama_paket: str = Field(description="Nama paket pekerjaan.")
    pagu: int = Field(description="Nilai pagu dalam rupiah (bilangan bulat).")
    id_jenis_pengadaan: int | None = Field(
        description="Kode jenis pengadaan: 1 Barang, 2 Pekerjaan Konstruksi, "
        "3 Jasa Konsultansi, 4 Jasa Lainnya."
    )
    jenis_pengadaan: str | None = Field(
        description="Nama jenis pengadaan. Kosong (null) bila SIRUP tidak mencantumkannya."
    )
    id_metode: int | None = Field(description="Kode metode pemilihan.")
    metode: str | None = Field(description="Metode pemilihan, mis. 'Tender', 'E-Purchasing'.")
    id_bulan: int | None = Field(description="Bulan rencana pemilihan penyedia (1–12).")
    pemilihan: str | None = Field(description="Teks waktu pemilihan dari SIRUP, mis. 'April 2026'.")
    id_kldi: str | None = Field(description="Kode instansi (K/L/D/I).")
    kldi: str | None = Field(description="Nama instansi (Kementerian/Lembaga/Daerah/Instansi).")
    id_satker: int | None = Field(description="Kode satuan kerja.")
    satuan_kerja: str | None = Field(description="Nama satuan kerja.")
    ids_lokasi: str | None = Field(description="Kode lokasi pekerjaan dari SIRUP.")
    lokasi: str | None = Field(description="Lokasi pekerjaan.")
    sumber_dana: str | None = Field(description="Sumber dana, mis. 'APBD', 'APBN'.")
    is_pdn: bool | None = Field(description="True bila mengutamakan Produk Dalam Negeri.")
    is_umk: bool | None = Field(description="True bila dicadangkan untuk Usaha Mikro dan Kecil.")
    pds: bool | None = Field(description="Penanda PDS dari SIRUP.")
    id_referensi: int | None = Field(description="ID referensi dari SIRUP.")
    is_active: bool = Field(
        description="False bila paket sudah tidak muncul lagi di SIRUP (mis. dibatalkan)."
    )
    first_seen_at: datetime = Field(description="Kapan paket ini pertama kali kami ambil.")
    last_seen_at: datetime = Field(description="Kapan paket ini terakhir terlihat di SIRUP.")
    updated_at: datetime = Field(description="Kapan isi paket terakhir berubah.")


class PaketDetail(Paket):
    """Paket lengkap, termasuk data asli dari SIRUP."""

    model_config = ConfigDict(
        json_schema_extra={"example": {**_PAKET_EXAMPLE, "raw": {"id": 60522827, "...": "..."}}}
    )

    raw: dict[str, Any] = Field(description="Baris asli persis seperti dikirim SIRUP.")


class PaketPage(BaseModel):
    """Hasil pencarian paket, per halaman."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 1234,
                "total_terbatas": False,
                "page": 1,
                "size": 20,
                "data": [_PAKET_EXAMPLE],
            }
        }
    )

    total: int = Field(
        description="Jumlah paket yang cocok dengan filter, dihitung sampai 10.000. Bila "
        "`total_terbatas` = true, jumlah sebenarnya **lebih dari 10.000**."
    )
    total_terbatas: bool = Field(
        description="`true` bila paket yang cocok lebih dari 10.000 (angka `total` "
        "berhenti di 10.000 supaya pencarian tetap cepat). Persempit filter untuk "
        "angka yang pasti."
    )
    page: int = Field(description="Nomor halaman yang dikirim.")
    size: int = Field(description="Jumlah paket per halaman.")
    data: list[Paket] = Field(description="Daftar paket di halaman ini.")


class ScrapeRun(BaseModel):
    """Catatan satu kali pengambilan data dari SIRUP."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 12,
                "tahun_anggaran": 2026,
                "status": "success",
                "started_at": "2026-11-11T02:00:00+07:00",
                "finished_at": "2026-11-11T02:31:12+07:00",
                "records_total": 3638893,
                "rows_received": 3638893,
                "rows_inserted": 1520,
                "rows_updated": 8311,
                "rows_invalid": 0,
                "rows_deactivated": 97,
                "checkpoint_start": 3638893,
                "error": None,
                "note": None,
            }
        }
    )

    id: int = Field(description="Nomor run.")
    tahun_anggaran: int = Field(description="Tahun anggaran yang diambil.")
    status: str = Field(description="running = sedang jalan, success = berhasil, failed = gagal.")
    started_at: datetime = Field(description="Waktu mulai.")
    finished_at: datetime | None = Field(description="Waktu selesai (kosong bila masih jalan).")
    records_total: int | None = Field(description="Jumlah paket menurut SIRUP.")
    rows_received: int = Field(description="Jumlah baris yang diterima dari SIRUP.")
    rows_inserted: int = Field(description="Paket baru yang masuk database.")
    rows_updated: int = Field(description="Paket lama yang isinya berubah.")
    rows_invalid: int = Field(description="Baris rusak yang dilewati.")
    rows_deactivated: int = Field(description="Paket yang ditandai hilang dari SIRUP.")
    checkpoint_start: int = Field(description="Posisi terakhir yang tersimpan (untuk lanjut).")
    error: str | None = Field(description="Pesan error bila gagal.")
    note: str | None = Field(
        description="Catatan, mis. alasan penandaan paket hilang dilewati demi keamanan data."
    )


class Health(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"status": "ok", "database": "ok"}})

    status: str = Field(description="'ok' bila layanan sehat.")
    database: str = Field(description="'ok' bila database bisa dihubungi.")


class ErrorMessage(BaseModel):
    detail: str = Field(description="Penjelasan singkat kesalahan.")
