# Keputusan Teknis (ADR)

## TD-01 — Python untuk scraper
- **Tanggal:** 10 Nov 2026 · **Status:** Usulan
- **Keputusan:** Scraper ditulis dengan Python.
- **Alasan:** SIRUP sudah mengirim JSON, jadi tidak ada halaman yang perlu diurai.
  Yang memperlambat adalah jeda ke server, bukan bahasa pemrogramannya. Python paling
  cepat dibuat dan paling mudah untuk mengolah data.
- **Ditolak:** Rust (lebih cepat tapi tidak terasa bedanya di sini, dan lebih sulit
  dirawat); Node.js (bisa, tapi tidak ada kelebihan khusus).

## TD-02 — FastAPI untuk API, bukan NestJS
- **Tanggal:** 10 Nov 2026 · **Status:** Usulan
- **Keputusan:** API memakai FastAPI (Python), menyimpang dari pilihan bawaan NestJS.
- **Alasan:** Cukup satu bahasa untuk scraper dan API. Model data Pydantic bisa dipakai
  bersama. Tim cukup merawat satu ekosistem.

## TD-03 — PostgreSQL + pg_trgm untuk pencarian kata kunci
- **Tanggal:** 10 Nov 2026 · **Status:** Usulan
- **Keputusan:** Pencarian nama paket memakai indeks trigram di PostgreSQL.
- **Alasan:** Gratis dan tanpa server tambahan. Untuk 3,6 juta baris, kecepatannya
  cukup (target < 1 detik). Elasticsearch atau Meilisearch baru dipertimbangkan kalau
  ternyata tidak cukup.

## TD-04 — Ambil ulang penuh setiap hari, urut berdasarkan `id` naik
- **Tanggal:** 10 Nov 2026 · **Status:** Usulan
- **Keputusan:** Setiap malam semua paket diambil ulang (±730 permintaan, jeda 2 detik,
  ±25–35 menit), diurutkan berdasarkan `id` naik, lalu disimpan dengan upsert.
- **Alasan:** SIRUP tidak menyediakan filter "berubah sejak tanggal tertentu". Hanya
  dengan ambil ulang penuh, perubahan pagu dan paket yang dibatalkan bisa ketahuan.
  Urutan `id` naik membuat halaman tidak bergeser saat ada paket baru masuk.
- **Catatan:** Tanpa parameter urutan, SIRUP membalas kosong. Hal ini sudah dibuktikan
  saat uji coba.
- **Kenapa tidak hanya mengambil yang baru:** SIRUP tidak punya kolom tanggal dibuat
  atau diubah, dan ID-nya belum terbukti selalu bertambah untuk paket baru (ID terbesar
  ±965 juta milik paket pemilihan Jan 2026). Selain itu, perubahan pagu dan pembatalan
  hanya ketahuan kalau semua paket dicek ulang.
- **Rencana penghematan:** setiap run mencatat ID mana saja yang baru muncul. Setelah
  ±2 minggu, polanya dianalisis. Kalau terbukti paket baru selalu mendapat ID lebih
  besar, kita pindah ke pola **harian: hanya paket baru (beberapa menit)** dan
  **mingguan: ambil ulang penuh**.

## TD-05 — Penyesuaian saat implementasi (Tahap 1)
- **Tanggal:** 10 Nov 2026 · **Status:** Berlaku
- **Keputusan dan alasannya:**
  1. **Satu paket kode, satu image.** Scraper dan API ada di satu paket Python
     (`src/sirup/`) dan satu `Dockerfile` (`python:3.12-slim`, berjalan sebagai user biasa,
     bukan root). Tiap layanan di docker-compose hanya beda perintah. Image diberi tag
     `sirup:<versi>` (bawaan `0.1.0`), tidak pernah `latest`. *Alasan:* lebih sedikit yang
     dirawat; scraper dan API memakai aturan data yang sama.
  2. **Satu putaran untuk semua jenis pengadaan.** `jenisPengadaan` dikosongkan, karena
     SIRUP langsung mengembalikan semua jenis (3.638.893 paket). Kolom `id_jenis` di
     `scrape_runs` dan env `JENIS_PENGADAAN` dihapus; penandaan paket hilang berlaku per
     tahun anggaran.
  3. **Penjadwal = loop Python biasa**, bukan `supercronic`. Env `SCRAPE_TIME=02:00` (WIB)
     menggantikan `SCRAPE_CRON`. Tidak perlu mengunduh program tambahan. Bila run gagal,
     dicoba lagi maksimal 3× dengan jeda 30 menit, melanjutkan dari checkpoint.
  4. **Aturan melanjutkan (AC-04):** run terakhir yang statusnya `running`/`failed`,
     checkpoint > 0, dan dimulai < 24 jam lalu akan dilanjutkan pada baris yang sama.
     Lebih lama dari itu, mulai baru dari nol.
  5. **Pengaman tambahan.** Bila lebih dari 50% baris dalam satu halaman tidak valid, run
     dihentikan (tanda format SIRUP berubah). Penandaan paket hilang hanya dilakukan bila
     run lengkap, `recordsTotal` > 0, baris valid ≥ 99%, dan jumlah tidak turun > 20%.
     Alasan bila dilewati dicatat di kolom baru `note`; jumlah yang ditandai di kolom baru
     `rows_deactivated`.
  6. **Retry yang sopan.** Diulang: gangguan jaringan, HTTP 429/5xx, body kosong, JSON
     rusak. Tidak diulang: HTTP 4xx lain (mis. 403) dan bentuk JSON yang berubah.
  7. **API memakai psycopg langsung** (dengan pool koneksi), bukan SQLAlchemy Core.
     SQLAlchemy hanya dipakai oleh Alembic. Semua nilai dari pengguna lewat parameter
     terikat; urutan (`sort`) memakai daftar putih.
  8. **Batas halaman API:** `page × size` maksimal 10.000. Lebih dari itu dibalas 422
     dengan pesan untuk mempersempit filter, supaya database tidak terbebani.
  9. **Akses baca saja untuk API, dua lapis:** role database hanya punya izin SELECT, dan
     koneksi API memakai `default_transaction_read_only=on` + batas waktu query 10 detik.
  10. **Backup:** container `postgres:16` yang mengecek tiap 10 menit; setelah pukul 04:00
      WIB dibuat satu `pg_dump` per hari, disimpan 7 hari. Gagal backup dicatat di log dan
      dicoba lagi, container tidak ikut mati.
  11. **Pagu disimpan sebagai rupiah bulat** (angka desimal dibulatkan setengah ke atas).
      Nilai yang tidak muat di tipe kolom database dianggap baris rusak, bukan membuat run
      gagal.
- **Dampak:** tidak ada perubahan pada kriteria diterima (AC-01..AC-11) maupun biaya.

## TD-06 — Paket tanpa jenis pengadaan diterima + API dipercepat
- **Tanggal:** 11 Nov 2026 · **Status:** Berlaku
- **Masalah 1:** Pengambilan penuh kedua menyimpan 3.638.692 dari 3.638.893 paket; 201 paket
  ditolak. Penyebabnya, SIRUP memang menerbitkan paket **tanpa nama jenis pengadaan**
  (`jenisPengadaan` tidak ada; yang terlihat `idJenisPengadaan` = 6), misalnya milik Kantor
  Bahasa Kepulauan Riau. Paket ini asli, jadi seharusnya ikut disimpan.
- **Keputusan 1:**
  - Kolom `id_jenis_pengadaan` dan `jenis_pengadaan` boleh kosong (migrasi 0002). Migrasi
    ini hanya bisa dibatalkan kalau tidak ada lagi baris yang kosong; sistem tidak akan
    menghapus data diam-diam.
  - Setiap halaman yang berisi baris rusak sekarang menulis peringatan di log: jumlahnya,
    kolom dan jenis kesalahannya, serta sampai 3 contoh ID. Sebelumnya baris rusak tidak
    meninggalkan jejak.
- **Masalah 2:** API lambat (1,2–9 detik), padahal targetnya < 1 detik (AC-07). Yang lambat
  ternyata **menghitung jumlah hasil secara persis** (`count(*)`). Untuk filter yang luas,
  database harus membaca seluruh tabel 3,3 GB. Mengambil 20 data teratasnya sendiri hanya
  butuh < 1 milidetik.
- **Keputusan 2:**
  1. **Jumlah hasil dihitung sampai 10.000 saja.** Halaman yang bisa dibuka memang dibatasi
     10.000 hasil (TD-05), jadi menghitung lebih dari itu tidak ada gunanya. Jawaban API
     mendapat kolom baru `total_terbatas`: `true` artinya hasilnya **lebih dari 10.000**
     (dan `total` berhenti di 10.000). Kolom lain tidak berubah, jadi pemakai lama tetap jalan.
  2. **Data asli SIRUP (`raw`) dipindah ke tabel sendiri `paket_raw`** (migrasi 0003).
     Kolom ini memakan ±600 dari ±900 byte per baris. Tabel `paket` menyusut dari 3,3 GB
     menjadi 1,1 GB, sehingga lebih banyak yang muat di memori server 4 GB. Saat dibangun
     ulang, baris disusun urut pagu, dan indeks pagu/terbaru diberi `id` sebagai penentu
     urutan yang sama dengan API.
  3. **Pengaturan PostgreSQL untuk VPS 2 CPU / 4 GB** (sesuai BRD) lewat `command:` di
     docker-compose: `shared_buffers=1GB`, `effective_cache_size=3GB`, `work_mem=32MB`,
     `maintenance_work_mem=256MB`, `random_page_cost=1.1`, `effective_io_concurrency=200`,
     paralel maksimal 2, `max_wal_size=2GB`, `jit=off`, `shm_size: 256mb`. Bisa ditimpa
     lewat variabel `PG_*` di `.env`.
  4. **Tidak memakai** indeks parsial `WHERE is_active`: saat ini semua paket aktif, jadi
     tidak ada untungnya. Dipertimbangkan lagi kalau paket nonaktif sudah banyak.
- **Hasil ukur** (database penuh 3,6 juta paket, 12 pencarian × 3 kali, dari curl):
  p95 sebelum **5,0 detik**; sesudah **0,50 detik**; sesaat setelah database dinyalakan
  ulang (memori masih kosong) **1,1 detik**, lalu turun lagi setelah beberapa pencarian.
  Tabel lengkap ada di `docs/ARCHITECTURE.md` §6.
- **Harga yang dibayar:**
  - Migrasi 0003 membangun ulang tabel. Di laptop pengembang butuh ±5 menit, dan selama itu
    API tertahan (bisa menjawab 503). Jalankan di luar jam kerja.
  - `total` bukan angka persis kalau hasilnya lebih dari 10.000.
  - Detail paket (`/v1/paket/{id}`) mengambil `raw` dari `paket_raw`.
- **Biaya:** Rp0.
