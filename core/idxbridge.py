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
import shutil
import subprocess
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


def status() -> dict:
    path = binary()
    if path is None:
        return {"available": False, "path": None, "version": None,
                "hint": 'Build it with: cd vendor/idx && cargo build --release'}
    try:
        payload = run("version", timeout=15)
        version = payload.get("version") or payload.get("data", {}).get("version") or "unknown"
    except BridgeUnavailable:
        version = "unknown"
    return {"available": True, "path": path, "version": version, "hint": None}


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


def _symbol(ticker: str) -> str:
    return ticker[:-3] if ticker.upper().endswith(".JK") else ticker.upper()


def bandar(ticker: str, period: str = "1w") -> dict:
    return _data(run("bandar", "detect", _symbol(ticker), "--period", period))


def foreign(ticker: str, days: int = 20) -> dict:
    return _data(run("stocks", "foreign", _symbol(ticker), "--days", str(days)))


def fundamental(ticker: str) -> dict:
    return _data(run("stocks", "fundamental", _symbol(ticker)))


def profile(ticker: str) -> dict:
    return _data(run("stocks", "profile", _symbol(ticker)))


def dividends(ticker: str) -> dict:
    return _data(run("stocks", "dividends", _symbol(ticker)))


def actions(ticker: str) -> dict:
    return _data(run("stocks", "actions", _symbol(ticker)))


def news(ticker: str) -> dict:
    return _data(run("stocks", "news", _symbol(ticker), "--source", "all"))


def sentiment(ticker: str) -> dict:
    return _data(run("stocks", "sentiment", _symbol(ticker)))


def holidays() -> dict:
    return _data(run("market", "holidays", timeout=30))
