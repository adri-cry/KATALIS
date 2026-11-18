"""09:00 WIB — execute the day's paper trades from the 07:15 scan.

Deterministic only: no AI, no broker, no discretion. It reads ``data/scan.json``,
checks open positions for stop/target, then buys qualified picks. If nothing
qualifies it still opens one small "belajar" lot, so a day at the market never
passes with zero activity. Every fill is recorded in the separate paper ledger.

    python tools/execute.py            # normal run at market open
    python tools/execute.py --wait 0   # instant, for a quick dry observation
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import desk, guidance, market, paper, store  # noqa: E402

WIB = timezone(timedelta(hours=7))
SCAN_FILE = ROOT / "data" / "scan.json"
MAX_POSITIONS = 5
MAX_EXPOSURE_PCT = 30.0
RISK_PER_IDEA = 0.01
MAX_IDEA_PCT = 0.10
LEARNING_ATR_CAP = 10.0


def load_scan(path: Path = SCAN_FILE) -> dict:
    if not path.exists():
        raise FileNotFoundError("data/scan.json belum ada — jalankan tools/daily.py dulu")
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        stamp = datetime.fromisoformat(payload["as_of"]).date()
    except (KeyError, ValueError):
        stamp = datetime.min.date()
    age = (datetime.now(WIB).date() - stamp).days
    if age > 3:
        raise ValueError(f"scan tanggal {payload.get('as_of')} sudah terlalu lama ({age} hari)")
    return payload


def first_price(symbol: str, wait: float) -> float:
    """The market just opened; Yahoo may need a few minutes to print a price."""
    end = time.time() + wait
    reason = "harga belum tersedia"
    while True:
        try:
            price = market.tick(symbol).get("price")
            if price and price > 0:
                return float(price)
            reason = "harga belum tersedia"
        except market.MarketError as exc:
            reason = str(exc)
        if time.time() >= end:
            raise market.MarketError(f"{symbol}: {reason}")
        time.sleep(min(10, max(1, wait / 30)))


def _lot_size(entry: float, stop: float, equity: float) -> int:
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or entry <= 0 or equity <= 0:
        return 0
    budget = RISK_PER_IDEA * equity
    lots = int(budget / risk_per_share / 100)
    value = lots * 100 * entry
    if value > MAX_IDEA_PCT * equity:
        lots = int(MAX_IDEA_PCT * equity / entry / 100)
    return max(0, lots)


def run(scan: dict, wait: float = 300.0, verbose: bool = True) -> dict:
    store.init()
    paper.init()
    equity = paper.state()["equity"]
    plans = {row["symbol"]: row for row in scan["picks"]}
    events: list[str] = []

    # 1) Close positions that hit their mechanical level.
    for position in paper.positions():
        plan = plans.get(position["symbol"]) or {}
        try:
            price = first_price(position["symbol"], wait)
        except market.MarketError as exc:
            events.append(f"jual {position['symbol']} dilewati: {exc}")
            continue
        target, stop = plan.get("target"), plan.get("stop")
        target = target if target and target > position["avg_price"] else position["avg_price"] * 1.03
        stop = stop if stop and stop < position["avg_price"] else position["avg_price"] * 0.97
        if price >= target or price <= stop:
            reason = "target" if price >= target else "stop"
            try:
                result = paper.sell(position["symbol"], position["quantity"], price, reason)
                events.append(f"JUAL {result['symbol']} {result['quantity']:.0f} @ {price:.0f} "
                              f"({reason}, P/L {result['pnl']:+.0f})")
            except ValueError as exc:
                events.append(f"jual {position['symbol']} gagal: {exc}")

    # 2) Buy qualified picks; keep a hard cap on count and exposure.
    state = paper.state()
    bought: list[str] = []
    for row in sorted(scan["picks"], key=lambda item: item.get("composite") or 0, reverse=True):
        symbol = row["symbol"]
        if symbol in [p["symbol"] for p in state["positions"]]:
            continue
        if len(state["positions"]) >= MAX_POSITIONS:
            break
        if state["exposure_pct"] >= MAX_EXPOSURE_PCT:
            break
        try:
            analysis = desk.analyse(symbol, "1y", 5, equity=state["equity"])
            guide = guidance.build(analysis)
            quality = guide["data_quality"]
            risk = guide["risk"]
            entry, stop = analysis["trader"]["entry"], analysis["trader"]["stop"]
            atr = risk.get("atr_pct")
            rr = risk.get("reward_risk")
            ok = (guide["action"] == "Beli" and quality["available_roles"] >= 3
                  and not quality["stale_snapshot"]
                  and (atr is None or atr <= guidance.MAX_ATR_PCT)
                  and (rr is None or rr >= guidance.MIN_REWARD_RISK))
            if not ok:
                events.append(f"{symbol}: tidak lolos ({guide['action']}, ATR {atr}, R:R {rr})")
                continue
            lots = _lot_size(entry, stop, state["equity"])
            if lots < 1:
                events.append(f"{symbol}: ukuran kurang dari 1 lot")
                continue
            price = first_price(symbol, wait)
            result = paper.buy(symbol, lots * 100, price,
                               f"Beli · {analysis['decision']['rating']} · {guide['action']}")
            bought.append(symbol)
            state = paper.state()
            events.append(f"BELI {result['symbol']} {result['quantity']:.0f} @ {price:.0f} "
                          f"({lots} lot, komposit {row.get('composite')})")
        except (market.MarketError, ValueError) as exc:
            events.append(f"{symbol}: {exc}")

    # 3) A day at the market must not pass with no buying at all. One small lot,
    # clearly tagged so the evaluation can tell it apart from a rule-driven fill.
    if not bought:
        state = paper.state()
        held = {p["symbol"] for p in state["positions"]}
        fallback = next((r for r in sorted(scan["picks"],
                                           key=lambda item: item.get("composite") or 0,
                                           reverse=True)
                         if r["symbol"] not in held and (r.get("atr_pct") or 0) <= LEARNING_ATR_CAP),
                        None)
        if fallback:
            try:
                price = first_price(fallback["symbol"], wait)
                result = paper.buy(fallback["symbol"], 100, price,
                                   "Minimal belajar harian: tidak ada kandidat lolos",
                                   mode="belajar")
                events.append(f"BELAJAR {result['symbol']} 100 @ {price:.0f} "
                              f"(1 lot, tidak ada sinyal Beli)")
            except (market.MarketError, ValueError) as exc:
                events.append(f"minimal belajar gagal: {exc}")
        else:
            events.append("Tidak ada kandidat yang bisa dibeli hari ini")

    final = paper.state()
    report = {"as_of": scan["as_of"], "bought": bought, "events": events,
              "cash": final["cash"], "equity": final["equity"],
              "positions": len(final["positions"]), "exposure_pct": final["exposure_pct"]}
    if verbose:
        for line in events:
            print(" ", line)
        print(f"Kas {report['cash']:,.0f} · ekuitas {report['equity']:,.0f} · "
              f"posisi {report['positions']} · eksposur {report['exposure_pct']:.1f}%")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/execute.py",
                                     description="Eksekusi paper trading pukul 09:00 WIB.")
    parser.add_argument("--wait", type=float, default=300,
                        help="tunggu harga maksimal beberapa detik (default 300)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        scan = load_scan()
        report = run(scan, wait=args.wait, verbose=not args.quiet)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Gagal: {exc}", file=sys.stderr)
        return 2
    return 0 if report["events"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
