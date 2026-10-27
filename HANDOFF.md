# HANDOFF — KATALIS

Catatan serah-terima untuk siapa pun (manusia atau agen/harness lain) yang melanjutkan
pekerjaan di repo ini. Baca ini dulu, lalu `README.md`.

## Apa ini

KATALIS adalah dashboard analisis saham IDX, **hanya pustaka standar Python** (tanpa
`pip install`). Alurnya:

- `core/market.py` — harga dari Yahoo Finance + indikator.
- `core/desk.py` — lima peran membaca snapshot yang sama, menghasilkan satu rating lima
  tingkat. **Deterministik**: snapshot sama, rating sama.
- `core/store.py` — SQLite: watchlist, jurnal keputusan, portofolio.
- `core/journal.py` — menilai keputusan lama terhadap IHSG (alpha).
- `core/advisor.py` + `core/llm.py` — lapisan AI opsional (OpenAI-compatible), satu
  panggilan per analisis.
- `core/idxbridge.py` — jembatan opsional ke biner `idx` untuk data khusus IDX
  (aliran asing, bandarmologi, fundamental, berita). Tanpa biner itu, peran terkait
  memakai proksi harga/volume dan **diberi label**, bukan dianggap netral.

## Apa yang baru ditambahkan: pemindai

`core/screener.py` menjawab "dari daftar saham ini, mana yang layak dibeli sekarang".

1. **Gerbang (kode):** satu snapshot per saham; tolak yang ilikuid, murah, terlalu liar,
   riwayat pendek, atau datanya basi; urutkan sisanya menurut skor teknikal; `shortlist`
   teratas lanjut.
2. **Dewan (kode):** `desk.analyse` penuh hanya untuk shortlist; tolak yang di bawah
   Overweight.
3. **Model (opsional, satu panggilan):** menilai semua kandidat sekaligus.

Titik masuk: `python -m core.screener`, atau `POST /api/screen` di `app.py`.
Daftar saham: `data/universe.txt` (contoh: `universe.example.txt`), jika kosong memakai
watchlist. Dokumentasi pengguna ada di bagian "Pemindai" di `README.md`.

## Aturan yang jangan dilonggarkan

Semua ini dijaga oleh **kode**, bukan hanya prompt, dan ada tesnya:

- Entry, stop, dan target selalu dari `desk`; model tidak pernah memberi level harga.
- Model boleh mengembalikan nol pilihan. Jangan menambah "kuota" yang memaksa terisi.
- Kode saham yang tidak ada di daftar kandidat dibuang dan dilaporkan (`ai.dropped`).
- Hanya rating Buy/Overweight dari model yang bisa jadi pilihan; `max_picks` dipaksa kode.
- Bila model gagal, hasil jatuh ke urutan mesin (`picks_source: "mesin"`) dan alasannya
  ada di `ai.error`. Tidak ada angka pengganti yang dikarang.
- Setiap saham yang ditolak masuk `rejected` (tahap + alasan), setiap yang gagal diambil
  masuk `errors`. Tidak boleh ada saham yang hilang diam-diam.
- `--record` menulis hanya pilihan ke jurnal; vonis model disimpan di kolom AI sehingga
  mesin vs model dinilai terpisah.

## Status verifikasi

Sudah:

- `python tools/test_pure.py` — seluruh cek lolos, termasuk bagian pemindai yang memakai
  data sintetis, `market.chart` palsu, dan LLM tiruan. Tidak butuh jaringan.
- Tiga mutasi sengaja (pilihan Hold lolos, cek data basi dimatikan, batas `max_picks`
  dihapus) masing-masing ditangkap tes.
- Endpoint `/api/screen` diuji lewat HTTP pada kondisi semua pengambilan data gagal.

**Belum** (sandbox tempat kode ini ditulis memblokir Yahoo):

- Belum pernah dijalankan terhadap data Yahoo sungguhan.
- Belum pernah memanggil model sungguhan, jadi kepatuhan model pada format JSON dan
  aturan di `_SYSTEM` belum teramati. Periksa `ai.dropped`, `ai.unassessed`, dan
  `picks_source` pada beberapa run pertama.
- Biner `idx` belum diuji bersama pemindai, jadi waktu tunggu untuk shortlist besar belum
  terukur.

## Batas yang diketahui

- Shortlist diurutkan menurut skor teknikal (cocok untuk horizon 5 sesi di jurnal).
  Saham dengan fundamental bagus tetapi tren lemah tidak lolos.
- UMA, suspensi, dan papan pemantauan khusus tidak terbaca dari data harga.
- `universe.example.txt` ditulis tangan, bukan dari sumber resmi. Ganti dengan LQ45/IDX30
  terbaru dari idx.co.id.
- Ambang gerbang (`Gate` di `core/screener.py`) adalah tebakan awal yang masuk akal, belum
  dikalibrasi terhadap hasil. Setelah jurnal punya cukup catatan, sesuaikan dari data.
- Yahoo membatasi permintaan beruntun; universe besar memakan waktu.
- Tidak ada backtest historis; satu-satunya bukti kinerja adalah jurnal yang terus
  bertambah. Jangan memakai uang sungguhan sebelum ada catatan yang cukup.

## Langkah berikutnya yang disarankan

1. `cp universe.example.txt data/universe.txt`, ganti isinya dengan daftar resmi.
2. `python -m core.screener --no-ai` — pastikan tahap 1–2 jalan dengan data nyata; baca
   `--json` untuk melihat siapa yang ditolak dan kenapa.
3. Isi `OPENAI_API_KEY` di `.env`, jalankan tanpa `--no-ai`, periksa keluaran model.
4. Jalankan harian dengan `--record`, lalu `POST /api/settle` setelah jatuh tempo dan bandingkan
   alpha mesin vs model di jurnal sebelum mengubah apa pun.
5. Jangan pernah menaruh `.env` atau kunci API di repo, di catatan serah-terima, atau di
   percakapan dengan agen lain.

Bukan nasihat investasi.
