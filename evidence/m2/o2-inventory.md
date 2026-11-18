# Inventaris metadata M2 O2 tahap 1

Operator menyimpan kelima publikasi sebagai PDF melalui browser. Inventaris arsip dan pencocokan checksum juga sudah selesai untuk `BTCUSDT`, `ETHUSDT` dan `DGBUSDT` dalam rentang `[2023-01-01T00:00:00Z, 2025-01-01T00:00:00Z)`. [Bukti pengumuman](o2-notices.json) · [Matriks JSON](o2-inventory.json) · [Keputusan O2](protocol.md#operator-decisions)

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

Pencarian discovery memakai pembatas tanggal dan kata kunci untuk listing, perubahan status, delisting, perubahan filter/tier, dan pergantian kontrak atau simbol termasuk multiplier. Pencarian listing lama juga mencakup 2019–2022. Log discovery mencatat penyedia, kelima batch kueri asli, kriteria penyaringan, dan lima URL publikasi resmi yang dipilih. Penyedia juga mengembalikan hasil di luar periode, yang diabaikan. Halaman hasil lengkap dan daftar setiap hasil yang ditolak belum disimpan pada run asli. Seluruh URL terpilih merespons HTTP `202` dengan `Content-Length: 0`. Tidak ada isi halaman yang dapat disimpan atau di-hash. Matriks menandai percobaan sebagai `BLOCKED_EMPTY_HTTP_202` dan mencatat waktu observasi serta hash kosong. Catatan hasil indeks dipakai untuk discovery URL saja. Bukti status dan tier DGBUSDT yang dipertahankan berasal dari manifest M1 yang sudah diterima, dengan sumbernya dicatat terpisah. [Log discovery](o2-discovery.json)

| Jenis bukti | Temuan dan celah |
|---|---|
| Listing, status dan epoch | Blog menyebut peluncuran BTCUSDT pada 9 September 2019. Blog ETHUSDT menyatakan perdagangan mulai pada hari publikasi, sementara tanggal yang ditampilkan berbeda antara tab dan PDF. Pengumuman DGBUSDT menyatakan larangan posisi baru pada 1 April 2024 pukul 08:30 UTC, settlement terjadwal pukul 09:00 UTC, dan delisting setelah settlement. Onboard instants, cakupan perubahan status sepanjang periode, dan relabel multiplier ketiga simbol masih belum lengkap. |
| Filter | Parameter filter USD-M *point-in-time*, termasuk step size, minimum kuantitas/notional dan tick size selama 2023–2024, belum cukup untuk menilai eksekusi. Hasil pencarian terkait spot atau kontrak delivery tidak dipakai sebagai bukti perpetual. |
| Maintenance tier | Salinan operator mencatat cap, rate dan leverage pascaperubahan untuk DGBUSDT serta dua jadwal BTCUSDT/ETHUSDT. Deduction tidak tercantum pada tabel. Pengumuman Januari melaporkan perubahan BTC/ETH yang efektif pada Desember, sebelum tanggal publikasinya. Pengumuman Mei menyebut penyelesaian perubahan dalam sekitar 30 menit. Gunakan kesempatan melihat bukti sesuai waktu publikasi dan batas penerapan yang dinyatakan. Cakupan lintas periode dan kelayakan penerapan masih perlu ditelaah di O3. |
| Spread | Inventaris tidak menemukan bukti spread retrospektif dalam set M1 yang menjadi rujukan. Bucket candle dan funding tidak mengukur spread. |

### Wayback dan salinan operator

| Publikasi | CDX Wayback | Salinan operator |
|---|---|---|
| DGBUSDT delisting dan tier | `NOT_ARCHIVED` | PDF diperiksa |
| Riwayat peluncuran BTCUSDT | `NOT_ARCHIVED` | PDF diperiksa |
| Peluncuran ETHUSDT | `NOT_ARCHIVED` | PDF diperiksa |
| Tier BTCUSDT/ETHUSDT, pengumuman Januari | `NOT_ARCHIVED` | PDF diperiksa |
| Tier BTCUSDT/ETHUSDT, perubahan Mei | `NOT_ARCHIVED` | PDF diperiksa |

CDX diperiksa dengan URL asli persis, status `200` dan batas capture `20241231235959`. Kelima respons HTTP `200` berisi array kosong. Status ini hanya berlaku untuk kueri tersebut. Satu percobaan Python gagal pada verifikasi TLS sebelum ada HTTP response. Lima kueri HTTP selesai menggunakan curl dengan Schannel, tanpa proxy atau redirect. Browser bawaan Codex kemudian membuka lima URL resmi atas permintaan operator. Jumlah subrequest otomatis browser tidak diukur. Operator mencetak kelimanya ke PDF dan salinan disimpan di `data/o2-inventory/notices-operator/`. Hash membuktikan byte PDF yang disimpan. Waktu simpan diambil dari `CreationDate` PDF dan dikonversi ke UTC. [URL kueri, hash dan fakta](o2-notices.json)

Untuk DGBUSDT, waktu larangan membuka posisi, batas settlement terjadwal, waktu perubahan tier dan nilai bracket terendah (cap 5.000 USDT, rate 2%, max leverage 11×) cocok dengan catatan M1. PDF menampilkan publikasi 25 Maret pukul 04:40 tanpa label zona waktu. Tab menampilkan 11:40. Dokumen tidak menetapkan waktu terpisah untuk penyelesaian settlement/delisting dan tidak menyatakan deduction. Catat pembatasan ini sebelum memakai hasil sebagai metadata eligibility. [Pembanding M1](../m1/feasibility.md) · [Pencocokan terstruktur](o2-notices.json)

### Mendaftarkan salinan operator

Simpan halaman dari browser biasa sebagai PDF atau HTML dalam `data/o2-inventory/notices-operator/`. Pertahankan URL di header/footer PDF. Catat waktu simpan UTC dan salin tanggal publikasi sebagaimana dicetak. Dari root checkout, gunakan perintah berikut untuk contoh DGBUSDT yang sudah diperiksa.

```powershell
.\.venv\Scripts\python.exe evidence/m2/ingest_notices.py --url 'https://www.binance.com/en/support/announcement/detail/ce74360a3fd444b2b35da7186f8dd0ad' --file data/o2-inventory/notices-operator/DGBUSDT-delisting-2024-03-25.pdf --saved-at 2026-11-18T22:18:25Z --published-display '2024-03-25 04:40' --facts data/o2-inventory/notices-operator/DGBUSDT-delisting-2024-03-25.facts.json
```

Untuk salinan baru, `--facts` opsional. Tanpanya ledger menandai fakta `PENDING_REVIEW`. Untuk menyelesaikan review, sediakan array JSON dengan `symbol`, `kind` dan fakta yang disalin setelah memeriksa isi halaman, lalu ulangi impor dengan file dan metadatanya yang sama. Gunakan `null` untuk nilai yang tidak dinyatakan. Impor bersifat offline. Salinan dan file transkripsi lokal tetap di luar Git. Ledger mencatat provenance `operator-saved browser copy` dan tetap menyimpan hasil CDX secara terpisah.

## Metode dan jumlah request

| Host | Request langsung | Hasil |
|---|---:|---|
| `data.binance.vision` | 455 × `HEAD` ZIP, 454 × `GET` CHECKSUM | 908 HTTP `200`, satu HTTP `404` pada key funding harian DGBUSDT. |
| `www.binance.com` | 5 × `GET` halaman pada run awal | 5 HTTP `202` kosong. Bukti lanjutan memakai lima navigasi browser atas permintaan operator. |
| `web.archive.org` | 5 × HTTP CDX pada follow-up | 5 HTTP `200` dengan daftar kosong. Satu percobaan TLS tambahan tidak mencapai HTTP. |

Discovery menggunakan 18 kueri indeks bertanggal. Request internal penyedia indeks tidak termasuk hitungan koneksi langsung. Arsip diperiksa dari key simbol × tanggal × seri yang dibentuk langsung. Koneksi langsung tidak mengikuti redirect dan tidak memakai listing direktori, daftar simbol aktif, GET ZIP, maupun permintaan Range. Payload pasar yang diunduh berjumlah **0 byte**.

Helper pengumpulan dan validator tersedia di Git. Jalankan dari root checkout dengan Python yang memiliki paket `acgs` terpasang. Gunakan direktori baru di bawah `data/` untuk pengumpulan ulang. Log request, byte CHECKSUM dan salinan mentah halaman yang berhasil diambil disimpan di sana. Kolektor menghentikan pengambilan dari host pengumuman pada respons HTTP `202` kosong pertama dalam pengumpulan ulang. Lima percobaan tetap tercatat sebagai bukti run asli.

```powershell
.\.venv\Scripts\python.exe evidence/m2/collect.py archives --work-dir data/o2-inventory-rerun
.\.venv\Scripts\python.exe evidence/m2/collect.py notices --work-dir data/o2-inventory-rerun
.\.venv\Scripts\python.exe evidence/m2/finalize.py --work-dir data/o2-inventory-rerun
```

Validator bekerja offline dan menulis matriks ke direktori run. Untuk mengaudit run asli, gunakan `--work-dir data/o2-inventory`, yang memuat log dan observasi asli pada checkout pelaksana. Validator mencocokkan request dengan baris matriks, mengecek urutan HEAD sebelum GET CHECKSUM, dan memakai aturan segel untuk memeriksa cakupan bundle. Run asli menyimpan nilai SHA-256 yang dibaca dari CHECKSUM. Pengumpulan ulang juga menyimpan byte CHECKSUM untuk verifikasi ulang. Snapshot pengumuman yang berhasil disimpan perlu ditelaah untuk memastikan waktu publikasi, waktu efektif, dan fakta yang relevan sebelum dipakai sebagai bukti penelitian.

Pemeriksaan repo untuk run asli dijalankan dengan perintah berikut.

```powershell
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

Pada follow-up auditabilitas, `mypy src evidence/m2/collect.py evidence/m2/finalize.py` lulus pada 28 berkas. Pemeriksaan dengan koneksi tiruan menguji penolakan GET ZIP, listing, key pasca-cutoff dan URL pengumuman di luar daftar. Pemeriksaan juga menguji penghentian request setelah HTTP `403`, `451`, dan `202` kosong untuk pengumuman. Rerender offline mempertahankan seluruh observasi run asli dan hanya menambahkan referensi ke log discovery.

Pada follow-up salinan pengumuman, kelima PDF dibuka dengan `pypdf`, URL pada footer diperiksa, dan halaman pertama dirender dengan `pdftoppm`. SHA-256 dan ukuran kelima PDF cocok dengan ledger. Enam kasus impor notice berada di `tests/test_ingest_notices.py` dan masuk dalam **296 tes** pada pemanggilan pytest standar. Image Docker `check` menyertakan helper impor yang diuji.

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe src evidence/m2/ingest_notices.py tests/test_ingest_notices.py
.\.venv\Scripts\pytest.exe -q --basetemp=C:\dev\acgs\data\o2-inventory\pytest-notices-ci
docker compose -p acgs-o2-notices-ci --profile check build check
docker compose -p acgs-o2-notices-ci --profile check run --rm check
.\.venv\Scripts\python.exe evidence/m2/finalize.py --work-dir data/o2-inventory --output evidence/m2/o2-inventory.json
git diff --check
```

## Handoff

- **T-15 untuk ruang lingkup inventaris** memenuhi pembatasan tanggal dan segregasi akses. Persetujuan manifest, payload dan holdout berada pada tahap berikutnya.
- **T-14** memiliki daftar celah provenance pada level inventaris. Kebijakan rekonstruksi O3 dan sensitivitas penelitian masih terbuka.
- **T-21** memiliki inventaris key mark dan funding. Kesesuaian settle-time dan mark intrabar memerlukan payload yang sudah disetujui.
- O2 tahap 2 tetap terbuka untuk ranking, publication lag, histori kontigu, exclusions dan aturan konflik kandidat. O1 final, pin manifest dan parameter O3–O9 tetap terbuka.
- Review matriks tiga probe sebelum memperluas simbol. Bawa batas bukti tanggal, efektivitas tier, dan deduction ke keputusan O3.
