# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Pemilik repo: investor/trader ritel Indonesia yang menjalankan KATALIS di komputernya
sendiri (localhost, tanpa autentikasi) untuk keputusan saham pribadi dengan horizon
5 sesi bursa. Ditambah audiens kedua yang dikonfirmasi: pembaca dan calon pengguna/kontributor
repo publik open-source di GitHub — mereka membaca README/HANDOFF dan menilai proyek dari
kode serta dokumentasinya.

## Product Purpose

Satu meja untuk membaca saham Indonesia: harga, aliran dana, bandarmologi, dan dewan
analis (lima peran berbobot) yang menghasilkan satu rating lima tingkat. Setiap keputusan
dicatat di jurnal dan dinilai setelah 5 sesi terhadap IHSG (alpha). Lapisan AI opsional
menambahkan tesis, risiko, dan syarat pembatalan di atas angka yang sama — mesin tetap
dihitung tanpa AI, dan itulah yang dinilai jurnal.

## Positioning

Yang tidak bisa ditiru produk sebelah secara jujur:

- **Deterministik** — snapshot sama selalu menghasilkan rating sama; karena itu catatan
  kinerja di jurnal berarti.
- **Mesin vs model dinilai terpisah** — opini AI disimpan di kolom sendiri, divonis
  sendiri, dan boleh berbeda dari mesin; perbedaannya ditampilkan, bukan disembunyikan.
- **Level harga selalu dari mesin** — entry, stop, target, R:R, lot, nilai rupiah;
  model tidak pernah memberi level harga (dijaga kode, bukan prompt).
- **Kekosongan dilabeli** — peran tanpa data tampil "tanpa data" dengan alasan, tidak
  pernah dilaporkan netral.
- **Nol dependensi** — hanya pustaka standar Python; tanpa `pip install`, tanpa build.

## Operating Context

- Dijalankan lewat `run.bat` / `run.sh` / `python app.py`; dashboard di
  `http://127.0.0.1:8770`.
- Harga harian dan intraday dari Yahoo Finance (gratis, tanpa kunci, tertunda); realtime
  via SSE tiap 5 detik, watchlist 30 detik, IHSG 60 detik.
- Data khusus IDX (aliran asing, akumulasi broker, KSEI, dividen, aksi korporasi,
  kalender, berita Kontan/CNBC) hanya bila biner `idx` ada di `vendor/idx/bin/`;
  tanpa itu peran terkait memakai proksi harga/volume dan diberi label.
- Lapisan AI aktif bila `OPENAI_API_KEY` terisi di `.env` (OpenAI-compatible;
  default `gpt-4o-mini`); satu analisis = satu panggilan model.
- Pemindai dijalankan lewat `python -m core.screener` atau `POST /api/screen`;
  universe dari `data/universe.txt` (idealnya LQ45/IDX30 resmi dari idx.co.id).
- Satu keputusan per saham per hari; opini AI pertama hari itu yang disimpan di jurnal.

## Capabilities and Constraints

Kemampuan terkonfirmasi: watchlist dengan kilasan 30 sesi; grafik lilin + SMA20 + volume
dengan rentang 1H–2T dan crosshair; kartu putusan (rating, keyakinan, rencana); tab Dewan
Analis / Nasihat AI / Debat / Risiko / Pemindai / Jurnal / Portofolio; jurnal dengan
masuk/keluar/return/alpha vs IHSG dan tombol penilaian; portofolio (posisi, kas, ekuitas)
yang dipakai untuk ukuran posisi (risiko 1% ekuitas, maks 10%, lot 100, tick IDX).

Bobot peran terkonfirmasi: Teknikal 34%, Aliran Dana 20%, Bandar 20%, Fundamental 16%,
Berita 10%; skor peran dinormalkan, bobot dinormalkan ulang saat peran tanpa data.
Skala rating: Buy / Overweight / Hold / Underweight / Sell pada skor gabungan −2…+2.

Kendala yang mengikat (dikonfirmasi pemilik): hanya pustaka standar; mesin deterministik;
entry/stop/target selalu dari mesin; data kosong diberi label; localhost tanpa autentikasi;
UI dan seluruh copy **bahasa Indonesia**; bukan nasihat investasi dan disclaimer selalu
tampil; harga tertunda (untuk eksekusi pakai data broker); tidak ada analisis historis
atau backtest — satu-satunya bukti kinerja adalah jurnal yang bertambah.

Fakta yang belum diputuskan: ambang gerbang pemindai (`Gate` di `core/screener.py`) belum
dikalibrasi — menunggu jurnal punya cukup catatan; pemindai belum pernah diuji bersama
biner `idx`.

## Brand Commitments

Nama **KATALIS**, tagline **IDX Intelligence Desk**. Seluruh copy UI dan dokumentasi dalam
bahasa Indonesia (mengikat, dikonfirmasi pemilik). Suara: jujur dan terukur — batas
disebut terang, kekosongan diakui, klaim tidak melebihi bukti; disclaimer tidak boleh
dihapus. Karya turunan dari TradingAgents (Apache-2.0) dan idx (MIT); atribusi ada di
`ATTRIBUTION.md` dan footer dashboard.

## Evidence on Hand

- `README.md`, `HANDOFF.md`, `ATTRIBUTION.md` — dokumentasi lengkap bahasa Indonesia.
- `docs/preview.png` — pratinjau dashboard yang dirender `tools/make_preview.py` dari data
  pasar sungguhan (bukan tangkapan layar).
- `tools/check_ui.mjs` — 40 pemeriksaan render dashboard (34 tanpa AI; manual, butuh
  server + jaringan); `tools/test_pure.py` — 103 cek backend dengan data sintetis, tanpa
  jaringan; `tools/test_pure.mjs` — 45 cek frontend, tanpa jaringan; `tools/check_crosshair.js`.
- `data/cache/` — cuplikan harga Yahoo sungguhan (BBCA.JK, BUVA.JK).
- Ketiadaan yang tidak boleh dikarang: belum pernah dijalankan terhadap Yahoo sungguhan
  lewat pemindai, belum pernah memanggil model sungguhan (per HANDOFF.md); tidak ada
  testimoni, studi kasus, atau klaim kinerja — jangan mengarangnya.

## Product Principles

1. **Mesin dulu, model menafsirkan.** Angka dan rating dihitung tanpa AI; AI menambah
   tafsir di atas bukti yang sama, tanpa pernah memberi level harga.
2. **Kekosongan diakui, bukan ditutupi.** Peran tanpa data berlabel "tanpa data" beserta
   alasannya; tidak ada angka pengganti yang dikarang.
3. **Setiap rating dinilai.** Tanpa jurnal dan alpha vs IHSG, rating hanyalah pendapat;
   keputusan dicatat dan divonis setelah 5 sesi.
4. **Murah dulu, mahal atas aksi eksplisit.** Membuka halaman tidak memanggil model;
   panggilan berbiaya hanya lewat tombol atau centang yang dimengerti penggunanya.
5. **Berjalan di mana saja tanpa instalasi.** Nol dependensi, lokal, deterministik —
   rumit ditolak.
