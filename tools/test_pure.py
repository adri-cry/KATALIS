"""Offline checks for the Python side of KATALIS.

No server and no network: indicators are computed from synthetic bars, and the
pure decision helpers are exercised directly. CI runs this on every push.

    python tools/test_pure.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import desk, market  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))


def bars_from(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    """Realistic bars: open at the previous close, h/l hug the traded range.

    The old helper drew h = c+2 / l = c-2, so consecutive bars always
    overlapped: a falling series kept touching yesterday's close, RSI lost its
    losses, and a flat series still carried ATR 4.
    """
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    volumes = volumes or [1_000_000.0] * len(closes)
    out = []
    for index, close in enumerate(closes):
        open_ = closes[index - 1] if index else close
        out.append({
            "t": int((start + timedelta(days=index)).timestamp()),
            "o": open_, "h": max(open_, close), "l": min(open_, close), "c": close,
            "v": volumes[index],
        })
    return out


def trend(start: float, step: float, n: int = 60) -> list[float]:
    """Trending closes: a pullback every 5th session and slight acceleration.

    A perfectly straight line leaves MACD hist at exactly 0, which says
    nothing about the sign the trend tests want to check.
    """
    out, close = [], start
    for i in range(n):
        out.append(close)
        close += step * (1 + 0.01 * i - (1.5 if i % 5 == 4 else 0.0))
    return out


# ---- indicators -------------------------------------------------------------
rising = trend(100.0, 1.0)
ind = market.indicators(bars_from(rising))
check("indikator: SMA20 dihitung", ind["sma20"] is not None and ind["sma20"] > 100, str(ind["sma20"]))
check("indikator: tren naik -> RSI tinggi", (ind["rsi14"] or 0) > 70, str(ind["rsi14"]))
check("indikator: MACD positif saat naik", (ind["macd_hist"] or 0) > 0, str(ind["macd_hist"]))
check("indikator: ATR positif", (ind["atr14"] or 0) > 0, str(ind["atr14"]))
check("indikator: posisi 52 minggu di ujung atas", (ind["pos_52w"] or 0) > 0.9, str(ind["pos_52w"]))

falling = trend(200.0, -1.0)
ind_down = market.indicators(bars_from(falling))
check("indikator: tren turun -> RSI rendah", (ind_down["rsi14"] or 100) < 30, str(ind_down["rsi14"]))
check("indikator: MACD negatif saat turun", (ind_down["macd_hist"] or 0) < 0)

flat = [100.0] * 60
ind_flat = market.indicators(bars_from(flat))
check("indikator: harga datar tidak membagi nol",
      ind_flat["sma20"] == 100 and not ind_flat["atr14"], f"sma={ind_flat['sma20']}")

short = market.indicators(bars_from([100, 101, 102]))
check("indikator: seri pendek memberi None, bukan angka palsu",
      short["sma20"] is None and short["rsi14"] is None, str(short["sma20"]))

# ---- IDX tick rounding ------------------------------------------------------
check("tick: di bawah 200 -> 1", desk._tick(150) == 1)
check("tick: 200-500 -> 2", desk._tick(400) == 2)
check("tick: 500-2000 -> 5", desk._tick(1200) == 5)
check("tick: 2000-5000 -> 10", desk._tick(3000) == 10)
check("tick: di atas 5000 -> 25", desk._tick(9000) == 25)
check("tick: pembulatan ke level sah", desk._round_tick(5023) == 5025, str(desk._round_tick(5023)))
check("tick: pembulatan ke bawah", desk._round_tick(5012) == 5000, str(desk._round_tick(5012)))

# ---- rating thresholds ------------------------------------------------------
cases = [(1.2, "Buy"), (0.5, "Overweight"), (0.0, "Hold"),
         (-0.5, "Underweight"), (-1.4, "Sell")]
for composite, expected in cases:
    check(f"rating: {composite:+.1f} -> {expected}", desk._rating(composite) == expected,
          desk._rating(composite))

# ---- role weighting ---------------------------------------------------------
analysis_roles = [
    {"key": "market", "available": True, "score": 1.0},
    {"key": "flow", "available": False, "score": 0.0},
    {"key": "bandar", "available": True, "score": -1.0},
    {"key": "fundamental", "available": False, "score": 0.0},
    {"key": "news", "available": False, "score": 0.0},
]
confidence = desk._confidence(0.2, analysis_roles)
check("keyakinan: berada di 0..1", 0.25 <= confidence <= 0.95, str(confidence))
check("keyakinan: peran tanpa data menurunkan keyakinan",
      desk._confidence(0.2, analysis_roles) < desk._confidence(0.2, [
          {**role, "available": True} for role in analysis_roles]) + 0.31)

check("bobot: jumlah bobot peran = 1", abs(sum(desk._WEIGHTS.values()) - 1) < 1e-9)
check("bobot: lima peran dikenal", set(desk._WEIGHTS) == {"market", "flow", "bandar", "fundamental", "news"})

# ---- role scaling ------------------------------------------------------------
# Jalur proksi dipaksa agar uji tetap offline: biner idx yang terpasang akan
# memanggil jaringan, dan CI tidak punya biner itu.
desk.idxbridge.available = lambda: False

check("skala: _role menormalkan ceiling ke ±2",
      desk._role("x", "X", 1.4, [], [], "t", ceiling=1.4)["score"] == 2.0
      and desk._role("x", "X", -0.7, [], [], "t", ceiling=1.4)["score"] == -1.0)

bull = {"indicators": {
    "last": 120.0, "sma20": 110.0, "sma50": 100.0, "sma200": 90.0,
    "rsi14": 60.0, "macd_hist": 1.0, "bb_pct_b": -0.1,
    "vol_ratio": 2.0, "change_pct": 2.0,
    "obv_slope": 0.8, "up_down_volume": 1.6,
    "mfi14": 60.0, "pos_52w": 0.2,
}}
saturated = {
    "market": desk._role_market(bull),
    "flow": desk._role_flow("BBCA", bull),
    "bandar": desk._role_bandar("BBCA", bull),
}
for key, role in saturated.items():
    check(f"skala: peran {key} bull maksimum menyentuh kisaran atas",
          role["available"] and role["score"] >= 1.8, str(role["score"]))
check("skala: tidak ada peran yang melampaui ±2",
      all(abs(role["score"]) <= 2.0 for role in saturated.values()))

# ---- plan -------------------------------------------------------------------
plan = desk._plan({"last": 5000.0, "atr14": 100.0}, "Buy", equity=50_000_000)
check("rencana: stop di bawah entry untuk Buy", plan["stop"] < plan["entry"], f"{plan['stop']}")
check("rencana: target di atas entry untuk Buy", plan["target"] > plan["entry"])
check("rencana: R:R wajar", plan["reward_risk"] and plan["reward_risk"] > 1, str(plan["reward_risk"]))
check("rencana: lot dibulatkan 100 saham", plan["quantity"] and plan["quantity"] % 100 == 0,
      str(plan["quantity"]))
check("rencana: tanpa ekuitas tidak mengarang ukuran", desk._plan({"last": 5000.0, "atr14": 100.0},
                                                                 "Buy", equity=0)["lots"] is None)

short_plan = desk._plan({"last": 5000.0, "atr14": 100.0}, "Sell", equity=0)
check("rencana: Sell menaruh stop di atas", short_plan["stop"] > short_plan["entry"])

# ---- symbol normalisation ---------------------------------------------------
check("simbol: empat huruf -> .JK", market.normalise("bbca") == "BBCA.JK")
check("simbol: bersufiks tetap", market.normalise("bbca.jk") == "BBCA.JK")
check("simbol: indeks tidak diubah", market.normalise("^JKSE") == "^JKSE")
check("simbol: kripto tidak diubah", market.normalise("BTC-USD") == "BTC-USD")
check("simbol: kosong tetap kosong", market.normalise("") == "")

# ---- report -----------------------------------------------------------------
failed = 0
for name, ok, detail in CHECKS:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
    failed += 0 if ok else 1
print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} cek murni lolos")
raise SystemExit(1 if failed else 0)
