# Panduan API Pencarian Paket SIRUP

API ini dipakai tim internal untuk mencari paket pengadaan pemerintah yang dikumpulkan
setiap malam dari SIRUP.

## 1. Membuka dokumentasi interaktif (Swagger)

Setelah sistem menyala (`docker compose up -d`), buka di browser pada server yang sama:

| Halaman | Alamat | Kegunaan |
|---|---|---|
| **Swagger** | http://127.0.0.1:8000/docs | Mencoba API langsung dari browser |
| ReDoc | http://127.0.0.1:8000/redoc | Dokumentasi yang enak dibaca/dicetak |
| Cek sehat | http://127.0.0.1:8000/health | Tanpa kunci; jawabannya `{"status":"ok"}` |

Kalau API diakses dari komputer lain, ganti `127.0.0.1` dengan alamat server, dan pastikan
admin sudah membukanya (lihat `docs/RUNBOOK.md`, bagian "Membuka API ke jaringan").

### Mencoba di Swagger
1. Klik tombol **Authorize** (ikon gembok) di kanan atas.
2. Tempel kunci API Anda, klik **Authorize**, lalu **Close**.
3. Buka `GET /v1/paket`, klik **Try it out**.
4. Isi filter yang diperlukan (boleh dikosongkan), lalu klik **Execute**.
5. Hasilnya muncul di bagian **Response body**. Swagger juga menampilkan perintah `curl`
   yang setara.

## 2. Kunci API

- Minta kunci ke admin. **Jangan dibagikan** dan jangan ditaruh di kode atau dokumen.
- Kirim kunci di setiap permintaan lewat header `X-API-Key`.
- Tanpa kunci atau kunci salah, API menjawab **401**.
- Admin membuat kunci baru dengan perintah:
  ```
  python -c "import secrets;print(secrets.token_urlsafe(32))"
  ```
  lalu menambahkannya ke `API_KEYS` di file `.env` (dipisah koma, minimal 24 karakter),
  kemudian menjalankan `docker compose up -d api`. Untuk mencabut kunci, hapus dari
  `API_KEYS` lalu jalankan perintah yang sama.

## 3. Filter yang tersedia — `GET /v1/paket`

| Filter | Arti | Contoh |
|---|---|---|
| `paket` | Nama paket **mengandung** kata ini (3–100 huruf, huruf besar/kecil sama saja) | `paket=laptop` |
| `satuan_kerja` | Nama satuan kerja **mengandung** kata ini (3–100 huruf) | `satuan_kerja=puskesmas` |
| `pemilihan_bulan` | Bulan pemilihan penyedia, 1–12. Boleh diulang | `pemilihan_bulan=3&pemilihan_bulan=4` |
| `pagu_min` | Pagu paling kecil (rupiah) | `pagu_min=100000000` |
| `pagu_max` | Pagu paling besar (rupiah) | `pagu_max=1000000000` |
| `tahun` | Tahun anggaran (kosong = tahun ini) | `tahun=2026` |
| `aktif` | `true` (bawaan) sembunyikan paket yang sudah hilang dari SIRUP; `false` tampilkan semua | `aktif=false` |
| `sort` | `pagu_desc` (bawaan), `pagu_asc`, atau `terbaru` | `sort=terbaru` |
| `page`, `size` | Halaman dan jumlah per halaman (bawaan 20, maksimal 100) | `page=2&size=50` |

Semua filter boleh digabung; hasilnya harus cocok dengan **semua** filter yang diisi.
Hanya 10.000 hasil pertama yang bisa dibuka (`page × size` ≤ 10.000). Kalau lebih,
persempit filternya.

**Tentang `total`:** supaya pencarian tetap di bawah 1 detik, jumlah hasil dihitung
**sampai 10.000 saja**.
- `total_terbatas: false`: `total` adalah jumlah yang persis.
- `total_terbatas: true`: hasilnya **lebih dari 10.000**; `total` ditulis 10.000.
  Persempit filter kalau perlu angka yang persis.

Contoh jawaban:
```json
{
  "total": 3,
  "total_terbatas": false,
  "page": 1,
  "size": 2,
  "data": [
    {
      "id": 61744010,
      "nama_paket": "PENGADAAN LAPTOP UNIT INTELKAM POLSEK JAJARAN POLDA KALBAR",
      "pagu": 2340000000,
      "pemilihan": "February 2026",
      "satuan_kerja": "DITINTELKAM POLDA KALBAR",
      "kldi": "Kepolisian Negara Republik Indonesia",
      "...": "..."
    }
  ]
}
```

## 4. Contoh perintah `curl`

Simpan kunci di variabel supaya tidak terketik berulang (contoh di Git Bash / Linux):
```bash
KEY="tempel-kunci-anda-di-sini"
URL="http://127.0.0.1:8000"
```

**Nama paket saja**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" --data-urlencode "paket=laptop"
```

**Satuan kerja saja**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" --data-urlencode "satuan_kerja=dinas kesehatan"
```

**Bulan pemilihan Maret dan April**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" -d pemilihan_bulan=3 -d pemilihan_bulan=4
```

**Rentang pagu Rp100 juta – Rp1 miliar**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" -d pagu_min=100000000 -d pagu_max=1000000000
```

**Nama paket + pagu minimal**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" \
  --data-urlencode "paket=laptop" -d pagu_min=100000000
```

**Satuan kerja + bulan**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" \
  --data-urlencode "satuan_kerja=puskesmas" -d pemilihan_bulan=6
```

**Semua filter digabung, urut pagu terbesar, 50 per halaman**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" \
  --data-urlencode "paket=konstruksi" \
  --data-urlencode "satuan_kerja=pekerjaan umum" \
  -d pemilihan_bulan=4 -d pemilihan_bulan=5 \
  -d pagu_min=1000000000 -d pagu_max=10000000000 \
  -d sort=pagu_desc -d size=50 -d page=1
```

**Paket yang paling baru kami temukan**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" -d sort=terbaru -d size=20
```

**Termasuk paket yang sudah hilang dari SIRUP**
```bash
curl -G "$URL/v1/paket" -H "X-API-Key: $KEY" --data-urlencode "paket=laptop" -d aktif=false
```

**Detail satu paket** (termasuk data asli dari SIRUP)
```bash
curl "$URL/v1/paket/61744010" -H "X-API-Key: $KEY"
```

**Riwayat pengambilan data** (kapan mulai/selesai, jumlah paket, error)
```bash
curl "$URL/v1/scrape-runs?limit=10" -H "X-API-Key: $KEY"
```

**Cek sehat** (tanpa kunci)
```bash
curl "$URL/health"
```

## 5. Arti kode jawaban

| Kode | Arti | Yang perlu dilakukan |
|---|---|---|
| 200 | Berhasil | — |
| 401 | Kunci API tidak ada atau salah | Periksa header `X-API-Key` |
| 404 | Paket tidak ditemukan | Periksa ID paket |
| 422 | Isian filter tidak valid (mis. kata kunci < 3 huruf, bulan 13, `pagu_min` > `pagu_max`, halaman terlalu dalam) | Baca bagian `detail`, perbaiki isian |
| 503 | Database sedang tidak bisa dihubungi atau pencarian terlalu lama | Coba lagi sebentar lagi; kalau berulang, hubungi admin |
