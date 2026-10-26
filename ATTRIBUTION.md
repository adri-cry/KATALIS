# Kredit dan lisensi pihak ketiga

KATALIS adalah perangkat lunak MIT (lihat [LICENSE](LICENSE)). Berkas ini
mencatat karya pihak lain yang gagasan atau datanya dipakai di sini.

Kode kedua proyek di bawah **tidak disertakan** di repo ini — `.gitignore`
mengecualikan `vendor/`, dan `tools/fetch_repos.mjs` mengambilnya sendiri bila
Anda ingin membacanya. Karena itu, hanya gagasan dan pola yang dipakai ulang,
bukan salinan berkas.

## TradingAgents

- Repositori: https://github.com/TauricResearch/TradingAgents
- Lisensi: **Apache License 2.0**
- Penulis: Yijia Xiao, Edward Sun, Di Luo, Wei Wang
- Makalah: https://arxiv.org/abs/2412.20138
- Yang dipakai: pola kantor riset (analis → debat bull/bear → manajer riset →
  trader → analis risiko → portfolio manager), skala rating lima tingkat, gagasan
  jurnal keputusan dengan refleksi dan penilaian terhadap benchmark.

## idx

- Repositori: https://github.com/fzn0x/idx
- Lisensi: **MIT License**
- Yang dipakai: bentuk keluaran JSON beserta makna exit code-nya, serta daftar
  data khusus IDX yang dibaca lewat jembatan `core/idxbridge.py` (aliran asing,
  bandarmologi, fundamental, dividen, kalender bursa).

## Sumber data

Data pasar bukan milik proyek ini, dan tunduk pada ketentuan masing-masing
penyedia:

| Penyedia | Dipakai untuk |
|---|---|
| Yahoo Finance | harga OHLCV, berita, profil perusahaan |
| Bursa Efek Indonesia (idx.co.id) | aliran asing, kalender bursa, indeks |
| KSEI (lewat idx) | register pemegang saham di atas 1% |
| Indo Premier (lewat idx) | ringkasan broker untuk bandarmologi |
| Kontan, CNBC Indonesia (lewat idx) | berita dan sentimen |
| OpenAI atau gateway kompatibel | lapisan penasihat AI (opsional, kunci milik pengguna) |

KATALIS hanya untuk riset dan bukan nasihat investasi.
