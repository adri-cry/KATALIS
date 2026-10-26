"""The AI advisor: a model reads the desk's evidence and argues about it.

The division of labour is deliberate.

- **The desk computes.** Prices, indicators, foreign flow, broker accumulation,
  valuation and the mechanical rating are all derived from data, and stay the
  same whether or not a model is present. They are what the journal scores.
- **The model interprets.** It receives those numbers as evidence and returns a
  recommendation, a thesis, the risks it sees, and what would change its mind.

Giving the model the numbers and asking for an opinion is the whole point; the
prompt therefore forbids inventing any figure that is not in the evidence, and
requires missing data to be named as missing. A model that cannot be reached
raises — the deterministic verdict stands rather than a fabricated one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import llm

WIB = timezone(timedelta(hours=7))
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
DISCLAIMER = ("Dihasilkan model bahasa dari data pasar di atas, bukan nasihat investasi "
              "dari penasihat berlisensi. Model dapat salah; angka dapat tertunda.")

_SYSTEM = """Anda analis ekuitas Indonesia (IDX) yang menulis untuk trader profesional.

ATURAN MUTLAK:
1. Gunakan HANYA angka yang ada di EVIDENCE. Jangan mengarang harga, tanggal, rasio,
   berita, atau peristiwa apa pun yang tidak tertulis di sana.
2. Bila sebuah peran ditandai tidak tersedia, sebutkan sebagai ketiadaan data —
   jangan menganggapnya netral.
3. Jangan memberi jaminan hasil. Sebutkan apa yang bisa membatalkan tesis.
4. Rating wajib salah satu: Buy, Overweight, Hold, Underweight, Sell.
5. Tulis dalam bahasa Indonesia yang padat dan konkret. Sebut angka saat berargumen:
   "asing net jual 1,06 miliar saham", bukan "aliran asing negatif".

Jawab HANYA dengan satu objek JSON:
{
  "rating": "Buy|Overweight|Hold|Underweight|Sell",
  "conviction": 0.0-1.0,
  "thesis": "2-4 paragraf: apa yang mendukung, apa yang melawan, mengapa rating ini",
  "risks": ["risiko konkret yang terbaca dari data"],
  "action": {"entry": angka|null, "stop": angka|null, "target": angka|null, "sizing_note": "..."},
  "what_would_change": ["syarat yang membatalkan tesis"],
  "confidence_note": "satu kalimat: seberapa lengkap datanya"
}"""


def _evidence(analysis: dict, portfolio: dict | None) -> dict:
    """The compact, numeric view a model can reason over without the bar array."""
    ind = analysis.get("indicators") or {}
    keep = ("last", "change_pct", "sma20", "sma50", "sma200", "rsi14", "macd_hist",
            "atr14", "atr_pct", "bb_pct_b", "mfi14", "obv_slope", "vol_ratio",
            "vol_avg20", "pos_52w", "max_drawdown_pct", "volatility_pct", "up_down_volume")
    plan = analysis.get("trader") or {}
    return {
        "instrumen": {
            "kode": analysis.get("symbol"),
            "nama": analysis.get("name"),
            "mata_uang": analysis.get("currency"),
            "per_tanggal": analysis.get("as_of"),
            "harga": analysis.get("price"),
        },
        "indikator": {key: ind.get(key) for key in keep},
        "peran": [
            {"nama": role["name"], "skor": role["score"], "sumber": role["source"],
             "tersedia": role["available"],
             "argumen_mendukung": role["bull"][:3],
             "argumen_menentang": role["bear"][:3]}
            for role in analysis.get("roles", [])
        ],
        "komposit_mekanis": (analysis.get("manager") or {}).get("composite"),
        "rating_mekanis": (analysis.get("decision") or {}).get("rating"),
        "rencana_mekanis": {key: plan.get(key) for key in
                            ("entry", "stop", "target", "reward_risk", "lots", "value")},
        "benchmark": "IHSG (^JKSE)",
        "portofolio": _portfolio(portfolio),
        "batas": ("Harga tertunda. Analisis memakai data harian, bukan intraday. "
                  "Tidak ada berita jika peran berita tidak tersedia."),
    }


def _portfolio(portfolio: dict | None) -> dict:
    if not portfolio or not portfolio.get("equity"):
        return {"ekuitas": 0, "catatan": "portofolio belum diisi; ukuran posisi tidak dihitung"}
    positions = portfolio.get("positions") or []
    return {
        "ekuitas": round(portfolio.get("equity") or 0),
        "kas": round(portfolio.get("cash") or 0),
        "posisi": [{"kode": p.get("symbol"), "jumlah": p.get("quantity"),
                    "harga_rata_rata": p.get("avg_price")} for p in positions[:10]],
    }


def _clean(value, fallback=None):
    """A rating the caller can trust, or the mechanical one when it is unusable."""
    rating = str(value or "").strip().title()
    return rating if rating in RATINGS else fallback


def _texts(value, limit: int = 5) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def advise(analysis: dict, portfolio: dict | None = None) -> dict:
    """Ask the model for a recommendation over the desk's evidence.

    Returns the normalised suggestion, or raises ``llm.LLMError`` when the key,
    the network, or the answer is unusable.
    """
    if not llm.status()["configured"]:
        raise llm.LLMError("OPENAI_API_KEY belum diisi")

    import json

    evidence = _evidence(analysis, portfolio)
    user = ("EVIDENCE (satu-satunya sumber angka):\n"
            + json.dumps(evidence, ensure_ascii=False, indent=2)
            + "\n\nBerikan rekomendasi untuk instrumen ini beserta alasannya.")

    raw = llm.chat_json(_SYSTEM, user)
    mechanical = (analysis.get("decision") or {}).get("rating")
    rating = _clean(raw.get("rating"), fallback=mechanical)
    if rating is None:
        raise llm.LLMError(f"rating dari model tidak dikenali: {raw.get('rating')!r}")

    conviction = raw.get("conviction")
    try:
        conviction = max(0.0, min(1.0, float(conviction)))
    except (TypeError, ValueError):
        conviction = 0.5

    action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
    levels = {}
    for key in ("entry", "stop", "target"):
        value = action.get(key)
        levels[key] = round(float(value), 2) if isinstance(value, (int, float)) else None

    return {
        "rating": rating,
        "conviction": round(conviction, 2),
        "agrees_with_desk": rating == mechanical,
        "mechanical_rating": mechanical,
        "thesis": str(raw.get("thesis") or "").strip()[:4000],
        "risks": _texts(raw.get("risks")),
        "action": {**levels, "sizing_note": str(action.get("sizing_note") or "").strip()[:400]},
        "what_would_change": _texts(raw.get("what_would_change")),
        "confidence_note": str(raw.get("confidence_note") or "").strip()[:400],
        "model": raw.get("_model") or llm.model(),
        "usage": raw.get("_usage") or {},
        "generated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "disclaimer": DISCLAIMER,
    }
