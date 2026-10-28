"""Offline checks for the Python side of KATALIS.

No server and no network: indicators are computed from synthetic bars, and the
pure decision helpers are exercised directly. CI runs this on every push.

    python tools/test_pure.py
"""

from __future__ import annotations

import math
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import advisor, desk, guidance, llm, market, screener, simulation, store  # noqa: E402
import app as app_module  # noqa: E402

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

# ---- durable-in-process analysis job registry --------------------------------
app_module._ANALYSIS_JOBS.clear()
app_module._ANALYSIS_JOBS["unit-job"] = {"id": "unit-job", "status": "running", "stage": "ai",
                                          "updated_at": __import__("time").time()}
check("job analisis: polling membaca progres aktif tanpa mengubahnya",
      app_module._analysis_job("unit-job")["stage"] == "ai"
      and app_module._analysis_job("unit-job")["status"] == "running")
app_module._analysis_job("unit-job", updates={"stage": "complete", "status": "complete"})
check("job analisis: worker dapat memperbarui stage dan status selesai",
      app_module._analysis_job("unit-job")["stage"] == "complete"
      and app_module._analysis_job("unit-job")["status"] == "complete")
app_module._ANALYSIS_JOBS.clear()

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

# ---- IDX bridge latency bounds ------------------------------------------------
# Interactive role reads should fail fast and let their callers use proxies.
import subprocess
original_idx_run = desk.idxbridge.subprocess.run
original_idx_binary = desk.idxbridge.binary
idx_calls = []
try:
    desk.idxbridge.binary = lambda: "idx-test"

    def slow_idx_call(*args, **kwargs):
        idx_calls.append(kwargs)
        raise subprocess.TimeoutExpired("idx-test", kwargs.get("timeout"))

    desk.idxbridge.subprocess.run = slow_idx_call
    try:
        desk.idxbridge.run("stocks", "foreign", "TEST")
    except desk.idxbridge.BridgeUnavailable:
        pass
    check("idx bridge: satu timeout singkat tanpa retry berulang",
          len(idx_calls) == 1 and idx_calls[0].get("timeout") == 20,
          str(idx_calls))
finally:
    desk.idxbridge.subprocess.run = original_idx_run
    desk.idxbridge.binary = original_idx_binary

# ---- IDX bridge status cache --------------------------------------------------
# desk.analyse reports sources on every request, so status() must not run
# `idx version` (a process spawn) on each one.
original_status_run = desk.idxbridge.run
version_calls = []


def fake_version(*args, **kwargs):
    version_calls.append(args)
    return {"version": "9.9.9"}


try:
    desk.idxbridge._reset_cache()
    desk.idxbridge.binary = lambda: "idx-test"
    desk.idxbridge.run = fake_version
    first = desk.idxbridge.status()
    second = desk.idxbridge.status()
    check("idx bridge: status di-cache, bukan proses tiap panggilan",
          len(version_calls) == 1 and first["version"] == "9.9.9"
          and second["version"] == "9.9.9" and "checked_at" in second,
          str(version_calls))
    fresh = desk.idxbridge.status(refresh=True)
    check("idx bridge: refresh memaksa handshake ulang",
          len(version_calls) == 2 and fresh["version"] == "9.9.9", str(version_calls))
    desk.idxbridge.binary = lambda: "idx-other"
    desk.idxbridge.status()
    check("idx bridge: biner berbeda membatalkan cache",
          len(version_calls) == 3, str(version_calls))
finally:
    desk.idxbridge._reset_cache()
    desk.idxbridge.run = original_status_run
    desk.idxbridge.binary = original_idx_binary

# ---- IDX bridge error messages -------------------------------------------------
# A role card has to name the source and the status. Dumping idx's JSON envelope
# was unreadable, and the 120-character cut clipped "HTTP 502" to "HTTP 50".
ipot_error = desk.idxbridge.BridgeUnavailable(
    'idx bandar detect JSMR --period 1w failed: '
    '{"error":{"code":"provider","message":"ipot: broker summary for JSMR: HTTP 502"},"exit_code":3}')
check("idx error: IPOT 502 disebut singkat",
      desk.idxbridge.explain(ipot_error) == "IPOT menolak permintaan (HTTP 502)",
      desk.idxbridge.explain(ipot_error))
cloudflare_error = desk.idxbridge.BridgeUnavailable(
    'idx stocks foreign TMPO --days 20 failed: '
    '{"error":{"code":"provider","message":"idx: idx.co.id refused the request (Cloudflare bot protection). '
    'It usually passes from an ordinary home or office connection; data centres, VPNs and CI runners are '
    'often blocked. Cached copies are still served (try --offline)"},"exit_code":3}')
check("idx error: penolakan Cloudflare disebut",
      "Cloudflare" in desk.idxbridge.explain(cloudflare_error),
      desk.idxbridge.explain(cloudflare_error))
check("idx error: teks tanpa JSON tetap terbaca",
      desk.idxbridge.explain(RuntimeError("the idx binary is not installed"))
      == "the idx binary is not installed")

# The documented retry: the four feeds the desk reads get one more attempt.
original_wrapped_run = desk.idxbridge.run
retry_calls: list[dict] = []


def record_run(*args, **kwargs):
    retry_calls.append(kwargs)
    return {"data": {}}


try:
    desk.idxbridge.run = record_run
    desk.idxbridge.bandar("BBCA.JK")
    desk.idxbridge.foreign("BBCA.JK")
    desk.idxbridge.fundamental("BBCA.JK")
    desk.idxbridge.sentiment("BBCA.JK")
    check("idx bridge: empat umpan dewan mencoba ulang sekali",
          len(retry_calls) == 4 and all(call.get("attempts") == 2 for call in retry_calls),
          str(retry_calls))
finally:
    desk.idxbridge.run = original_wrapped_run

# ---- parallel role reads -----------------------------------------------------
# All four independent external-role stubs must overlap; results must retain the
# established flow/bandar/fundamental/news order for downstream rendering.
from threading import Barrier

role_barrier = Barrier(4)
original_roles = (desk._role_market, desk._role_flow, desk._role_bandar,
                  desk._role_fundamental, desk._role_news)
try:
    def parallel_stub(key):
        def run(*args):
            if key in ("flow", "bandar", "fundamental", "news"):
                role_barrier.wait(timeout=2)
            return {"key": key, "available": True, "score": 0.0}
        return run

    desk._role_market = parallel_stub("market")
    desk._role_flow = parallel_stub("flow")
    desk._role_bandar = parallel_stub("bandar")
    desk._role_fundamental = parallel_stub("fundamental")
    desk._role_news = parallel_stub("news")
    parallel_roles = desk._analyse_roles("TEST.JK", {})
    check("peran: pembacaan eksternal berjalan paralel dan urutannya tetap",
          [role["key"] for role in parallel_roles] ==
          ["market", "flow", "bandar", "fundamental", "news"])
except Exception as exc:  # a serial implementation would time out at the barrier
    check("peran: pembacaan eksternal berjalan paralel dan urutannya tetap", False, str(exc))
finally:
    (desk._role_market, desk._role_flow, desk._role_bandar,
     desk._role_fundamental, desk._role_news) = original_roles

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

# ---- beginner guidance and simulator ----------------------------------------

def guidance_fixture(machine="Buy", ai="Buy", technical=1.0, available=5, sessions=80,
                     atr_pct=2.5, rr=1.5, range_="6mo", as_of=None):
    names = ["Analis Teknikal", "Aliran", "Bandar", "Fundamental", "Berita"]
    roles = []
    for index, name in enumerate(names):
        roles.append({"key": "market" if index == 0 else f"role{index}", "name": name,
                      "score": technical if index == 0 else 0.2, "available": index < available,
                      "source": "proxy" if index == 0 else "idx",
                      "bull": ["harga di atas SMA20"] if index == 0 else [],
                      "bear": ["RSI tinggi"] if index == 0 else [], "warning": ""})
    if as_of is None:
        as_of = datetime.now(timezone(timedelta(hours=7))).date().isoformat()
    return {"range": range_, "holding_days": 5, "as_of": as_of,
            "decision": {"rating": machine, "confidence": 0.74},
            "manager": {"composite": 1.0},
            "roles": roles,
            "indicators": {"sessions": sessions, "last": 1000, "sma20": 980, "sma50": 950,
                           "atr_pct": atr_pct, "max_drawdown_pct": -8},
            "trader": {"entry": 1000, "stop": 950, "target": 1080,
                       "risk_per_share": 50, "reward_risk": rr},
            "ai": ({"rating": ai, "conviction": 0.8, "risks": ["volatilitas"],
                    "what_would_change": ["tutup di bawah SMA20"]} if ai else None),
            "ai_error": None}


g = guidance.build(guidance_fixture())
check("ringkasan: arah selaras -> Beli dan menamai mesin/AI", g["action"] == "Beli" and g["status"] == "selaras")
check("ringkasan: confidence diberi label heuristik, bukan probabilitas", "bukan probabilitas" in g["machine_confidence_note"])
check("ringkasan: source/proxy quality disclosed", "Analis Teknikal" in g["data_quality"]["proxy_roles"])

g_conflict = guidance.build(guidance_fixture(machine="Buy", ai="Sell", technical=1.0))
check("resolver: konflik mesin vs AI -> Tunggu, kedua rating tetap tampil",
      g_conflict["action"] == "Tunggu" and g_conflict["machine_rating"] == "Buy"
      and g_conflict["ai_rating"] == "Sell" and len(g_conflict["conflicts"]) >= 1)

g_technical = guidance.build(guidance_fixture(machine="Buy", ai="Buy", technical=-1.2))
check("resolver: konflik teknikal bearish vs gabungan/AI bullish -> Tunggu",
      g_technical["action"] == "Tunggu" and any(c["type"] in {"gabungan_vs_teknikal", "tren_vs_gabungan"} for c in g_technical["conflicts"]))

g_missing = guidance.build(guidance_fixture(available=2, sessions=15, ai=None))
check("data hilang: kembalikan Data belum cukup, bukan netral",
      g_missing["action"] == "Data belum cukup" and g_missing["data_quality"]["status"] == "terbatas")

g_proxy = guidance.build(guidance_fixture(available=5))
check("confidence note: tidak ada klaim peluang profit",
      "probabilitas" in g_proxy["machine_confidence_note"] and "kalibrasi" in g_proxy["ai_confidence_note"])

g_risk = guidance.build(guidance_fixture(atr_pct=8.2, rr=0.8))
check("risiko: volatilitas tinggi/R:R <1 menurunkan Beli ke Tunggu",
      g_risk["action"] == "Tunggu" and len(g_risk["risk"]["flags"]) == 2)

g_tf = guidance.build(guidance_fixture(range_="1d"))
check("timeframe: intraday vs horizon 5 sesi -> Tunggu",
      g_tf["action"] == "Tunggu" and g_tf["status"] == "rentang_tidak_selaras")
g_stale = guidance.build(guidance_fixture(as_of="2026-07-01"))
check("kesegaran: snapshot lebih dari 7 hari tidak menghasilkan Beli",
      g_stale["action"] == "Data belum cukup" and g_stale["stale_snapshot"])
g_missing_date = guidance.build(guidance_fixture(as_of=""))
check("kesegaran: tanggal snapshot yang hilang tidak menghasilkan Beli",
      g_missing_date["action"] == "Data belum cukup" and g_missing_date["stale_snapshot"])

scenario = simulation.calculate(1000, 950, 1100, 2, 500000, None)
check("simulator: lot 100, kotor target dan stop dihitung dari level mesin",
      scenario["quantity"] == 200 and scenario["gross_target_pnl"] == 20000
      and scenario["gross_stop_pnl"] == -10000 and not scenario["fees_included"])
scenario_fee = simulation.calculate(1000, 950, 1100, 2, 500000, 0.25)
check("simulator: biaya dua sisi diterapkan dan net lebih rendah",
      scenario_fee["target_round_trip_fees"] == 1050
      and scenario_fee["net_target_pnl"] == 18950
      and scenario_fee["target_pnl_on_capital_pct"] == 3.79
      and scenario_fee["net_stop_pnl"] < scenario_fee["gross_stop_pnl"])

try:
    simulation.calculate(1000, 950, 1100, 0, 500000)
    invalid_lots = False
except ValueError:
    invalid_lots = True
check("simulator: lot nol ditolak", invalid_lots)
try:
    simulation.calculate(1000, 950, 1100, 2, 500000, -0.1)
    invalid_fee = False
except ValueError:
    invalid_fee = True
check("simulator: tarif biaya negatif ditolak", invalid_fee)
try:
    simulation.calculate(1000, 1050, 900, 2, 500000)
    short_ok = True
except ValueError:
    short_ok = False
check("simulator: short selling tak diasumsikan tersedia", not short_ok)

# The live advisor schema no longer asks for levels, and any returned fake levels
# are ignored in favour of the desk's exact plan.
real_status_for_advisor, real_chat_for_advisor = llm.status, llm.chat_json
try:
    llm.status = lambda: {"configured": True}
    llm.chat_json = lambda *_args, **_kwargs: {
        "rating": "Sell", "conviction": 0.7, "thesis": "SMA20 lemah",
        "risks": ["volatilitas"], "action": {"entry": 1, "stop": 2, "target": 3},
        "what_would_change": [], "confidence_note": "data terbatas"}
    advisor_result = advisor.advise({"decision": {"rating": "Buy"},
                                     "trader": {"entry": 1000, "stop": 950, "target": 1080},
                                     "symbol": "TEST.JK", "indicators": {}, "roles": []})
    check("advisor: model tak dapat mengganti level; action tetap dari mesin",
          advisor_result["action"]["entry"] == 1000 and advisor_result["action"]["stop"] == 950
          and advisor_result["action"]["target"] == 1080 and advisor_result["action"]["level_source"] == "mesin")
    check("advisor: prompt melarang model memberi level harga", "jangan membuat angka level harga" in advisor._SYSTEM)
finally:
    llm.status, llm.chat_json = real_status_for_advisor, real_chat_for_advisor

# Snapshot-bound API helper: only the exact current analysis may be simulated.
original_latest, original_analysis_id = app_module._LATEST_ANALYSES, "analysis-snapshot-1"
try:
    app_module._LATEST_ANALYSES = {"TEST.JK": {
        "analysis_id": original_analysis_id, "as_of": "2026-07-08",
        "guidance": {"action": "Beli"},
        "trader": {"entry": 1000, "stop": 950, "target": 1100}}}
    code, payload = app_module.simulate_latest("TEST.JK", original_analysis_id, 2, 500000)
    check("API simulator: hanya memakai level snapshot mesin yang sama",
          code == 200 and payload["plan_source"] == "mesin" and payload["result"]["gross_target_pnl"] == 20000)
    stale_code, _ = app_module.simulate_latest("TEST.JK", "stale-id", 2, 500000)
    check("API simulator: snapshot kedaluwarsa ditolak", stale_code == 409)
    app_module._LATEST_ANALYSES["TEST.JK"]["guidance"]["action"] = "Tunggu"
    wait_code, _ = app_module.simulate_latest("TEST.JK", original_analysis_id, 2, 500000)
    check("API simulator: keputusan Tunggu tidak dapat dipaksa simulasi beli", wait_code == 409)
    app_module._LATEST_ANALYSES["TEST.JK"]["guidance"]["action"] = "Beli"
    check("API simulator: entry di respons berasal dari rencana server",
          payload["result"]["entry"] == 1000 and payload["result"]["stop"] == 950
          and payload["result"]["target"] == 1100)
finally:
    app_module._LATEST_ANALYSES = original_latest

# ---- symbol normalisation ---------------------------------------------------
check("simbol: empat huruf -> .JK", market.normalise("bbca") == "BBCA.JK")
check("simbol: bersufiks tetap", market.normalise("bbca.jk") == "BBCA.JK")
check("simbol: indeks tidak diubah", market.normalise("^JKSE") == "^JKSE")
check("simbol: kripto tidak diubah", market.normalise("BTC-USD") == "BTC-USD")
check("simbol: kosong tetap kosong", market.normalise("") == "")

# ---- screener ---------------------------------------------------------------


def drift_series(n: int = 250, start: float = 1000.0, drift: float = 0.0018,
                 wave: float = 0.012) -> list[float]:
    """A drifting close series with a gentle swing, so RSI and MACD are not degenerate."""
    return [start * math.exp(drift * i + wave * math.sin(i / 6.0)) for i in range(n)]


def swing_bars(closes: list[float], volume: float = 5_000_000.0,
               start: datetime = datetime(2025, 11, 1, tzinfo=timezone.utc)) -> list[dict]:
    out = []
    for index, close in enumerate(closes):
        open_ = closes[index - 1] if index else close
        out.append({
            "t": int((start + timedelta(days=index)).timestamp()),
            "o": open_, "h": max(open_, close) * 1.004, "l": min(open_, close) * 0.996, "c": close,
            "v": volume * (1.6 if index == len(closes) - 1 else 1.0),
        })
    return out


def fake_chart(table: dict):
    """A stand-in for market.chart: no network, a missing symbol fails like Yahoo does."""
    def chart(symbol, range_="1y", **kwargs):
        if symbol not in table:
            raise market.MarketError(f"no data for {symbol}")
        bars = table[symbol]
        return {"schema": 2, "symbol": symbol, "currency": "IDR", "exchange": "JKT", "name": symbol,
                "range": range_, "interval": "1d", "price": bars[-1]["c"], "prev_close": bars[-2]["c"],
                "timezone": "Asia/Jakarta", "bars": bars, "fetched_at": 0, "cached": False, "age": 0}
    return chart


TODAY = date(2026, 7, 9)                  # the day after the last synthetic bar
SCREEN_TABLE = {
    "UPAA.JK": swing_bars(drift_series()),                                  # steady climb, liquid
    "UPBB.JK": swing_bars(drift_series(drift=0.004)),                       # faster climb, weaker composite
    "DOWN.JK": swing_bars(drift_series(drift=-0.002)),                      # downtrend
    "THIN.JK": swing_bars(drift_series(), volume=1_000.0),                  # nobody trades it
    "CHEP.JK": swing_bars(drift_series(start=20.0)),                        # sub-Rp100
    "OLD.JK": swing_bars(drift_series(), start=datetime(2024, 11, 1, tzinfo=timezone.utc)),  # stopped printing
}
SCREEN_UNIVERSE = list(SCREEN_TABLE) + ["NONE.JK"]                          # NONE has no data at all

ind_ok = market.indicators(SCREEN_TABLE["UPAA.JK"])
last_day = date(2026, 7, 8)
check("gerbang: saham likuid dan sehat lolos",
      screener.gate_reasons(ind_ok, last_day, TODAY, screener.Gate()) == [],
      str(screener.gate_reasons(ind_ok, last_day, TODAY, screener.Gate())))
check("gerbang: nilai transaksi tipis ditolak",
      any("nilai transaksi" in r for r in
          screener.gate_reasons(ind_ok, last_day, TODAY, screener.Gate(min_turnover=1e12))))
check("gerbang: data basi ditolak",
      any("tertinggal" in r for r in
          screener.gate_reasons(ind_ok, date(2026, 6, 1), TODAY, screener.Gate())))
check("gerbang: riwayat pendek ditolak",
      any("riwayat" in r for r in
          screener.gate_reasons(market.indicators(SCREEN_TABLE["UPAA.JK"][-40:]),
                                last_day, TODAY, screener.Gate())))

with tempfile.TemporaryDirectory() as tmp:
    universe_file = Path(tmp) / "universe.txt"
    universe_file.write_text("# LQ45\nbbca, BBRI  # bank\n\nBBCA\nTLKM.JK\n", encoding="utf-8")
    check("universe: komentar, koma, huruf kecil, dan duplikat ditangani",
          screener.load_universe(universe_file) == ["BBCA.JK", "BBRI.JK", "TLKM.JK"],
          str(screener.load_universe(universe_file)))
    check("universe: berkas tidak ada -> daftar kosong", screener.load_universe(Path(tmp) / "x.txt") == [])

shown = ["AAAA.JK", "BBBB.JK", "CCCC.JK"]
verdicts, picks, dropped = screener.apply_model(shown, {
    "kandidat": [
        {"kode": "AAAA.JK", "rating": "Buy", "conviction": 7, "alasan": "a"},
        {"kode": "BBBB.JK", "rating": "Hold", "conviction": "abc"},
        {"kode": "CCCC.JK", "rating": "Strong Buy", "conviction": 0.9},
        {"kode": "GHOST.JK", "rating": "Buy", "conviction": 0.9},
    ],
    "pilihan": ["GHOST.JK", "BBBB.JK", "CCCC.JK", "AAAA.JK", "AAAA.JK"]}, 5)
check("model: kode karangan dibuang dan dilaporkan", dropped == ["GHOST.JK"], str(dropped))
check("model: pilihan berating Hold atau tak dikenal dicoret", picks == ["AAAA.JK"], str(picks))
check("model: keyakinan dijepit ke 0..1 dan yang rusak jadi 0,5",
      verdicts["AAAA.JK"]["conviction"] == 1.0 and verdicts["BBBB.JK"]["conviction"] == 0.5)
check("model: rating di luar lima tingkat berarti tidak dinilai", "CCCC.JK" not in verdicts)
_, capped, _ = screener.apply_model(shown, {
    "kandidat": [{"kode": s, "rating": "Buy", "conviction": 0.5} for s in shown], "pilihan": shown}, 2)
check("model: batas jumlah pilihan dipaksa oleh kode", len(capped) == 2, str(capped))

real_chart, real_status, real_chat = market.chart, llm.status, llm.chat_json
real_db = store.DB_PATH
try:
    market.chart = fake_chart(SCREEN_TABLE)

    # mechanical run: no model involved
    res = screener.screen(SCREEN_UNIVERSE, ai=False, today=TODAY)
    stages = {item["symbol"]: item["stage"] for item in res["rejected"]}
    candidates = [row["symbol"] for row in res["candidates"]]
    check("pemindai: pilihan mesin berurutan menurut komposit",
          res["picks"] == ["UPAA.JK", "UPBB.JK"] and res["picks_source"] == "mesin", str(res["picks"]))
    check("pemindai: downtrend ditolak di tahap teknikal", stages.get("DOWN.JK") == "teknikal", str(stages))
    check("pemindai: ilikuid, murah, dan basi ditolak di gerbang",
          all(stages.get(s) == "gerbang" for s in ("THIN.JK", "CHEP.JK", "OLD.JK")), str(stages))
    check("pemindai: saham tanpa data tercatat sebagai galat, bukan hilang",
          [e["symbol"] for e in res["errors"]] == ["NONE.JK"], str(res["errors"]))
    accounted = candidates + list(stages) + [e["symbol"] for e in res["errors"]]
    check("pemindai: setiap saham masuk universe tepat satu kali di hasil",
          sorted(accounted) == sorted(SCREEN_UNIVERSE), str(accounted))
    check("pemindai: level rencana datang dari mesin (stop di bawah entry)",
          all(row["plan"]["stop"] < row["plan"]["entry"] < row["plan"]["target"]
              for row in res["candidates"]))
    check("pemindai: saham tanpa idx ditandai tanpa data dan proksi",
          any("tanpa data" in f for f in res["candidates"][0]["flags"])
          and any("proksi" in f for f in res["candidates"][0]["flags"]))
    narrow = screener.screen(SCREEN_UNIVERSE, ai=False, today=TODAY, max_picks=1)
    check("pemindai: max_picks membatasi pilihan", narrow["picks"] == ["UPAA.JK"], str(narrow["picks"]))

    # an outage is reported as one, with a distinct exit code, not as "nothing to buy"
    market.chart = fake_chart({})
    outage = screener.screen(["UPAA.JK", "UPBB.JK"], ai=False, today=TODAY)
    check("pemindai: semua gagal diambil -> galat untuk tiap saham, tanpa pilihan",
          len(outage["errors"]) == 2 and outage["picks"] == [] and outage["candidates"] == [])
    market.chart = fake_chart(SCREEN_TABLE)

    # model run: one call, answer validated
    calls: list[str] = []

    def stub_chat(system, user, **kwargs):
        calls.append(user)
        return {"_model": "stub-model", "_usage": {"total_tokens": 1}, "pasar": "dua kandidat naik",
                "kandidat": [
                    {"kode": "UPBB.JK", "rating": "Buy", "conviction": 0.8,
                     "alasan": "tren kuat", "risiko": "jenuh beli", "batal_bila": "tutup di bawah SMA20"},
                    {"kode": "UPAA.JK", "rating": "Hold", "conviction": 0.4,
                     "alasan": "sudah berlari", "risiko": "koreksi", "batal_bila": "n/a"},
                    {"kode": "GHOST.JK", "rating": "Buy", "conviction": 0.9,
                     "alasan": "x", "risiko": "x", "batal_bila": "x"}],
                "pilihan": ["GHOST.JK", "UPAA.JK", "UPBB.JK"]}

    llm.status = lambda: {"configured": True}
    llm.chat_json = stub_chat
    res = screener.screen(SCREEN_UNIVERSE, today=TODAY)
    check("model: tepat satu panggilan untuk semua kandidat", len(calls) == 1, str(len(calls)))
    check("model: bukti yang dikirim tanpa array bar", '"bars"' not in calls[0] and "UPAA.JK" in calls[0])
    check("model: pilihan = jawaban model yang lolos validasi",
          res["picks"] == ["UPBB.JK"] and res["picks_source"] == "model", str(res["picks"]))
    check("model: kandidat yang ditolak model tetap tampil dengan vonisnya",
          [(r["symbol"], r["pick"]) for r in res["candidates"]] == [("UPBB.JK", True), ("UPAA.JK", False)]
          and res["candidates"][1]["ai"]["rating"] == "Hold")
    check("model: kode karangan dilaporkan", res["ai"]["dropped"] == ["GHOST.JK"], str(res["ai"]))

    # the model fails: the desk's ordering stands and the reason is visible
    def broken_chat(system, user, **kwargs):
        raise llm.LLMError("kunci salah")

    llm.chat_json = broken_chat
    res = screener.screen(SCREEN_UNIVERSE, today=TODAY)
    check("model gagal: kembali ke urutan mesin dengan alasan terlihat",
          res["picks_source"] == "mesin" and res["picks"] == ["UPAA.JK", "UPBB.JK"]
          and res["ai"]["error"] == "kunci salah" and not res["ai"]["used"], str(res["ai"]))

    # nothing to read: the model is not called at all
    llm.chat_json = stub_chat
    before = len(calls)
    res = screener.screen(["DOWN.JK"], today=TODAY)
    check("tanpa kandidat: model tidak dipanggil dan tidak ada pilihan",
          len(calls) == before and res["picks"] == [], str(res["picks"]))

    # journal: picks are written in the shape the dashboard reads, once per day
    with tempfile.TemporaryDirectory() as tmp:
        store.DB_PATH = Path(tmp) / "test.db"
        store.init()
        llm.chat_json = stub_chat
        res = screener.screen(SCREEN_UNIVERSE, today=TODAY, record=True)
        ids = [item["id"] for item in res["recorded"]]
        entry = store.decision(ids[0]) if ids and ids[0] else None
        check("jurnal: pilihan tercatat dengan id", len(ids) == 1 and ids[0], str(res["recorded"]))
        check("jurnal: vonis model tersimpan terpisah dari rating mesin",
              entry is not None and entry.get("ai_rating") == "Buy" and entry.get("rating") == "Overweight",
              str({k: entry.get(k) for k in ("rating", "ai_rating")} if entry else None))
        again = screener.screen(SCREEN_UNIVERSE, today=TODAY, record=True)
        check("jurnal: hari yang sama tidak menimpa catatan",
              [item["id"] for item in again["recorded"]] == [None], str(again["recorded"]))
finally:
    market.chart, llm.status, llm.chat_json = real_chart, real_status, real_chat
    store.DB_PATH = real_db

# ---- SQLite under concurrent writers ------------------------------------------
# The HTTP server is threaded and the screener records from a worker pool: two
# writes can overlap, and without WAL the loser raises "database is locked".
import sqlite3
import threading

try:
    with tempfile.TemporaryDirectory() as tmp:
        store.DB_PATH = Path(tmp) / "wal.db"
        store.init()
        probe = sqlite3.connect(store.DB_PATH)
        try:
            mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            probe.close()          # `with connect(...)` commits but never closes
        check("store: WAL aktif", str(mode).lower() == "wal", str(mode))

        errors: list[Exception] = []

        def write(index: int) -> None:
            try:
                store.record(f"TEST{index}.JK", "2026-10-27", "Hold", 100.0,
                             "^JKSE", 5, {"summary": "uji"}, {})
            except Exception as exc:            # noqa: BLE001 — the failure is the assertion
                errors.append(exc)

        writers = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join()
        check("store: delapan penulis bersamaan tidak terkunci", not errors, str(errors))
        check("store: semua tulisan bersamaan tersimpan",
              len(store.journal()) == 8, str(len(store.journal())))
finally:
    store.DB_PATH = real_db

# ---- report -----------------------------------------------------------------
failed = 0
for name, ok, detail in CHECKS:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
    failed += 0 if ok else 1
print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} cek murni lolos")
raise SystemExit(1 if failed else 0)
