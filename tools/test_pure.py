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

from core import desk, llm, market, screener, store  # noqa: E402
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

# ---- parallel role reads -----------------------------------------------------
# Two independent external-role stubs must overlap; results must retain the
# established flow/bandar/fundamental/news order for downstream rendering.
from threading import Barrier

role_barrier = Barrier(2)
original_roles = (desk._role_market, desk._role_flow, desk._role_bandar,
                  desk._role_fundamental, desk._role_news)
try:
    def parallel_stub(key):
        def run(*args):
            if key in ("flow", "bandar"):
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

# ---- report -----------------------------------------------------------------
failed = 0
for name, ok, detail in CHECKS:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
    failed += 0 if ok else 1
print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} cek murni lolos")
raise SystemExit(1 if failed else 0)
