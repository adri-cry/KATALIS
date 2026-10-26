"""Recording decisions and settling them against IHSG.

A rating is only a claim until it is scored. Every decision names the price it
was made at, the benchmark it will be judged against, and the holding window —
then, once that window has traded, the realised return and the alpha are
written back. This is the loop that makes the journal worth keeping, and it is
deliberately the same arithmetic as a benchmarked settlement elsewhere:

- entry is the **first close after** the decision date, because a run dated *d*
  has read the news published after *d*'s close;
- the exit is the close ``holding_days`` **trading** sessions later;
- alpha is the raw return minus the benchmark's return over the same dates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import market, store

WIB = timezone(timedelta(hours=7))


def _dated_bars(symbol: str) -> dict[str, dict]:
    payload = market.chart(symbol, "2y")
    return {datetime.fromtimestamp(bar["t"], WIB).strftime("%Y-%m-%d"): bar
            for bar in payload["bars"]}


def _close_on_or_before(bars: dict[str, dict], day: str) -> float | None:
    """The benchmark's last close by ``day`` — a different calendar must not change the window."""
    candidates = [d for d in bars if d <= day]
    return bars[max(candidates)]["c"] if candidates else None


def settle_all(holding_days: int | None = None) -> dict:
    """Score every pending decision whose holding window has traded."""
    rows = store.pending()
    if not rows:
        return {"settled": [], "waiting": [], "failed": [], "stats": store.stats()}

    try:
        benchmark = _dated_bars(market.BENCHMARK)
    except market.MarketError as exc:
        return {"settled": [], "waiting": [], "failed": [
            {"symbol": "*", "reason": f"benchmark IHSG tidak tersedia: {exc}"}], "stats": store.stats()}

    settled, waiting, failed = [], [], []
    cache: dict[str, dict | market.MarketError] = {}

    for row in rows:
        symbol = row["symbol"]
        if symbol not in cache:
            try:
                cache[symbol] = _dated_bars(symbol)
            except market.MarketError as exc:
                cache[symbol] = exc
        bars = cache[symbol]
        if isinstance(bars, market.MarketError):
            failed.append({"symbol": symbol, "date": row["trade_date"], "reason": str(bars)})
            continue

        sessions = sorted(day for day in bars if day > row["trade_date"])
        window = int(holding_days or row["holding_days"] or 5)
        if len(sessions) <= window:
            waiting.append({"symbol": symbol, "date": row["trade_date"],
                            "reason": f"jendela {window} sesi belum terlewati"})
            continue

        entry_day, exit_day = sessions[0], sessions[window]
        entry_price, exit_price = bars[entry_day]["c"], bars[exit_day]["c"]
        benchmark_entry = _close_on_or_before(benchmark, entry_day)
        benchmark_exit = _close_on_or_before(benchmark, exit_day)
        if not benchmark_entry or not benchmark_exit:
            waiting.append({"symbol": symbol, "date": row["trade_date"],
                            "reason": "IHSG belum punya harga pada tanggal yang sama"})
            continue

        raw = exit_price / entry_price - 1
        benchmark_return = benchmark_exit / benchmark_entry - 1
        alpha = raw - benchmark_return
        store.settle(row["id"], exit_day, exit_price, benchmark_return, raw, alpha)
        settled.append({"symbol": symbol, "rating": row["rating"], "entry_date": entry_day,
                        "exit_date": exit_day, "raw": raw, "benchmark": benchmark_return,
                        "alpha": alpha})

    return {"settled": settled, "waiting": waiting, "failed": failed, "stats": store.stats()}
