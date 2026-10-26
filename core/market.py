"""Market data: Yahoo Finance chart API, cached, plus the indicators the desk reads.

Only the standard library is used. The Yahoo endpoint needs a browser-like
User-Agent; without one it answers 429 rather than data.

Nothing here invents a number: a missing bar stays missing, and an indicator
whose window is not covered returns None instead of a plausible-looking value.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

BENCHMARK = "^JKSE"                      # IHSG, the benchmark every IDX call is scored against

# range -> (interval, detik cache). Jendela pendek di-cache sangat singkat
# supaya pergerakan intraday benar-benar terlihat; sebelumnya satu jendela
# 6 bulan di-cache 15 menit, jadi harga bisa tertinggal seperempat jam.
_RANGES = {
    "1d":  ("5m", 10),
    "5d":  ("15m", 20),
    "1mo": ("1d", 30),
    "3mo": ("1d", 60),
    "6mo": ("1d", 120),
    "1y":  ("1d", 300),
    "2y":  ("1d", 600),
}
_DEFAULT_RANGE = "6mo"
INTRADAY = ("1d", "5d")

# A cached payload written by an older build may lack fields this one reads
# (interval, for one). Bump on any shape change so stale files are re-fetched
# instead of raising KeyError deep inside a request.
_CACHE_SCHEMA = 2
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"


class MarketError(RuntimeError):
    """A quote or history request that did not produce usable bars."""


def safe_symbol(symbol: str) -> str:
    """Reject anything that could escape the cache directory."""
    cleaned = "".join(c for c in str(symbol).strip().upper() if c.isalnum() or c in ".^=+-_")
    if not cleaned or set(cleaned) == {"."}:
        raise MarketError(f"not a usable symbol: {symbol!r}")
    return cleaned


def normalise(symbol: str) -> str:
    """Bare four-letter tickers are IDX listings, so give them their suffix.

    ``BBCA`` means ``BBCA.JK`` here; ``^JKSE`` and ``BTC-USD`` are left alone.
    """
    value = str(symbol or "").strip().upper()
    if not value:
        return ""
    return f"{value}.JK" if len(value) == 4 and value.isalpha() else value


def _cache_path(symbol: str, range_: str) -> Path:
    return CACHE / f"{safe_symbol(symbol).replace('/', '_')}__{range_}.json"


def _get(url: str, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    })
    # Yahoo throttles a burst of requests with 429; two quick retries turn that
    # into a slow watchlist rather than a page full of "tidak tersedia".
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(1.2 * (attempt + 1))
                continue
            raise MarketError(f"Yahoo answered HTTP {exc.code} for {url.split('?')[0]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise MarketError(f"could not reach the price source: {exc}") from exc
    raise MarketError(f"could not reach the price source for {url.split('?')[0]}")


def chart(symbol: str, range_: str = _DEFAULT_RANGE, *, fresh: bool = False,
          max_age: float | None = None) -> dict:
    """Bars and quote metadata for ``symbol``, served from cache when fresh.

    ``max_age`` overrides the range's own TTL — the live tick asks for a few
    seconds, a chart asks for a minute or two.
    """
    symbol = safe_symbol(symbol)
    range_ = range_ if range_ in _RANGES else _DEFAULT_RANGE
    interval, ttl = _RANGES[range_]
    ttl = ttl if max_age is None else max_age
    path = _cache_path(symbol, range_)

    if not fresh and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < ttl:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema") == _CACHE_SCHEMA:
                    payload["cached"] = True
                    payload["age"] = round(age, 1)
                    return payload
                path.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)

    url = (f"{BASE}{urllib.parse.quote(symbol)}?range={range_}"
           f"&interval={interval}&includePrePost=false")
    raw = _get(url)
    result = (raw.get("chart") or {}).get("result") or []
    if not result:
        detail = (raw.get("chart") or {}).get("error") or {}
        raise MarketError(f"no data for {symbol}" + (f": {detail.get('description')}" if detail else ""))

    payload = _parse(result[0], symbol, range_, interval)
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass                      # a read-only cache directory must not break a run
    payload["cached"] = False
    payload["age"] = 0
    return payload


def _parse(node: dict, symbol: str, range_: str, interval: str) -> dict:
    meta = node.get("meta") or {}
    stamps = node.get("timestamp") or []
    quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows = quote.get("open") or [], quote.get("high") or [], quote.get("low") or []
    closes, volumes = quote.get("close") or [], quote.get("volume") or []

    bars = []
    for index, stamp in enumerate(stamps):
        close = _at(closes, index)
        if close is None:
            continue            # a halted or half-written session is not a bar
        bars.append({
            "t": int(stamp),
            "o": _round(_at(opens, index), close),
            "h": _round(_at(highs, index), close),
            "l": _round(_at(lows, index), close),
            "c": float(close),
            "v": float(_at(volumes, index) or 0.0),
        })
    if not bars:
        raise MarketError(f"no priced bars for {symbol}")

    price = meta.get("regularMarketPrice")
    return {
        "schema": _CACHE_SCHEMA,
        "symbol": meta.get("symbol") or symbol,
        "currency": meta.get("currency") or "IDR",
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
        "name": meta.get("shortName") or meta.get("longName") or "",
        "range": range_,
        "interval": interval,
        "price": float(price) if price is not None else bars[-1]["c"],
        "prev_close": _num(meta.get("chartPreviousClose")) or (bars[-2]["c"] if len(bars) > 1 else bars[-1]["c"]),
        "timezone": meta.get("exchangeTimezoneName") or "Asia/Jakarta",
        "bars": bars,
        "fetched_at": int(time.time()),
    }


def _at(values: list, index: int):
    return values[index] if index < len(values) else None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, fallback: float):
    return round(float(value), 4) if value is not None else round(float(fallback), 4)


# ---------------------------------------------------------------------------
# Indicators — every one returns None when its window is not covered
# ---------------------------------------------------------------------------


def _closes(bars: list) -> list[float]:
    return [b["c"] for b in bars]


def _sma(values: list[float], window: int):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _ema_series(values: list[float], window: int) -> list[float]:
    if len(values) < window:
        return []
    k = 2 / (window + 1)
    series = [sum(values[:window]) / window]
    for value in values[window:]:
        series.append(value * k + series[-1] * (1 - k))
    return series


def _rsi(values: list[float], window: int = 14):
    if len(values) <= window:
        return None
    gains, losses = [], []
    for previous, current in zip(values[-window - 1:], values[-window:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain, avg_loss = sum(gains) / window, sum(losses) / window
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(bars: list, window: int = 14):
    if len(bars) <= window:
        return None
    spans = []
    for previous, current in zip(bars[-window - 1:-1], bars[-window:]):
        spans.append(max(current["h"] - current["l"],
                         abs(current["h"] - previous["c"]),
                         abs(current["l"] - previous["c"])))
    return sum(spans) / len(spans)


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _mfi(bars: list, window: int = 14):
    """Money Flow Index: volume-weighted RSI. The desk's bandar proxy without a broker feed."""
    if len(bars) <= window:
        return None
    positive = negative = 0.0
    for previous, current in zip(bars[-window - 1:-1], bars[-window:]):
        typical = (current["h"] + current["l"] + current["c"]) / 3
        previous_typical = (previous["h"] + previous["l"] + previous["c"]) / 3
        flow = typical * current["v"]
        if typical > previous_typical:
            positive += flow
        elif typical < previous_typical:
            negative += flow
    if negative == 0:
        return 100.0 if positive > 0 else 50.0
    return 100 - 100 / (1 + positive / negative)


def _obv_slope(bars: list, window: int = 20):
    """On-balance volume change over ``window`` sessions, in average day-volumes."""
    if len(bars) <= window:
        return None
    obv = 0.0
    series = []
    for previous, current in zip(bars[:-1], bars[1:]):
        obv += current["v"] if current["c"] > previous["c"] else (-current["v"] if current["c"] < previous["c"] else 0.0)
        series.append(obv)
    recent = series[-window:]
    average_volume = sum(b["v"] for b in bars[-window:]) / window or 1.0
    return (recent[-1] - recent[0]) / (average_volume * window)


def _max_drawdown(values: list[float]) -> float:
    peak, worst = 0.0, 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
    return worst


def indicators(bars: list) -> dict:
    closes = _closes(bars)
    last = closes[-1]
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    ema10 = _ema_series(closes, 10)
    macd_line = signal_line = hist = None
    if len(closes) >= 35:
        fast, slow = _ema_series(closes, 12), _ema_series(closes, 26)
        offset = len(fast) - len(slow)
        macd_series = [f - s for f, s in zip(fast[offset:], slow)]
        signal_series = _ema_series(macd_series, 9)
        if signal_series:
            macd_line, signal_line = macd_series[-1], signal_series[-1]
            hist = macd_line - signal_line

    atr = _atr(bars)
    atr_pct = (atr / last * 100) if atr and last else None
    window20 = closes[-20:]
    band_mid = sum(window20) / len(window20) if window20 else None
    band_sd = _stdev(window20)
    percent_b = None
    if band_mid and band_sd:
        upper, lower = band_mid + 2 * band_sd, band_mid - 2 * band_sd
        percent_b = (last - lower) / (upper - lower) if upper > lower else None
    else:
        upper = lower = None

    returns = {}
    for label, sessions in (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)):
        returns[label] = (last / closes[-sessions - 1] - 1) if len(closes) > sessions else None

    daily = [b / a - 1 for a, b in zip(closes[:-1], closes[1:]) if a]
    high52 = max(closes[-252:]) if len(closes) >= 60 else max(closes)
    low52 = min(closes[-252:]) if len(closes) >= 60 else min(closes)
    volumes = [b["v"] for b in bars]
    vol_avg20 = sum(volumes[-20:]) / min(20, len(volumes)) if volumes else None

    up_days = [b["v"] for a, b in zip(bars[:-1], bars[1:]) if b["c"] > a["c"]][-20:]
    down_days = [b["v"] for a, b in zip(bars[:-1], bars[1:]) if b["c"] < a["c"]][-20:]

    return {
        "last": round(last, 2),
        "change_pct": round((last / closes[-2] - 1) * 100, 2) if len(closes) > 1 else None,
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "ema10": round(ema10[-1], 2) if ema10 else None,
        "rsi14": round(_rsi(closes), 1) if _rsi(closes) is not None else None,
        "macd": round(macd_line, 2) if macd_line is not None else None,
        "macd_signal": round(signal_line, 2) if signal_line is not None else None,
        "macd_hist": round(hist, 2) if hist is not None else None,
        "atr14": round(atr, 2) if atr else None,
        "atr_pct": round(atr_pct, 2) if atr_pct else None,
        "bb_mid": round(band_mid, 2) if band_mid else None,
        "bb_upper": round(upper, 2) if upper else None,
        "bb_lower": round(lower, 2) if lower else None,
        "bb_pct_b": round(percent_b, 3) if percent_b is not None else None,
        "mfi14": round(_mfi(bars), 1) if _mfi(bars) is not None else None,
        "obv_slope": round(_obv_slope(bars), 3) if _obv_slope(bars) is not None else None,
        "vol_avg20": round(vol_avg20, 0) if vol_avg20 else None,
        "vol_ratio": round(volumes[-1] / vol_avg20, 2) if volumes and vol_avg20 else None,
        "high_52w": round(high52, 2),
        "low_52w": round(low52, 2),
        "pos_52w": round((last - low52) / (high52 - low52), 3) if high52 > low52 else None,
        "volatility_pct": round(_stdev(daily[-63:]) * math.sqrt(252) * 100, 1) if len(daily) > 20 else None,
        "max_drawdown_pct": round(_max_drawdown(closes[-126:]) * 100, 1) if len(closes) > 20 else None,
        "up_down_volume": (round(sum(up_days) / sum(down_days), 2)
                           if up_days and down_days and sum(down_days) else None),
        "returns": {k: (round(v * 100, 2) if v is not None else None) for k, v in returns.items()},
        "sessions": len(bars),
    }


def snapshot(symbol: str, range_: str = _DEFAULT_RANGE, *, fresh: bool = False) -> dict:
    """Bars plus indicators, the unit every other module consumes."""
    payload = chart(symbol, range_, fresh=fresh)
    payload["indicators"] = indicators(payload["bars"])
    return payload


def tick(symbol: str) -> dict:
    """The live number for one instrument: last price, change, and today's bar.

    Uses the intraday series (5-minute bars, cached a few seconds) so the last
    candle carries today's high and low rather than yesterday's close.
    """
    payload = chart(symbol, "1d", max_age=8)
    bars = payload["bars"]
    price = payload.get("price")
    prev_close = payload.get("prev_close")
    change_pct = None
    if price is not None and prev_close:
        change_pct = round((price / prev_close - 1) * 100, 2)

    return {
        "symbol": payload["symbol"],
        "name": payload.get("name"),
        "price": price,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "bar": bars[-1] if bars else None,
        "day_high": max(b["h"] for b in bars) if bars else None,
        "day_low": min(b["l"] for b in bars) if bars else None,
        "interval": payload.get("interval"),
        "at": int(time.time()),
        "age": payload.get("age", 0),
    }
