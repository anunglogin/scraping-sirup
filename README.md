# Scraping SIRUP — Pengumpul Paket Pengadaan + API Penyaring

Sistem ini mengambil **semua paket pengadaan penyedia** dari
[SIRUP](https://sirup.inaproc.id) (Rencana Umum Pengadaan pemerintah) setiap malam,
menyimpannya ke **PostgreSQL**, lalu menyediakan **API internal** untuk mencari peluang
tender berdasarkan nama paket, bulan pemilihan, pagu, satuan kerja, dan sumber data.
Sebagai sumber kedua, paket dari [data.inaproc.id](https://data.inaproc.id/rup) bisa
ditambahkan lewat pengambil INAPROC yang dijalankan manual.

- Tahun anggaran: otomatis **tahun berjalan** (WIB)
- Volume: ±3,6 juta paket per tahun dari SIRUP (data 2026), ditambah paket INAPROC
  yang sudah diambil
- Setiap paket punya penanda `sumber`: `sirup`, `inaproc`, atau keduanya
- Dokumentasi API interaktif: **Swagger** di `/docs`, **ReDoc** di `/redoc`

---

## Cara kerja (sekilas)

```
     SUMBER 1 (otomatis, 02:00 WIB)              SUMBER 2 (manual, bila dijalankan)
 ┌──────────┐                                   ┌──────────────────┐
 │  SIRUP   │  5.000 paket/permintaan           │ data.inaproc.id  │ 100 baris/permintaan
 │ (publik) │                                   │ (izin tertulis)  │ + cursor halaman
 └────┬─────┘                                   └────────┬─────────┘
      │ ▲                                                │ ▲
      ▼ │                                                ▼ │
 ┌──────────┐                                   ┌──────────────────┐   output/inaproc/
 │ scraper  │                                   │ pengambil        │──► .jsonl .csv
 │  SIRUP   │                                   │ INAPROC          │    state (posisi)
 └────┬─────┘                                   └────────┬─────────┘
      │ upsert, sumber += sirup                          │ upsert ke inaproc_rup,
      │                                                  │ lalu gabung ke paket
      ▼                                                  ▼ (sumber += inaproc)
 ┌─────────────────────────────────────────────────────────────────┐
 │ PostgreSQL 16                                                   │
 │   paket        satu baris per kode RUP, kolom sumber            │
 │   paket_raw    baris asli SIRUP                                 │
 │   inaproc_rup  salinan lengkap data INAPROC                     │
 │   scrape_runs  riwayat putaran SIRUP                            │
 └───────────────┬─────────────────────────────────────┬───────────┘
                 │ baca saja                           │ pg_dump harian 04:00
                 ▼                                     ▼
     API FastAPI :8000 (X-API-Key)               backups/
     /v1/paket?sumber=sirup|inaproc
                 ▲
                 │
        Tim / aplikasi
```

**Sumber 1: SIRUP (otomatis setiap malam)**

1. **Ambil.** Scraper meminta data ke SIRUP per halaman (5.000 paket), diurutkan
   berdasarkan `id`, dengan jeda 2 detik di antara permintaan. Satu putaran penuh berisi
   ±730 permintaan.
2. **Periksa.** Setiap baris dicek bentuknya. Baris rusak dicatat dan dilewati. Kalau
   lebih dari 50% baris dalam satu halaman rusak, proses berhenti, karena itu tandanya
   format SIRUP berubah.
3. **Simpan.** Paket baru ditambahkan, paket lama diperbarui (tidak pernah dobel).
   Kapan sebuah paket pertama kali terlihat dicatat di `first_seen_at`. Paket diberi
   tanda `sumber` = `sirup`; tanda `inaproc` yang sudah ada tetap dipertahankan.
4. **Lanjutkan kalau putus.** Posisi halaman terakhir disimpan. Kalau proses terputus,
   putaran berikutnya melanjutkan dari posisi itu, bukan dari nol.
5. **Tandai yang hilang.** Paket yang tidak muncul lagi di SIRUP (misalnya batal)
   ditandai `is_active = false`, bukan dihapus. Ini hanya dilakukan kalau putarannya
   lengkap (≥ 99%) dan jumlah total tidak anjlok lebih dari 20%. Paket yang hanya ada
   di INAPROC tidak pernah dinonaktifkan oleh SIRUP.

**Sumber 2: INAPROC (manual, hanya bila dijalankan)**

6. **Ambil per halaman.** Pengambil INAPROC meminta 100 baris per halaman ke
   data.inaproc.id dengan header akses dari `.env` (izin tertulis, TD-08), jeda 2 detik.
7. **Simpan ke file dan database.** Setiap halaman ditulis ke `output/inaproc/` dan
   disimpan ke tabel `inaproc_rup`. Posisi halaman baru disimpan setelah database
   berhasil, jadi tidak ada baris yang terlewat.
8. **Gabung ke `paket`.** Kalau paketnya sudah ada dari SIRUP, isinya tetap dari SIRUP
   dan hanya ditambah tanda `inaproc`. Kalau belum ada, paket ditambahkan dengan
   `sumber` = `inaproc`. Satu kode RUP selalu satu baris.

**Penyajian**

9. **Sajikan.** API membaca database dengan akun yang **hanya bisa membaca**. Filter
   `sumber=sirup` atau `sumber=inaproc` memilih paket menurut sumbernya. Rincian
   pengambil INAPROC ada di
   [Sumber kedua: data.inaproc.id](#sumber-kedua-datainaprocid-pengambil-inaproc).

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
  "http://localhost:8000/v1/paket?paket=laptop&satuan_kerja=dinas&pemilihan=2026-01&pemilihan=2026-02&pagu_min=100000000"
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

### Tabel `paket` (satu baris = satu paket, dari SIRUP dan/atau INAPROC)

| Kolom DB | Field SIRUP | Tipe | Arti |
|---|---|---|---|
| `id` | `id` | bigint, PK | ID paket di SIRUP |
| `tahun_anggaran` | *(parameter)* | smallint | Tahun anggaran |
| `nama_paket` | `paket` | text | Nama paket (spasi di ujung dibuang) |
| `pagu` | `pagu` | bigint | Nilai pagu dalam rupiah (dibulatkan). Beberapa paket SIRUP memang bernilai negatif dan disimpan apa adanya |
| `id_jenis_pengadaan` / `jenis_pengadaan` | `idJenisPengadaan` / `jenisPengadaan` | smallint / text | 1 Barang, 2 Pekerjaan Konstruksi, 3 Jasa Konsultansi, 4 Jasa Lainnya. **Bisa kosong**: ±200 paket SIRUP tidak punya nama jenis (mis. kode 6) |
| `id_metode` / `metode` | `idMetode` / `metode` | smallint / text | Metode pemilihan, mis. 8 Pengadaan Langsung, 9 E-Purchasing |
| `id_bulan` / `pemilihan` | `idBulan` / `pemilihan` | smallint / text | Bulan rencana pemilihan penyedia (1–12) / teks "April 2026" |
| `pemilihan_tahun` | *(dari teks `pemilihan`)* | smallint | Tahun pemilihan, dihitung otomatis oleh database (mis. 2025 untuk "December 2025"); kosong bila teksnya tidak berbentuk "Bulan YYYY" |
| `missing_runs` | — | smallint | Berapa run lengkap berturut-turut paket ini tidak terlihat; nonaktif setelah 2 |
| `id_kldi` / `kldi` | `idKldi` / `kldi` | text | Instansi: K = kementerian/lembaga, D = pemerintah daerah |
| `id_satker` / `satuan_kerja` | `idSatker` / `satuanKerja` | bigint / text | Satuan kerja pemilik paket |
| `ids_lokasi` / `lokasi` | `idsLokasi` / `lokasi` | text | Lokasi pekerjaan, "Provinsi, Kab/Kota" |
| `sumber_dana` | `sumberDana` | text | APBN, APBD, BLUD, dll. (bisa berulang) |
| `is_pdn` | `isPDN` | boolean | Produk dalam negeri |
| `is_umk` | `isUMK` | boolean | Dicadangkan untuk usaha mikro dan kecil |
| `pds` | `pds` | boolean | Penanda dari SIRUP |
| `id_referensi` | `id_referensi` | integer | Referensi internal SIRUP |
| `is_active` | — | boolean | `false` = sudah tidak muncul di SIRUP |
| `sumber` | — | text[] | Sumber yang mencantumkan paket: `{sirup}`, `{inaproc}`, atau keduanya (TD-10) |
| `first_seen_at` | — | timestamptz | Pertama kali terlihat (untuk mencari "paket baru") |
| `last_seen_at` | — | timestamptz | Terakhir terlihat di SIRUP |
| `updated_at` | — | timestamptz | Terakhir isinya berubah |

Indeks: trigram (`pg_trgm`) pada `nama_paket` dan `satuan_kerja` untuk pencarian kata,
`(tahun_anggaran, pemilihan_tahun, id_bulan)`, `(tahun_anggaran, pagu, id)`, `(first_seen_at, id)`,
dan GIN pada `sumber`.

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

## Sumber kedua: data.inaproc.id (pengambil INAPROC)

Dashboard [data.inaproc.id/rup](https://data.inaproc.id/rup) menampilkan data RUP yang
mirip dengan SIRUP, lewat endpoint `/dashboard-api/rup/table`. Pengambil INAPROC
(`src/sirup/inaproc`) mengambil data dari endpoint itu, menyimpannya ke file dan
database, lalu menggabungkannya ke tabel `paket` sehingga bisa dicari lewat API yang sama.

Pengambil ini **tidak terjadwal** dan tidak ikut menyala saat `docker compose up`.
Ia hanya jalan kalau dijalankan dengan tangan.

> **Izin akses.** Situs ini meminta header `x-secret` + `x-request-time`. Bos menyatakan
> ada **izin tertulis** dari INAPROC/LKPP untuk memakainya (TD-08). Salinan surat izin
> **wajib** disimpan di `docs/` sebelum pengambil dijalankan di server produksi.
> Tanpa nilai header itu, situs menjawab `403 Forbidden` dan pengambil berhenti.

### Cara kerja

```
 ┌──────────────────┐  GET ?tahun=&limit=100&cursor=   ┌──────────────────┐
 │ data.inaproc.id  │ ◄─────────────────────────────── │ pengambil        │
 │ /dashboard-api/  │ ───────────────────────────────► │ INAPROC (manual) │
 │ rup/table        │  100 baris + cursor berikutnya   └───┬──────────┬───┘
 └──────────────────┘                                      │          │
                                    tulis file (tambah)    │          │ upsert per halaman
                                                           ▼          ▼
                            output/inaproc/          PostgreSQL: inaproc_rup
                            rup_<tahun>.jsonl                  │ gabung
                            rup_<tahun>.csv                    ▼
                            state_<tahun>.json        tabel paket (kolom sumber)
                                                               │
                                                               ▼
                                              API /v1/paket?sumber=inaproc
```

1. **Ambil per halaman.** Satu permintaan berisi 100 baris (batas maksimum situs). Data
   2026 ada ±4,77 juta baris, jadi ±47.690 halaman. Antarhalaman ada jeda 2 detik.
2. **Periksa.** Setiap baris dicek bentuknya (`kode_rup`, `tahun_anggaran`, nama, nilai,
   dan seterusnya). Kolom tambahan dari situs tetap disimpan di data asli (`raw`).
3. **Tulis ke file.** Baris ditambahkan ke `rup_<tahun>.jsonl` dan `rup_<tahun>.csv`.
   Isi CSV yang diawali `=`, `+`, `-`, atau `@` diberi tanda kutip supaya aman dibuka
   di Excel.
4. **Simpan ke database.** Kalau `INAPROC_DATABASE_URL` diisi, setiap halaman disimpan
   ke tabel `inaproc_rup` dan digabung ke `paket` dalam satu transaksi.
5. **Simpan posisi.** Setelah halaman tersimpan, posisi halaman berikutnya (`cursor`)
   ditulis ke `state_<tahun>.json`. Urutannya sengaja begini: kalau database gagal,
   posisi tidak maju, jadi halaman itu diambil lagi pada jalan berikutnya. Tidak ada
   baris yang terlewat, dan tidak ada yang dobel di database.
6. **Lanjutkan kalau putus.** Menjalankan ulang pengambil akan melanjutkan dari posisi
   terakhir, bukan dari nol. Setelah semua halaman habis, `finished` bernilai `true`.

### Aturan penggabungan ke tabel `paket` (TD-10)

Satu kode RUP = satu baris di `paket`. Kolom `sumber` mencatat siapa yang
mencantumkannya.

| Kondisi | Yang terjadi | `sumber` |
|---|---|---|
| Paket sudah ada dari SIRUP | Isi **tetap dari SIRUP**; hanya tanda sumber yang ditambah | `{sirup,inaproc}` |
| Paket tidak ada di SIRUP | Ditambahkan sebagai baris baru dari data INAPROC | `{inaproc}` |
| Paket `{inaproc}` diambil ulang dan isinya berubah | Isinya diperbarui | `{inaproc}` |
| Paket `{inaproc}` kemudian muncul di SIRUP | Isinya diganti data SIRUP | `{inaproc,sirup}` |

Pemetaan kolom untuk paket yang hanya ada di INAPROC:

| Kolom `paket` | Dari INAPROC | Catatan |
|---|---|---|
| `id` | `kode_rup` | |
| `nama_paket` | `nama_paket` | |
| `pagu` | `total_nilai` | dibulatkan ke rupiah; kosong menjadi 0 |
| `jenis_pengadaan` / `metode` | `jenis_pengadaan` / `metode_pengadaan` | `-` disimpan kosong |
| `kldi` / `satuan_kerja` | `nama_instansi` / `nama_satker` | |
| `sumber_dana` | `sumber_dana` | `-` disimpan kosong |
| `is_pdn` / `is_umk` | `produk_dalam_negeri` / `status_umk` | `Ya` = true, `Tidak` = false |
| `id_bulan`, `pemilihan`, `lokasi`, `id_satker`, kode-kode lain | — | tidak tersedia di INAPROC, jadi kosong |

Akibatnya:
- Paket yang hanya ada di INAPROC **tidak muncul** kalau filter `pemilihan` dipakai,
  karena tidak punya bulan pemilihan.
- Scraper SIRUP **tidak pernah menonaktifkan** paket yang hanya ada di INAPROC. Kalau
  INAPROC menghapus sebuah paket, paket itu tetap tampil aktif di sini (catatan review S2).

### Menjalankan

Isi dulu `.env` (lihat `.env.example`):

| Variabel | Bawaan | Arti |
|---|---|---|
| `INAPROC_X_SECRET` | kosong | Nilai header `x-secret` dari izin INAPROC. **Rahasia, jangan di-commit** |
| `INAPROC_REQUEST_TIME` | kosong | Nilai header `x-request-time` yang berpasangan dengan `x-secret` |
| `INAPROC_TAHUN` | tahun berjalan | Tahun anggaran yang diambil |
| `INAPROC_LIMIT` | 100 | Baris per halaman (1–100) |
| `INAPROC_MAX_PAGES` | 10 | Halaman per jalan; `0` = sampai habis |
| `INAPROC_DELAY_SECONDS` | 2 | Jeda antarhalaman (minimal 0,5 detik) |

`INAPROC_DATABASE_URL` diisi otomatis oleh `docker-compose.yml` (database yang sama
dengan scraper). Kalau dikosongkan, hasilnya hanya ditulis ke file.

```bash
# Ambil sesuai INAPROC_MAX_PAGES (bawaan 10 halaman = 1.000 baris)
docker compose --profile inaproc run --rm inaproc

# Ambil 1.000 halaman (±100 ribu baris, ±40 menit)
docker compose --profile inaproc run --rm -e INAPROC_MAX_PAGES=1000 inaproc

# Masukkan file JSONL yang sudah ada ke database (tanpa mengambil dari situs)
docker compose --profile inaproc run --rm inaproc python -m sirup.inaproc load

# Mulai dari halaman pertama lagi: hapus file posisi
rm output/inaproc/state_2026.json
```

Perkiraan waktu: ±2,2 detik per halaman. Semua data 2026 (±47.690 halaman) butuh
sekitar **29 jam**. Pengambil boleh dihentikan kapan saja (Ctrl+C) dan dilanjutkan nanti.

Kode keluar:

| Kode | Arti | Yang perlu dilakukan |
|---|---|---|
| 0 | Selesai (batas halaman tercapai atau data habis) | — |
| 1 | Error (jaringan, format jawaban berubah, database) | Lihat log; jalankan ulang, posisi tetap aman |
| 2 | **Situs menolak (401/403)** | Periksa `INAPROC_X_SECRET` dan `INAPROC_REQUEST_TIME`; pengambil tidak mencoba ulang |

Jawaban 429 (terlalu sering) dan 5xx dicoba ulang otomatis (maksimal 3 kali, dengan
jeda yang makin lama).

### Melihat hasilnya

```bash
# Lewat API: hanya paket yang dicantumkan INAPROC
curl -H "X-API-Key: <kunci>" "http://127.0.0.1:8000/v1/paket?sumber=inaproc&size=5"
```

```sql
-- Jumlah paket per sumber
SELECT sumber, count(*) FROM paket GROUP BY 1;
-- Data INAPROC apa adanya
SELECT kode_rup, nama_paket, nama_instansi, total_nilai FROM inaproc_rup LIMIT 10;
```

File CSV di `output/inaproc/rup_<tahun>.csv` bisa langsung dibuka di Excel.

### Tabel `inaproc_rup`

Salinan lengkap data INAPROC (migrasi 0006, TD-09), terpisah dari `paket`.

| Kolom | Tipe | Arti |
|---|---|---|
| `kode_rup` | bigint, PK | Kode RUP (sama dengan `paket.id`) |
| `tahun_anggaran` | smallint | Tahun anggaran |
| `nama_paket`, `nama_instansi`, `nama_satker` | text | Nama paket, instansi, satuan kerja |
| `total_nilai` | numeric(20,2) | Nilai paket dalam rupiah |
| `cara_pengadaan`, `cara_pengadaan_label` | text | Penyedia / Swakelola |
| `jenis_pengadaan`, `metode_pengadaan` | text | `-` = tidak berlaku |
| `sumber_dana`, `produk_dalam_negeri`, `status_umk` | text | Teks asli dari INAPROC (`Ya`/`Tidak`) |
| `raw` | jsonb | Baris asli lengkap dari INAPROC |
| `first_seen_at` / `last_seen_at` | timestamptz | Pertama / terakhir kali diambil |

### Rollback

- `alembic downgrade 0006`: menghapus kolom `paket.sumber` **dan** paket yang hanya
  dari INAPROC. Data INAPROC tetap aman di `inaproc_rup`.
- `alembic downgrade 0005`: menghapus juga tabel `inaproc_rup`. File di
  `output/inaproc/` tidak tersentuh, jadi bisa dimuat ulang dengan perintah `load`.

---

## Struktur folder

```
├─ docker-compose.yml      db, migrate, scraper, api, backup, inaproc (manual)
├─ Dockerfile              satu image untuk scraper, api, dan migrate
├─ .env.example            contoh konfigurasi (tanpa rahasia)
├─ src/sirup/
│  ├─ scraper/             client (HTTP + retry), models (validasi), store (DB), runner, __main__
│  ├─ inaproc/             pengambil data.inaproc.id: client, config, runner, store (DB), __main__
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
