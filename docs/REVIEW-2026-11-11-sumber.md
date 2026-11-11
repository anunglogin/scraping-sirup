# Review Kode & Audit Keamanan — Pengambil INAPROC + kolom `paket.sumber`

- **Tanggal:** 11 Nov 2026
- **Peninjau:** code-security-reviewer
- **Cakupan:** `git diff 6003ee5..HEAD` (commit 2a50e89, 77b22cd, 7121a5f, b8dd294, 3fdbb38, 73ceb71) + `tests/integration/test_qa_sumber.py` (belum di-commit)
- **Dasar:** QA vonis LULUS (`docs/QA-REPORT-2026-11-11-sumber.md`), ADR TD-08, TD-09, TD-10
- **Cara kerja:** hanya membaca. Tidak ada kode aplikasi yang diubah, tidak ada tulisan ke database, tidak ada panggilan ke data.inaproc.id, tidak ada container yang di-restart. Kueri ke database hanya `SELECT` / `EXPLAIN`.

## Vonis: AMAN DEPLOY (dengan 2 syarat operasional)

Kodenya tidak punya cacat keamanan kritis: tidak ada celah SQL injection, rahasia INAPROC tidak pernah masuk git,
dan API tetap memakai akun database yang hanya bisa membaca. Dua syarat sebelum dipakai di server produksi:

1. **Jangan jalankan pengambil INAPROC di produksi** sebelum salinan surat izin INAPROC/LKPP disimpan di `docs/` (temuan T1).
2. **Jalankan `ANALYZE paket` setelah migrasi 0007** di database yang sudah berisi data (temuan S1).

---

### Temuan Kritis (wajib diperbaiki sebelum deploy)
Tidak ada.

### Temuan Tinggi

**T1. Surat izin INAPROC/LKPP belum disimpan; pengambil masih meniru browser** — `docs/DECISIONS.md:211`, `docs/DECISIONS.md:198`, `src/sirup/inaproc/client.py:109`
- Risiko bagi bisnis: pengambil memakai pasangan header rahasia (`x-secret` + `x-request-time` yang nilainya tetap). Kalau surat izinnya tidak bisa ditunjukkan, atau isinya tidak mencakup pengambilan otomatis dan penayangan ulang lewat API kita, bos menanggung risiko hukum / akses diblokir. ADR kita sendiri mewajibkan surat itu ada di `docs/`.
- Bukti:
  ```
  docs/DECISIONS.md:211  Batas bawaan per halaman dinaikkan ke 100 (maksimum situs). **Belum selesai:** salinan
                          surat izin wajib disimpan di `docs/` sesuai butir 4.
  docs/DECISIONS.md:198  `x-request-time`. Tidak ada `x-secret`, cookie browser, penyamaran browser, atau
  src/sirup/inaproc/client.py:109   "Referer": "https://data.inaproc.id/rup",
  ```
  Butir 1 TD-08 masih melarang `x-secret` dan penyamaran browser, tetapi kode mengirim `x-secret` (diizinkan oleh pembaruan TD-08) dan header `Referer` seolah-olah dari halaman situs.
- Perbaikan: (a) simpan salinan surat izin di `docs/` (hitamkan nilai rahasia bila tertulis); (b) pastikan surat mencakup pengambilan otomatis, frekuensi, dan izin menampilkan ulang lewat API; (c) perbarui butir 1 TD-08 agar tidak bertentangan dengan pembaruannya; (d) hapus header `Referer` kecuali memang diminta INAPROC. Biaya: Rp0.

### Temuan Sedang (perbaiki dalam sprint ini)

**S1. Migrasi 0007 lupa `ANALYZE paket`; filter `sumber=sirup` bisa gagal (503)** — `db/migrations/versions/0007_paket_sumber.py:72-78`
- Risiko bagi bisnis: pencarian "hanya paket SIRUP, urut terbaru" di halaman dalam memakan 3,4 detik, melebihi batas 3 detik API, sehingga pengguna mendapat pesan "Pencarian terlalu lama". Penyebab: database tidak tahu bahwa 98% paket bersumber SIRUP, jadi memilih jalur yang salah.
- Bukti (database lokal, 3.675.722 baris; `pg_stats` tidak punya baris untuk kolom `sumber`; `last_analyze` = 11:05, sebelum kolom ditambah):
  ```
  EXPLAIN ANALYZE ... WHERE tahun_anggaran=2026 AND is_active AND sumber @> '{sirup}'
                   ORDER BY first_seen_at DESC, id DESC LIMIT 20 OFFSET 9980
   Bitmap Index Scan on ix_paket_sumber (cost=0.00..707.09 rows=18531) (actual ... rows=3703670)
   Execution Time: 3372.299 ms
  -- tanpa filter sumber, kueri yang sama: Index Scan using ix_paket_first_seen ... Execution Time: 21.900 ms
  src/sirup/api/config.py:26  db_statement_timeout_ms: int = Field(default=3_000, ...)
  ```
  Migrasi sebelumnya sudah memakai pola ini: `0003_split_raw_and_rebuild_paket.py:86` dan `0005_pemilihan_tahun.py:43` (`op.execute("ANALYZE paket")`). Migrasi 0007 tidak.
- Perbaikan: tambahkan migrasi 0008 berisi `op.execute("ANALYZE paket")` (atau jalankan langkah RUNBOOK `VACUUM (ANALYZE) paket`, `docs/RUNBOOK.md:186`), lalu ulangi EXPLAIN di atas. Pertimbangkan juga: indeks GIN hanya berguna untuk `sumber=inaproc` (1,4% baris); untuk `sirup` tidak membantu. Biaya: Rp0.
- Catatan: filter `sumber=inaproc` sudah cepat (36–81 ms, memakai `ix_paket_sumber`).

**S2. Paket yang hanya ada di INAPROC tidak pernah menjadi "tidak aktif" dan tanggal "terakhir terlihat" tidak pernah bergerak** — `src/sirup/inaproc/store.py:65-77`, `src/sirup/api/schemas.py:84`
- Risiko bagi bisnis: paket yang sudah dibatalkan dan hilang dari INAPROC tetap tampil sebagai aktif selamanya; pengguna tidak bisa membedakan paket lama dan baru karena `last_seen_at` paket INAPROC tetap tanggal pertama dimasukkan. Saat ini ada 12.436 paket seperti ini.
- Bukti: bagian `ON CONFLICT (id) DO UPDATE SET` pada `MERGE_PAKET_SQL` tidak memperbarui `last_seen_at` maupun `is_active`; deskripsi API berbunyi `"Kapan paket ini terakhir terlihat di SIRUP."` (schemas.py:84). Scraper hanya menonaktifkan paket bertanda `sirup` (sesuai TD-10 butir 3).
- Perbaikan: (a) di `MERGE_PAKET_SQL`, set `last_seen_at = now()` untuk paket `{inaproc}` yang terlihat lagi; (b) setelah satu putaran pengambilan INAPROC selesai penuh (`finished=True`), tandai tidak aktif paket `{inaproc}` tahun itu yang `last_seen_at`-nya lebih lama dari awal putaran (meniru aturan "2 kali hilang" di scraper); (c) perbarui deskripsi `last_seen_at`. Ini tidak mengubah keputusan bisnis TD-10, hanya melengkapinya. Biaya: Rp0.

### Temuan Rendah / Saran Perbaikan (boleh nanti)

**R1. Satu baris aneh dari INAPROC bisa menghentikan pengambil terus-menerus** — `src/sirup/inaproc/client.py:48-53`, `src/sirup/inaproc/store.py:92`
- Risiko: `kode_rup` hanya dibatasi 40 karakter tetapi disimpan sebagai `BIGINT` (maks. 19 digit); `tahun_anggaran` tanpa batas tetapi kolomnya `SMALLINT`; `total_nilai` tanpa batas (dan menerima NaN/Infinity) tetapi kolomnya `NUMERIC(20,2)` / `pagu BIGINT`. Satu nilai di luar batas membatalkan seluruh halaman, posisi halaman tidak disimpan, dan setiap pengulangan gagal di halaman yang sama.
- Bukti: `kode_rup: str = Field(min_length=1, max_length=40)`, `tahun_anggaran: int`, `total_nilai: float | None = None`; `kode = int(row.kode_rup)` (juga menerima `"1_000"` dan angka negatif).
- Perbaikan: `kode_rup` pola `^\d{1,18}$`; `tahun_anggaran` `ge=2000, le=2100`; `total_nilai` `ge=0, le=1e15, allow_inf_nan=False`; baris yang gagal dicatat dan dilewati, bukan menghentikan halaman.

**R2. Tidak ada batas ukuran jawaban dan panjang teks dari INAPROC** — `src/sirup/inaproc/client.py:146,159`, `client.py:47,50-60`
- Risiko: data dari luar ditelan utuh ke memori dan ke JSONB `raw`; `extra="allow"` membuat semua kolom tambahan ikut tersimpan dan ditayangkan di detail API. Sumbernya situs pemerintah, jadi risikonya kecil.
- Perbaikan: tolak jawaban di atas mis. 5 MB (cek panjang `resp.content`), beri `max_length` pada kolom teks (mis. 2.000), dan batasi ukuran `raw`.

**R3. Jalur lama `python -m sirup.inaproc.client` ganda dan tanpa pelindung rumus Excel** — `src/sirup/inaproc/client.py:166-243`
- Bukti: `save()` menulis CSV langsung (`writer.writerow({k: ... v ...})`) tanpa `_safe_csv` (`runner.py:54-58`) yang sudah benar. `DEFAULT_TABLE_URL` juga ditulis dua kali (client.py:25, config.py:12).
- Perbaikan: hapus `collect`/`save`/`main` di client.py (sudah digantikan `runner.py` + `__main__.py`), atau pakai `_safe_csv` yang sama.

**R4. File cache Python lokal masih berisi nilai rahasia INAPROC** — `src/sirup/inaproc/__pycache__/client.cpython-313.pyc` (dibuat 21:16)
- Risiko: berarti pada suatu saat `client.py` di laptop pernah berisi nilai rahasia secara langsung. Sudah dipastikan nilai itu **tidak** ada di riwayat git, **tidak** ada di image Docker, dan **tidak** dikirim ke container api/scraper (lihat bukti). Hanya ada di disk laptop ini.
- Perbaikan: hapus folder `__pycache__` di `src/`. Bila laptop dipakai bersama atau dicadangkan ke cloud, minta INAPROC mengganti pasangan header.

**R5. Kemungkinan deadlock bila scraper dan pengambil INAPROC berjalan bersamaan** — `src/sirup/inaproc/store.py:49-52,121-124` vs `src/sirup/scraper/store.py:222-233`
- Risiko: keduanya mengunci baris `paket` yang sama dalam urutan yang bisa berbeda; PostgreSQL lalu membatalkan salah satunya. Data **tidak** rusak (syarat `WHERE` dievaluasi ulang pada versi baris terbaru), hanya satu proses gagal dan harus diulang.
- Perbaikan: jalankan pengambil di luar jam scraper, atau kunci baris lebih dulu dengan `SELECT id FROM paket WHERE id = ANY(%s) ORDER BY id FOR UPDATE`.

**R6. Pengambil memakai akun pemilik database** — `docker-compose.yml:194`
- Bukti: `INAPROC_DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}`
- Saran: buat peran khusus (mis. `sirup_collector`) dengan hak `SELECT, INSERT, UPDATE` hanya pada `inaproc_rup` dan `paket`. Sesuai desain saat ini, jadi tidak menahan rilis.

**R7. Migrasi 0007 mengunci tabel `paket` selama membangun indeks** — `db/migrations/versions/0007_paket_sumber.py:75-78`
- Risiko: pengecekan `CHECK` dan pembuatan indeks GIN atas 3,6 juta baris berjalan dalam satu transaksi yang memegang kunci penuh; scraper menunggu sampai selesai (lamanya tidak diukur karena dilarang menulis ke database). Tidak berpengaruh bila produksi dimulai dari database kosong; API memang baru menyala setelah migrasi selesai.
- Saran: bila memigrasi database yang sudah berisi, lakukan di luar jam scraper.

**R8. Dokumen rollback sudah tidak tepat** — `docs/DECISIONS.md:228`, `db/migrations/versions/0007_paket_sumber.py:83`
- Bukti: TD-09 menulis "`alembic downgrade 0005` menghapus tabel `inaproc_rup` saja", padahal jalur itu sekarang juga melewati downgrade 0007 yang menghapus semua paket tanpa tanda `sirup` dari tabel `paket` (`DELETE FROM paket WHERE NOT (...)`). Setelah itu salinan INAPROC hanya tersisa di file `output/inaproc/*.jsonl`.
- Perbaikan: perbarui TD-09; di RUNBOOK sebutkan untuk mencadangkan `output/inaproc/` sebelum rollback.

**R9. Berkas uji QA belum di-commit** — `tests/integration/test_qa_sumber.py` (status `??`)
- Perbaikan: commit sebagai `test: add QA edge cases for paket.sumber` agar ikut berjalan di CI.

---

### Yang Sudah Bagus
- **SQL aman.** Filter baru `sumber` memakai parameter terikat (`queries.py:91`, nilai lewat `params`) dan nilainya dibatasi `Literal["sirup", "inaproc"]`; QA membuktikan input injeksi ditolak 422. SQL f-string di `inaproc/store.py:42-46` hanya merangkai nama kolom tetap dari konstanta `COLUMNS`; semua nilai lewat `%s`. Bandit B608 di baris itu adalah positif palsu. Migrasi 0006/0007 tidak memakai input luar.
- **Rahasia tidak bocor ke git.** `.env` diabaikan git dan Docker, `.env.example` berisi nilai kosong, rahasia dibaca sebagai `SecretStr` dengan `hide_input_in_errors=True`, header hanya dikirim bila diisi.
- **Akun API tetap hanya-baca.** `sirup_api` hanya punya hak `r` (SELECT) di `inaproc_rup` dan `paket`; join baru di detail paket memakai kunci utama.
- **Penonaktifan oleh scraper aman.** Kedua `UPDATE` di `scraper/store.py:303-315` sekarang hanya menyentuh paket yang sumbernya memuat `sirup`.
- **Penggabungan data benar saat bersamaan.** `MERGE_PAKET_SQL` hanya menimpa isi paket yang sumbernya tepat `{inaproc}`; scraper menambah tanda `sirup` tanpa duplikat; pembatas `ck_paket_sumber` mencegah nilai liar. QA menguji 4 pengambil bersamaan.
- **Sopan ke situs sumber.** 401/403 langsung berhenti tanpa mengulang, 429/5xx paling banyak 3 kali dengan jeda bertambah, jeda minimal 0,5 detik, tidak mengikuti pengalihan.
- **Pelindung rumus Excel** di `runner.py:54-58` sudah benar.
- **Posisi halaman disimpan setelah database berhasil**, jadi tidak ada baris yang terlewat bila gagal di tengah.
- Lint, format, unit test, dan audit dependensi bersih.

---

### Bukti perintah yang dijalankan

```
$ git log --oneline 6003ee5..HEAD
73ceb71 docs: add TD-10 for paket.sumber and correct TD-09 API note
3fdbb38 feat(paket): add sumber column and serve INAPROC packages via /v1/paket
b8dd294 docs: add TD-09 for storing inaproc rows in the database
7121a5f feat(inaproc): store collected rows in PostgreSQL table inaproc_rup
77b22cd docs: document inaproc collector and TD-08 access update
2a50e89 feat(inaproc): optional data.inaproc.id RUP collector

$ gitleaks git --redact --log-opts="6003ee5..HEAD" .   -> 6 commits scanned ... no leaks found
$ gitleaks git --redact .   (seluruh riwayat)          -> 25 commits scanned ... no leaks found
$ gitleaks dir --redact src                             -> no leaks found

# Pencocokan langsung nilai dari .env (nilai tidak dicetak):
secret set in .env: yes len=20
secret in any git history: 0
time in git history: 0
secret in working tree (excluding .env): 1  -> ./src/sirup/inaproc/__pycache__/client.cpython-313.pyc (R4)
== sirup-api-1      files with secret: 0   env has X_SECRET var: 0
== sirup-scraper-1  files with secret: 0   env has X_SECRET var: 0
# Kandidat string mirip base64 (>=24 karakter) di baris tambahan: semuanya path file / nama tes.
# Satu-satunya nilai uji: x_secret="dGVzdA==" di tests (= kata "test", bukan rahasia).

$ git ls-files | grep -i .env   -> .env.example
$ git check-ignore -v .env      -> .gitignore:2:.env
.dockerignore memuat: .env, .env.*, **/__pycache__, **/*.pyc, output

$ ruff check src tests db                                -> All checks passed!
$ ruff format --check src tests db                       -> 41 files already formatted
$ ruff check --select S,BLE,PERF,ASYNC (berkas berubah)  -> All checks passed!
$ bandit -r src/sirup/inaproc src/sirup/api src/sirup/scraper/store.py db/migrations/versions
   High: 0, Medium: 2 (B608 di 0003:75 dan inaproc/store.py:43; nama kolom tetap, positif palsu)
$ uvx pip-audit -r (pip freeze .venv) --no-deps --disable-pip  -> No known vulnerabilities found
$ pytest tests/unit -q                                   -> 53 passed in 6.14s
   (tes integrasi tidak dijalankan: tes itu mengosongkan tabel, tidak boleh diarahkan ke database asli)

# Database lokal (hanya baca)
alembic_version = 0007
paket: 3.675.722 baris | {sirup} 3.622.902 | {sirup,inaproc} 40.384 | {inaproc} 12.436
inaproc_rup: 52.820 baris
\dp inaproc_rup -> sirup_api=r/sirup_admin
EXPLAIN ANALYZE sumber=inaproc, urut pagu, offset 9980  -> 81,5 ms
EXPLAIN ANALYZE count sumber=inaproc                    -> 36,5 ms
EXPLAIN ANALYZE sumber=inaproc + kata laptop            -> 25,1 ms
EXPLAIN ANALYZE sumber=sirup, urut pagu, halaman 1      -> 0,2 ms
EXPLAIN ANALYZE count sumber=sirup                      -> 375,8 ms
EXPLAIN ANALYZE sumber=sirup, urut terbaru, offset 9980 -> 3.372 ms  (S1, melebihi batas 3.000 ms)
```

---

### Ringkasan untuk bos
Pembaruan ini **aman untuk dinaikkan ke server**: tidak ada celah yang bisa dipakai orang luar untuk mencuri atau merusak data, dan kunci rahasia INAPROC tidak pernah ikut tersimpan di riwayat kode. Ada dua syarat: simpan dulu salinan surat izin INAPROC/LKPP di folder dokumen sebelum pengambil INAPROC dijalankan di server, dan setelah pembaruan database jalankan satu perintah perawatan supaya pencarian "hanya SIRUP" tidak lambat. Dalam sprint ini sebaiknya diperbaiki juga: paket yang hanya ada di INAPROC saat ini tidak pernah ditandai "tidak aktif" walaupun sudah dibatalkan. Semua perbaikan ini biayanya Rp0 dan bisa dikerjakan developer dalam kurang dari satu hari. Langkah berikutnya: developer memperbaiki S1 dan S2, lalu DevOps melakukan deploy.
