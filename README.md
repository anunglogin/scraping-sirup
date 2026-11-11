# Scraping SIRUP — Pengumpul Paket Pengadaan + API Penyaring

Sistem ini mengambil **semua paket pengadaan penyedia** dari
[SIRUP](https://sirup.inaproc.id) (Rencana Umum Pengadaan pemerintah) setiap malam,
menyimpannya ke **PostgreSQL**, lalu menyediakan **API internal** untuk mencari peluang
tender berdasarkan nama paket, bulan pemilihan, pagu, dan satuan kerja.

- Tahun anggaran: otomatis **tahun berjalan** (WIB)
- Volume: ±3,6 juta paket per tahun (data 2026)
- Dokumentasi API interaktif: **Swagger** di `/docs`, **ReDoc** di `/redoc`

---

## Cara kerja (sekilas)

```
            setiap hari 02:00 WIB
 ┌──────────┐   GET 5.000 paket/permintaan   ┌──────────┐
 │  SIRUP   │ ◄───────────────────────────── │ scraper  │
 │ (publik) │ ─────────────────────────────► │ (Python) │
 └──────────┘   JSON, urut berdasarkan id    └────┬─────┘
                                                  │ simpan/perbarui (upsert)
                                                  ▼
 ┌──────────┐   pg_dump harian 04:00   ┌─────────────────────┐
 │ backups/ │ ◄─────────────────────── │ PostgreSQL 16       │
 └──────────┘                          │ tabel paket,        │
                                       │ tabel scrape_runs   │
                                       └─────────┬───────────┘
                                                 │ baca saja
                                                 ▼
                     Tim / aplikasi ──►  API FastAPI :8000 (X-API-Key)
```

1. **Ambil.** Scraper meminta data ke SIRUP per halaman (5.000 paket), diurutkan
   berdasarkan `id`, dengan jeda 2 detik di antara permintaan. Satu putaran penuh berisi
   ±730 permintaan.
2. **Periksa.** Setiap baris dicek bentuknya. Baris rusak dicatat dan dilewati. Kalau
   lebih dari 50% baris dalam satu halaman rusak, proses berhenti, karena itu tandanya
   format SIRUP berubah.
3. **Simpan.** Paket baru ditambahkan, paket lama diperbarui (tidak pernah dobel).
   Kapan sebuah paket pertama kali terlihat dicatat di `first_seen_at`.
4. **Lanjutkan kalau putus.** Posisi halaman terakhir disimpan. Kalau proses terputus,
   putaran berikutnya melanjutkan dari posisi itu, bukan dari nol.
5. **Tandai yang hilang.** Paket yang tidak muncul lagi di SIRUP (misalnya batal)
   ditandai `is_active = false`, bukan dihapus. Ini hanya dilakukan kalau putarannya
   lengkap (≥ 99%) dan jumlah total tidak anjlok lebih dari 20%.
6. **Sajikan.** API membaca database dengan akun yang **hanya bisa membaca**.

Alasan di balik setiap keputusan teknis ada di [docs/DECISIONS.md](docs/DECISIONS.md).

---

## Setup

### Prasyarat
- Docker + Docker Compose v2
- Git
- Python 3.12+ (hanya untuk development dan menjalankan tes)

### 1. Ambil kode dan siapkan `.env`
```bash
git clone https://github.com/anunglogin/scraping-sirup.git
cd scraping-sirup
cp .env.example .env
```

Isi nilai di `.env`:

| Variabel | Isi |
|---|---|
| `POSTGRES_PASSWORD`, `API_DB_PASSWORD` | Huruf dan angka saja. Buat dengan `python -c "import secrets;print(secrets.token_hex(24))"` |
| `API_KEYS` | Minimal 24 karakter; boleh lebih dari satu, dipisah koma. Buat dengan `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `API_BIND` | Biarkan `127.0.0.1` (hanya dari server itu sendiri). Dari luar server pakai SSH tunnel atau profil HTTPS (Caddy); lihat bagian "Akses dari luar server" |
| `TAHUN_ANGGARAN` | Kosongkan = tahun berjalan |
| `SCRAPE_TIME` | Jam mulai harian (WIB), bawaan `02:00` |

> File `.env` **tidak boleh** di-commit. File ini sudah masuk `.gitignore`.

### 2. Nyalakan
```bash
docker compose up -d --build
docker compose ps
```
Yang terjadi: `db` menyala → `migrate` membuat tabel lalu berhenti (Exited 0) →
`scraper`, `api`, dan `backup` berjalan.

### 3. Cek
```bash
curl http://localhost:8000/health
```
Buka **http://localhost:8000/docs**, klik **Authorize**, lalu masukkan salah satu
`API_KEYS`.

### 4. Isi data pertama kali (tanpa menunggu jam 02:00)
```bash
docker compose run --rm scraper python -m sirup.scraper once
```
Pengambilan penuh pertama (11 Nov 2026) butuh **52 menit** untuk 728 halaman
(±3,6 juta paket). Untuk uji coba singkat, ambil 2 halaman saja:
```bash
docker compose run --rm -e SCRAPE_MAX_PAGES=2 scraper python -m sirup.scraper once
```

### Contoh pemakaian API
```bash
curl -H "X-API-Key: <kunci>" \
  "http://localhost:8000/v1/paket?paket=laptop&satuan_kerja=dinas&pemilihan_bulan=1&pemilihan_bulan=2&pagu_min=100000000"
```
Jawaban berisi `total` (dihitung sampai 10.000) dan `total_terbatas` (`true` = lebih dari
10.000 hasil). Penjelasan lengkap semua filter ada di [docs/API.md](docs/API.md).

### Akses dari luar server
- **SSH tunnel (bawaan, Rp0):** `ssh -N -L 8000:127.0.0.1:8000 user@IP-SERVER`, lalu buka
  http://127.0.0.1:8000/docs di komputer sendiri.
- **HTTPS dengan Caddy (opsional):** isi `API_DOMAIN` di `.env` (DNS A record ke server; domain
  ±Rp150–200 ribu/tahun kalau belum punya), lalu `docker compose --profile https up -d`.
  Sertifikat gratis dan diperbarui otomatis.
- Jangan membuka API dengan `API_BIND=0.0.0.0` (kunci terkirim tanpa enkripsi).
Detail: [docs/RUNBOOK.md](docs/RUNBOOK.md) §7.

### Development & tes
```bash
python -m venv .venv
.venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
pytest                          # tes unit (tes database otomatis dilewati)

# tes lengkap dengan PostgreSQL sementara
docker run -d --rm --name sirup-test-db -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres pytest
docker rm -f sirup-test-db
```

Operasional harian (log, backup dan pemulihan, rollback, masalah umum):
[docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## Struktur data

### Sumber: endpoint SIRUP
```
GET https://sirup.inaproc.id/sirup/caripaketctr/search
    ?tahunAnggaran=2026&jenisPengadaan=&draw=1
    &columns[11][data]=id&order[0][column]=11&order[0][dir]=ASC
    &start=0&length=5000&search[value]=
```
> ⚠️ Parameter urutan (`order[...]`) **wajib**. Tanpanya SIRUP membalas kosong.
> `jenisPengadaan` dikosongkan supaya semua jenis terambil dalam satu putaran.

Balasan (format DataTables):
```json
{
  "recordsTotal": 3638893,
  "recordsFiltered": 3638893,
  "draw": 1,
  "data": [
    {
      "id": 63838517, "paket": "Belanja Modal Personal Komputer ", "pagu": 30000000,
      "idJenisPengadaan": 1, "jenisPengadaan": "Barang",
      "idMetode": 9, "metode": "E-Purchasing",
      "idBulan": 4, "pemilihan": "April 2026",
      "idKldi": "D169", "kldi": "Kab. Blitar",
      "idSatker": 354007, "satuanKerja": "Puskesmas Srengat",
      "idsLokasi": "14831", "lokasi": "Jawa Timur, Blitar (Kab.)",
      "sumberDana": "BLUD", "isPDN": true, "isUMK": true, "pds": false,
      "id_referensi": 475, "idlokasi": 0
    }
  ]
}
```

### Tabel `paket` (satu baris = satu paket SIRUP)

| Kolom DB | Field SIRUP | Tipe | Arti |
|---|---|---|---|
| `id` | `id` | bigint, PK | ID paket di SIRUP |
| `tahun_anggaran` | *(parameter)* | smallint | Tahun anggaran |
| `nama_paket` | `paket` | text | Nama paket (spasi di ujung dibuang) |
| `pagu` | `pagu` | bigint | Nilai pagu dalam rupiah (dibulatkan) |
| `id_jenis_pengadaan` / `jenis_pengadaan` | `idJenisPengadaan` / `jenisPengadaan` | smallint / text | 1 Barang, 2 Pekerjaan Konstruksi, 3 Jasa Konsultansi, 4 Jasa Lainnya. **Bisa kosong**: ±200 paket SIRUP tidak punya nama jenis (mis. kode 6) |
| `id_metode` / `metode` | `idMetode` / `metode` | smallint / text | Metode pemilihan, mis. 8 Pengadaan Langsung, 9 E-Purchasing |
| `id_bulan` / `pemilihan` | `idBulan` / `pemilihan` | smallint / text | Bulan rencana pemilihan penyedia (1–12) / teks "April 2026" |
| `id_kldi` / `kldi` | `idKldi` / `kldi` | text | Instansi: K = kementerian/lembaga, D = pemerintah daerah |
| `id_satker` / `satuan_kerja` | `idSatker` / `satuanKerja` | bigint / text | Satuan kerja pemilik paket |
| `ids_lokasi` / `lokasi` | `idsLokasi` / `lokasi` | text | Lokasi pekerjaan, "Provinsi, Kab/Kota" |
| `sumber_dana` | `sumberDana` | text | APBN, APBD, BLUD, dll. (bisa berulang) |
| `is_pdn` | `isPDN` | boolean | Produk dalam negeri |
| `is_umk` | `isUMK` | boolean | Dicadangkan untuk usaha mikro dan kecil |
| `pds` | `pds` | boolean | Penanda dari SIRUP |
| `id_referensi` | `id_referensi` | integer | Referensi internal SIRUP |
| `is_active` | — | boolean | `false` = sudah tidak muncul di SIRUP |
| `first_seen_at` | — | timestamptz | Pertama kali terlihat (untuk mencari "paket baru") |
| `last_seen_at` | — | timestamptz | Terakhir terlihat di SIRUP |
| `updated_at` | — | timestamptz | Terakhir isinya berubah |

Indeks: trigram (`pg_trgm`) pada `nama_paket` dan `satuan_kerja` untuk pencarian kata,
`(tahun_anggaran, id_bulan)`, `(tahun_anggaran, pagu, id)`, dan `(first_seen_at, id)`.

### Tabel `paket_raw`

Baris asli SIRUP (`id`, `raw jsonb`), untuk jaga-jaga. Dipisah dari `paket` supaya tabel
utama kecil (1,1 GB, bukan 3,3 GB) dan pencarian cepat (ADR TD-06). Ditampilkan di
`GET /v1/paket/{id}`.

### Tabel `scrape_runs` (satu baris = satu putaran pengambilan)

| Kolom | Arti |
|---|---|
| `status` | `running` / `success` / `failed` |
| `started_at`, `finished_at` | Waktu mulai dan selesai |
| `records_total` | Jumlah paket menurut SIRUP |
| `rows_received`, `rows_inserted`, `rows_updated`, `rows_invalid`, `rows_deactivated` | Statistik putaran |
| `checkpoint_start` | Posisi terakhir, dipakai untuk melanjutkan |
| `error`, `note` | Pesan galat / alasan penandaan paket hilang dilewati |

Bisa dilihat juga lewat `GET /v1/scrape-runs`.

---

## Struktur folder

```
├─ docker-compose.yml      db, migrate, scraper, api, backup
├─ Dockerfile              satu image untuk scraper, api, dan migrate
├─ .env.example            contoh konfigurasi (tanpa rahasia)
├─ src/sirup/
│  ├─ scraper/             client (HTTP + retry), models (validasi), store (DB), runner, __main__
│  └─ api/                 main (FastAPI + Swagger), queries, schemas, config
├─ db/
│  ├─ init/                pembuatan akun database baca-saja untuk API
│  └─ migrations/          migrasi skema (Alembic)
├─ ops/backup.sh           backup harian
├─ tests/                  unit + integrasi (PostgreSQL)
└─ docs/                   BRD, ARCHITECTURE, DECISIONS, API, RUNBOOK
```

## Dokumen
- [docs/BRD.md](docs/BRD.md): kebutuhan bisnis dan kriteria diterima
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): rancangan teknis
- [docs/DECISIONS.md](docs/DECISIONS.md): catatan keputusan (ADR)
- [docs/API.md](docs/API.md): panduan API
- [docs/RUNBOOK.md](docs/RUNBOOK.md): panduan operasional
