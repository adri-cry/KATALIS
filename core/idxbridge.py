"""Optional bridge to the bundled `idx` CLI (the Rust repository under vendor/idx).

That tool is what brings IDX-only data this program cannot derive from prices:
broker accumulation ("bandar"), foreign flow, KSEI ownership, dividends and the
exchange calendar. When its binary is absent every caller degrades to a proxy
computed from price and volume, and says so — a missing feature must never look
like a neutral finding.

The binary is looked for in this order: ``IDX_BIN``, a release/debug build inside
``vendor/idx``, then ``PATH``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CANDIDATES = (
    ROOT / "vendor" / "idx" / "bin" / "idx.exe",     # prebuilt release, no toolchain needed
    ROOT / "vendor" / "idx" / "bin" / "idx",
    ROOT / "vendor" / "idx" / "target" / "release" / "idx.exe",
    ROOT / "vendor" / "idx" / "target" / "release" / "idx",
    ROOT / "vendor" / "idx" / "target" / "debug" / "idx.exe",
    ROOT / "vendor" / "idx" / "target" / "debug" / "idx",
    ROOT / "vendor" / "idx" / "idx.exe",
)


class BridgeUnavailable(RuntimeError):
    """The idx binary is absent, failed, or answered something unusable."""


# status() runs `idx version`, and desk.analyse asks for it on every request. The
# answer only changes when the binary itself changes, so it is cached; the cache
# key is the resolved path, which makes installing the binary take effect at once.
_STATUS_TTL = 60.0
_STATUS_LOCK = threading.Lock()
_STATUS: dict = {"at": 0.0, "path": None, "value": None}


def _reset_cache() -> None:
    """Test hook: forget the cached version handshake."""
    with _STATUS_LOCK:
        _STATUS.update({"at": 0.0, "path": None, "value": None})


def binary() -> str | None:
    configured = os.environ.get("IDX_BIN")
    if configured and Path(configured).exists():
        return configured
    for candidate in _CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return shutil.which("idx")


def available() -> bool:
    return binary() is not None


def status(refresh: bool = False) -> dict:
    """Availability and version, at most one subprocess per minute.

    `refresh=True` forces the handshake again — the startup banner uses it so the
    operator sees the truth immediately instead of a cached answer.
    """
    path = binary()
    now = time.monotonic()
    with _STATUS_LOCK:
        cached = _STATUS["value"]
        if (not refresh and cached is not None and _STATUS["path"] == path
                and now - _STATUS["at"] < _STATUS_TTL):
            return dict(cached)
    if path is None:
        payload = {"available": False, "path": None, "version": None,
                   "hint": 'Build it with: cd vendor/idx && cargo build --release'}
    else:
        try:
            answer = run("version", timeout=15)
            version = answer.get("version") or answer.get("data", {}).get("version") or "unknown"
        except BridgeUnavailable:
            version = "unknown"
        payload = {"available": True, "path": path, "version": version, "hint": None}
    payload["checked_at"] = int(time.time())
    with _STATUS_LOCK:
        _STATUS.update({"at": now, "path": path, "value": dict(payload)})
    return payload


def run(*args: str, timeout: int = 20, attempts: int = 1) -> dict:
    """Run one idx command with a bounded wait.

    IDX data is optional to the desk: if a public source is slow, the caller
    degrades to its visible proxy instead of retrying a stalled network call
    long enough to make a single-stock analysis feel hung. Callers may opt into
    a longer timeout/retry for non-interactive tasks.
    """
    executable = binary()
    if executable is None:
        raise BridgeUnavailable("the idx binary is not installed")

    failure = "unknown failure"
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.6)
        try:
            proc = subprocess.run(
                [executable, "-o", "json", *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failure = f"idx {' '.join(args)} could not run: {exc}"
            continue
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                failure = f"idx printed non-JSON: {exc}"
                continue
        detail = (proc.stdout or proc.stderr or "").strip().splitlines()
        failure = f"idx {' '.join(args)} failed: {detail[-1] if detail else proc.returncode}"
    raise BridgeUnavailable(failure)


def _data(payload: dict):
    return payload.get("data", payload)


_ERROR_HTTP = re.compile(r"HTTP (\d{3})")


def explain(exc: BaseException) -> str:
    """A short Indonesian reason for a failed idx call.

    idx prints a JSON envelope on failure and run() folds it into the exception
    message. Showing that blob on a role card is unreadable, and truncating it
    cuts the HTTP status in half — yet the source and the status are exactly
    what tells an operator whether this is a bug or an outage upstream.
    """
    raw = str(exc)
    payload = None
    start = raw.find("{")
    if start >= 0:
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw[start:])
        except json.JSONDecodeError:
            payload = None
    error = (payload or {}).get("error")
    detail = str((error or {}).get("message") or raw).strip()
    lowered = detail.lower()
    if "cloudflare" in lowered or "bot protection" in lowered:
        return "idx.co.id menolak permintaan (Cloudflare)"
    status = _ERROR_HTTP.search(detail)
    if status:
        who = "IPOT" if "ipot" in lowered else "sumber idx"
        return f"{who} menolak permintaan (HTTP {status.group(1)})"
    code = (error or {}).get("code")
    if code:
        return f"{code}: {detail[:140]}"
    return detail[:140]


def _symbol(ticker: str) -> str:
    return ticker[:-3] if ticker.upper().endswith(".JK") else ticker.upper()


# The four feeds the desk reads get one retry: idx.co.id refuses a share of
# requests at random, and a single refusal should not demote a role to its proxy.
def bandar(ticker: str, period: str = "1w") -> dict:
    return _data(run("bandar", "detect", _symbol(ticker), "--period", period, attempts=2))


def foreign(ticker: str, days: int = 20) -> dict:
    return _data(run("stocks", "foreign", _symbol(ticker), "--days", str(days), attempts=2))


def fundamental(ticker: str) -> dict:
    return _data(run("stocks", "fundamental", _symbol(ticker), attempts=2))


def profile(ticker: str) -> dict:
    return _data(run("stocks", "profile", _symbol(ticker)))


def dividends(ticker: str) -> dict:
    return _data(run("stocks", "dividends", _symbol(ticker)))


def actions(ticker: str) -> dict:
    return _data(run("stocks", "actions", _symbol(ticker)))


def news(ticker: str) -> dict:
    return _data(run("stocks", "news", _symbol(ticker), "--source", "all"))


def sentiment(ticker: str) -> dict:
    return _data(run("stocks", "sentiment", _symbol(ticker), attempts=2))


def holidays() -> dict:
    return _data(run("market", "holidays", timeout=30))
