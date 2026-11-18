"""Record today's screening picks, then score every matured decision.

One command to run once a day — from cron, Task Scheduler, or by hand. Machine
only by default, so it costs nothing: ``python tools/daily.py``. Pass ``--ai``
when you explicitly want the model to review the shortlist (one paid call).

    # Linux/macOS, every weekday at 07:15 WIB
    15 7 * * 1-5 cd /root/katalis && /usr/bin/python3 tools/daily.py >> data/daily.log 2>&1

    # Windows: Task Scheduler -> Basic Task -> "python tools\\daily.py",
    # start in the KATALIS folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import journal, screener, store  # noqa: E402


WIB = timezone(timedelta(hours=7))
SCAN_FILE = Path(__file__).resolve().parent.parent / "data" / "scan.json"


def write_scan(result: dict, path: Path = SCAN_FILE) -> dict:
    """The 09:00 executor reads this, not a fresh screen (which would be wasteful)."""
    payload = {
        "as_of": result["as_of"],
        "generated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "picks": [
            {
                "symbol": row["symbol"],
                "rating": row["mechanical"]["rating"],
                "composite": row["mechanical"]["composite"],
                "atr_pct": row.get("atr_pct"),
                "entry": row["plan"]["entry"],
                "stop": row["plan"]["stop"],
                "target": row["plan"]["target"],
                "reward_risk": row["plan"]["reward_risk"],
            }
            for row in result["candidates"] if row["pick"]
        ],
        "errors": result["errors"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="python tools/daily.py",
        description="Catat pilihan pemindai hari ini dan nilai keputusan yang jatuh tempo.")
    parser.add_argument("--ai", action="store_true", help="sertakan satu panggilan model")
    parser.add_argument("--top", type=int, default=5, help="maksimal pilihan (default 5)")
    parser.add_argument("--shortlist", type=int, default=30, help="jumlah saham ke analisis penuh")
    args = parser.parse_args(argv)

    store.init()
    screen = screener.screen(max_picks=args.top, shortlist=args.shortlist,
                             ai=True if args.ai else False, record=True)
    scan = write_scan(screen)
    print(f"Pemindai {screen['as_of']}: {screen['universe']} saham, "
          f"{len(screen['candidates'])} kandidat, {len(scan['picks'])} pilihan "
          f"({screen['picks_source']}) → data/scan.json")
    for item in screen["recorded"]:
        print("  jurnal: " + item["symbol"] + (
            f" #{item['id']}" if item["id"] else " sudah tercatat hari ini"))
    for symbol in screen["errors"]:
        print(f"  gagal: {symbol['symbol']}: {symbol['error']}")

    settled = journal.settle_all()
    stats = store.stats()
    print(f"Penilaian: {len(settled['settled'])} selesai · {len(settled['waiting'])} menunggu · "
          f"{len(settled['failed'])} gagal. "
          f"Jurnal: {stats['settled']} dinilai, {stats['pending']} menunggu, "
          f"{stats['directional_count']} sampel berarah.")
    # Distinguish "nothing worth buying" from "nothing could be read".
    return 2 if screen["universe"] and len(screen["errors"]) == screen["universe"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
