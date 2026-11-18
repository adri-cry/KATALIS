# Mandat 3 bulan — KATALIS paper trading

Dokumen ini mengunci aturan. Bacanya setiap kali mengevaluasi. **Dilarang
mengubah aturan sebelum kriteria bulan berjalan terpenuhi.**

## Jadwal (cron di mesin ini, Senin–Jumat, WIB)

| Jam | Skrip | Fungsi |
|---|---|---|
| 07:15 | `tools/daily.py` | scan universe, catat pilihan ke jurnal, `scan.json`, settle |
| 09:00 | `tools/execute.py` | jual stop/target, beli sesuai aturan, tulis buku paper |
| 16:05 | `tools/report.py` | laporan harian, settle, kirim Telegram bila dikonfigurasi |

Semua output di `data/` (terabaikan git). Log: `data/cron.log`.

## Aturan yang dikunci

- Ini **simulasi**. Tidak ada order broker, tidak ada uang sungguhan.
- Level entry/stop/target selalu dari mesin. AI hanya tafsir, tidak ikut eksekusi.
- Tidak ada short selling.
- Risiko 1% ekuitas per ide, maks 10% ekuitas per posisi, lot 100, tick IDX.
- Maksimal 5 posisi, eksposur total ≤30% ekuitas.
- Beli hanya bila `guidance.action == "Beli"`, ATR ≤7%, R:R ≥1, minimal 3 peran,
  snapshot ≤7 hari.
- Jual bila harga menyentuh stop atau target mesin.
- **Minimal belajar:** bila tidak ada yang lolos, tetap beli 1 lot kandidat
  komposit tertinggi dengan ATR ≤10%, ditandai `mode: belajar`. Ini pengecualian
  sadar supaya tidak ada hari pasar tanpa aktivitas; dievaluasi terpisah.
- Buku paper (`core/paper.py`) **terpisah** dari portofolio asli. Jangan pernah
  memakai `/api/portfolio` atau `/api/cash` untuk simulasi.

## Gerbang evaluasi

| Bulan | Fokus | Kriteria lulus |
|---|---|---|
| 1 | Kumpul data, aturan dikunci | ≥96 keputusan berarah, tanpa perubahan aturan |
| 2 | Evaluasi per bucket (ATR, R:R, arah IHSG) | 1–2 hipotesis diuji ke data **baru**, bukan data lama |
| 3 | Uji tebal & disiplin | alpha mesin positif, batas bawah CI >0, lebih baik dari Hold |

Sampel berarah saat ini: lihat `/api/journal` → `stats.directional_count`.
Presisi ±10% butuh ~96 sampel; ±5% butuh ~384.

## Larangan keras

- Dilarang mengubah bobot 34/20/20/16/10, ambang ±0,3/±0,9, atau `Gate`
  sebelum gerbang bulan 1 terpenuhi.
- Dilarang mengejar profit dengan mengkalibrasi berdasarkan rugi hari ini.
- Dilarang mencampur portofolio asli dengan paper.
- Dilarang menganggap kegagalan pengambilan harga sebagai "tidak ada pilihan".

## Telegram

Diisi lewat `.env`: `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`.
Tanpa keduanya, laporan tetap ditulis ke `data/` dan cron tetap sukses.
Kunci tidak pernah dicetak ke log atau commit.
