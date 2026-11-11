# Laporan QA: Kolom `sumber` (INAPROC + SIRUP) — 11 Nov 2026

Yang diuji: commit 7121a5f, b8dd294, 3fdbb38, 73ceb71 (ADR TD-09 dan TD-10).
Migrasi di database asli: 0007 (terbukti, lihat bagian data asli).

## VONIS: LULUS

Semua persyaratan TD-09 dan TD-10 terbukti lewat tes otomatis dan pengecekan data asli.
Tidak ada cacat tingkat Kritis, Tinggi, atau Sedang. Ada 3 catatan Rendah (di bawah).

## Cara tes dan hasil (output asli)

Database tes terpisah (container `sirup-qadb`, sudah dihentikan). Database asli hanya dibaca.

| Perintah | Hasil |
|---|---|
| `pytest -q tests/unit` | `53 passed in 6.69s` |
| `TEST_DATABASE_URL=...55433/sirup_test pytest -q` (semua tes, sebelum tes tambahan QA) | `157 passed` |
| sama, setelah tes tambahan QA ditambahkan | `165 passed, 1 warning in 56.92s` |
| `pytest -q tests/integration/test_qa_sumber.py` (final, setelah format ruff) | `8 passed in 6.23s` |
| `ruff check` file tes QA | bersih (setelah diperbaiki) |

## Persyaratan -> bukti

| Persyaratan | Bukti |
|---|---|
| Kolektor tulis ke file dan (bila `INAPROC_DATABASE_URL` ada) upsert per halaman SEBELUM simpan kursor | `test_run_also_upserts_each_page_into_store`, `test_db_failure_does_not_advance_cursor` (tidak ada `state_2026.json` bila DB gagal) |
| `load [FILE]` memuat file JSONL; baris rusak dilewati | `test_load_file_skips_invalid_lines`; kode di `__main__.py` (tanpa URL DB -> keluar kode 1) |
| Kumpul ulang tidak menggandakan baris | `test_upsert_inserts_then_updates_without_duplicates`; QA: `test_td10_concurrent_collectors_do_not_duplicate_tag_or_rows` (4 kolektor bersamaan, 50 baris: tetap 50 baris, tag tidak dobel, tanpa error) |
| Migrasi 0006 / 0007 (default `{sirup}`, check, indeks GIN) | QA: `test_td10_check_constraint_rejects_empty_and_unknown_tag` (`{}`, `{lkpp}`, NULL ditolak), `test_td10_default_sumber_is_sirup`; `\d paket` di DB tes menampilkan `ck_paket_sumber` dan `ix_paket_sumber gin` |
| Downgrade 0007 -> 0006 -> 0007 | QA: `test_td10_downgrade_0007_to_0006_and_back...`: paket hanya-INAPROC terhapus, paket SIRUP tetap, naik lagi semua `{sirup}`. Juga fixture `db_url` selalu uji naik-turun-naik dari 0 |
| Satu baris per kode RUP; INAPROC hanya menambah tag pada paket SIRUP; isi tetap isi SIRUP | `test_existing_sirup_package_only_gets_inaproc_tag` |
| Paket yang tidak ada di SIRUP dimasukkan `{inaproc}` dan diperbarui hanya selama `{inaproc}` | `test_inaproc_only_package_is_added_and_refreshed`; QA: lifecycle (INAPROC kumpul ulang tidak menimpa isi SIRUP) |
| Scraper menimpa isi, tambah `sirup` sekali, hanya menonaktifkan paket ber-`sirup` | `test_sirup_takes_over_content_and_keeps_inaproc_tag`, `test_inaproc_only_package_is_never_deactivated_by_sirup`; QA: `test_td10_package_lifecycle...` (INAPROC-saja -> SIRUP -> hilang dari SIRUP: setelah 3 run paket nonaktif, tag `{inaproc,sirup}` tetap) |
| API `/v1/paket` menampilkan `sumber`, filter `sumber=sirup|inaproc`, selain itu 422 | `test_default_page_size_and_fields`, `test_validation_errors[sumber=lkpp]`; QA: `test_td10_sumber_filter_combined_with_other_filters` (digabung `paket`, `pagu_min`, `satuan_kerja`, `pemilihan`; nilai `""`, `SIRUP`, `sirup,inaproc`, `{sirup}`, `x' OR 1=1--` semua 422; tanpa kunci API 401) |
| `/v1/paket/{id}` paket INAPROC-saja mengembalikan `raw` dari `inaproc_rup` | `test_filter_sumber_and_inaproc_only_detail`; data asli: id 42605114 -> `sumber=['inaproc']`, `raw` berisi `kode_rup, nama_paket, ...` |
| Nilai `-` | QA: `test_td10_dash_values_become_null...` (`-` jadi NULL) |
| Rahasia akses hanya dari env | `git grep` pola x-secret/`INAPROC_X_SECRET=nilai` di file terlacak: tidak ada temuan; `git check-ignore -v .env` -> `.gitignore:2:.env`; `.env` tidak terlacak git; tes unit `test_secret_is_read_from_env_and_hidden` lulus. (Nilai rahasia tidak dicetak di laporan ini.) |

## Data asli (hanya baca)

```
sumber            | jumlah
{inaproc}         |   12436
{sirup}           | 3622902
{sirup,inaproc}   |   40384      -> total 3.675.722 = total paket (cocok)
paket tanpa sumber / kosong      : 0
inaproc_rup                      : 52820 = 12436 + 40384 (cocok persis)
inaproc_rup tanpa tag di paket   : 0
tag inaproc tanpa baris inaproc_rup : 0
paket dengan tag dobel           : 0
paket {inaproc} yang nonaktif    : 0
alembic_version                  : 0007
```

API hidup (127.0.0.1): `sumber=inaproc` 200, `sumber=sirup` 200, tanpa filter 200, `sumber=lkpp` 422 dengan pesan `Input should be 'sirup' or 'inaproc'`.

## Cacat

Tidak ada cacat Kritis/Tinggi/Sedang.

Catatan Rendah (perilaku yang sebaiknya diketahui, bukan pelanggaran persyaratan):

1. **L-1 `total_nilai` kosong tercatat `pagu = 0`.** Paket INAPROC dengan total_nilai NULL masuk ke `paket` dengan pagu 0 (tes `test_td10_dash_values_become_null_and_null_total_is_zero_pagu`). Pembaca API tidak bisa membedakan "0 rupiah" dan "tidak diketahui". Kolom `pagu` memang NOT NULL; perlu keputusan bos/tech-lead bila ingin dibedakan.
2. **L-2 Paket hanya-INAPROC tidak punya bulan pemilihan**, sehingga tidak muncul bila filter `pemilihan` dipakai (terbukti di tes; sudah tertulis di dokumentasi API).
3. **L-3 Paket hanya-INAPROC tidak pernah dinonaktifkan** (sesuai desain TD-10). Bila INAPROC menghapus paket, paket itu tetap aktif di API. Risiko data basi, bukan bug.

## Belum diverifikasi

- Kolektor `python -m sirup.inaproc` belum dijalankan ke situs asli (dilarang di tugas ini); alur HTTP hanya diuji lewat tiruan (respx).
- Layanan Docker `inaproc` di compose dengan `depends_on: migrate` tidak dijalankan.
- Uji beban/ukuran besar (3,6 juta baris) hanya lewat query baca; kecepatan filter `sumber` pada data asli tidak diukur (total dibatasi 10.000 oleh API).

## Risiko tersisa

Jika scraper SIRUP dan kolektor INAPROC berjalan bersamaan di data asli, hanya diuji antar-kolektor (aman). Kombinasi scraper + kolektor bersamaan belum diuji.

## File yang ditambahkan

- `tests/integration/test_qa_sumber.py` (8 tes). Tidak ada kode aplikasi yang diubah.
