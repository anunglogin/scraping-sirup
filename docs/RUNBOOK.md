# RUNBOOK — Menjalankan dan Merawat Sistem SIRUP

Semua perintah dijalankan dari folder proyek (yang berisi `docker-compose.yml`).

## 1. Pertama kali menyalakan

1. Pasang Docker (Docker Desktop di Windows/Mac, Docker Engine di Linux).
2. Salin `.env.example` menjadi `.env`, lalu isi:
   - `POSTGRES_PASSWORD` dan `API_DB_PASSWORD`: **huruf dan angka saja**. Buat dengan
     `python -c "import secrets;print(secrets.token_hex(24))"`.
   - `API_KEYS`: buat dengan `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
   - File `.env` berisi rahasia: **jangan** dikirim lewat chat dan **jangan** di-commit.
3. Nyalakan:
   ```
   docker compose up -d --build
   ```
4. Periksa:
   ```
   docker compose ps
   curl http://127.0.0.1:8000/health
   ```
   Yang benar: `db` dan `api` berstatus *healthy*, `migrate` *Exited (0)*, `scraper` dan
   `backup` *Up*. `/health` menjawab `{"status":"ok","database":"ok"}`.

Scraper otomatis berjalan setiap hari pukul **02:00 WIB**. Pengambilan pertama (±3,6 juta
paket) butuh ±25–35 menit. Kalau tidak ingin menunggu sampai jam 2 pagi, jalankan manual
(bagian 3).

## 2. Mematikan dan menyalakan lagi

```
docker compose stop          # matikan sementara, data aman
docker compose start         # nyalakan lagi
docker compose down          # matikan dan hapus container, data TETAP aman (volume)
docker compose up -d         # nyalakan lagi
```
**Jangan** memakai `docker compose down -v` kecuali memang ingin **menghapus seluruh
database**.

## 3. Menjalankan pengambilan data secara manual

```
docker compose run --rm scraper python -m sirup.scraper once
```
- Uji coba singkat (hanya 2 halaman = 10.000 paket):
  ```
  docker compose run --rm -e SCRAPE_MAX_PAGES=2 scraper python -m sirup.scraper once
  ```
- Ambil ulang tahun tertentu:
  ```
  docker compose run --rm -e TAHUN_ANGGARAN=2025 scraper python -m sirup.scraper once
  ```
- Kalau muncul pesan *another scrape run is in progress*, berarti pengambilan lain sedang
  berjalan. Tunggu sampai selesai.
- Kalau pengambilan terputus (internet mati, server mati), jalankan lagi perintah yang sama
  dalam 24 jam: proses **melanjutkan** dari posisi terakhir, tidak mulai dari nol.

## 4. Melihat log dan riwayat

```
docker compose logs -f scraper        # log scraper (Ctrl+C untuk keluar)
docker compose logs --tail 100 api
docker compose logs --tail 50 backup
```
Riwayat pengambilan (waktu mulai/selesai, jumlah, error):
```
curl "http://127.0.0.1:8000/v1/scrape-runs?limit=10" -H "X-API-Key: <kunci>"
```
Atau langsung di database:
```
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id,status,started_at,finished_at,records_total,rows_received,rows_inserted,rows_updated,rows_deactivated,error,note FROM scrape_runs ORDER BY id DESC LIMIT 10"'
```
Arti kolom `note`: alasan sistem **tidak** menandai paket yang hilang (demi keamanan data),
misalnya karena jumlah dari SIRUP turun drastis. Ini bukan error.

## 5. Backup dan pemulihan

- Backup otomatis dibuat sekali sehari setelah pukul **04:00 WIB** di folder `./backups`
  (nama file `sirup-YYYY-MM-DD.dump`), disimpan **7 hari**.
- Sebaiknya folder `backups` disalin juga ke tempat lain (disk lain / cloud) secara rutin.

### Memulihkan backup
1. Hentikan yang menulis ke database:
   ```
   docker compose stop scraper api
   ```
2. Pulihkan (ganti tanggalnya):
   ```
   docker compose exec backup sh -c 'pg_restore --clean --if-exists --no-owner --dbname="$PGDATABASE" /backups/sirup-2026-11-10.dump'
   ```
3. Berikan lagi izin baca untuk API (perlu bila tabel dibuat ulang):
   ```
   docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO $API_DB_USER"'
   ```
4. Nyalakan lagi:
   ```
   docker compose start scraper api
   ```

### Uji pemulihan tanpa mengganggu data asli
```
docker compose exec backup sh -c 'createdb sirup_restore_test && pg_restore --no-owner --dbname=sirup_restore_test /backups/sirup-2026-11-10.dump && psql -d sirup_restore_test -c "SELECT count(*) FROM paket" && dropdb sirup_restore_test'
```

## 6. Memperbarui versi dan rollback

Setiap versi punya tag image sendiri (`sirup:0.1.0`, `sirup:0.2.0`, ...). Tidak pernah
memakai `latest`.

**Memperbarui:**
1. Ambil kode versi baru, ubah `APP_VERSION` di `.env` (mis. `0.2.0`).
2. `docker compose up -d --build`
   (migrasi database berjalan otomatis lewat layanan `migrate`).

**Rollback ke versi sebelumnya:**
1. Bila versi baru mengubah skema database, turunkan dulu migrasinya **dengan image versi
   baru** (yang tahu cara menurunkannya), mis.:
   ```
   docker compose run --rm migrate alembic downgrade -1
   ```
2. Ubah `APP_VERSION` di `.env` kembali ke versi lama (mis. `0.1.0`).
3. `docker compose up -d`
4. Periksa `docker compose ps` dan `curl http://127.0.0.1:8000/health`.

Sebelum memperbarui di server produksi, pastikan ada backup hari itu di folder `backups`.

## 7. Membuka API dari luar server

API hanya mendengarkan di server itu sendiri (`API_BIND=127.0.0.1`). **Jangan** mengubahnya
ke `0.0.0.0`: kunci API akan terkirim tanpa enkripsi dan bisa disadap. Docker juga melewati
firewall `ufw` untuk port yang dibuka. Ada dua cara yang aman.

### Cara A (bawaan): SSH tunnel, Rp0, tanpa domain
Cocok untuk beberapa orang di tim. Setiap orang perlu akun SSH di server.

Di komputer masing-masing (Windows PowerShell, macOS, atau Linux):
```
ssh -N -L 8000:127.0.0.1:8000 namauser@ALAMAT-IP-SERVER
```
Biarkan jendela itu terbuka, lalu buka http://127.0.0.1:8000/docs di browser komputer
sendiri. Lalu lintasnya terenkripsi oleh SSH. Tekan Ctrl+C untuk menutup tunnel.
Kalau port 8000 di komputer sendiri sudah terpakai, ganti angka pertama, misalnya
`-L 18000:127.0.0.1:8000`, lalu buka http://127.0.0.1:18000/docs.

### Cara B (opsional): HTTPS dengan Caddy, untuk aplikasi lain atau tim yang lebih besar
1. Siapkan (sub)domain, mis. `sirup.namaperusahaan.co.id`, lalu buat DNS record **A** yang
   mengarah ke IP server. Biaya: Rp0 kalau sudah punya domain. Kalau belum, domain `.com`
   atau `.id` sekitar **Rp150.000–200.000 per tahun**.
2. Buka port 80 dan 443 di firewall server/penyedia VPS.
3. Isi `API_DOMAIN=sirup.namaperusahaan.co.id` di `.env`.
4. Nyalakan:
   ```
   docker compose --profile https up -d
   ```
   Caddy mengambil dan memperbarui sertifikat HTTPS gratis (Let's Encrypt) secara otomatis.
   Caddy juga menambahkan header keamanan (`nosniff`, `no-store`, HSTS).
5. Cek: buka `https://sirup.namaperusahaan.co.id/health`, lalu `/docs`.
   Log: `docker compose logs caddy`.

`API_BIND` tetap `127.0.0.1`; Caddy menghubungi API lewat jaringan internal Docker.
Port database tidak pernah dibuka ke luar.

Untuk mematikan Caddy: `docker compose --profile https stop caddy`.

## 8. Pengaturan PostgreSQL dan perawatan

Database diatur untuk **VPS 2 CPU / 4 GB RAM** (lihat `command:` di `docker-compose.yml`):

| Pengaturan | Nilai | Variabel `.env` untuk mengubah |
|---|---|---|
| `shared_buffers` | 1GB | `PG_SHARED_BUFFERS` |
| `effective_cache_size` | 3GB | `PG_EFFECTIVE_CACHE_SIZE` |
| `work_mem` | 32MB | `PG_WORK_MEM` |
| `maintenance_work_mem` | 256MB | `PG_MAINTENANCE_WORK_MEM` |
| `max_parallel_workers_per_gather` / `max_parallel_workers` | 2 / 2 | `PG_MAX_PARALLEL_WORKERS_PER_GATHER` / `PG_MAX_PARALLEL_WORKERS` |
| `random_page_cost`, `effective_io_concurrency` | 1.1, 200 (disk SSD) | — |
| `max_wal_size`, `jit` | 2GB, off | — |
| `shm_size` (container) | 256mb | — |

Untuk server 8 GB: kira-kira `PG_SHARED_BUFFERS=2GB`, `PG_EFFECTIVE_CACHE_SIZE=6GB`.
Setelah mengubah: `docker compose up -d db`.

**Setelah migrasi besar** (mis. 0003, yang membangun ulang tabel `paket` dalam ±5 menit;
selama itu API tertahan, jadi jalankan di luar jam kerja), segarkan statistik database:
```
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM (ANALYZE) paket" -c "VACUUM (ANALYZE) paket_raw"'
```
Setelah database dinyalakan ulang, beberapa pencarian pertama bisa sedikit lebih lambat
(±1 detik) karena memorinya masih kosong. Ini normal.

## 9. Masalah yang sering terjadi

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| `/health` menjawab 503 | Database belum siap / mati | `docker compose ps`, `docker compose logs db` |
| API menjawab 401 | Kunci salah atau belum ada di `API_KEYS` | Periksa `.env`, lalu `docker compose up -d api` |
| `api` gagal menyala, log menyebut *API_KEYS* | Ada kunci < 24 karakter | Buat kunci baru (bagian 1) |
| Run `failed` dengan pesan *format* | SIRUP mengubah format datanya | Hubungi tim pengembang (perbaikan ±1 hari) |
| Log scraper berisi *rows invalid: reasons [...]* | Ada baris SIRUP yang bentuknya tidak biasa | Kirim baris log itu (berisi kolom, jenis kesalahan, contoh ID) ke tim pengembang |
| Run `failed` dengan *HTTP 403* | SIRUP menolak akses | Jangan dipaksa; tunggu dan hubungi tim pengembang |
| Ganti `API_DB_PASSWORD` tidak berpengaruh | Role database dibuat hanya saat pertama kali | Jalankan `ALTER ROLE sirup_api PASSWORD '...'` lewat `psql` sebagai admin |
