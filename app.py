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
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import advisor, desk, idxbridge, journal, llm, market, store

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
VERSION = "1.2.0"
WIB = timezone(timedelta(hours=7))


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


def portfolio_view() -> dict:
    positions, market_value, cost_total = [], 0.0, 0.0
    for position in store.positions():
        price = position["avg_price"]
        try:
            price = market.chart(position["symbol"], "1mo")["price"]
        except market.MarketError:
            pass
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
                portfolio = portfolio_view()
                analysis = desk.analyse(symbol, body.get("range") or "6mo", holding,
                                        equity=portfolio["equity"])

                # The desk's numbers stand on their own; the model only reads
                # them. A failure here is reported, never filled in.
                want_ai = body.get("ai")
                if want_ai is None:
                    want_ai = llm.status()["configured"]
                ai_block, ai_error = None, None
                if want_ai:
                    try:
                        ai_block = advisor.advise(analysis, portfolio)
                    except llm.LLMError as exc:
                        ai_error = str(exc)
                analysis["ai"] = ai_block
                analysis["ai_error"] = ai_error

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
                     "risk": analysis["risk"], "decision": analysis["decision"]},
                    ai=ai_block,
                )
                analysis["recorded"] = decision_id
                return self._json(analysis)

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
    bridge = idxbridge.status()
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
