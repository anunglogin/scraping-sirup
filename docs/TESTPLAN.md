# Rencana Uji — Pengumpul Data SIRUP

Tanggal: 10 Nov 2026 · Penguji: QA · Sumber kriteria: `docs/BRD.md` §6 (AC-01..AC-11),
filter yang disepakati bos (nama paket, pemilihan/bulan, pagu min–max, satuan kerja),
dan permintaan dokumentasi API lewat Swagger.

## 1. Cara uji (ringkas)

| Lapisan | Alat | Catatan |
|---|---|---|
| Otomatis | `pytest` (99 tes) + `ruff check .` | Database tiruan: `postgres:16` sementara di port 55434 |
| Manual di sistem hidup | skrip Python + `curl` ke `127.0.0.1:8000` | Kunci API dibaca dari `.env`, tidak ditulis di laporan |
| Pengujian merusak | database sementara + SIRUP palsu lokal | Tidak menyentuh volume `pgdata`, tidak membebani server SIRUP asli |
| Mesin bersih (AC-09) | proyek compose terpisah `sirupqa`, dibangun dari `git archive HEAD`, `.env` sendiri, port 18000 | Dihapus dengan `down -v` setelah selesai |

Perintah otomatis:
```
.venv\Scripts\python -m ruff check .
set TEST_DATABASE_URL=postgresql://qa:qa@127.0.0.1:55434/qa
.venv\Scripts\python -m pytest -q
```

## 2. Kriteria diterima

| Kode | Cara menguji | Tes otomatis | Perintah manual |
|---|---|---|---|
| AC-01 jumlah sama (selisih ≤ 0,5%) | Bandingkan `records_total` SIRUP dengan jumlah baris di database; nilai selisih dihitung | `test_full_scrape_stores_everything_and_logs_run`, `test_real_rows_without_jenis_pengadaan_are_stored` | `psql -c "select count(*) from paket"`; `GET /v1/scrape-runs` (kolom `records_total`) |
| AC-02 tanpa paket ganda | Hitung `count(*)` vs `count(distinct id)`; jalankan ulang scraper dan hitung lagi | `test_rerun_creates_no_duplicates`, `test_duplicate_ids_within_a_page_are_collapsed` | `select count(*), count(distinct id) from paket`; jalankan `python -m sirup.scraper once` dua kali |
| AC-03 paket baru ≤ 24 jam | Cek jadwal harian (02:00 WIB) dan bahwa paket baru di SIRUP ikut masuk di run berikutnya | `test_changed_pagu_updates_updated_at_only_for_that_row`, `test_next_run_*` | Sesudah run 02:00: `GET /v1/paket?sort=terbaru` dan `select count(*) from paket` dibanding `recordsTotal` |
| AC-04 bisa dilanjutkan | Matikan paksa proses scraper di tengah jalan (kill), jalankan lagi, lihat mulai dari halaman terakhir, bukan dari 0 | `test_failure_mid_run_then_resume_from_checkpoint`, `test_killed_process_leaves_resumable_run`, `test_old_failed_run_is_not_resumed` | SIRUP palsu lokal 1.000 paket, halaman 100: kill saat berjalan, jalankan lagi, baca log "resuming run … from start=" |
| AC-05 jeda + berhenti sendiri | Periksa jeda antarhalaman dan perilaku saat 429/500/403/body kosong/JSON rusak | `test_delay_between_pages`, `test_invalid_json_and_429_are_retried`, `test_5xx_retried_then_gives_up`, `test_empty_body_is_retried`, `test_403_not_retried` | SIRUP palsu mode `always429/always500/always403/empty/garbage`: hitung jumlah permintaan dan status run |
| AC-06 empat filter | Tiap filter sendiri dan gabungan; tiap baris hasil dicek benar-benar cocok | `test_filter_*`, `test_filters_combined`, `test_like_wildcards_are_literal` | Skrip `qa_api.py filters` (lihat laporan) |
| AC-07 < 1 detik untuk 95% | ≥ 30 permintaan beragam, ukur p95, ulangi dengan variasi baru | — (tidak otomatis; sifatnya ukuran) | Skrip `qa_api.py latency` dan `latency2` |
| AC-08 tolak tanpa kunci | Tanpa kunci, kunci salah, kunci kosong, kunci terpotong/ditambah | `test_requires_valid_key`, `test_any_configured_key_works`, `test_health_is_public` | `curl -i http://127.0.0.1:8000/v1/paket` → 401 |
| AC-09 `docker compose up -d` | Mesin bersih: checkout `HEAD`, `.env` dari `.env.example`, `up -d --build`, cek health, 401, scraper 1 halaman nyata → API | — | `docker compose -p sirupqa up -d --build` lalu `curl /health` |
| AC-10 backup harian + pulih | File backup ada; restore ke database sementara; hitung baris sama dengan sumber | — | `pg_dump`/`pg_restore` ke postgres sementara; `select count(*)` |
| AC-11 catatan run | Tabel `scrape_runs`: mulai, selesai, jumlah, error | `test_scrape_runs`, `test_full_scrape_stores_everything_and_logs_run` | `GET /v1/scrape-runs`; run gagal (429) terisi kolom `error` |

## 3. Empat filter yang disepakati

| Filter | Yang dicek | Tes otomatis |
|---|---|---|
| `paket` (nama paket, kata kunci) | Mengandung kata; besar/kecil sama; `%` dan `_` dicari apa adanya; 3–100 huruf; unicode; input mirip SQL injection | `test_filter_paket_case_insensitive`, `test_like_wildcards_are_literal`, `test_validation_errors` |
| `pemilihan_bulan` (1–12, boleh diulang) | Hanya bulan terpilih; duplikat tidak masalah; 0, 13, huruf → 422 | `test_filter_pemilihan_bulan_repeatable` |
| `pagu_min` / `pagu_max` | Batas ikut (≥, ≤); min = max; min > max → 422; angka sangat besar; negatif → 422 | `test_filter_pagu_range` |
| `satuan_kerja` (kata kunci) | Sama seperti `paket` | `test_filter_satuan_kerja` |

Tambahan: pagination (`page`, `size`, batas 10.000), `total_terbatas`, urutan (`sort`), detail paket
(200/404/422), `scrape-runs` (`limit`), metode selain GET → 405.

## 4. Dokumentasi Swagger (permintaan bos)

| Cek | Cara |
|---|---|
| `/docs` (Swagger) dan `/redoc` terbuka, `/openapi.json` ada | `test_swagger_and_redoc_pages`, `curl` |
| Ada tombol **Authorize** dan skema kunci `X-API-Key` | `test_openapi_documents_security_and_parameters`; dibuka di browser, klik Authorize, tempel kunci, Execute |
| Parameter tiap filter ada deskripsi + contoh, kode 401/404/422/503 terdokumentasi | `test_openapi_documents_security_and_parameters`, `test_openapi_documents_total_terbatas`; baca `/docs` |

## 5. Kasus tepi yang diburu

Kosong/spasi, kata kunci 2 dan 101 huruf, karakter NUL, emoji, huruf Turki `İ`, `%%%`/`___`/`\`,
pagu sangat besar (2^63, 10^30), `pemilihan_bulan` berulang (100 kali), halaman di luar batas,
`page=10^9`, ID detail 0/negatif/huruf/2^63, SQL injection di `paket`, `satuan_kerja`, `sort`,
`pagu_min`, ID detail. Scraper: 429, 500, 403, body kosong, JSON rusak, > 50% baris rusak,
`recordsTotal` = 0, `recordsTotal` turun > 20%, halaman kosong di tengah run, proses dimatikan paksa.

## 6. Uji ketahanan tes (apakah tes bisa gagal)

Pada salinan kode di folder sementara, batas pengaman diubah sengaja (batas turun 20% → 99%,
kelengkapan 99% → 0, penjaga total-nol dimatikan, 429 tidak diulang, batas baris rusak 50% → 99%).
Tes harus gagal; hasilnya ada di laporan QA.

---

# Pembaruan 11 Nov 2026 (uji ulang setelah review keamanan)

Kode yang diuji: `main` @ `e47a304` (commit `3165088..e47a304`, belum di-push). Laporan: `docs/QA-REPORT-2026-11-11.md`.

## Perubahan pada rencana

| Area | Perubahan | Cara menguji | Tes otomatis baru/ubahan |
|---|---|---|---|
| Kata kunci `paket`/`satuan_kerja` (M1/F-01, D2, D3) | Wajib ada 3 huruf/angka berurutan, tanpa karakter kontrol → 422 | 18 nilai (`%%%`, `___`, `a..`, emoji, NUL, tab, DEL, `100%`, `ab1`, …) ke sistem hidup | `test_untrigrammable_keywords_rejected_fast`, `test_validation_errors` |
| Beban berlebih (F-01) | `statement_timeout` 3 detik, batas pencarian serentak (429 + `Retry-After`), pool penuh → 503 "sibuk" | 20 permintaan serentak tak-bertrigram; 30 serentak gabungan berat; periksa `/health` dan detail selama itu | `test_search_slots_full_returns_429_but_health_and_detail_work`, `test_concurrent_slow_searches_do_not_starve_pool` |
| Halaman kosong di tengah run (M2/F-02, D4) | Jadi error yang diulang; run `failed`, checkpoint utuh, tanpa penonaktifan | SIRUP palsu mode `emptymid` dan `emptylast`, lalu SIRUP pulih → harus melanjutkan | `test_empty_page_before_total_fails_run_and_keeps_checkpoint`, `test_empty_page_before_records_total_is_retried_then_fails` |
| Penonaktifan 2 kali berturut-turut (F-05) | `missing_runs`, `rows_missing`; nonaktif hanya setelah hilang di 2 run lengkap berturut-turut | SIRUP palsu mode `skip` (10% hilang): run 2 = missing 100/nonaktif 0, run 3 = nonaktif 100, muncul lagi = aktif; hilang–muncul–hilang tidak menonaktifkan | `test_missing_package_deactivated_after_two_complete_runs` |
| Filter `pemilihan=YYYY-MM` (D6, migrasi 0005) | Menggantikan `pemilihan_bulan` | Format valid/tidak valid (13 kasus), 24 vs 25 nilai, gabungan dengan filter lain, `pemilihan_bulan` lama | `test_filter_pemilihan_month_and_year`, `test_pemilihan_tahun_returned`, `test_pemilihan_tahun_parsed_by_database` |
| Pagu negatif | Diterima | SIRUP palsu mode `negpagu`: 10 baris negatif tersimpan | `test_negative_pagu_from_sirup_is_kept` |
| Akses jarak jauh (M3/F-03) | SSH tunnel bawaan; profil `https` (Caddy) opsional | `docker compose config` bawaan vs `--profile https`; `caddy validate`; Caddy tanpa `API_DOMAIN` menolak jalan; tidak ada saran `0.0.0.0` | — |
| F-10 / F-11 / F-12 / F-14 | Log kegagalan CLI; kesalahan konfigurasi tidak membocorkan nilai; resume hanya hari WIB yang sama dan < 20 jam; backup umask 077 + timeout | Lihat laporan | `test_config_errors_do_not_echo_secrets`, `test_once_logs_failures_before_exit_code`, `test_failed_run_from_previous_wib_day_is_not_resumed` |
| AC-03 / AC-10 | Dinilai dari run 4 (02:00 WIB) dan backup 11 Nov | Bandingkan `recordsTotal` SIRUP (1 permintaan) dengan database; pulihkan dump 11 Nov + migrasi | — |

## Skrip uji ulang (di folder sementara sesi, bukan di repo)

`qa2.py` (kata kunci, pemilihan, serentak, latensi 4 benih × 92 permintaan), `qa2h.py` (campuran berat),
`qa7.py` (regresi filter), `fake_sirup.py` (SIRUP palsu: `ok`, `skip`, `emptymid`, `emptylast`, `negpagu`, `always429`, …),
`mutate.py` (uji mutasi). Perintah otomatis tetap: `ruff check .` dan `pytest -q` dengan `TEST_DATABASE_URL`.
