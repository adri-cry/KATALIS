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

1. **Gerbang (kode):** satu snapshot per saham; tolak yang datanya basi (>7 hari), harganya
   di bawah Rp1, atau riwayatnya < 20 sesi. Turnover dan ATR dicatat, bukan ditolak — gerbang
   sistem di tahap 2 yang menentukan. Urutkan sisanya menurut skor teknikal, tambahkan 5 top
   gainer hari itu, lalu `shortlist` teratas (default 30) lanjut.
2. **Dewan (kode):** `desk.analyse` penuh hanya untuk shortlist; tolak yang di bawah
   Overweight.
3. **Model (opsional, satu panggilan):** menilai semua kandidat sekaligus.

Titik masuk: `python -m core.screener`, atau `POST /api/screen` di `app.py`.
Daftar saham: `data/universe.txt` (contoh: `universe.example.txt`), jika kosong memakai
watchlist. Dokumentasi pengguna ada di bagian "Pemindai" di `README.md`.

## Klien AI: streaming, bukan menunggu dalam diam

Audit latensi 28-10-2026 (lihat jurnal keputusan: jawaban AI tercatat 10-34 menit
setelah klik) menemukan bahwa `glm-5.3-flash` lewat gateway memikirkan 1,6-3,2 ribu
token *reasoning* — 60-105 detik keheningan total — sebelum jawaban ada. Klien
non-streaming lama memotong panggilan itu di timeout lalu mengulanginya, sehingga
satu klik terasa 10-15 menit dan sering berakhir `ai_error`. `core/llm.py` sekarang
meminta `stream: true` lebih dulu: chunk mulai mengalir dalam detik, timeout hanya
mengukur stream yang benar-benar macet, event `thinking`/`writing` diteruskan ke
`advisor.advise(on_event=…)` untuk memperbarui baris progres, dan `thinking`
berulang tiap 30 detik agar job tidak dianggap kedaluwarsa. Gateway tanpa SSE
kembali ke satu permintaan biasa. Verifikasi: panggilan advisor asli yang sebelumnya
selalu gagal kini selesai 105,6 dtk dengan jawaban utuh.

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

## Audit 10-11-2026: perbaikan dari review kode

- Input ticker: hanya empat huruf (IDX) atau simbol dengan akhiran eksplisit; kode
  ambigu ditolak 400 di server dan dijelaskan di form (`_requested_symbol`,
  `symbolProblem`).
- `/api/*` GET juga memetakan `BadRequest`/`ValueError` ke 400; pesan 500 generik dan
  tidak lagi memuat nama kelas. `_static` memakai `Path.is_relative_to`.
- Body POST wajib `application/json` (415), maksimal 1 MB (413); Host/Origin di luar
  localhost ditolak 403. `/api/health` tidak lagi memuat path absolut `.env`.
- Wilder RSI/ATR; drawdown dari seluruh riwayat; SMA200 diambil dari seri 1 tahun bila
  rentang default 6 bulan tidak cukup; `prev_close` intraday memakai close sesi
  sebelumnya; batas rating mengikuti tabel README; confidence memakai kurva tanh
  sehingga sinyal kuat tidak selalu mentok 0,95.
- `guidance.simulate` (duplikat yang masih mengizinkan short) dihapus;
  `simulation.calculate` satu-satunya jalur simulasi.
- Watchlist dan penyelesaian jurnal mengambil harga paralel; pemindai memakai ekuitas
  tersimpan (lot tidak lagi kosong) dan menerima `max_candidates` dari API.
- Penilaian jurnal juga dijalankan otomatis di background saat `app.main()` start, supaya
  keputusan yang sudah jatuh tempo tidak menumpuk menunggu tombol ditekan (`_startup_settle`).
- `journal._dated_bars` membuang bar volume 0. Yahoo menyimpan harga lama pada bar sesi
  yang belum diperdagangkan; tanpa filter ini alpha bisa dihitung dari hari yang tidak
  bisa ditransaksikan (MKNT.JK dan TMPO.JK menjadi contoh nyata).
- UI: fallback server lama memakai status 404, bukan teks pesan; tab punya
  `aria-controls`/`aria-labelledby`; watchlist tidak direbuild bila markup sama; poll
  berhenti saat tab tersembunyi.
- Verifikasi: `tools/test_pure.py` 139/139, `tools/test_pure.mjs` 51/51, plus probe
  HTTP langsung (400/403/413/415, traversal 404, SSE/tick/benchmark).

## Audit 18-11-2026: delapan bug runtime

Semua ditemukan dengan mereproduksinya, lalu ada tes regresinya:

- `journal.settle_all` mengambil keluar di `sessions[window]` (sesi ke-6 untuk
  `holding_days=5`); sekarang `sessions[window - 1]` sesuai README. Baris yang sudah
  dinilai dengan angka lama di-reset dan dihitung ulang; jadi jangan mengira alpha lama
  masih berlaku.
- `IHSG.JK` (hasil normalisasi UI) tidak pernah dipetakan ke `^JKSE`; sekarang server
  dan `web/app.js` memetakan keduanya.
- `prev_close` memakai `meta.chartPreviousClose` = close sebelum **rentang** mulai, jadi
  garis "penutupan sebelumnya" di chart menunjuk harga enam bulan lalu. Sekarang
  dihitung dari `bars[-2]`; cache schema naik ke 3.
- Rating model yang tidak dikenali dulu diam-diam diganti rating mesin dan tetap
  dilaporkan sebagai setuju. Sekarang `advisor.advise` gagal dengan `LLMError` sehingga
  `ai.error` menjelaskan dan kolom AI jurnal tidak berbohong.
- Ticker karangan di `pilihan` pemindai tidak masuk `ai.dropped`; sekarang dilaporkan.
- Pemindai menghitung ukuran posisi dari harga beli, bukan harga pasar; sekarang
  `market.last_price()` dipakai bersama `/api/analyze`.
- Satu kode invalid di kolom pemindai menggagalkan seluruh request (400); sekarang token
  buruk disaring di UI dan dilaporkan per saham di `errors` API.
- Slot "5 top gainer" dalam dokumentasi sekarang dibaca sebagai "5 top gainer di antara
  saham yang lolos gerbang lapis 1" — sama dengan perilaku kode.

## Tindak lanjut 18-11-2026 (permintaan pemilik)

- `.env` sekarang memakai `OPENAI_MODEL=deepseek-v4.1-flash-1017` lewat gateway SumoPod.
  Satu panggilan nyata: **4,3 dtk**, 0 reasoning token, JSON valid, level tetap dari
  mesin. Model lama `glm-5.3-flash` memakai 1,1–3,9 rb token reasoning dan bisa 60–105 dtk.
- `tools/daily.py` — satu perintah harian: pemindai `record=True` tanpa AI (gratis), lalu
  `journal.settle_all()`. Jadwalkan lewat cron/Task Scheduler. Ini jalan pengumpulan
  sampel; jangan ubah bobot/ambang sebelum ~96 keputusan berarah terkumpul.
- `store._score_rows` menyimpan `alpha_stddev` dan `ci95_halfwidth`; tab Jurnal menampilkan
  margin kesalahan 95% supaya hit rate dari sampel kecil tidak disalahartikan.
- Gerbang sistem pemindai (`screener._system_gate`) sekarang memakai konstanta
  `guidance.MAX_ATR_PCT` (7%), `guidance.MIN_REWARD_RISK` (1:1), dan minimal 3 peran.
  Pengecekan kesegaran tetap di lapis 1 karena ia memakai tanggal `today` dari pemanggil,
  bukan jam dinding. Lapis 1 sengaja tetap inklusif untuk saham murah/tipis.

## Pipeline paper trading (permintaan pemilik)

Tanpa bot AI. Cron di mesin ini, Senin–Jumat WIB: 07:15 `tools/daily.py`,
09:00 `tools/execute.py`, 16:05 `tools/report.py`. Aturan dikunci di `MANDATE.md`.

- `core/paper.py` — buku besar terpisah (`paper_cash`, `paper_positions`,
  `paper_trades`) dengan modal awal Rp10 juta. Tidak pernah menyentuh
  `store.portfolio` / `store.cash`.
- `daily.py` menulis `data/scan.json` berisi pilihan + level mesin; `execute.py`
  membacanya, menjual stop/target, lalu membeli. Kalau tidak ada yang lolos,
  tetap beli 1 lot `mode: belajar` supaya tidak ada hari tanpa aktivitas.
- `report.py` menulis `data/laporan-YYYY-MM-DD.md`, menjalankan `settle_all()`,
  dan mengirim versi pendek ke Telegram lewat `core/notify.py` bila
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` terisi. Tanpa itu diam-diam dilewati.
- Dilarang mengubah bobot/Gate sebelum ≥96 keputusan berarah (lihat MANDATE).

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
