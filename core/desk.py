"""The desk: five roles read the same snapshot, a manager weighs them, and one
five-tier rating comes out with the plan behind it.

This is the part the two source projects contribute to:

- **TradingAgents** supplies the shape — analyst reports, a bull/bear debate, a
  research manager, a trader with concrete levels, three risk stances, and a
  portfolio manager emitting exactly one of Buy / Overweight / Hold /
  Underweight / Sell.
- **idx** supplies the data a price chart cannot show: foreign flow, broker
  accumulation, valuation, dividends, news. Without its binary those roles
  degrade to a proxy computed from price and volume, and every such role says
  so — a missing feed is never reported as a neutral finding.

The result is deterministic: the same snapshot always produces the same rating,
so a track record measured later means something. Nothing here is investment
advice.
"""

from __future__ import annotations

import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import idxbridge, market

WIB = timezone(timedelta(hours=7))
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

_WEIGHTS = {"market": 0.34, "flow": 0.20, "bandar": 0.20, "fundamental": 0.16, "news": 0.10}
_BANDAR_POINTS = {"big_accumulation": 1.8, "accumulation": 1.2, "neutral": 0.0,
                  "distribution": -1.2, "big_distribution": -1.8}


def _clamp(value: float, low: float = -2.0, high: float = 2.0) -> float:
    return max(low, min(high, value))


def _stance(score: float) -> str:
    if score >= 0.5:
        return "bullish"
    if score <= -0.5:
        return "bearish"
    return "neutral"


def _rp(value: float | None, unit: str = "") -> str:
    """Rupiah-style thousands: 5025 -> 5.025."""
    if value is None:
        return "–"
    text = f"{value:,.0f}".replace(",", ".")
    return f"{text}{unit}"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:+.2f}%".replace(".", ",")


def _compact(value: float | None) -> str:
    """Share counts the way a broker sheet writes them: 1.056.023.900 -> 1,06 miliar."""
    if value is None:
        return "–"
    magnitude = abs(value)
    for threshold, label, digits in ((1e12, "triliun", 2), (1e9, "miliar", 2), (1e6, "juta", 1)):
        if magnitude >= threshold:
            return f"{value / threshold:.{digits}f} {label}".replace(".", ",")
    return f"{value:,.0f}".replace(",", ".")


def _tick(price: float) -> float:
    """IDX tick size by price band, so a stop lands on a tradable price."""
    for ceiling, tick in ((200, 1), (500, 2), (2000, 5), (5000, 10)):
        if price < ceiling:
            return tick
    return 25


def _round_tick(price: float) -> float:
    tick = _tick(price)
    return round(round(price / tick) * tick, 2)


def _role(key: str, name: str, score: float, bull: list[str], bear: list[str],
          source: str, note: str = "", warning: str = "",
          ceiling: float = 2.0) -> dict:
    # Every role speaks the same ±2 language: the raw sum is divided by the
    # largest sum its own terms can reach. Without this a 16% role that
    # saturates easily outweighs a 34% role that rarely does — BBCA on
    # 2026-10-26: fundamental +2.0 alone flipped Underweight into Hold.
    score = round(_clamp(score / ceiling * 2.0), 2)
    headline = (bull[0] if score > 0 and bull
                else bear[0] if score < 0 and bear
                else note or "Tidak ada sinyal tegas dari data yang tersedia.")
    return {"key": key, "name": name, "score": score, "stance": _stance(score),
            "headline": headline, "bull": bull, "bear": bear,
            "source": source, "available": True, "warning": warning}


def _missing(key: str, name: str, why: str) -> dict:
    return {"key": key, "name": name, "score": 0.0, "stance": "neutral",
            "headline": why, "bull": [], "bear": [], "source": "none", "available": False}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def _role_market(snap: dict) -> dict:
    ind = snap["indicators"]
    last = ind["last"]
    score, bull, bear = 0.0, [], []

    if ind["sma20"] and ind["sma50"]:
        if last > ind["sma20"] > ind["sma50"]:
            score += 0.7
            bull.append(f"Harga {_rp(last)} di atas SMA20 ({_rp(ind['sma20'])}) dan SMA50 "
                        f"({_rp(ind['sma50'])}): struktur naik.")
        elif last < ind["sma20"] < ind["sma50"]:
            score -= 0.7
            bear.append(f"Harga {_rp(last)} di bawah SMA20 dan SMA50: struktur turun.")
    if ind["sma200"]:
        if last > ind["sma200"]:
            score += 0.35
            bull.append(f"Di atas SMA200 ({_rp(ind['sma200'])}): tren jangka panjang belum rusak.")
        else:
            score -= 0.35
            bear.append(f"Di bawah SMA200 ({_rp(ind['sma200'])}): tekanan jangka panjang.")

    rsi = ind["rsi14"]
    if rsi is not None:
        if rsi >= 70:
            score -= 0.35
            bear.append(f"RSI {rsi}: jenuh beli, risiko koreksi jangka pendek.")
        elif rsi <= 30:
            score += 0.35
            bull.append(f"RSI {rsi}: jenuh jual, potensi rebound.")
        elif rsi >= 55:
            score += 0.25
            bull.append(f"RSI {rsi}: momentum di sisi kuat, belum jenuh.")
        elif rsi <= 45:
            score -= 0.2
            bear.append(f"RSI {rsi}: momentum lemah di bawah titik tengah.")

    if ind["macd_hist"] is not None:
        if ind["macd_hist"] > 0:
            score += 0.4
            bull.append("MACD di atas garis sinyal: momentum positif.")
        else:
            score -= 0.4
            bear.append("MACD di bawah garis sinyal: momentum negatif.")

    percent_b = ind["bb_pct_b"]
    if percent_b is not None:
        if percent_b > 1:
            score -= 0.25
            bear.append("Harga menembus pita Bollinger atas: mahal secara jangka pendek.")
        elif percent_b < 0:
            score += 0.25
            bull.append("Harga di bawah pita Bollinger bawah: tertekan berlebih.")

    if ind["vol_ratio"] and ind["change_pct"]:
        if ind["vol_ratio"] >= 1.5 and ind["change_pct"] > 0:
            score += 0.3
            bull.append(f"Volume {ind['vol_ratio']}x rata-rata pada hari naik: minat meningkat.")
        elif ind["vol_ratio"] >= 1.5 and ind["change_pct"] < 0:
            score -= 0.3
            bear.append(f"Volume {ind['vol_ratio']}x rata-rata pada hari turun: distribusi.")

    return _role("market", "Analis Teknikal", score, bull, bear, source="yahoo",
                 ceiling=2.35)  # 0.7+0.35+0.35+0.4+0.25+0.3


def _role_flow(ticker: str, snap: dict) -> dict:
    """Foreign flow from idx (`data.totals`), or volume pressure as the proxy."""
    fallback = ""
    if idxbridge.available():
        try:
            totals = idxbridge.foreign(ticker).get("totals") or {}
            net = totals.get("foreign_net")
            sessions = int(totals.get("sessions") or 0)
            if net is not None and sessions:
                gross = (totals.get("foreign_buy") or 0) + (totals.get("foreign_sell") or 0) or 1
                side = 1 if net > 0 else -1
                score = _clamp(side * (0.55 + abs(net) / gross * 2.4))
                streak = int(totals.get("streak") or 0)
                bull, bear = ([], [])
                text = (f"Asing net {'beli' if net > 0 else 'jual'} {_compact(abs(net))} saham "
                        f"dalam {sessions} sesi "
                        f"({totals.get('net_buy_sessions', 0)} sesi beli, "
                        f"{totals.get('net_sell_sessions', 0)} sesi jual).")
                (bull if net > 0 else bear).append(text)
                if abs(streak) >= 3:
                    (bull if streak > 0 else bear).append(
                        f"Aliran {'masuk' if streak > 0 else 'keluar'} {abs(streak)} sesi beruntun.")
                return _role("flow", "Analis Aliran Asing", score, bull, bear, source="idx")
        except Exception as exc:  # noqa: BLE001 — a broken feed degrades to the proxy, visibly
            fallback = f"data idx gagal: {idxbridge.explain(exc)}"

    ind = snap["indicators"]
    score, bull, bear = 0.0, [], []
    slope = ind["obv_slope"]
    if slope is not None:
        if slope > 0.5:
            score += 0.9
            bull.append(f"Akumulasi volume (OBV) naik {slope:.1f}x volume harian dalam 20 sesi.")
        elif slope > 0.1:
            score += 0.4
            bull.append("OBV naik tipis: tekanan beli lebih kuat.")
        elif slope < -0.5:
            score -= 0.9
            bear.append(f"Distribusi volume: OBV turun {abs(slope):.1f}x volume harian.")
        elif slope < -0.1:
            score -= 0.4
            bear.append("OBV turun tipis: tekanan jual lebih kuat.")
    ratio = ind["up_down_volume"]
    if ratio is not None:
        if ratio >= 1.5:
            score += 0.5
            bull.append(f"Volume hari naik {ratio}x volume hari turun (20 sesi).")
        elif ratio <= 0.7:
            score -= 0.5
            bear.append(f"Volume hari turun lebih besar: rasio naik/turun {ratio}x.")
    return _role("flow", "Analis Aliran Dana", score, bull, bear, source="proxy",
                 warning=fallback, ceiling=1.4)  # 0.9+0.5


def _role_bandar(ticker: str, snap: dict) -> dict:
    """Broker accumulation from idx (`bandar detect` returns a list), or an MFI/OBV proxy."""
    fallback = ""
    if idxbridge.available():
        try:
            payload = idxbridge.bandar(ticker)
            item = payload[0] if isinstance(payload, list) and payload else payload
            if isinstance(item, dict):
                signal = str(item.get("signal") or "").lower()
                if signal in _BANDAR_POINTS:
                    points = _BANDAR_POINTS[signal]
                    net_pct = item.get("net_pct")
                    phase = str(item.get("phase") or "").lower()
                    detail = (f", net {_pct(float(net_pct))} dari nilai transaksi"
                              if isinstance(net_pct, (int, float)) else "")
                    phase_text = f", fase {phase}" if phase else ""
                    text = f"Bandarmologi: {signal.replace('_', ' ')}{detail}{phase_text}."
                    gain = item.get("bandar_gain_pct")
                    extra = ([] if not isinstance(gain, (int, float)) else
                             [f"Rata-rata harga bandar {_pct(float(gain))} dari harga kini."])
                    bull = ([text] + extra) if points > 0 else []
                    bear = ([text] + extra) if points < 0 else []
                    return _role("bandar", "Analis Bandar", points, bull, bear,
                                 source="idx", ceiling=1.8)
        except Exception as exc:  # noqa: BLE001
            fallback = f"data idx gagal: {idxbridge.explain(exc)}"

    ind = snap["indicators"]
    score, bull, bear = 0.0, [], []
    mfi = ind["mfi14"]
    if mfi is not None:
        if mfi >= 80:
            score -= 0.5
            bear.append(f"MFI {mfi}: aliran uang jenuh, risiko distribusi.")
        elif mfi >= 55:
            score += 0.55
            bull.append(f"MFI {mfi}: aliran uang masuk, pembeli menguasai volume.")
        elif mfi <= 20:
            score += 0.45
            bull.append(f"MFI {mfi}: aliran uang keluar ekstrem, kandidat akumulasi diam-diam.")
        elif mfi <= 45:
            score -= 0.4
            bear.append(f"MFI {mfi}: aliran uang keluar.")
    slope = ind["obv_slope"]
    position = ind["pos_52w"]
    if slope is not None and position is not None:
        if position < 0.25 and slope > 0.2:
            score += 0.7
            bull.append(f"OBV naik saat harga masih di {position:.0%} rentang 52 minggu: pola akumulasi.")
        elif position > 0.85 and slope < -0.2:
            score -= 0.7
            bear.append("OBV turun saat harga di puncak rentang 52 minggu: pola distribusi.")
    return _role("bandar", "Analis Bandar", score, bull, bear, source="proxy",
                 warning=fallback, ceiling=1.25)  # 0.55+0.7


def _role_fundamental(ticker: str) -> dict:
    """Valuation and quality from idx (`data.valuation`, `.profitability`, `.dividend`)."""
    if not idxbridge.available():
        return _missing("fundamental", "Analis Fundamental",
                        "Butuh data idx: P/E, P/B, ROE dan dividen tidak bisa disimpulkan dari harga.")
    try:
        data = idxbridge.fundamental(ticker) or {}
    except Exception as exc:  # noqa: BLE001
        return _missing("fundamental", "Analis Fundamental",
                        f"Data idx tidak terbaca: {idxbridge.explain(exc)}")

    valuation = data.get("valuation") or {}
    profitability = data.get("profitability") or {}
    dividend = data.get("dividend") or {}
    analyst = data.get("analyst") or {}
    verdict = data.get("verdict") or {}

    def num(source: dict, *keys):
        for key in keys:
            value = source.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    pe = num(valuation, "pe_trailing")
    pb = num(valuation, "price_to_book")
    roe = num(profitability, "roe_pct")
    yield_pct = num(dividend, "yield_pct")
    target = num(analyst, "target_mean")
    recommendation = str(analyst.get("recommendation") or "").replace("_", " ")
    label = str(verdict.get("label") or "")

    score, bull, bear = 0.0, [], []
    if pe is not None and pe > 0:
        if pe < 12:
            score += 0.5
            bull.append(f"P/E {pe:.1f}x: murah untuk pasar Indonesia.")
        elif pe > 25:
            score -= 0.5
            bear.append(f"P/E {pe:.1f}x: mahal, butuh pertumbuhan untuk menopang.")
    if pb is not None and pb > 0:
        if pb < 1:
            score += 0.4
            bull.append(f"P/B {pb:.2f}x: di bawah nilai buku.")
        elif pb > 4:
            score -= 0.3
            bear.append(f"P/B {pb:.2f}x: premi tinggi terhadap nilai buku.")
    if roe is not None:
        if roe > 15:
            score += 0.6
            bull.append(f"ROE {roe:.1f}%: modal bekerja efisien.")
        elif roe < 8:
            score -= 0.4
            bear.append(f"ROE {roe:.1f}%: imbal hasil modal tipis.")
    if yield_pct is not None and yield_pct > 3:
        score += 0.3
        bull.append(f"Dividen {yield_pct:.1f}%: imbal hasil tunai menarik.")
    price = num(data, "price")
    if target and price:
        upside = (target / price - 1) * 100
        if upside > 10:
            score += 0.5
            bull.append(f"Konsensus {recommendation or 'analis'} menargetkan {_rp(target)} "
                        f"({upside:+.0f}% dari harga kini).")
        elif upside < -10:
            score -= 0.4
            bear.append(f"Target konsensus {_rp(target)} justru {upside:+.0f}% dari harga kini.")
    if label in ("undervalued", "overvalued"):
        score += 0.3 if label == "undervalued" else -0.3
        (bull if label == "undervalued" else bear).append(
            f"Pembacaan idx: {label} (skor {verdict.get('score')}).")

    if not bull and not bear:
        return _missing("fundamental", "Analis Fundamental",
                        "Data idx tersedia tetapi rasio yang dipakai tidak lengkap.")
    # ponytail: one ceiling (+2.6) covers both sides, so the bear side tops
    # out at -1.9/2.6*2 ≈ -1.46. Split into per-side ceilings if fundamental's
    # bear reach ever needs to match the other roles' -2.
    return _role("fundamental", "Analis Fundamental", score, bull, bear, source="idx",
                 ceiling=2.6)  # 0.5+0.4+0.6+0.3+0.5+0.3


def _role_news(ticker: str) -> dict:
    """Headline sentiment from idx. 'No coverage' is an absence, not neutrality."""
    if not idxbridge.available():
        return _missing("news", "Analis Berita", "Butuh data idx: berita Kontan/CNBC dan sentimennya.")
    try:
        data = idxbridge.sentiment(ticker) or {}
    except Exception as exc:  # noqa: BLE001
        return _missing("news", "Analis Berita", f"Data idx tidak terbaca: {idxbridge.explain(exc)}")

    items = int(data.get("items") or 0)
    label = str(data.get("label") or "").lower()
    if items == 0 or label == "no_coverage":
        return _missing("news", "Analis Berita",
                        "Tidak ada liputan berita yang cocok untuk saham ini pekan ini.")

    score = data.get("score")
    points = float(score) * 2.5 if isinstance(score, (int, float)) else 0.0
    counts = (f"{int(data.get('positive') or 0)} positif, "
              f"{int(data.get('negative') or 0)} negatif, "
              f"{int(data.get('neutral') or 0)} netral dari {items} berita.")
    headline = f"Sentimen berita: {label.replace('_', ' ')} — {counts}"
    return _role("news", "Analis Berita", points,
                 [headline] if points > 0 else [], [headline] if points < 0 else [],
                 source="idx", ceiling=2.5)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _analyse_roles(ticker: str, snap: dict) -> list[dict]:
    """Evaluate independent evidence sources concurrently, preserving role order.

    Market data is already in ``snap``; the other roles may each invoke the
    optional IDX CLI and wait on network-backed sources. Running those reads in
    parallel keeps total wait near the slowest source instead of their sum.
    """
    market_role = _role_market(snap)  # purely local; keep external concurrency bounded
    tasks = (
        lambda: _role_flow(ticker, snap),
        lambda: _role_bandar(ticker, snap),
        lambda: _role_fundamental(ticker),
        lambda: _role_news(ticker),
    )
    # Each role reads an independent source (IDX, IPOT, fundamentals and RSS).
    # Start all four together: with a two-worker pool the second pair could not
    # begin until the first pair's slowest remote source returned. map preserves
    # evidence order while bounding concurrency to the number of roles.
    with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="katalis-role") as pool:
        return [market_role, *pool.map(lambda task: task(), tasks)]


def _rating(composite: float) -> str:
    if composite >= 0.9:
        return "Buy"
    if composite >= 0.3:
        return "Overweight"
    if composite > -0.3:
        return "Hold"
    if composite > -0.9:
        return "Underweight"
    return "Sell"


def _confidence(composite: float, roles: list[dict]) -> float:
    available = [r for r in roles if r["available"]]
    spread = statistics.pstdev([r["score"] for r in available]) if len(available) > 1 else 0.0
    value = 0.48 + 0.22 * abs(composite) + 0.05 * len(available) - 0.16 * spread
    return round(min(0.95, max(0.25, value)), 2)


def _plan(ind: dict, rating: str, equity: float) -> dict:
    last = ind["last"]
    atr = ind["atr14"] or last * 0.02
    bullish = rating in ("Buy", "Overweight")
    bearish = rating in ("Sell", "Underweight")
    if bullish:
        stop, target = last - 1.8 * atr, last + 3.0 * atr
    elif bearish:
        stop, target = last + 1.8 * atr, last - 3.0 * atr
    else:
        stop, target = last - 2.0 * atr, last + 2.0 * atr

    entry, stop, target = _round_tick(last), _round_tick(stop), _round_tick(target)
    risk_per_share = abs(entry - stop)
    reward_per_share = abs(target - entry)
    rr = round(reward_per_share / risk_per_share, 2) if risk_per_share else None

    lots = quantity = value = None
    if equity > 0 and risk_per_share > 0:
        budget = 0.01 * equity                      # 1% of equity at risk per idea
        lots = max(0, math.floor(budget / risk_per_share / 100))
        quantity = lots * 100                       # IDX trades in lots of 100
        value = round(quantity * entry, 0)
        if value > 0.1 * equity:                    # a single idea may not be the whole book
            lots = max(0, math.floor((0.1 * equity) / entry / 100))
            quantity = lots * 100
            value = round(quantity * entry, 0)

    return {
        "entry": entry, "stop": stop, "target": target,
        "risk_per_share": round(risk_per_share, 0),
        "reward_risk": rr,
        "lots": lots, "quantity": quantity, "value": value,
        "tick": _tick(entry),
        "note": ("Ukuran posisi: risiko 1% ekuitas per ide, dibatasi 10% ekuitas, "
                 "dibulatkan ke lot 100 saham." if equity > 0 else
                 "Isi nilai portofolio agar ukuran posisi dihitung."),
    }


def _risk_panel(ind: dict, rating: str) -> list[dict]:
    atr_pct = ind["atr_pct"] or 0
    drawdown = abs(ind["max_drawdown_pct"] or 0)
    turnover = (ind["vol_avg20"] or 0) * ind["last"]
    volatility = "rendah" if atr_pct < 2 else "sedang" if atr_pct < 4 else "tinggi"
    liquidity = "cair" if turnover > 5e9 else "cukup" if turnover > 5e8 else "tipis"
    return [
        {"name": "Agresif", "stance": "Mendorong posisi penuh" if rating in ("Buy", "Overweight") else "Menahan diri",
         "note": f"ATR {atr_pct:.2f}% (volatilitas {volatility}); ruang untuk menambah eksposur."},
        {"name": "Neutral", "stance": "Ikuti rencana",
         "note": f"Drawdown terburuk {drawdown:.1f}%; patuhi stop dan jangan rata-rata turun."},
        {"name": "Konservatif", "stance": "Kurangi ukuran" if atr_pct > 3.5 else "Ukuran standar",
         "note": f"Nilai transaksi harian ±Rp{_rp(turnover)}; likuiditas {liquidity}."},
    ]


def analyse(ticker: str, range_: str = "6mo", holding_days: int = 5,
            equity: float = 0.0) -> dict:
    """Run the whole desk over one instrument and return the decision plus its working."""
    snap = market.snapshot(ticker, range_)
    ind = snap["indicators"]

    roles = _analyse_roles(ticker, snap)

    available = [r for r in roles if r["available"]]
    total_weight = sum(_WEIGHTS[r["key"]] for r in available) or 1.0
    composite = round(sum(r["score"] * _WEIGHTS[r["key"]] for r in available) / total_weight, 3)
    rating = _rating(composite)
    confidence = _confidence(composite, roles)

    bull = [text for r in roles for text in r["bull"]][:4]
    bear = [text for r in roles for text in r["bear"]][:4]
    if not bear:
        bear = ["Tidak ada tekanan berarti yang terbaca dari data yang tersedia."]
    if not bull:
        bull = ["Belum ada bukti positif yang cukup kuat."]

    plan = _plan(ind, rating, equity)
    manager = {
        "composite": composite,
        "verdict": (f"Skor gabungan {composite:+.2f} pada skala -2…+2 → {rating}. "
                    f"{len(available)} dari {len(roles)} peran punya data; "
                    f"keyakinan {confidence:.0%}."),
    }
    pm = {
        "rating": rating,
        "confidence": confidence,
        "summary": (f"{rating} untuk {ticker} di {_rp(ind['last'])}. "
                    f"Stop {_rp(plan['stop'])}, target {_rp(plan['target'])}"
                    + (f", R:R {plan['reward_risk']}" if plan["reward_risk"] else "")
                    + ". Ini hasil mesin deterministik atas data pasar, bukan nasihat investasi."),
    }

    last_bar = snap["bars"][-1]["t"]
    as_of = datetime.fromtimestamp(last_bar, WIB).strftime("%Y-%m-%d")

    return {
        "symbol": snap["symbol"],
        "range": range_,
        "name": snap["name"],
        "currency": snap["currency"],
        "as_of": as_of,
        "holding_days": holding_days,
        "price": ind["last"],
        "change_pct": ind["change_pct"],
        "indicators": ind,
        "roles": roles,
        "debate": {"bull": bull, "bear": bear},
        "manager": manager,
        "trader": plan,
        "risk": _risk_panel(ind, rating),
        "decision": pm,
        "sources": {
            "price": "Yahoo Finance",
            "idx": idxbridge.status(),
            "mode": "live",
        },
        "bars": snap["bars"][-160:],
    }
