"""Scenario-only P/L estimate using immutable mechanical desk levels."""
from __future__ import annotations

import math


def _finite(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} harus berupa angka") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} harus berupa angka terbatas")
    return number


def calculate(entry, stop, target, lots, capital, fee_rate_pct=None) -> dict:
    """Estimate round-trip outcomes. Fee is an optional user-entered rate per side."""
    entry = _finite(entry, "Entry")
    stop = _finite(stop, "Stop")
    target = _finite(target, "Target")
    capital = _finite(capital, "Modal")
    lots_n = _finite(lots, "Lot")
    fee_rate = None if fee_rate_pct in (None, "") else _finite(fee_rate_pct, "Biaya per sisi")
    if any(value <= 0 for value in (entry, stop, target, capital)):
        raise ValueError("Entry, stop, target, dan modal harus lebih besar dari nol")
    if lots_n <= 0 or not lots_n.is_integer():
        raise ValueError("Jumlah lot harus bilangan bulat positif")
    if fee_rate is not None and not 0 <= fee_rate <= 100:
        raise ValueError("Biaya per sisi harus antara 0 dan 100 persen")

    direction = "long" if target > entry else "short" if target < entry else None
    if direction == "long" and stop >= entry:
        raise ValueError("Untuk skenario beli, stop harus di bawah entry dan target di atas entry")
    if direction == "short":
        raise ValueError("Rencana ini mengarah turun. KATALIS tidak memverifikasi ketersediaan jual kosong, jadi simulator transaksi short dinonaktifkan.")
    if direction is None:
        raise ValueError("Target harus berbeda dari entry")

    quantity = int(lots_n) * 100
    sign = 1 if direction == "long" else -1
    gross_target = (target - entry) * quantity * sign
    gross_stop = (stop - entry) * quantity * sign
    target_fees = (entry + target) * quantity * fee_rate / 100 if fee_rate is not None else 0.0
    stop_fees = (entry + stop) * quantity * fee_rate / 100 if fee_rate is not None else 0.0
    net_target = gross_target - target_fees
    net_stop = gross_stop - stop_fees
    risk = abs(net_stop)
    notional = entry * quantity
    return {
        "direction": direction,
        "quantity": quantity,
        "lots": int(lots_n),
        "entry": entry,
        "stop": stop,
        "target": target,
        "entry_notional": round(notional, 2),
        "capital": round(capital, 2),
        "capital_share_pct": round(notional / capital * 100, 2),
        "capital_covers_entry": notional <= capital if direction == "long" else None,
        "gross_target_pnl": round(gross_target, 2),
        "gross_stop_pnl": round(gross_stop, 2),
        "gross_reward_risk": round(gross_target / abs(gross_stop), 2) if gross_stop else None,
        "fee_rate_per_side_pct": fee_rate,
        "target_round_trip_fees": round(target_fees, 2),
        "stop_round_trip_fees": round(stop_fees, 2),
        "fees_included": fee_rate is not None,
        "net_target_pnl": round(net_target, 2),
        "net_stop_pnl": round(net_stop, 2),
        "target_pnl_on_capital_pct": round(net_target / capital * 100, 2),
        "stop_pnl_on_capital_pct": round(net_stop / capital * 100, 2),
        "net_reward_risk": round(net_target / risk, 2) if risk else None,
        "short_availability_verified": False if direction == "short" else None,
        "disclaimer": ("Estimasi skenario berdasarkan level mesin dan input Anda. "
                       "Tidak memasukkan slippage, pajak, atau biaya yang tidak diinput; "
                       "hasil nyata dapat berbeda."),
    }


__all__ = ["calculate"]
