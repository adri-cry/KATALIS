"""KATALIS — IDX Intelligence Desk.

A single standard-library HTTP server: the JSON API the dashboard talks to, and
the dashboard itself. No framework, no build step, no third-party package.

    python app.py                 # http://127.0.0.1:8770
    python app.py --port 8800
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import advisor, desk, guidance, idxbridge, journal, llm, market, screener, simulation, store

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
VERSION = "1.2.0"
WIB = timezone(timedelta(hours=7))

_ANALYSIS_LOCK = threading.Lock()
_ANALYSIS_JOBS: dict[str, dict] = {}
_LATEST_ANALYSES: dict[str, dict] = {}  # process-local; simulation accepts only these machine plans
_ANALYSIS_JOB_TTL = 60 * 60

# The dashboard builds its markup from template strings, so a strict script policy
# is the second line of defence behind web/app.js's esc(). 'unsafe-inline' is
# limited to styles: the view templates set style="…" for score bars and widths.
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'")


def _analysis_job(job_id: str, *, updates: dict | None = None) -> dict | None:
    now = time.time()
    with _ANALYSIS_LOCK:
        for key, job in list(_ANALYSIS_JOBS.items()):
            if job.get("updated_at", now) < now - _ANALYSIS_JOB_TTL:
                _ANALYSIS_JOBS.pop(key, None)
        job = _ANALYSIS_JOBS.get(job_id)
        if job is None:
            return None
        if job.get("status") == "running" and now - job.get("updated_at", now) > 600:
            job.update({"status": "error", "stage": "error",
                        "message": "Proses analisis berhenti atau server dimulai ulang.",
                        "error": "status analisis kedaluwarsa"})
            job["updated_at"] = now
        if updates:
            job.update(updates)
            job["updated_at"] = now
        return dict(job)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def quotes(symbols: list[str]) -> list[dict]:
    """Quote rows for the watchlist, with a 30-session sparkline for the card."""
    rows = []
    for symbol in symbols:
        try:
            payload = market.chart(symbol, "1mo")
            indicators = market.indicators(payload["bars"])
            rows.append({
                "symbol": payload["symbol"],
                "name": payload["name"],
                "price": payload["price"],
                "change_pct": indicators["change_pct"],
                "rsi14": indicators["rsi14"],
                "spark": [round(bar["c"], 2) for bar in payload["bars"][-30:]],
            })
        except market.MarketError as exc:
            rows.append({"symbol": symbol, "error": str(exc)})
    return rows


def _price_of(symbol: str, fallback: float) -> float:
    try:
        return market.chart(symbol, "1mo")["price"]
    except market.MarketError:
        return fallback


def portfolio_view() -> dict:
    stored = store.positions()
    # N positions mean N round-trips to the price source; a small pool turns
    # N sequential waits into roughly one.
    with ThreadPoolExecutor(max_workers=8) as pool:
        prices = list(pool.map(lambda p: _price_of(p["symbol"], p["avg_price"]), stored))
    positions, market_value, cost_total = [], 0.0, 0.0
    for position, price in zip(stored, prices):
        value = price * position["quantity"]
        cost = position["avg_price"] * position["quantity"]
        pnl = value - cost
        positions.append({**position, "price": price, "value": value, "cost": cost,
                          "pnl": pnl, "pnl_pct": (pnl / cost if cost else 0.0)})
        market_value += value
        cost_total += cost
    cash = store.cash()
    return {"positions": positions, "market_value": market_value, "cost": cost_total,
            "pnl": market_value - cost_total, "cash": cash, "equity": market_value + cash}


def _execute_analysis(symbol: str, holding: int, range_: str, want_ai: bool,
                      progress=None) -> dict:
    """One analysis run shared by the legacy synchronous and job endpoints."""
    portfolio = portfolio_view()
    if progress:
        progress("market", "Mengambil harga dan menghitung analisis pasar…")
    analysis = desk.analyse(symbol, range_, holding, equity=portfolio["equity"])

    ai_block, ai_error = None, None
    if want_ai:
        if progress:
            progress("ai", "Data analis siap; menunggu jawaban model AI…")
        ai_started = time.time()

        def ai_event(event: str) -> None:
            # Streaming tells us what the model is doing; the label keeps the
            # operator informed instead of a silent spinner for minutes.
            labels = {"thinking": "Model AI sedang menimbang bukti…",
                      "writing": "Model AI menyusun jawaban final…"}
            text = labels.get(event)
            if text and progress:
                progress("ai", f"{text} ({time.time() - ai_started:.0f} dtk)")

        try:
            ai_block = advisor.advise(analysis, portfolio, on_event=ai_event)
        except llm.LLMError as exc:
            ai_error = str(exc)
    analysis["ai"] = ai_block
    analysis["ai_error"] = ai_error
    analysis["analysis_id"] = uuid.uuid4().hex
    analysis["guidance"] = guidance.build(analysis)
    with _ANALYSIS_LOCK:
        _LATEST_ANALYSES[analysis["symbol"]] = analysis

    if progress:
        progress("save", "Menyimpan hasil analisis…")
    decision_id = store.record(
        analysis["symbol"], analysis["as_of"], analysis["decision"]["rating"],
        analysis["price"], market.BENCHMARK, holding,
        {"rating": analysis["decision"]["rating"],
         "confidence": analysis["decision"]["confidence"],
         "composite": analysis["manager"]["composite"],
         "price": analysis["price"], "as_of": analysis["as_of"],
         "summary": analysis["decision"]["summary"]},
        {"roles": analysis["roles"], "debate": analysis["debate"],
         "manager": analysis["manager"], "trader": analysis["trader"],
         "risk": analysis["risk"], "decision": analysis["decision"],
         "guidance": analysis["guidance"]},
        ai=ai_block,
    )
    analysis["recorded"] = decision_id
    return analysis


def simulate_latest(symbol: str, analysis_id: str, lots, capital, fee_rate_pct=None) -> tuple[int, dict]:
    """Calculate against the exact latest server-held machine plan for a symbol."""
    symbol = market.normalise(symbol)
    if not symbol:
        return 400, {"error": "symbol is required"}
    with _ANALYSIS_LOCK:
        analysis = _LATEST_ANALYSES.get(symbol)
    if not analysis:
        return 404, {"error": "Jalankan analisis terbaru sebelum simulasi."}
    if analysis_id != analysis.get("analysis_id"):
        return 409, {"error": "Hasil analisis berubah. Muat ulang simulasi dari ringkasan terbaru."}
    if (analysis.get("guidance") or {}).get("action") != "Beli":
        return 409, {"error": "Simulator hanya tersedia saat ringkasan terbaru menyatakan Beli."}
    plan = analysis.get("trader") or {}
    try:
        result = simulation.calculate(plan.get("entry"), plan.get("stop"), plan.get("target"),
                                      lots, capital, fee_rate_pct)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"symbol": symbol, "as_of": analysis.get("as_of"),
                 "plan_source": "mesin", "result": result}


def _background_analysis(job_id: str, symbol: str, holding: int, range_: str,
                         want_ai: bool) -> None:
    def progress(stage: str, message: str) -> None:
        _analysis_job(job_id, updates={"stage": stage, "message": message})
    try:
        result = _execute_analysis(symbol, holding, range_, want_ai, progress)
        _analysis_job(job_id, updates={"status": "complete", "stage": "complete",
                                       "message": "Analisis selesai.", "result": result})
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("KATALIS_LOG"):
            traceback.print_exc()
        _analysis_job(job_id, updates={"status": "error", "stage": "error",
                                       "message": f"Analisis gagal: {exc}", "error": str(exc)})


def health() -> dict:
    return {
        "ok": True,
        "version": VERSION,
        "benchmark": market.BENCHMARK,
        "idx": idxbridge.status(),
        "llm": {**llm.status(), "diagnostics": llm.diagnose()},
        "journal": store.stats(),
        "equity": portfolio_view()["equity"],
        "clock": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "vendor": {"tradingagents": (ROOT / "vendor" / "tradingagents").exists(),
                   "idx": (ROOT / "vendor" / "idx").exists()},
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"Katalis/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # keep the console for the banner
        if os.environ.get("KATALIS_LOG"):
            super().log_message(fmt, *args)

    # -- helpers ----------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, message: str, code: int = 400) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _static(self, route: str) -> None:
        target = (WEB / (route.lstrip("/") or "index.html")).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        guessed, _ = mimetypes.guess_type(str(target))
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
        self._send(200, target.read_bytes(), types.get(target.suffix, guessed or "application/octet-stream"))

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:                                    # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        try:
            if route == "/api/health":
                return self._json(health())
            if route == "/api/analyze/status":
                job_id = self._query().get("id") or ""
                job = _analysis_job(job_id)
                if job is None:
                    return self._error("status analisis tidak ditemukan atau sudah kedaluwarsa", 404)
                return self._json({key: value for key, value in job.items() if key not in ("result", "error")})
            if route.startswith("/api/analyze/"):
                return self._error("unknown endpoint", 404)
            if route == "/api/watchlist":
                symbols = store.watchlist()
                return self._json({"symbols": symbols, "quotes": quotes(symbols)})
            if route == "/api/market":
                symbol = market.normalise(self._query().get("symbol") or "")
                if not symbol:
                    return self._error("symbol is required")
                payload = market.snapshot(
                    symbol,
                    self._query().get("range") or "6mo",
                    fresh=self._query().get("fresh") == "1",
                )
                return self._json(payload)
            if route == "/api/tick":
                symbol = market.normalise(self._query().get("symbol") or "")
                if not symbol:
                    return self._error("symbol is required")
                return self._json(market.tick(symbol))
            if route == "/api/stream":
                symbol = market.normalise(self._query().get("symbol") or "")
                if not symbol:
                    return self._error("symbol is required")
                return self._stream(symbol)
            if route == "/api/benchmark":
                payload = market.snapshot(market.BENCHMARK, "3mo")
                return self._json({**payload, "indicators": market.indicators(payload["bars"])})
            if route == "/api/journal":
                return self._json({"entries": store.journal(), "stats": store.stats()})
            if route == "/api/decision/snapshot":
                symbol = market.normalise(self._query().get("symbol") or "")
                with _ANALYSIS_LOCK:
                    analysis = _LATEST_ANALYSES.get(symbol)
                if not analysis:
                    return self._error("Jalankan analisis terbaru untuk simbol ini sebelum simulasi.", 404)
                return self._json({
                    "symbol": symbol,
                    "price": analysis.get("price"),
                    "as_of": analysis.get("as_of"),
                    "trader": analysis.get("trader") or {},
                    "guidance": analysis.get("guidance") or {},
                })
            if route == "/api/decision":
                identifier = self._query().get("id")
                if not identifier:
                    return self._error("id is required")
                entry = store.decision(int(identifier))
                if entry is None:
                    return self._error("keputusan tidak ditemukan", 404)
                return self._json(entry)
            if route == "/api/portfolio":
                return self._json(portfolio_view())
            if route == "/api/idx":
                return self._idx(self._query())
            if route.startswith("/api/"):
                return self._error("unknown endpoint", 404)
            return self._static(route)
        except market.MarketError as exc:
            return self._error(str(exc), 502)
        except Exception as exc:                                  # noqa: BLE001
            if os.environ.get("KATALIS_LOG"):
                traceback.print_exc()
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:                                   # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        body = self._body()
        try:
            if route == "/api/watchlist":
                if body.get("remove"):
                    return self._json({"symbols": store.remove_watch(str(body["remove"]))})
                incoming = body.get("add") or body.get("symbols") or []
                if isinstance(incoming, str):
                    incoming = [incoming]
                if not incoming:
                    return self._error("add at least one symbol")
                return self._json({"symbols": store.add_watch([str(s) for s in incoming])})

            if route == "/api/analyze":
                symbol = market.normalise(body.get("symbol") or "")
                if not symbol:
                    return self._error("symbol is required")
                holding = int(body.get("holding_days") or 5)
                want_ai = body.get("ai")
                if want_ai is None:
                    want_ai = llm.status()["configured"]
                analysis = _execute_analysis(symbol, holding, body.get("range") or "6mo", bool(want_ai))
                return self._json(analysis)

            if route == "/api/analyze/start":
                symbol = market.normalise(body.get("symbol") or "")
                if not symbol:
                    return self._error("symbol is required")
                job_id = uuid.uuid4().hex
                holding = int(body.get("holding_days") or 5)
                want_ai = body.get("ai")
                if want_ai is None:
                    want_ai = llm.status()["configured"]
                job = {"id": job_id, "symbol": symbol, "status": "running", "stage": "queue",
                       "message": "Menyiapkan analisis…", "started_at": time.time(),
                       "updated_at": time.time()}
                with _ANALYSIS_LOCK:
                    _ANALYSIS_JOBS[job_id] = job
                worker = threading.Thread(
                    target=_background_analysis,
                    args=(job_id, symbol, holding, body.get("range") or "6mo", bool(want_ai)),
                    name=f"analysis-{job_id[:8]}", daemon=True)
                worker.start()
                return self._json({key: value for key, value in job.items() if key != "result"}, 202)

            if route == "/api/analyze/result":
                job_id = self._query().get("id") or ""
                job = _analysis_job(job_id)
                if job is None:
                    return self._error("status analisis tidak ditemukan atau sudah kedaluwarsa", 404)
                if job["status"] == "error":
                    return self._error(job.get("error") or "analisis gagal", 500)
                if job["status"] != "complete":
                    return self._json({"status": job["status"], "stage": job["stage"]}, 202)
                return self._json(job["result"])

            if route == "/api/screen":
                # Slow by nature: one Yahoo request per ticker, idx lookups for the shortlist,
                # and at most one model call. `ai` omitted = use the model if one is configured.
                symbols = body.get("symbols") or []
                if isinstance(symbols, str):
                    symbols = [symbols]
                return self._json(screener.screen(
                    [str(s) for s in symbols] or None,
                    max_picks=int(body.get("max_picks") or 5),
                    shortlist=int(body.get("shortlist") or 15),
                    ai=body.get("ai"),
                    record=bool(body.get("record")),
                    holding_days=int(body.get("holding_days") or 5),
                ))

            if route == "/api/simulate":
                code, result = simulate_latest(body.get("symbol") or "", body.get("analysis_id") or "",
                                               body.get("lots"), body.get("capital"),
                                               body.get("fee_rate_pct"))
                return self._json(result, code)

            if route == "/api/settle":
                return self._json(journal.settle_all(body.get("holding_days")))

            if route == "/api/portfolio":
                symbol = market.normalise(body.get("symbol") or "")
                if not symbol:
                    return self._error("symbol is required")
                quantity = float(body.get("quantity") or 0)
                average = float(body.get("avg_price") or 0)
                store.set_position(symbol, quantity, average)
                return self._json(portfolio_view())

            if route == "/api/cash":
                store.set_cash(float(body.get("value") or 0))
                return self._json(portfolio_view())

            return self._error("unknown endpoint", 404)
        except market.MarketError as exc:
            return self._error(str(exc), 502)
        except ValueError as exc:
            return self._error(f"nilai tidak valid: {exc}", 400)
        except Exception as exc:                                  # noqa: BLE001
            if os.environ.get("KATALIS_LOG"):
                traceback.print_exc()
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def _stream(self, symbol: str) -> None:
        """Server-sent events: one tick every few seconds until the client leaves.

        A quote is cached server-side for a few seconds, so several tabs share
        one upstream request instead of each hammering the price source.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        deadline = time.time() + 1800          # a browser tab may hold it 30 minutes
        while time.time() < deadline:
            try:
                payload = market.tick(symbol)
            except market.MarketError as exc:
                payload = {"symbol": symbol, "error": str(exc), "at": int(time.time())}
            try:
                self.wfile.write(
                    b"data: " + json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8") + b"\n\n"
                )
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return                          # the tab closed; that is not an error
            time.sleep(5)

    def _idx(self, query: dict) -> None:
        """Optional idx passthrough: the IDX-only data this program cannot derive."""
        command = (query.get("cmd") or "").strip()
        symbol = (query.get("symbol") or "").strip()
        if not idxbridge.available():
            return self._json({"available": False,
                               "hint": "Bangun binernya: cd vendor/idx && cargo build --release"})
        table = {
            "bandar": lambda: idxbridge.bandar(symbol),
            "foreign": lambda: idxbridge.foreign(symbol),
            "fundamental": lambda: idxbridge.fundamental(symbol),
            "profile": lambda: idxbridge.profile(symbol),
            "dividends": lambda: idxbridge.dividends(symbol),
            "actions": lambda: idxbridge.actions(symbol),
            "news": lambda: idxbridge.news(symbol),
            "holidays": idxbridge.holidays,
        }
        handler = table.get(command)
        if handler is None:
            return self._error(f"cmd harus salah satu: {', '.join(table)}", 400)
        try:
            return self._json({"available": True, "cmd": command, "data": handler()})
        except idxbridge.BridgeUnavailable as exc:
            return self._json({"available": False, "error": str(exc)})


class Server(ThreadingHTTPServer):
    """A browser that closes a tab mid-request is not an error worth a traceback."""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main() -> None:
    # Windows consoles default to cp1252, where the banner's box-drawing
    # characters raise UnicodeEncodeError before the server ever starts.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    store.init()
    desired = 8770
    for index, arg in enumerate(sys.argv):
        if arg == "--port" and index + 1 < len(sys.argv):
            desired = int(sys.argv[index + 1])

    server = None
    for offset in range(0, 20):
        try:
            server = Server(("127.0.0.1", desired + offset), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("Tidak ada port yang bisa dipakai (8770-8789).", file=sys.stderr)
        raise SystemExit(1)

    port = server.server_address[1]
    bridge = idxbridge.status(refresh=True)
    brain = llm.status()
    stats = store.stats()
    print("")
    print(f"  KATALIS {VERSION}  |  IDX Intelligence Desk")
    print("  " + "-" * 52)
    print(f"  dashboard  http://127.0.0.1:{port}")
    print(f"  data       Yahoo Finance | benchmark IHSG ({market.BENCHMARK})")
    print(f"  idx CLI    {bridge['version'] if bridge['available'] else 'belum terpasang (peran idx memakai proksi harga)'}")
    print(f"  AI         {brain['model'] if brain['configured'] else 'mati (isi OPENAI_API_KEY di .env)'}")
    print(f"  jurnal     {stats['settled']} keputusan dinilai | {stats['pending']} menunggu")
    print("  Ctrl+C untuk berhenti")
    print("")
    # A redirected stdout is block-buffered, and the URL is the one line the
    # operator needs; flush so it appears before the first request, not after.
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  KATALIS berhenti.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
