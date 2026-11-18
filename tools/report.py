"""16:05 WIB — daily paper-trading report and light evaluation.

Writes ``data/laporan-YYYY-MM-DD.md``, settles matured journal decisions, and
optionally pushes a short version to Telegram. Telegram is off until
``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` are filled in ``.env``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import journal, market, notify, paper, store  # noqa: E402

WIB = timezone(timedelta(hours=7))


def _fmt(value, digits: int = 0) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def build_report(today: datetime | None = None) -> tuple[str, str]:
    """Return (markdown, short Telegram text). Does NOT send anything."""
    today = today or datetime.now(WIB)
    store.init()
    paper.init()
    settled_now = journal.settle_all()

    state = paper.state()
    day = today.strftime("%Y-%m-%d")
    todays = [trade for trade in paper.trades(200) if trade["trade_date"] == day]
    stats = store.stats()

    lines = [
        f"# Laporan KATALIS — {day}",
        "",
        f"Dibuat {today.strftime('%Y-%m-%d %H:%M:%S WIB')}. "
        "Ini simulasi, bukan nasihat investasi.",
        "",
        "## Portofolio paper",
        "",
        f"- Kas: Rp{_fmt(state['cash'])}",
        f"- Nilai saham: Rp{_fmt(state['market_value'])}",
        f"- Ekuitas: Rp{_fmt(state['equity'])}",
        f"- P/L belum terealisasi: Rp{_fmt(state['pnl'])} "
        f"({_pct(state['pnl'] / state['cost'] if state['cost'] else None)})",
        f"- Eksposur: {_fmt(state['exposure_pct'], 1)}%",
        "",
        "| Kode | Jumlah | Harga | Nilai | P/L |",
        "|---|---:|---:|---:|---:|",
    ]
    for position in state["positions"]:
        lines.append(f"| {position['symbol']} | {_fmt(position['quantity'])} | "
                     f"{_fmt(position['price'])} | {_fmt(position['value'])} | "
                     f"{_fmt(position['pnl'])} ({_pct(position['pnl_pct'])}) |")
    if not state["positions"]:
        lines.append("| — | — | — | — | — |")

    lines += ["", "## Transaksi hari ini", "",
              "| Waktu | Kode | Aksi | Jumlah | Harga | Alasan | Mode | P/L |",
              "|---|---|---|---:|---:|---|---|---:|"]
    for trade in reversed(todays):
        stamp = datetime.fromtimestamp(trade["created_at"], WIB).strftime("%H:%M")
        lines.append(f"| {stamp} | {trade['symbol']} | {trade['side']} | "
                     f"{_fmt(trade['quantity'])} | {_fmt(trade['price'])} | "
                     f"{trade['reason'] or '—'} | {trade['mode']} | "
                     f"{_fmt(trade['pnl']) if trade['pnl'] is not None else '—'} |")
    if not todays:
        lines.append("| — | — | — | — | — | — | — | — |")

    try:
        ihsg = market.tick(market.BENCHMARK)
        market_line = (f"IHSG {_fmt(ihsg.get('price'))} "
                       f"({_pct((ihsg.get('change_pct') or 0) / 100)})")
    except market.MarketError as exc:
        market_line = f"IHSG tidak tersedia: {exc}"

    lines += ["", "## Jurnal", "",
              f"- Dinilai: {stats['settled']} · menunggu: {stats['pending']} · "
              f"sampel berarah: {stats['directional_count']}",
              f"- Baru dinilai hari ini: {len(settled_now['settled'])}",
              f"- {market_line}", "",
              "| Rating | n | Tepat | Alpha rata-rata | Margin 95% |",
              "|---|---:|---:|---:|---:|"]
    for rating, value in (stats.get("by_rating") or {}).items():
        hit = "—" if value["hit_rate"] is None else f"{value['hit_rate'] * 100:.0f}%"
        ci = "—" if value["ci95_halfwidth"] is None else f"±{value['ci95_halfwidth'] * 100:.1f}%"
        lines.append(f"| {rating} | {value['count']} | {hit} | "
                     f"{_pct(value['mean_alpha'])} | {ci} |")

    lines += [
        "",
        "## Catatan evaluasi",
        "",
        "- Aturan dikunci sampai sampel berarah mencapai 96."
        " Perubahan hanya setelah persetujuan pemilik.",
        "- Transaksi `belajar` (1 lot tanpa sinyal Beli) dihitung terpisah"
        " dari transaksi `aturan`.",
        "- Jurnal menilai keputusan terhadap IHSG setelah 5 sesi, bukan backtest.",
        "",
    ]
    markdown = "\n".join(lines)

    short = (
        f"📄 Laporan KATALIS {day}\n"
        f"Ekuitas paper Rp{_fmt(state['equity'])} · kas Rp{_fmt(state['cash'])} · "
        f"P/L Rp{_fmt(state['pnl'])}\n"
        f"Posisi {len(state['positions'])} · transaksi hari ini {len(todays)}\n"
        f"Jurnal {stats['settled']} dinilai · {stats['directional_count']} sampel berarah\n"
        f"{market_line}"
    )
    return markdown, short


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/report.py",
                                     description="Laporan harian 16:05 WIB.")
    parser.add_argument("--no-send", action="store_true", help="jangan kirim ke Telegram")
    args = parser.parse_args(argv)
    now = datetime.now(WIB)
    markdown, short = build_report(now)
    target = ROOT / "data" / f"laporan-{now.strftime('%Y-%m-%d')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    print(f"Laporan ditulis: {target}")

    if args.no_send:
        print("Telegram dilewati (--no-send).")
    else:
        sent, reason = notify.send(short)
        print(f"Telegram: {'terkirim' if sent else 'tidak dikirim'} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
