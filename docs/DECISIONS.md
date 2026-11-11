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

## TD-07 — Perbaikan sebelum deploy (hasil QA 10 Nov dan review keamanan)
- **Tanggal:** 11 Nov 2026 · **Status:** Berlaku
- **Acuan:** `docs/REVIEW-2026-11-10.md` (vonis TAHAN DEPLOY: M1–M3), `docs/QA-REPORT-2026-11-10.md`.
- **Keputusan:**
  1. **API tidak bisa dibuat macet oleh pencarian aneh (M1 / F-01).**
     - Kata kunci `paket` dan `satuan_kerja` wajib memuat minimal 3 huruf/angka berurutan dan
       tidak boleh berisi karakter kontrol. Kalau tidak, dibalas 422 dengan pesan jelas.
       Kata kunci seperti `...`, `a..`, `%%%`, atau emoji dulu memaksa database membaca semua
       baris.
     - Batas waktu query diturunkan dari 10 detik menjadi **3 detik**.
     - Paling banyak `ukuran pool - 2` pencarian (bawaan 8) boleh berjalan bersamaan.
       Pencarian berikutnya menunggu 1 detik, lalu dibalas **429** dengan `Retry-After: 2`.
       Dengan begitu `/health` dan detail paket selalu tetap jalan.
     - Pesan 503 sekarang membedakan "server sibuk" dari "database mati".
     - Semua batas ini bisa diatur lewat `.env` (`DB_STATEMENT_TIMEOUT_MS`, `DB_POOL_*`,
       `SEARCH_MAX_CONCURRENT`).
  2. **Halaman kosong di tengah run = gagal (M2 / F-02).** Halaman kosong padahal posisi
     belum mencapai `recordsTotal` dianggap gangguan SIRUP: dicoba ulang, lalu run berstatus
     `failed` dengan checkpoint utuh. Run seperti ini tidak lagi berstatus "berhasil" dan
     tidak menonaktifkan paket.
  3. **Paket baru ditandai hilang setelah 2 run lengkap berturut-turut (F-05).** Kolom
     `paket.missing_runs` dan `scrape_runs.rows_missing` (migrasi 0004). Paket yang sudah
     nonaktif sebelum migrasi dianggap sudah 2 kali hilang. Pengaman lama (run sebagian,
     anjlok > 20%, < 99%) tetap berlaku.
  4. **Filter pemilihan = bulan + tahun (keputusan bos 11 Nov 2026, D6).**
     - Parameter `pemilihan=YYYY-MM` (boleh diulang, maks. 24) menggantikan
       `pemilihan_bulan`. Ini perubahan yang memutus kompatibilitas: parameter lama diabaikan.
     - Tahun diambil dari teks SIRUP ("December 2025") ke kolom `pemilihan_tahun`.
     - **Penyimpangan dari brief:** kolom ini dihitung oleh **database** (kolom STORED
       generated, migrasi 0005), bukan ditulis oleh scraper. Hasilnya sama ("diisi untuk data
       baru"), tetapi isi lama dan baru tidak mungkin berbeda aturan.
     - Indeks `(tahun_anggaran, pemilihan_tahun, id_bulan)` menggantikan `ix_paket_bulan`.
  5. **Akses dari luar server (M3 / F-03).**
     - Saran `API_BIND=0.0.0.0` dihapus.
     - Cara bawaan: SSH tunnel (Rp0).
     - Pilihan: profil `https` dengan Caddy 2.8.4 (sertifikat Let's Encrypt otomatis, header
       `nosniff`/`no-store`/HSTS). Butuh domain; ±Rp150–200 ribu/tahun kalau belum punya.
     - Uvicorn hanya memercayai header forwarded dari alamat tetap Caddy (`10.89.250.2`).
  6. **Paket berpagu negatif disimpan.** Run 4 menolak 3 paket karena pagu < 0 (mis. id
     68897096 "Belanja Barang dan Jasa BLUD", −58.598.261, Puskesmas Antang). Paket ini asli
     di SIRUP, jadi disimpan apa adanya. Paket ini hanya muncul kalau `pagu_min` tidak diisi.
  7. **Perbaikan kecil:**
     - F-10: kegagalan CLI selalu dicatat di log.
     - F-11: pesan error konfigurasi tidak lagi menampilkan `API_KEYS`/`DATABASE_URL`.
     - F-12: checkpoint hanya dilanjutkan dari run pada **tanggal WIB yang sama** dan
       < 20 jam.
     - F-14: file backup hanya bisa dibaca pemiliknya (`umask 077`), dan `pg_dump` dibatasi
       3 jam.
     - I-01: dokumen sekarang menyebut dengan benar endpoint mana yang terbuka tanpa kunci.
- **Hasil ukur:**
  - 20 pencarian aneh serentak semuanya dijawab 422 dalam ≤ 0,40 detik. Selama itu
    pencarian normal 0,25 detik dan `/health` 0,01 detik.
  - 30 pencarian gabungan berat serentak: 8 dijawab 200, 22 dijawab 429. Selama itu
    `/health` 0,03 detik dan detail paket 0,02 detik.
  - Kecepatan (12 pencarian × 3): p95 **0,93 detik** (lihat ARCHITECTURE §6).
  - Migrasi 0004 + 0005 pada 3,66 juta baris: **9 menit 45 detik**. API tertahan selama
    itu, jadi jalankan di luar jam kerja.
- **Belum dikerjakan (menunggu keputusan bos):** F-04 pemberitahuan gagal (Healthchecks.io),
  F-06 role non-superuser, F-07 lockfile/pin digest, F-08 backup di luar VPS, F-09/D1
  pencarian gabungan, F-13 pengetatan CI, F-15 batas memori, F-16 header di API sendiri.
- **Biaya:** Rp0 (domain opsional untuk HTTPS).

## TD-08 — Pengambil opsional data.inaproc.id tanpa menembus pengaman
- **Tanggal:** 11 Nov 2026 · **Status:** Berlaku
- **Konteks:** Bos meminta data diambil dari `data.inaproc.id/dashboard-api/rup/table`.
  Situs itu memakai Cloudflare dan header `x-secret`. Permintaan dengan `x-secret` acak
  ataupun tanpa `x-secret` (hanya `x-request-time`) sama-sama dibalas
  `403 {"error":"Forbidden."}`.
- **Keputusan:**
  1. Modul `sirup.inaproc` hanya mengirim `tahun`, `limit`, `cursor`, dan header
     `x-request-time`. Tidak ada `x-secret`, cookie browser, penyamaran browser, atau
     trik menembus tantangan anti-bot.
  2. Jawaban 401/403 berarti berhenti (kode keluar 2), tanpa mencoba ulang. 429/5xx dicoba
     ulang paling banyak 3 kali. Jeda antarhalaman minimal 0,5 detik (bawaan 2 detik).
  3. Hasil ditulis ke file (`output/inaproc/`), tidak ke database. Struktur tabel `paket`
     tidak berubah.
  4. Header rahasia atau kunci API baru boleh ditambahkan kalau ada izin tertulis atau
     akses resmi dari LKPP/INAPROC (disimpan di `docs/`). Nilainya disimpan di `.env`,
     tidak di kode.
- **Biaya:** Rp0.
- **Pembaruan 11 Nov 2026:** Bos menyatakan ada izin tertulis dari INAPROC/LKPP untuk
  memakai pasangan header `x-secret` + `x-request-time`. Nilainya dipindah dari kode ke
  `.env` (`INAPROC_X_SECRET`, `INAPROC_REQUEST_TIME`); kalau kosong, header tidak dikirim.
  Batas bawaan per halaman dinaikkan ke 100 (maksimum situs). **Belum selesai:** salinan
  surat izin wajib disimpan di `docs/` sesuai butir 4.

## TD-09 — Hasil pengambil INAPROC disimpan ke tabel `inaproc_rup`
- **Tanggal:** 11 Nov 2026 · **Status:** Berlaku · Mengubah TD-08 butir 3.
- **Konteks:** Bos meminta hasil pengambilan data.inaproc.id masuk ke database, bukan
  hanya ke file.
- **Keputusan:**
  1. Tabel baru `inaproc_rup` (migrasi 0006), kunci utama `kode_rup`. Tabel `paket`
     (sumber SIRUP, dipakai API) tidak diubah dan tidak dicampur.
  2. Pengambilan ulang memperbarui baris yang sama (upsert), tidak membuat duplikat.
     Baris lengkap dari sumber disimpan di kolom `raw` (JSONB).
  3. Pengambil tetap menulis file `output/inaproc/`. Kalau `INAPROC_DATABASE_URL` diisi,
     tiap halaman juga di-upsert ke database **sebelum** posisi halaman disimpan, jadi
     kalau database gagal, halaman itu diambil ulang pada jalan berikutnya.
  4. File yang sudah ada dimasukkan dengan `python -m sirup.inaproc load [FILE]`.
  5. API belum membaca tabel ini (peran baca-saja API tidak diberi akses).
- **Rollback:** `alembic downgrade 0005` menghapus tabel `inaproc_rup` saja.
- **Biaya:** Rp0 (database yang sama).
