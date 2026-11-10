# BRD — Pengumpul Data Paket SIRUP untuk Peluang Tender

| | |
|---|---|
| Status | **DISETUJUI bos — 10 Nov 2026** |
| Tanggal | 10 November 2026 |
| Penyusun | Tech Lead (Claude) |
| Pemilik | bos |

---

## 1. Latar belakang

Perusahaan perlu menemukan **peluang tender pemerintah** lebih cepat. Semua rencana
belanja pemerintah diumumkan di **SIRUP** (sirup.inaproc.id). Masalahnya, situs itu
hanya bisa dicari satu per satu lewat browser. Hasilnya tidak bisa disimpan, tidak
bisa disaring sesuai kebutuhan perusahaan, dan tidak ada pemberitahuan kalau ada
paket baru.

## 2. Tujuan

1. **Menyimpan semua paket SIRUP** di database milik perusahaan.
2. **Memperbarui data secara otomatis** supaya paket baru ikut masuk.
3. **Menyediakan API penyaring** agar tim atau aplikasi lain bisa mencari paket yang
   cocok, misalnya per wilayah, nilai pagu, instansi, kata kunci, dan bulan.

## 3. Hasil uji coba ke SIRUP (10 Nov 2026, sudah dijalankan)

| Jenis pengadaan (tahun 2026) | Jumlah paket |
|---|---|
| Barang | 2.222.996 |
| Pekerjaan Konstruksi | 261.171 |
| Jasa Konsultansi | 212.351 |
| Jasa Lainnya | 942.176 |
| **Semua jenis** | **3.638.893** |

- Satu permintaan bisa mengambil **10.000 paket** dalam waktu ±1,2 detik.
- Halaman paling belakang tetap bisa diambil.
- Belum terlihat ada pemblokiran.
- Perkiraan waktu untuk mengambil semua paket, dengan jeda sopan 2 detik: **±15–30 menit**.

## 4. Ruang lingkup

### Termasuk (Tahap 1)
- Mengambil **semua paket penyedia** dari SIRUP untuk tahun anggaran yang ditentukan.
- Menyimpannya ke **PostgreSQL**. Paket yang sudah ada diperbarui, tidak disimpan dua kali.
- Memperbarui data otomatis **1× sehari pada malam hari**.
- Menandai paket yang **hilang dari SIRUP** (misalnya dibatalkan), tanpa menghapusnya.
- **API penyaring** dengan kunci akses (API key).
- Semuanya berjalan dengan **Docker** (satu perintah untuk menyalakan).
- Cadangan (backup) database harian.

### Tidak termasuk (bisa jadi Tahap 2)
- Halaman detail tiap paket (uraian pekerjaan, spesifikasi, jadwal lengkap).
- Paket **swakelola**. Paket ini dikerjakan sendiri oleh instansi, jadi bukan peluang
  untuk perusahaan.
- Tampilan web atau dashboard.
- Pemberitahuan otomatis lewat Telegram atau email kalau ada paket yang cocok.
- Data dari LPSE (pengumuman tender resmi). SIRUP baru berisi **rencana**.

## 5. Asumsi yang PERLU DIKONFIRMASI bos

| # | Asumsi saya | Kalau salah, dampaknya |
|---|---|---|
| A1 | Keempat jenis pengadaan diambil (Barang, Konstruksi, Konsultansi, Jasa Lainnya) | Kalau hanya Barang, data menyusut ±40% |
| A2 | Data diperbarui 1× sehari | Kalau perlu lebih sering, beban ke server SIRUP naik |
| A3 | **Disepakati:** tahun anggaran = **tahun berjalan**, dihitung otomatis memakai waktu WIB. Saat ganti tahun, sistem otomatis pindah, dan data tahun lalu tetap tersimpan tapi tidak diperbarui lagi | Paket tahun depan yang sudah diumumkan di bulan Nov–Des belum ikut terambil sampai 1 Januari |
| A4 | API hanya dipakai **tim internal**, tidak dibuka untuk umum | Kalau dibuka untuk umum, perlu pengamanan tambahan |
| A5 | Aplikasi dijalankan di **satu server**, baik VPS maupun server kantor | — |

## 6. Kriteria diterima (acceptance criteria)

| Kode | Kriteria | Cara cek |
|---|---|---|
| AC-01 | Setelah pengambilan pertama, jumlah paket di database sama dengan jumlah di SIRUP (selisih maksimal 0,5%, karena data SIRUP terus berubah) | Bandingkan angka hitungan |
| AC-02 | Menjalankan ulang pengambilan **tidak** membuat paket ganda | Hitung ID yang dobel = 0 |
| AC-03 | Paket baru di SIRUP sudah masuk database paling lambat 24 jam kemudian | Cek paket baru |
| AC-04 | Kalau pengambilan terputus (internet mati), proses bisa dilanjutkan tanpa mulai dari nol | Matikan di tengah jalan, lalu jalankan lagi |
| AC-05 | Pengambilan memakai jeda dan berhenti sendiri kalau SIRUP menolak berulang kali | Lihat catatan (log) |
| AC-06 | API bisa mencari berdasarkan **nama paket** (kata kunci), **pemilihan** (bulan), **pagu** (rentang minimum–maksimum), dan **satuan kerja** (kata kunci), sendiri-sendiri maupun digabung | Uji tiap filter dan gabungannya |
| AC-07 | Pencarian API selesai dalam waktu **< 1 detik** untuk 95% permintaan | Uji beban |
| AC-08 | API menolak permintaan tanpa kunci akses yang benar | Uji tanpa kunci |
| AC-09 | Seluruh sistem menyala dengan `docker compose up -d` | Coba di mesin bersih |
| AC-10 | Backup database dibuat setiap hari dan bisa dipulihkan | Uji pemulihan |
| AC-11 | Setiap kali pengambilan berjalan, tercatat waktu mulai, waktu selesai, jumlah paket, dan error | Lihat tabel catatan |

## 7. Untung-rugi

**Manfaat**
- Tim tidak perlu lagi mencari manual di SIRUP.
- Peluang tender bisa ketahuan lebih awal karena SIRUP berisi rencana, sebelum tender dibuka.
- Data bisa dianalisis, misalnya instansi mana yang paling banyak belanja dan bulan apa
  yang paling ramai.

**Biaya**

| Komponen | Perkiraan |
|---|---|
| Pembuatan | Waktu tim (tanpa lisensi berbayar, semua perangkat lunaknya gratis) |
| Server, kalau sewa VPS (2 CPU, 4 GB RAM, 80 GB disk) | **± Rp150.000–250.000 per bulan** |
| Server kantor sendiri | Rp0 tambahan |
| Penyimpanan data | ± 3–5 GB per tahun anggaran |

**Alternatif yang lebih murah (sesuai aturan "cari yang termurah dulu")**
- Tetap mencari manual di SIRUP: gratis, tetapi lambat dan tidak bisa disaring sesuai kebutuhan.
- Langganan layanan pihak ketiga yang menjual data tender: biasanya Rp500 ribu sampai
  jutaan rupiah per bulan.
- Sistem sendiri lebih murah dalam jangka panjang, dan datanya sepenuhnya milik perusahaan.

## 8. Risiko

| Risiko | Kemungkinan | Penanganan |
|---|---|---|
| SIRUP mengubah format atau alamat datanya | Sedang | Sistem memberi tanda kalau format berubah; perbaikan perlu ±1 hari |
| SIRUP memblokir akses karena terlalu sering | Rendah–sedang | Ada jeda, cukup 1× sehari, dan berhenti sendiri kalau ditolak |
| Ketentuan penggunaan SIRUP | Rendah | Datanya publik; kita mengambilnya dengan sopan dan tidak menjualnya kembali |
| Data di SIRUP baru rencana dan bisa berubah atau batal | Pasti | Data diperbarui harian; paket yang hilang ditandai |

## 9. Persetujuan

- [x] bos menyetujui ruang lingkup dan asumsi A1–A5
- Tanggal disetujui: 10 November 2026 (lewat percakapan: "Setuju, mulai bangun")
- Perubahan sebelum disetujui: tahun anggaran = tahun berjalan; filter API = paket,
  pemilihan, pagu, satuan kerja.
