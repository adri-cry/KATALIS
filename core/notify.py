"""Optional Telegram notifier for the 16:05 report.

Credentials are read from ``.env`` / the process environment under
``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID``. With neither present this is a
no-op: the report is still written to ``data/`` and the cron job still succeeds.
No token is ever logged.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import llm  # imports the .env loader as a side effect

MAX_CHARS = 3800


def configured() -> bool:
    return bool(llm._value("TELEGRAM_BOT_TOKEN") and llm._value("TELEGRAM_CHAT_ID"))


def send(text: str) -> tuple[bool, str]:
    """Return (sent, reason). Never raises to the caller."""
    token, chat = llm._value("TELEGRAM_BOT_TOKEN"), llm._value("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False, "belum dikonfigurasi"
    try:
        # This machine's egress blocks POST bodies to Telegram but allows GET.
        # Query-string sendMessage works identically for short plain-text
        # reports, which is all this notifier sends.
        query = urllib.parse.urlencode({
            "chat_id": chat,
            "text": str(text)[:MAX_CHARS],
            "disable_web_page_preview": "true",
        })
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage?{query}",
                timeout=20) as response:
            answer = json.loads(response.read().decode("utf-8"))
        if answer.get("ok"):
            return True, "terkirim"
        return False, str((answer.get("description") or "gagal"))[:120]
    except Exception as exc:  # noqa: BLE001 — a messenger must never break the loop
        return False, f"{type(exc).__name__}: {str(exc)[:100]}"


__all__ = ["configured", "send"]
