# Rancangan Teknis — SIRUP Scraper + API

| | |
|---|---|
| Status | **BERLAKU — BRD disetujui 10 Nov 2026** · diperbarui sesuai TD-05 (10 Nov 2026) dan TD-06 (11 Nov 2026) |
| Tanggal | 10 November 2026 |
| Acuan | `docs/BRD.md`, `docs/DECISIONS.md` |

---

## 1. Gambaran besar

```
                ┌──────────────── docker compose ─────────────────┐
 SIRUP  ◄─HTTP──┤ scraper  (Python, terjadwal 02:00 WIB)           │
 (publik)       │    │ upsert                                      │
                │    ▼                                             │
                │ db  (PostgreSQL 16)  ◄── backup (pg_dump harian) │
                │    ▲                                             │
                │    │ query (read-only)                           │
 Tim/aplikasi ──┤ api  (FastAPI, port 8000, X-API-Key)             │
                └──────────────────────────────────────────────────┘
```

Ada tiga container dan satu volume database. Tidak ada layanan berbayar.

## 2. Teknologi

| Bagian | Pilihan | Alasan |
|---|---|---|
| Bahasa | Python 3.12 | Lihat TD-01 |
| HTTP client | `httpx` + `tenacity` (retry/backoff) | Sederhana, mendukung timeout dan HTTP/2 |
| Validasi data masuk | `pydantic` v2 | Aturan wajib: semua input dari luar divalidasi |
| Database | PostgreSQL 16 + ekstensi `pg_trgm` | Pencarian kata kunci cepat (TD-03) |
| Akses DB | `psycopg` 3 (COPY untuk impor massal; pool koneksi di API). SQLAlchemy hanya dipakai Alembic | TD-05 |
| Migrasi skema | Alembic | |
| API | FastAPI + Uvicorn | Satu bahasa dengan scraper (TD-02) |
| Penjadwal | Loop Python sederhana (`SCRAPE_TIME=02:00` WIB) di container scraper | Tanpa unduhan program tambahan (TD-05) |
| Tes | pytest + `respx` (tiruan SIRUP) + Postgres asli di Docker | |
| CI | GitHub Actions (lint `ruff`, tes, build image) | |

## 3. Sumber data — temuan uji coba

Endpoint:
```
GET https://sirup.inaproc.id/sirup/caripaketctr/search
```
Parameter yang kita pakai:

| Parameter | Nilai | Catatan |
|---|---|---|
| `tahunAnggaran` | tahun berjalan | Dihitung saat run dimulai: `datetime.now(ZoneInfo("Asia/Jakarta")).year`. Bisa ditimpa lewat env `TAHUN_ANGGARAN` (opsional, untuk ambil ulang tahun tertentu) |
| `jenisPengadaan` | kosong | Kosong = semua jenis sekaligus (TD-05) |
| `columns[11][data]=id` + `order[0][column]=11` + `order[0][dir]=ASC` | — | **WAJIB.** Tanpa parameter urutan, SIRUP membalas kosong (0 byte). Sudah dibuktikan. |
| `start`, `length` | `length=5000` | Diuji sampai 10.000 dan berhasil; kita pakai 5.000 agar lebih ringan |
| `draw`, `search[value]` | 1, kosong | Diperlukan oleh DataTables |

**Kenapa diurutkan berdasarkan `id` naik (ASC):** urutan yang tetap membuat halaman
tidak berubah susunannya selama pengambilan berjalan.

**Perhatian (temuan 10 Nov 2026):** ID **belum tentu** mengikuti urutan waktu dibuat.
ID terbesar (±965 juta) justru milik paket dengan pemilihan "January 2026" dan
"December 2025", sedangkan paket yang lain ber-ID ±60–68 juta. SIRUP juga tidak
menyediakan kolom tanggal dibuat atau diubah. Jadi saat ini belum ada cara pasti untuk
mengambil "yang baru saja". Lihat TD-04.

Parameter `jenisPengadaan` yang dikosongkan langsung mengembalikan semua jenis
(3.638.893 paket), jadi cukup satu putaran tanpa perlu diulang per jenis.

**Celah yang tetap ada:** kalau ada paket yang dihapus di tengah proses, satu atau dua
paket bisa terlewat. Celah ini tertutup sendiri pada putaran harian berikutnya.

## 4. Alur scraper

```
tahun = env TAHUN_ANGGARAN or tahun berjalan (Asia/Jakarta)
ambil advisory lock PostgreSQL            # gagal -> keluar, run lain sedang jalan
run = run terakhir tahun itu bila status running/failed, checkpoint > 0,
      dan dimulai < 24 jam lalu (LANJUTKAN, started_at tetap)
      selain itu buat baris scrape_runs baru (status=running)
start = run.checkpoint_start
loop:
   GET page(start, length=5000, jenisPengadaan='')   # timeout 60s, 5x percobaan, backoff 2..60s
   validasi tiap baris (pydantic)          # baris rusak dihitung, tidak menghentikan proses
   bila > 50% baris satu halaman rusak -> run GAGAL (format SIRUP kemungkinan berubah)
   satu transaksi: COPY ke staging -> INSERT ... ON CONFLICT (id) DO UPDATE
                   (last_seen_at = now(), is_active = true, updated_at berubah hanya
                    bila isi baris asli berubah) + update penghitung & checkpoint
   berhenti bila data kosong / start >= recordsTotal / SCRAPE_MAX_PAGES tercapai
   sleep(SCRAPE_DELAY_SECONDS=2)
selesai:
   tandai is_active=false untuk paket tahun itu yang last_seen_at < run.started_at
     HANYA bila: run lengkap (bukan dibatasi SCRAPE_MAX_PAGES), recordsTotal > 0,
     baris valid >= 99% recordsTotal, dan recordsTotal tidak turun > 20% dari run
     sukses sebelumnya. Bila tidak, alasannya dicatat di kolom `note`.
   tutup run (status=success, rows_deactivated, finished_at)
gagal: status=failed + pesan error; checkpoint tetap, jadi run berikutnya melanjutkan.
```

Mode `schedule` menunggu sampai `SCRAPE_TIME` (WIB) setiap hari. Bila run gagal, dicoba
lagi (melanjutkan dari checkpoint) maksimal 3x dengan jeda 30 menit.

Pengaman:
- Kalau 5× berturut-turut gagal (HTTP 429/5xx, timeout, body kosong, atau JSON rusak), run
  berhenti dengan status `failed`. Server SIRUP tidak dipaksa.
- HTTP 4xx lain (mis. 403) dan bentuk JSON yang berubah **tidak** diulang: langsung gagal.
- Kalau `recordsTotal` turun lebih dari 20% dibanding run sebelumnya, paket **tidak**
  ditandai nonaktif. Ini menangkap kasus SIRUP sedang bermasalah.
- User-Agent generik dan jujur: `Mozilla/5.0 (compatible; sirup-collector/0.1)` (sudah
  diuji diterima SIRUP, 10 Nov 2026). Tidak ada penyamaran atau rotasi IP.
- Hanya satu run yang boleh berjalan dalam satu waktu (advisory lock PostgreSQL).
- Mematikan proses di tengah jalan aman: run tetap `running` dengan checkpoint terakhir dan
  dilanjutkan pada run berikutnya (AC-04).

## 5. Skema database

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE paket (
  id                 BIGINT PRIMARY KEY,          -- id SIRUP
  tahun_anggaran     SMALLINT     NOT NULL,
  nama_paket         TEXT         NOT NULL,       -- "paket"
  pagu               BIGINT       NOT NULL,       -- rupiah
  id_jenis_pengadaan SMALLINT,                    -- boleh kosong: ada paket SIRUP tanpa jenis (TD-06)
  jenis_pengadaan    TEXT,
  id_metode          SMALLINT,
  metode             TEXT,
  id_bulan           SMALLINT,                    -- bulan pemilihan 1-12
  pemilihan          TEXT,                        -- "April 2026"
  id_kldi            TEXT,
  kldi               TEXT,
  id_satker          BIGINT,
  satuan_kerja       TEXT,
  ids_lokasi         TEXT,
  lokasi             TEXT,
  sumber_dana        TEXT,
  is_pdn             BOOLEAN,
  is_umk             BOOLEAN,
  pds                BOOLEAN,
  id_referensi       INTEGER,
  is_active          BOOLEAN      NOT NULL DEFAULT true,
  first_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()  -- berubah hanya bila isi berubah
);

CREATE INDEX ix_paket_nama_trgm   ON paket USING gin (nama_paket gin_trgm_ops);
CREATE INDEX ix_paket_satker_trgm ON paket USING gin (satuan_kerja gin_trgm_ops);
CREATE INDEX ix_paket_bulan       ON paket (tahun_anggaran, id_bulan);
CREATE INDEX ix_paket_pagu        ON paket (tahun_anggaran, pagu, id);
CREATE INDEX ix_paket_first_seen ON paket (first_seen_at DESC, id DESC);   -- "paket baru"

-- Baris asli SIRUP, dipisah dari tabel utama supaya tabel paket kecil (TD-06)
CREATE TABLE paket_raw (
  id  BIGINT PRIMARY KEY,                 -- = paket.id
  raw JSONB  NOT NULL
);

CREATE TABLE scrape_runs (
  id               BIGSERIAL PRIMARY KEY,
  tahun_anggaran   SMALLINT NOT NULL,   -- satu run = semua jenis pengadaan (TD-05)
  status           TEXT NOT NULL CHECK (status IN ('running','success','failed')),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ,
  records_total    INTEGER,             -- angka dari SIRUP
  rows_received    INTEGER NOT NULL DEFAULT 0,   -- termasuk baris rusak
  rows_inserted    INTEGER NOT NULL DEFAULT 0,
  rows_updated     INTEGER NOT NULL DEFAULT 0,   -- isi berubah
  rows_invalid     INTEGER NOT NULL DEFAULT 0,
  rows_deactivated INTEGER NOT NULL DEFAULT 0,
  checkpoint_start INTEGER NOT NULL DEFAULT 0,
  error            TEXT,
  note             TEXT                  -- mis. alasan penandaan nonaktif dilewati
);
CREATE INDEX ix_scrape_runs_year ON scrape_runs (tahun_anggaran, id DESC);
```

Migrasi (SQL mentah, semuanya punya `downgrade`): `0001_initial_schema`,
`0002_jenis_pengadaan_nullable`, `0003_split_raw_and_rebuild_paket`. Migrasi 0003
membangun ulang tabel `paket` (±5 menit untuk 3,6 juta baris; API tertahan selama itu).
Setelahnya jalankan `VACUUM (ANALYZE)` (lihat RUNBOOK).

Ukuran terukur (11 Nov 2026, 3,6 juta baris): `paket` 1,9 GB (data 1,1 GB + indeks
0,8 GB), `paket_raw` 2,4 GB. Total ±4,3 GB.

## 6. Kontrak API (v1)

Semua endpoint, kecuali `/health`, wajib memakai header `X-API-Key`. Kunci disimpan di
environment variable `API_KEYS` (bisa lebih dari satu, dipisah koma). Parameter
divalidasi dengan Pydantic; nilai yang tidak valid dibalas **422**.

### `GET /v1/paket`

**Filter utama (disepakati bos):** paket, pemilihan, pagu, satuan kerja. Semua filter
bersifat opsional dan bisa digabung (logika DAN).

| Query | Tipe | Arti | Contoh |
|---|---|---|---|
| `paket` | teks, 3–100 karakter | Nama paket **mengandung** kata ini (tidak peka huruf besar/kecil) | `laptop` |
| `satuan_kerja` | teks, 3–100 karakter | Nama satuan kerja **mengandung** kata ini | `puskesmas` |
| `pemilihan_bulan` | int[] 1–12 | Bulan pemilihan penyedia (kolom `idBulan`) | `pemilihan_bulan=1&pemilihan_bulan=2` |
| `pagu_min`, `pagu_max` | int ≥ 0, rupiah | Rentang pagu; `pagu_min` ≤ `pagu_max`, kalau tidak dibalas 422 | `pagu_min=100000000` |

**Pendukung:**

| Query | Tipe | Arti |
|---|---|---|
| `tahun` | int, bawaan tahun berjalan | Tahun anggaran |
| `aktif` | bool, bawaan `true` | Sembunyikan paket yang sudah hilang dari SIRUP |
| `sort` | `pagu_desc` (bawaan) \| `pagu_asc` \| `terbaru` | Urutan |
| `page`, `size` | int, `size` bawaan 20, maksimal 100 | Halaman. `page × size` maksimal 10.000; lebih dari itu dibalas 422 (persempit filter) |

Filter lain (jenis pengadaan, metode, lokasi, instansi/KLDI, UMK/PDN) **tidak** masuk
Tahap 1. Datanya sudah tersimpan, jadi kalau dibutuhkan, menambahkannya cukup ±½ hari
per filter.

Contoh balasan:
```json
{ "total": 1234, "total_terbatas": false, "page": 1, "size": 50,
  "data": [ { "id": 63838517, "nama_paket": "...", "pagu": 30000000, "kldi": "...", "...": "..." } ] }
```

`total` dihitung **sampai 10.000 saja**. Kalau yang cocok lebih banyak, `total` = 10.000 dan
`total_terbatas` = `true` (artinya "lebih dari 10.000"). Menghitung persis jutaan baris butuh
3–8 detik, dan halaman di atas 10.000 memang tidak bisa dibuka (TD-06).

**Kecepatan** (12 pencarian × 3 kali, curl ke API, database penuh; detik):

| Pencarian | Sebelum | Sesudah (normal) | Sesudah DB baru dinyalakan |
|---|---|---|---|
| `size=20` | 1,59–1,91 | 0,015–0,017 | 0,07–0,32 |
| `sort=terbaru` | 1,19–1,81 | 0,015–0,035 | 0,05–0,07 |
| `paket=laptop` | 0,15–0,18 | 0,08–0,14 | 0,15–0,63 |
| `paket=belanja` | 3,67–4,61 | 0,04–0,06 | 0,07–0,10 |
| `paket=komputer&pagu_min=100000000` | 1,41–1,75 | 0,19–0,24 | 0,37–0,73 |
| `satuan_kerja=puskesmas` | 0,98–1,56 | 0,26–0,29 | 0,52–**1,60** |
| `satuan_kerja=dinas` | 4,26–5,09 | 0,05–0,06 | 0,06–0,12 |
| `pemilihan_bulan=1` | 1,15–2,10 | 0,02–0,04 | 0,04–0,09 |
| `pagu_min=1000000000` | 0,34–0,53 | 0,02 | 0,02–0,04 |
| `pagu_min=100000000&pagu_max=500000000` | 0,85–1,01 | 0,02 | 0,04–0,11 |
| `paket=makan&satuan_kerja=dinas&pemilihan_bulan=3&pagu_min=50000000` | 2,12–3,16 | 0,50–0,61 | 0,53–**1,11** |
| `paket=belanja&page=500&size=20` (halaman terdalam) | 3,05–4,53 | 0,11–0,12 | 0,15–0,20 |
| **p95 (36 permintaan)** | **5,00** | **0,50** | **1,11** |

"Sesudah DB baru dinyalakan" = memori database masih kosong. Pencarian pertama yang memakai
indeks trigram bisa lewat sedikit dari 1 detik, lalu cepat lagi. Diukur di mesin pengembang
dengan pengaturan PostgreSQL untuk VPS 4 GB; angka di VPS asli perlu dicek ulang (T8).

### Endpoint lain
- `GET /v1/paket/{id}`: satu paket beserta `raw` (dari `paket_raw`); 404 kalau tidak ada.
- `GET /v1/scrape-runs?limit=20`: riwayat pengambilan data (AC-11).
- `GET /health`: cek koneksi database, tanpa kunci.

User database untuk API **hanya punya akses baca** (read-only role, dibuat oleh
`db/init/10-api-role.sh`). Koneksi API juga memakai `default_transaction_read_only=on`
dan `statement_timeout` 10 detik. Dokumentasi interaktif: Swagger `/docs`, ReDoc `/redoc`
(lihat `docs/API.md`).

## 7. Konfigurasi (`.env.example`)

```
APP_VERSION=0.1.0              # tag image: sirup:0.1.0
POSTGRES_DB=sirup
POSTGRES_USER=sirup_admin
POSTGRES_PASSWORD=...          # huruf & angka saja (masuk ke URL)
API_DB_USER=sirup_api          # read-only
API_DB_PASSWORD=...
API_KEYS=key1,key2             # tiap kunci minimal 24 karakter
API_BIND=127.0.0.1
API_PORT=8000
TAHUN_ANGGARAN=                # kosong = tahun berjalan (WIB)
SCRAPE_PAGE_SIZE=5000          # 1..10000
SCRAPE_DELAY_SECONDS=2
SCRAPE_TIME=02:00              # WIB
SCRAPE_MAX_PAGES=              # kosong = tanpa batas; hanya untuk uji coba
BACKUP_HOUR=4
BACKUP_KEEP_DAYS=7
```
Nilai kosong dianggap "tidak diisi". File `.env` asli **tidak pernah** di-commit.

## 8. Struktur folder

```
sirup/
├─ Dockerfile            (satu image untuk migrate, scraper, api)
├─ docker-compose.yml    (db, migrate, scraper, api, backup)
├─ pyproject.toml
├─ alembic.ini
├─ .env.example
├─ src/sirup/
│   ├─ timeutil.py
│   ├─ scraper/  (config, client, models, store, runner, __main__)
│   └─ api/      (config, queries, schemas, main)
├─ db/migrations/        (Alembic)
├─ db/init/10-api-role.sh
├─ ops/backup.sh
├─ tests/  (unit/, integration/)
├─ .github/workflows/ci.yml
└─ docs/
```

## 9. Deploy & rollback

- `docker compose up -d`. Image diberi tag versi (`sirup:0.1.0`, dari `APP_VERSION`), jangan `latest`.
- **Rollback:** ganti tag image ke versi sebelumnya, lalu jalankan `docker compose up -d`.
  Migrasi skema selalu punya `downgrade`.
- **PostgreSQL** diatur untuk VPS 2 CPU / 4 GB lewat `command:` di docker-compose
  (`shared_buffers=1GB`, `effective_cache_size=3GB`, `work_mem=32MB`, dll.; TD-06).
  Nilainya bisa ditimpa lewat variabel `PG_*` di `.env`.
- **Backup:** container `backup` mengecek setiap 10 menit; setelah pukul 04:00 WIB dibuat
  `pg_dump` hari itu (sekali sehari) ke folder `./backups`, disimpan 7 hari. Pemulihannya diuji
  sebelum go-live (AC-10).
- Port database **tidak** dibuka ke luar. API sebaiknya diletakkan di belakang reverse
  proxy dengan HTTPS (Caddy, gratis) kalau diakses dari luar kantor.

## 10. Pembagian tugas

| # | Tugas | Mencakup AC | Tergantung |
|---|---|---|---|
| T1 | Kerangka repo, docker-compose, `.env.example`, CI | AC-09 | — |
| T2 | Skema DB + migrasi Alembic + role read-only | — | T1 |
| T3 | Klien SIRUP (retry, jeda, validasi pydantic) + tes dengan tiruan | AC-05 | T1 |
| T4 | Penyimpanan upsert + checkpoint + `scrape_runs` + penanda nonaktif | AC-01, 02, 04, 11 | T2, T3 |
| T5 | Penjadwal harian (loop Python, TD-05) + lock satu run | AC-03 | T4 |
| T6 | API: `/v1/paket` (filter paket, pemilihan, pagu, satuan kerja) + auth | AC-06, 07, 08 | T2 |
| T7 | Backup harian + uji pemulihan + RUNBOOK | AC-10 | T2 |
| T8 | Uji penuh: pengambilan pertama 3,6 juta paket + uji beban API | semua | T4–T7 |

T3 dan T6 bisa dikerjakan bersamaan setelah T2 selesai.
