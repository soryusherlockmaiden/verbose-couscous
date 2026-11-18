# Inventaris metadata M2 O2 tahap 1

**Status tugas: `BLOCKED` untuk salinan mentah pengumuman.** Inventaris arsip dan pencocokan checksum selesai pada 2026-11-18 UTC. Lingkup operator mencakup `BTCUSDT`, `ETHUSDT` dan `DGBUSDT` dalam rentang `[2023-01-01T00:00:00Z, 2025-01-01T00:00:00Z)`. Aset berperiode 2025 dan sesudahnya tetap di luar inventaris. [Matriks JSON](o2-inventory.json) · [Keputusan O2](protocol.md#operator-decisions)

## Cakupan arsip

| Simbol | Baris bulanan | `FOUND` | `NOT_FOUND` | `UNVERIFIED` | `OUTSIDE_ACTIVE_PERIOD` |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 168 | 168 | 0 | 0 | 0 |
| ETHUSDT | 168 | 168 | 0 | 0 | 0 |
| DGBUSDT | 168 | 112 | 0 | 0 | 56 |
| Total | 504 | 448 | 0 | 0 | 56 |

Tiap bulan memiliki tujuh seri: `klines` dan `markPriceKlines` masing-masing pada `15m`, `1h` dan `1d`, serta `fundingRate`. Mei–Desember 2024 untuk DGBUSDT diklasifikasikan di luar masa aktif berdasarkan batas delisting yang sudah dicatat di M1. Bulan-bulan itu tidak diminta dari host dan tidak masuk hitungan celah. Tidak ada bulan sebelum listing yang dapat diklasifikasikan pada rentang ini. [Bukti M1](../m1/feasibility.md)

April 2024 adalah bulan batas DGBUSDT. Ketujuh key bulanan ditemukan. Pada 1 April, keenam bundle harian candle ditemukan dan key `fundingRate` harian mengembalikan HTTP `404`. Bundle funding bulanan tersedia, sehingga hasil ini hanya membuktikan ketidaktersediaan key harian terpisah. Keberadaan settlement penutupan tetap perlu diverifikasi dari payload yang kelak disetujui. Tidak ada celah key bulanan lain yang memicu pemeriksaan harian.

Seluruh **454** ZIP yang ditemukan mempunyai ukuran dari header dan CHECKSUM dengan SHA-256 serta nama file yang cocok. Pada URL yang sama, **33/33** checksum publik cocok dengan pin M1. Sampel M1 berisi bar `1d` dalam bundle `monthly` dan funding bulanan untuk BTCUSDT dan ETHUSDT Januari–April 2024 serta DGBUSDT Januari–Maret. Ukuran dan checksum di sini diamati saat pengambilan metadata. Keberadaan bundle tidak membuktikan kontinuitas candle, kontinuitas funding, atau keanggotaan universe *point-in-time*. [Manifest M1](../m1/binance-2024.json) · [Coverage M1](../m1/feasibility.md)

## Pengumuman dan celah bukti

Pencarian discovery memakai pembatas tanggal dan kata kunci untuk listing, perubahan status, delisting, perubahan filter/tier, dan pergantian kontrak atau simbol termasuk multiplier. Pencarian listing lama juga mencakup 2019–2022. Hanya lima URL publikasi resmi pra-cutoff yang dipilih untuk pengambilan langsung. Seluruhnya merespons HTTP `202` dengan `Content-Length: 0`. Tidak ada isi halaman yang dapat disimpan atau di-hash. Matriks menandai percobaan sebagai `BLOCKED_EMPTY_HTTP_202` dan mencatat waktu observasi serta hash kosong. Catatan hasil indeks dipakai untuk discovery URL saja. Bukti status dan tier DGBUSDT yang dipertahankan berasal dari manifest M1 yang sudah diterima, dengan sumbernya dicatat terpisah.

| Jenis bukti | Temuan dan celah |
|---|---|
| Listing, status dan epoch | BTCUSDT dan ETHUSDT memiliki kandidat sumber peluncuran resmi dari discovery, sementara snapshot dan tanggal efektif yang terverifikasi pada run ini masih kosong. DGBUSDT memiliki `CLOSE_ONLY` pada 1 April 2024 pukul 08:30 UTC dan delisting pukul 09:00 UTC dari bukti M1. Tanggal onboard DGBUSDT yang persis, cakupan perubahan status sepanjang periode, dan relabel multiplier ketiga simbol belum terverifikasi. |
| Filter | Parameter filter USD-M *point-in-time*, termasuk step size, minimum kuantitas/notional dan tick size selama 2023–2024, belum cukup untuk menilai eksekusi. Hasil pencarian terkait spot atau kontrak delivery tidak dipakai sebagai bukti perpetual. |
| Maintenance tier | Bukti M1 mencakup bracket terendah DGBUSDT yang efektif 25 Maret 2024 pukul 10:30 UTC, diterbitkan pukul 04:40 UTC. Cakupan tier lengkap dan masa berlakunya, termasuk untuk BTCUSDT/ETHUSDT, masih perlu bukti bertanggal. |
| Spread | Inventaris tidak menemukan bukti spread retrospektif dalam set M1 yang menjadi rujukan. Bucket candle dan funding tidak mengukur spread. |

## Metode dan jumlah request

| Host | Request langsung | Hasil |
|---|---:|---|
| `data.binance.vision` | 455 × `HEAD` ZIP, 454 × `GET` CHECKSUM | 908 HTTP `200`, satu HTTP `404` pada key funding harian DGBUSDT. |
| `www.binance.com` | 5 × `GET` halaman | 5 HTTP `202` kosong. Jalur snapshot mentah `BLOCKED`. |

Discovery menggunakan 18 kueri indeks bertanggal. Request internal penyedia indeks tidak termasuk hitungan koneksi langsung. Arsip diperiksa dari key simbol × tanggal × seri yang dibentuk langsung. Koneksi langsung tidak mengikuti redirect dan tidak memakai listing direktori, daftar simbol aktif, GET ZIP, maupun permintaan Range. Payload pasar yang diunduh berjumlah **0 byte**.

Di Windows, pengumpulan dan validasi dijalankan dari root checkout dengan perintah berikut. Helper, log request, daftar kandidat dan direktori untuk snapshot mentah berada di `data/o2-inventory/` yang diabaikan Git. Matriks merekam metadata hasil observasi, status, waktu UTC, dan checksum yang diterima.

```powershell
.\.venv\Scripts\python.exe data/o2-inventory/collect.py archives
.\.venv\Scripts\python.exe data/o2-inventory/collect.py notices
.\.venv\Scripts\python.exe data/o2-inventory/finalize.py
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest -q --basetemp=C:\dev\acgs\data\o2-inventory\pytest
docker compose config --quiet
docker compose -p acgs-o2-inventory --profile check build acgs check
docker compose -p acgs-o2-inventory --profile check run --rm check
docker compose -p acgs-o2-inventory run --rm acgs smoke
docker compose -p acgs-o2-inventory run --rm acgs smoke
git diff --check
```

Validator memeriksa 504 baris bulanan unik, tujuh key harian di batas DGBUSDT, cakupan bundle melalui aturan segel yang ada, metode request, seluruh kecocokan M1, dan kesetaraan objek `access` terhadap basis `a2c1352`. Ruff, mypy dan pytest lulus secara native di Windows dan di Docker Linux, dengan **290 tes** di masing-masing lingkungan. Kedua image berhasil dibangun. Dua smoke Docker mencatat dan memutar ulang digest yang sama, `35be0f1204fa4a70ff517a6643703bfbc657ee64b8cd0827682a183d3db7bc34`.

## Handoff

- **T-15 untuk ruang lingkup inventaris** memenuhi pembatasan tanggal dan segregasi akses. Persetujuan manifest, payload dan holdout berada pada tahap berikutnya.
- **T-14** memiliki daftar celah provenance pada level inventaris. Kebijakan rekonstruksi O3 dan sensitivitas penelitian masih terbuka.
- **T-21** memiliki inventaris key mark dan funding. Kesesuaian settle-time dan mark intrabar memerlukan payload yang sudah disetujui.
- O2 tahap 2 tetap terbuka untuk ranking, publication lag, histori kontigu, exclusions dan aturan konflik kandidat. O1 final, pin manifest dan parameter O3–O9 tetap terbuka.
- Review matriks tiga probe sebelum memperluas simbol. Bukti pengumuman yang belum tersimpan memerlukan salinan dari akses resmi yang diizinkan atau dokumen primer yang sudah dipegang operator.
