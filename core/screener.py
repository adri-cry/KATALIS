"""The screener: which stocks in a universe are worth buying right now.

Three stages, cheapest first, so a 40-ticker universe costs one price request per
ticker, a handful of idx lookups, and exactly one model call:

1. **Gate (code).** One Yahoo snapshot per ticker. Anything illiquid, wildly
   volatile, too young, too cheap, or stale is rejected with its reason written
   down. Survivors are ranked by the desk's technical role and the best
   ``shortlist`` move on.
2. **Desk (code).** The full five-role ``desk.analyse`` runs only on that
   shortlist (this is where the slow idx lookups happen). Anything the desk does
   not rate Buy or Overweight is rejected, again with its reason.
3. **Model (optional, one call).** The remaining candidates are shown to the
   model side by side. It may downgrade a candidate and may return no picks at
   all; it may not invent a ticker, a number, or a price level. Entry, stop and
   target always come from the desk.

If the model is off or fails, the picks fall back to the desk's own ordering and
the result says so. Nothing here is investment advice.

    python -m core.screener                # data/universe.txt, else the watchlist
    python -m core.screener BBCA BBRI --no-ai
    python -m core.screener --top 3 --record
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import advisor, desk, llm, market, store

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "data" / "universe.txt"
WIB = timezone(timedelta(hours=7))
BUYABLE = ("Buy", "Overweight")
MAX_UNIVERSE = 120                     # a runaway request must not hammer Yahoo


@dataclass(frozen=True)
class Gate:
    """Hard limits. A stock that fails any of them is not a candidate, whatever its score."""

    min_turnover: float = 1e9          # rupiah traded per day (20-session average volume x price)
    min_price: float = 100.0           # below this the tick is a large share of the price
    max_atr_pct: float = 7.0           # average daily range; above this it behaves like a "gorengan"
    min_sessions: int = 120            # a young listing has no trend worth reading
    max_stale_days: int = 7            # last bar older than this = suspended or feed problem


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------


def load_universe(path: Path = UNIVERSE_FILE) -> list[str]:
    """Tickers from ``data/universe.txt``: one or more per line, ``#`` starts a comment."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    symbols: list[str] = []
    for line in text.splitlines():
        for token in line.split("#", 1)[0].replace(",", " ").split():
            symbol = market.normalise(token)
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _resolve(symbols: list[str] | None) -> list[str]:
    chosen = [market.normalise(s) for s in (symbols or []) if str(s).strip()]
    if not chosen:
        chosen = load_universe() or store.watchlist()
    unique = list(dict.fromkeys(s for s in chosen if s))
    return unique[:MAX_UNIVERSE]


# ---------------------------------------------------------------------------
# Stage 1: the gate
# ---------------------------------------------------------------------------


def _last_session(snap: dict) -> date:
    return datetime.fromtimestamp(snap["bars"][-1]["t"], WIB).date()


def gate_reasons(ind: dict, last_session: date, today: date, gate: Gate) -> list[str]:
    """Why a stock fails the gate. An empty list means it passes."""
    reasons: list[str] = []
    if (ind.get("sessions") or 0) < gate.min_sessions:
        reasons.append(f"riwayat hanya {ind.get('sessions') or 0} sesi (butuh minimal {gate.min_sessions})")
    last = ind.get("last") or 0.0
    if last < gate.min_price:
        reasons.append(f"harga Rp{desk._rp(last)} di bawah batas Rp{desk._rp(gate.min_price)}")
    turnover = (ind.get("vol_avg20") or 0.0) * last
    if turnover < gate.min_turnover:
        reasons.append(f"nilai transaksi harian ±Rp{desk._compact(turnover)} di bawah batas "
                       f"Rp{desk._compact(gate.min_turnover)}")
    atr_pct = ind.get("atr_pct")
    if atr_pct is None:
        reasons.append("ATR tidak terhitung")
    elif atr_pct > gate.max_atr_pct:
        reasons.append(f"ATR {atr_pct}% terlalu liar (batas {gate.max_atr_pct}%)")
    lag = (today - last_session).days
    if lag > gate.max_stale_days:
        reasons.append(f"data terakhir {last_session.isoformat()} tertinggal {lag} hari")
    return reasons


def _in_pool(fn, items: list[str], workers: int) -> list[tuple[str, object, str | None]]:
    """Run ``fn`` over ``items``; a failure becomes (symbol, None, message), never a silent drop."""

    def one(symbol: str):
        try:
            return symbol, fn(symbol), None
        except Exception as exc:  # noqa: BLE001 — reported to the caller, not swallowed
            return symbol, None, f"{type(exc).__name__}: {str(exc)[:160]}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, items))


# ---------------------------------------------------------------------------
# Stage 3: the model
# ---------------------------------------------------------------------------

_SYSTEM = """Anda analis ekuitas Indonesia (IDX) yang skeptis. Tugas: dari daftar KANDIDAT yang sudah lolos saringan mesin, tentukan mana yang benar-benar layak dibeli sekarang, dan tolak sisanya.

ATURAN MUTLAK:
1. Gunakan HANYA angka yang ada di EVIDENCE. Jangan mengarang harga, rasio, berita, atau peristiwa. Jangan menyebut level entry, stop, atau target baru: level itu dihitung mesin.
2. Peran dengan tersedia=false adalah KETIADAAN data, bukan netral. Kandidat yang bertumpu pada proksi harga (sumber "proxy") atau kehilangan fundamental/berita harus mendapat keyakinan lebih rendah, dan itu harus disebut.
3. Default-nya TIDAK membeli. "pilihan" boleh kosong, dan kosong adalah jawaban yang sah bila tidak ada yang meyakinkan. Jangan mengisi kuota.
4. Nilai SETIAP kandidat. Rating wajib salah satu: Buy, Overweight, Hold, Underweight, Sell. Rating mesin hanya titik awal; turunkan bila buktinya rapuh atau saling bertentangan (mis. RSI jenuh beli, harga jauh di atas SMA20, volume sepi, hanya satu peran yang mendukung).
5. Hanya kandidat berating Buy atau Overweight yang boleh masuk "pilihan", urut dari yang paling meyakinkan. Maksimal __MAX__.
6. Tulis ringkas dan konkret dalam bahasa Indonesia; sebut angka saat berargumen. Jangan menjanjikan hasil.

Jawab HANYA dengan satu objek JSON:
{
  "pasar": "1-2 kalimat: pola yang sama di antara kandidat, hanya dari EVIDENCE",
  "kandidat": [
    {"kode": "...", "rating": "Buy|Overweight|Hold|Underweight|Sell", "conviction": 0.0-1.0,
     "alasan": "1-2 kalimat dengan angka dari EVIDENCE",
     "risiko": "risiko terbesar yang terbaca dari data",
     "batal_bila": "satu syarat terukur yang membatalkan"}
  ],
  "pilihan": ["kode", "..."]
}"""

_BRIEF_INDICATORS = ("last", "change_pct", "sma20", "sma50", "sma200", "rsi14", "macd_hist",
                     "atr_pct", "mfi14", "obv_slope", "vol_ratio", "pos_52w",
                     "max_drawdown_pct", "volatility_pct")


def _flags(analysis: dict) -> list[str]:
    """Soft warnings: not grounds for rejection, but the model should weigh them."""
    ind = analysis["indicators"]
    out: list[str] = []
    rsi = ind.get("rsi14")
    if rsi is not None and rsi >= 70:
        out.append(f"RSI {rsi}: jenuh beli")
    last, sma20 = ind["last"], ind.get("sma20")
    if sma20 and last / sma20 - 1 > 0.15:
        out.append(f"harga {last / sma20 - 1:.0%} di atas SMA20: sudah berlari")
    if ind.get("vol_ratio") is not None and ind["vol_ratio"] < 0.5:
        out.append(f"volume {ind['vol_ratio']}x rata-rata: sepi")
    roles = analysis["roles"]
    missing = [r["name"] for r in roles if not r["available"]]
    if missing:
        out.append("tanpa data: " + ", ".join(missing))
    proxies = [r["name"] for r in roles if r["source"] == "proxy"]
    if proxies:
        out.append("hanya proksi harga: " + ", ".join(proxies))
    return out


def _brief(analysis: dict) -> dict:
    """The compact, numeric view of one candidate (about a third of what /api/analyze sends)."""
    ind = analysis["indicators"]
    plan = analysis["trader"]
    return {
        "kode": analysis["symbol"],
        "nama": analysis["name"],
        "per_tanggal": analysis["as_of"],
        "harga": analysis["price"],
        "nilai_transaksi_harian_rp": round((ind.get("vol_avg20") or 0) * ind["last"]),
        "indikator": {key: ind.get(key) for key in _BRIEF_INDICATORS},
        "imbal_hasil_pct": ind.get("returns"),
        "peran": [
            {"nama": r["name"], "skor": r["score"], "sumber": r["source"], "tersedia": r["available"],
             **({"mendukung": r["bull"][:2], "menentang": r["bear"][:2]} if r["available"]
                else {"keterangan": r["headline"]})}
            for r in analysis["roles"]
        ],
        "rating_mesin": analysis["decision"]["rating"],
        "komposit_mesin": analysis["manager"]["composite"],
        "rencana_mesin": {key: plan.get(key) for key in ("entry", "stop", "target", "reward_risk")},
        "catatan": _flags(analysis),
    }


def _conviction(value) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return 0.5


def apply_model(symbols: list[str], raw: dict, max_picks: int) -> tuple[dict, list[str], list[str]]:
    """Validate the model's answer against the candidates it was actually shown.

    Returns (verdicts by symbol, picks, dropped). Unknown tickers are dropped and
    reported; a pick whose own rating is not Buy/Overweight is removed; the pick
    count is enforced here whatever the model returned.
    """
    allowed = set(symbols)
    verdicts: dict[str, dict] = {}
    dropped: list[str] = []
    for item in raw.get("kandidat") or []:
        if not isinstance(item, dict):
            continue
        symbol = market.normalise(str(item.get("kode") or ""))
        if symbol not in allowed:
            dropped.append(symbol or "?")
            continue
        rating = advisor._clean(item.get("rating"))
        if rating is None:
            continue                                   # an unusable verdict counts as not assessed
        verdicts[symbol] = {
            "rating": rating,
            "conviction": _conviction(item.get("conviction")),
            "alasan": str(item.get("alasan") or "").strip()[:600],
            "risiko": str(item.get("risiko") or "").strip()[:300],
            "batal_bila": str(item.get("batal_bila") or "").strip()[:300],
        }

    picks: list[str] = []
    for entry in raw.get("pilihan") or []:
        symbol = market.normalise(str(entry or ""))
        verdict = verdicts.get(symbol)
        if verdict and verdict["rating"] in BUYABLE and symbol not in picks:
            picks.append(symbol)
    return verdicts, picks[:max(0, max_picks)], dropped


def _ask_model(analyses: list[dict], today: date, holding_days: int, max_picks: int) -> dict:
    evidence = {"tanggal": today.isoformat(), "benchmark": "IHSG (^JKSE)",
                "horizon_sesi_bursa": holding_days,
                "kandidat": [_brief(a) for a in analyses]}
    user = ("EVIDENCE (satu-satunya sumber angka):\n"
            + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
            + "\n\nSaring kandidat di atas.")
    return llm.chat_json(_SYSTEM.replace("__MAX__", str(max_picks)), user, temperature=0.1)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


def _ai_block(verdict: dict, analysis: dict, model_name: str) -> dict:
    """The model's verdict in the shape the journal and the dashboard already read."""
    plan = analysis["trader"]
    mechanical = analysis["decision"]["rating"]
    return {
        "rating": verdict["rating"],
        "conviction": verdict["conviction"],
        "agrees_with_desk": verdict["rating"] == mechanical,
        "mechanical_rating": mechanical,
        "thesis": verdict["alasan"],
        "risks": [verdict["risiko"]] if verdict["risiko"] else [],
        "action": {"entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
                   "sizing_note": "Level dari mesin, bukan dari model."},
        "what_would_change": [verdict["batal_bila"]] if verdict["batal_bila"] else [],
        "confidence_note": "Dinilai bersama kandidat lain dalam satu panggilan pemindai.",
        "model": model_name,
        "usage": {},
        "generated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "disclaimer": advisor.DISCLAIMER,
    }


def _record(analysis: dict, ai_block: dict | None, holding_days: int) -> int | None:
    """Write one pick to the journal exactly as /api/analyze would, tagged as the screener's."""
    return store.record(
        analysis["symbol"], analysis["as_of"], analysis["decision"]["rating"],
        analysis["price"], market.BENCHMARK, holding_days,
        {"rating": analysis["decision"]["rating"],
         "confidence": analysis["decision"]["confidence"],
         "composite": analysis["manager"]["composite"],
         "price": analysis["price"], "as_of": analysis["as_of"],
         "summary": analysis["decision"]["summary"], "via": "screener"},
        {"roles": analysis["roles"], "debate": analysis["debate"],
         "manager": analysis["manager"], "trader": analysis["trader"],
         "risk": analysis["risk"], "decision": analysis["decision"]},
        ai=ai_block,
    )


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _row(analysis: dict, verdict: dict | None, pick: bool) -> dict:
    plan = analysis["trader"]
    return {
        "symbol": analysis["symbol"],
        "name": analysis["name"],
        "price": analysis["price"],
        "as_of": analysis["as_of"],
        "pick": pick,
        "mechanical": {
            "rating": analysis["decision"]["rating"],
            "composite": analysis["manager"]["composite"],
            "confidence": analysis["decision"]["confidence"],
            "sources": {r["key"]: (r["source"] if r["available"] else "none") for r in analysis["roles"]},
        },
        "plan": {key: plan.get(key) for key in ("entry", "stop", "target", "reward_risk")},
        "flags": _flags(analysis),
        "ai": verdict,
    }


def screen(symbols: list[str] | None = None, *, max_picks: int = 5, shortlist: int = 15,
           max_candidates: int = 8, ai: bool | None = None, record: bool = False,
           holding_days: int = 5, range_: str = "1y", gate: Gate | None = None,
           today: date | None = None, workers: int = 4) -> dict:
    """Run the three stages over ``symbols`` (default: the universe file, else the watchlist).

    ``ai=None`` uses the model when a key is configured. ``record=True`` writes the
    picks to the journal so they are scored against IHSG like any other decision.
    """
    started = time.time()
    gate = gate or Gate()
    today = today or datetime.now(WIB).date()
    max_picks = max(1, min(10, int(max_picks)))
    shortlist = max(1, min(40, int(shortlist)))
    max_candidates = max(1, min(15, int(max_candidates)))
    universe = _resolve(symbols)

    errors: list[dict] = []
    rejected: list[dict] = []

    # -- stage 1: price snapshot and the gate -------------------------------
    passed: list[tuple[str, float]] = []
    for symbol, snap, error in _in_pool(lambda s: market.snapshot(s, range_), universe, workers):
        if error:
            errors.append({"symbol": symbol, "error": error})
            continue
        reasons = gate_reasons(snap["indicators"], _last_session(snap), today, gate)
        if reasons:
            rejected.append({"symbol": symbol, "stage": "gerbang", "reasons": reasons})
            continue
        technical = desk._role_market(snap)            # same-package use of the desk's technical role
        if technical["score"] <= 0:
            rejected.append({"symbol": symbol, "stage": "teknikal",
                             "reasons": [f"skor teknikal {technical['score']:+.2f}: tidak ada momentum positif"]})
            continue
        passed.append((symbol, technical["score"]))
    passed.sort(key=lambda item: item[1], reverse=True)
    for symbol, score in passed[shortlist:]:
        rejected.append({"symbol": symbol, "stage": "shortlist",
                         "reasons": [f"skor teknikal {score:+.2f} di luar {shortlist} teratas"]})
    short = [symbol for symbol, _ in passed[:shortlist]]

    # -- stage 2: the full desk, only for the shortlist ----------------------
    analyses: list[dict] = []
    for symbol, analysis, error in _in_pool(
            lambda s: desk.analyse(s, range_, holding_days, equity=0.0), short, min(workers, 2)):
        if error:
            errors.append({"symbol": symbol, "error": error})
            continue
        rating = analysis["decision"]["rating"]
        if rating not in BUYABLE:
            rejected.append({"symbol": symbol, "stage": "mesin", "reasons": [
                f"rating mesin {rating} (komposit {analysis['manager']['composite']:+.2f})"]})
            continue
        analyses.append(analysis)
    analyses.sort(key=lambda a: a["manager"]["composite"], reverse=True)
    for analysis in analyses[max_candidates:]:
        rejected.append({"symbol": analysis["symbol"], "stage": "kandidat", "reasons": [
            f"komposit {analysis['manager']['composite']:+.2f} di luar {max_candidates} kandidat teratas"]})
    analyses = analyses[:max_candidates]

    # -- stage 3: the model reads the candidates once ------------------------
    use_ai = llm.status()["configured"] if ai is None else bool(ai)
    ai_info: dict = {"used": False, "model": None, "error": None, "dropped": [], "unassessed": []}
    verdicts: dict[str, dict] = {}
    picks: list[str] = []
    market_note = None
    picks_source = "mesin"

    if use_ai and analyses:
        try:
            raw = _ask_model(analyses, today, holding_days, max_picks)
            verdicts, picks, dropped = apply_model([a["symbol"] for a in analyses], raw, max_picks)
            market_note = str(raw.get("pasar") or "").strip()[:500] or None
            ai_info.update(used=True, model=raw.get("_model") or llm.model(), dropped=dropped,
                           unassessed=[a["symbol"] for a in analyses if a["symbol"] not in verdicts],
                           usage=raw.get("_usage") or {})
            picks_source = "model"
        except llm.LLMError as exc:
            ai_info["error"] = str(exc)               # the desk's ordering stands; say why
            verdicts, picks = {}, []
    if picks_source == "mesin":
        picks = [a["symbol"] for a in analyses[:max_picks]]

    by_symbol = {a["symbol"]: a for a in analyses}
    ordered = picks + [a["symbol"] for a in analyses if a["symbol"] not in picks]
    rows = [_row(by_symbol[s], verdicts.get(s), s in picks) for s in ordered]

    recorded: list[dict] = []
    if record:
        for symbol in picks:
            verdict = verdicts.get(symbol)
            block = _ai_block(verdict, by_symbol[symbol], ai_info["model"] or llm.model()) if verdict else None
            recorded.append({"symbol": symbol, "id": _record(by_symbol[symbol], block, holding_days)})

    return {
        "as_of": today.isoformat(),
        "universe": len(universe),
        "candidates": rows,
        "picks": picks,
        "picks_source": picks_source,
        "market_note": market_note,
        "rejected": rejected,
        "errors": errors,
        "ai": ai_info,
        "recorded": recorded,
        "gate": gate.__dict__,
        "elapsed_s": round(time.time() - started, 1),
        "disclaimer": advisor.DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _print_report(result: dict) -> None:
    print(f"\nPemindai KATALIS | {result['as_of']} | universe {result['universe']} saham | "
          f"{result['elapsed_s']} dtk")
    if result["market_note"]:
        print(f"Pasar: {result['market_note']}")
    if result["ai"]["error"]:
        print(f"AI gagal: {result['ai']['error']} -> memakai urutan mesin")
    if result["errors"] and len(result["errors"]) == result["universe"]:
        # Every fetch failed: that is an outage, not a market with nothing worth buying.
        print("Tidak ada hasil: semua saham gagal diambil.")
    else:
        print(f"Pilihan ({result['picks_source']}):"
              + ("" if result["picks"] else " tidak ada yang layak hari ini"))
    for row in result["candidates"]:
        mark = "*" if row["pick"] else " "
        ai = row["ai"]
        plan = row["plan"]
        print(f" {mark} {row['symbol']:<9} mesin {row['mechanical']['rating']:<10} "
              f"komposit {row['mechanical']['composite']:+.2f}"
              + (f" | AI {ai['rating']} ({ai['conviction']:.0%})" if ai else "")
              + f" | entry {desk._rp(plan['entry'])} stop {desk._rp(plan['stop'])} "
                f"target {desk._rp(plan['target'])} R:R {plan['reward_risk']}")
        if ai and ai["alasan"]:
            print(f"      {ai['alasan']}")
        if ai and ai["risiko"]:
            print(f"      risiko: {ai['risiko']}")
        for flag in row["flags"]:
            print(f"      ! {flag}")
    if result["rejected"]:
        print(f"\nDitolak: {len(result['rejected'])} saham (lihat --json untuk alasannya)")
    for item in result["errors"]:
        print(f"Gagal: {item['symbol']}: {item['error']}")
    for item in result["recorded"]:
        print(f"Jurnal: {item['symbol']} " + (f"tercatat #{item['id']}" if item["id"] else "sudah tercatat hari ini"))
    print(f"\n{result['disclaimer']}\n")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="python -m core.screener",
                                     description="Cari saham IDX yang layak dibeli dari sebuah universe.")
    parser.add_argument("symbols", nargs="*", help="kode saham; kosong = data/universe.txt, lalu watchlist")
    parser.add_argument("--top", type=int, default=5, help="maksimal pilihan (default 5)")
    parser.add_argument("--shortlist", type=int, default=15, help="berapa saham lolos ke analisis penuh")
    parser.add_argument("--no-ai", action="store_true", help="urutan mesin saja, tanpa panggilan model")
    parser.add_argument("--record", action="store_true", help="catat pilihan ke jurnal untuk dinilai nanti")
    parser.add_argument("--json", action="store_true", help="cetak hasil lengkap sebagai JSON")
    args = parser.parse_args(argv)

    store.init()
    result = screen(args.symbols or None, max_picks=args.top, shortlist=args.shortlist,
                    ai=False if args.no_ai else None, record=args.record)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(result)
    # A scheduled run must be able to tell "nothing worth buying" (0) from "nothing could be read" (2).
    return 2 if result["universe"] and len(result["errors"]) == result["universe"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
