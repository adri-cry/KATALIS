"""Deterministic, beginner-facing explanations built from desk/model evidence.

This module maps existing ratings; it does not make a new market forecast. Its
labels are a UI policy, not probabilities or investment advice.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

_POSITIVE = {"Buy", "Overweight"}
_NEUTRAL = {"Hold"}
_NEGATIVE = {"Underweight", "Sell"}
_ACTION = {"Buy": "Beli", "Overweight": "Beli", "Hold": "Tunggu",
           "Underweight": "Hindari", "Sell": "Hindari"}
_BUCKET = {**{rating: "positif" for rating in _POSITIVE},
           **{rating: "netral" for rating in _NEUTRAL},
           **{rating: "negatif" for rating in _NEGATIVE}}
_INTRADAY = {"1d", "5d"}
_DAILY = {"1mo", "3mo", "6mo", "1y", "2y"}
_MIN_ROLES = 3
_MIN_SESSIONS = 20


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _technical_bucket(score) -> str:
    score = _number(score) or 0.0
    return "positif" if score >= 0.5 else "negatif" if score <= -0.5 else "netral"


def build(analysis: dict) -> dict:
    """Translate existing evidence into a plain-language summary and explicit policy."""
    decision = analysis.get("decision") or {}
    machine_rating = decision.get("rating")
    machine_action = _ACTION.get(machine_rating)
    ai = analysis.get("ai") if isinstance(analysis.get("ai"), dict) else None
    ai_rating = ai.get("rating") if ai else None
    roles = analysis.get("roles") or []
    available = [role for role in roles if role.get("available")]
    missing = [role.get("name", role.get("key", "Peran")) for role in roles
               if not role.get("available")]
    proxies = [role.get("name", role.get("key", "Peran")) for role in available
               if role.get("source") == "proxy"]
    warnings = [f"{role.get('name', role.get('key', 'Peran'))}: {role['warning']}"
                for role in roles if role.get("warning")]
    indicators = analysis.get("indicators") or {}
    sessions = int(indicators.get("sessions") or 0)
    range_ = analysis.get("range")
    holding_days = int(analysis.get("holding_days") or 5)
    timeframe_mismatch = range_ in _INTRADAY and holding_days > 1
    if range_ in _DAILY and sessions < 60:
        timeframe_mismatch = True
    if range_ is None and sessions < 60:
        timeframe_mismatch = True
    try:
        as_of_date = date.fromisoformat(str(analysis.get("as_of") or ""))
    except ValueError:
        as_of_date = None
    today = datetime.now(WIB).date()
    staleness_days = (today - as_of_date).days if as_of_date else None
    stale_snapshot = as_of_date is None or staleness_days < 0 or staleness_days > 7

    technical = next((role for role in roles if role.get("key") == "market"), None)
    technical_signal = None
    if technical:
        last = _number(indicators.get("last"))
        sma20 = _number(indicators.get("sma20"))
        sma50 = _number(indicators.get("sma50"))
        if last is not None and sma20 is not None and sma50 is not None:
            trend = "naik" if last > sma20 > sma50 else "turun" if last < sma20 < sma50 else "campuran"
        else:
            trend = "belum cukup data"
        score = _number(technical.get("score")) or 0.0
        technical_signal = {
            "label": _technical_bucket(score),
            "strength": "kuat" if abs(score) >= 1.0 else "sedang" if abs(score) >= 0.5 else "lemah",
            "trend": trend,
            "score": technical.get("score"),
            "available": bool(technical.get("available")),
            "supporting": (technical.get("bull") or [])[:3],
            "opposing": (technical.get("bear") or [])[:3],
        }

    plan = analysis.get("trader") or {}
    atr_pct = _number(indicators.get("atr_pct"))
    reward_risk = _number(plan.get("reward_risk"))
    high_risk = bool((atr_pct is not None and atr_pct > 7.0)
                     or (reward_risk is not None and reward_risk < 1.0))
    risk_flags = []
    if atr_pct is not None and atr_pct > 7.0:
        risk_flags.append(f"ATR {atr_pct:.2f}% melewati batas volatilitas pemindai 7%")
    if reward_risk is not None and reward_risk < 1.0:
        risk_flags.append(f"R:R {reward_risk:.2f}:1 kurang dari risiko 1 banding 1")

    conflicts = []
    if machine_rating in _BUCKET and ai_rating in _BUCKET and _BUCKET[ai_rating] != _BUCKET[machine_rating]:
        conflicts.append({
            "type": "gabungan_vs_ai", "machine": machine_rating, "ai": ai_rating,
            "explanation": f"Rating gabungan mesin {machine_rating} ({_BUCKET[machine_rating]}) berbeda dari AI {ai_rating} ({_BUCKET[ai_rating]}).",
        })
    if machine_rating in _BUCKET and technical_signal and technical_signal["available"] and _BUCKET[machine_rating] != technical_signal["label"]:
        conflicts.append({
            "type": "gabungan_vs_teknikal", "machine": machine_rating,
            "technical": technical_signal["label"],
            "explanation": f"Rating gabungan {machine_rating} ({_BUCKET[machine_rating]}) berbeda dari skor teknikal ({technical_signal['label']}).",
        })
    if ai_rating in _BUCKET and technical_signal and technical_signal["available"] and _BUCKET[ai_rating] != technical_signal["label"]:
        conflicts.append({
            "type": "teknikal_vs_ai", "technical": technical_signal["label"], "ai": ai_rating,
            "explanation": f"Sinyal teknikal ({technical_signal['label']}) berbeda dari rating AI {ai_rating} ({_BUCKET[ai_rating]}).",
        })
    trend_bucket = {"naik": "positif", "turun": "negatif"}.get((technical_signal or {}).get("trend"))
    machine_bucket = _BUCKET.get(machine_rating)
    if trend_bucket and machine_bucket and technical_signal and technical_signal["available"] and trend_bucket != machine_bucket:
        conflicts.append({
            "type": "tren_vs_gabungan", "trend": technical_signal["trend"], "machine": machine_rating,
            "explanation": f"Arah tren harga ({technical_signal['trend']}, dari harga terhadap SMA20/SMA50) berlawanan dengan arah gabungan {machine_rating} ({machine_bucket}).",
        })

    insufficient = len(available) < _MIN_ROLES or sessions < _MIN_SESSIONS or not machine_action
    insufficient = insufficient or stale_snapshot
    if insufficient:
        action = "Data belum cukup"
        status = "data_tidak_cukup"
        why_parts = []
        if len(available) < _MIN_ROLES:
            why_parts.append(f"baru {len(available)} dari {len(roles)} peran memiliki data; kebijakan ringkasan memerlukan minimal {_MIN_ROLES}")
        if sessions < _MIN_SESSIONS:
            why_parts.append(f"riwayat baru {sessions} sesi; dibutuhkan minimal {_MIN_SESSIONS} untuk indikator dasar")
        if not machine_action:
            why_parts.append("rating mesin tidak dikenali")
        if stale_snapshot:
            why_parts.append(f"tanggal data {as_of_date.isoformat() if as_of_date else 'tidak tercatat'} berjarak {staleness_days if staleness_days is not None else 'tidak diketahui'} hari; ringkasan hanya memakai snapshot maksimal 7 hari")
        why = ". ".join(why_parts) + "."
    elif timeframe_mismatch:
        action = "Tunggu"
        status = "rentang_tidak_selaras"
        if range_ in _INTRADAY:
            why = (f"Data analisis memakai rentang intrahari ({range_}), sedangkan horizon keputusan "
                   f"adalah {holding_days} sesi bursa. Gunakan rentang harian atau evaluasi ulang; "
                   "sistem tidak menganggap timeframe ini setara.")
        else:
            why = f"Rentang harian yang dipakai hanya memuat {sessions} sesi, terlalu singkat untuk menilai horizon {holding_days} sesi secara memadai."
    elif conflicts:
        action = "Tunggu"
        status = "sinyal_berbeda"
        why = " ".join(item["explanation"] for item in conflicts) + " Konflik ini tidak diputuskan dengan mengutamakan salah satu pihak."
    elif high_risk and machine_action == "Beli":
        action = "Tunggu"
        status = "risiko_tinggi"
        why = "Sinyal mesin mengarah ke Beli, tetapi risiko terukur pada data yang tersedia melewati batas kehati-hatian: " + "; ".join(risk_flags) + "."
    else:
        action = machine_action
        status = "selaras" if ai_rating else ("ai_gagal" if analysis.get("ai_error") else "mesin_saja")
        if ai_rating:
            why = f"Rating gabungan mesin {machine_rating} dan AI {ai_rating} berada pada arah yang sama ({_BUCKET[machine_rating]})."
        elif analysis.get("ai_error"):
            why = f"AI tidak tersedia ({analysis['ai_error']}); label mengikuti rating mesin {machine_rating} saja."
        else:
            why = f"Belum ada analisis AI. Label mengikuti rating mesin {machine_rating} saja."

    if action == "Beli":
        next_action = "Pertimbangkan hanya setelah memeriksa level mesin, risiko, data sumber, dan simulasi. Ini bukan perintah transaksi."
    elif action == "Hindari":
        next_action = "Jangan membuka posisi beli hanya berdasarkan snapshot ini. Tinjau ulang jika data atau sinyal berubah."
    elif status == "sinyal_berbeda":
        conditions = (ai or {}).get("what_would_change") or []
        next_action = "Tunggu; jangan paksa sinyal menjadi searah." + (" Kondisi yang disebut model: " + "; ".join(conditions[:2]) if conditions else " Periksa kembali setelah ada data atau sinyal baru.")
    elif status == "rentang_tidak_selaras":
        next_action = "Tunggu atau jalankan ulang dengan rentang harian yang sesuai dengan horizon keputusan."
    elif status == "risiko_tinggi":
        next_action = "Jangan memaksa transaksi. Tinjau batas risiko dan level mesin; pertimbangkan ulang hanya jika risiko atau sinyal berubah."
    elif status == "data_tidak_cukup":
        next_action = "Tunggu sampai sumber/riwayat yang dibutuhkan tersedia; kekurangan data bukan sinyal netral."
    else:
        next_action = "Tinjau alasan, risiko, dan level mesin sebelum mengambil keputusan sendiri."

    risk = {
        "flags": risk_flags,
        "high_risk": high_risk,
        "risk_per_share": plan.get("risk_per_share"),
        "target_distance_pct": (round((plan.get("target") / plan.get("entry") - 1) * 100, 2)
                                if plan.get("entry") and plan.get("target") else None),
        "stop_distance_pct": (round((plan.get("stop") / plan.get("entry") - 1) * 100, 2)
                              if plan.get("entry") and plan.get("stop") else None),
        "reward_risk": plan.get("reward_risk"),
        "atr_pct": indicators.get("atr_pct"),
        "max_drawdown_pct": indicators.get("max_drawdown_pct"),
        "supporting": [text for role in roles if role.get("available") for text in (role.get("bull") or [])][:3],
        "opposing": [text for role in roles if role.get("available") for text in (role.get("bear") or [])][:3],
        "ai_risks": ((ai or {}).get("risks") or [])[:3],
    }
    rr = _number(risk["reward_risk"])
    if rr is not None:
        risk["explanation"] = f"R:R {rr:.2f}:1 membandingkan jarak target dengan jarak stop menurut rencana mesin; ini bukan peluang berhasil."
    else:
        risk["explanation"] = "R:R belum dapat dihitung dari level mesin; jangan menebak nilai yang hilang."

    confidence = _number(decision.get("confidence"))
    ai_conviction = _number((ai or {}).get("conviction"))
    return {
        "policy": "Pemetaan deterministik, bukan model probabilitas: Buy/Overweight→Beli, Hold→Tunggu, Underweight/Sell→Hindari. Pengaman ringkasan: minimal 3 peran tersedia dan 20 sesi; snapshot maksimal 7 hari; rentang intrahari tidak dipakai untuk horizon >1 sesi; rentang harian di bawah 60 sesi ditandai tidak selaras; konflik arah atau ATR di atas 7%/R:R di bawah 1:1 membuat rekomendasi Beli menjadi Tunggu. Ambang ini aturan kehati-hatian yang terlihat, bukan bukti strategi terkalibrasi. Bukan nasihat investasi.",
        "action": action,
        "status": status,
        "why": why,
        "next_action": next_action,
        "machine_rating": machine_rating,
        "as_of": as_of_date.isoformat() if as_of_date else None,
        "staleness_days": staleness_days,
        "stale_snapshot": stale_snapshot,
        "machine_action": machine_action,
        "machine_composite": (analysis.get("manager") or {}).get("composite"),
        "machine_confidence": confidence,
        "machine_confidence_note": "Keyakinan mekanis adalah skor heuristik internal, bukan probabilitas keuntungan.",
        "ai_rating": ai_rating,
        "ai_action": _ACTION.get(ai_rating),
        "ai_conviction": ai_conviction,
        "ai_confidence_note": "Keyakinan model bukan probabilitas profit dan belum terkalibrasi.",
        "ai_error": analysis.get("ai_error"),
        "technical": technical_signal,
        "conflicts": conflicts,
        "data_quality": {
            "available_roles": len(available), "total_roles": len(roles),
            "sessions": sessions, "range": range_, "holding_days": holding_days,
            "timeframe_mismatch": timeframe_mismatch,
            "as_of": as_of_date.isoformat() if as_of_date else None,
            "staleness_days": staleness_days,
            "stale_snapshot": stale_snapshot,
            "missing_roles": missing, "proxy_roles": proxies, "warnings": warnings,
            "status": "cukup" if not insufficient else "terbatas",
            "freshness_status": "stale" if stale_snapshot else "current",
            "policy_min_available_roles": _MIN_ROLES,
            "policy_min_sessions": _MIN_SESSIONS,
            "policy_max_snapshot_age_days": 7,
        },
        "risk": risk,
    }


def simulate(entry, stop, target, lots, capital, fee_rate_pct=None) -> dict:
    """Estimate a round-trip scenario from explicit user size and machine levels.

    ``fee_rate_pct`` is a user-entered one-side rate; it is applied separately
    to entry+exit notionals. If omitted, estimates are gross and fees are not
    implied to be zero in the real market.
    """
    entry, stop, target = (_number(entry), _number(stop), _number(target))
    capital = _number(capital)
    lots_n = _number(lots)
    fee_rate = None if fee_rate_pct in (None, "") else _number(fee_rate_pct)
    if any(value is None or value <= 0 for value in (entry, stop, target)):
        raise ValueError("entry, stop, dan target harus berupa harga positif dari rencana mesin")
    if capital is None or capital <= 0:
        raise ValueError("modal simulasi harus lebih besar dari nol")
    if lots_n is None or lots_n <= 0 or not lots_n.is_integer():
        raise ValueError("jumlah lot harus bilangan bulat positif")
    if fee_rate_pct not in (None, "") and (fee_rate is None or fee_rate < 0 or fee_rate > 100):
        raise ValueError("biaya per sisi harus antara 0 dan 100 persen")

    direction = "long" if target > entry else "short" if target < entry else None
    if direction == "long" and stop >= entry:
        raise ValueError("untuk skenario beli, stop harus di bawah entry dan target di atas entry")
    if direction == "short" and stop <= entry:
        raise ValueError("untuk skenario turun, stop harus di atas entry dan target di bawah entry")
    if direction is None:
        raise ValueError("target harus berbeda dari entry")

    quantity = int(lots_n) * 100
    sign = 1 if direction == "long" else -1
    gross_target = (target - entry) * quantity * sign
    gross_stop = (stop - entry) * quantity * sign
    target_fees = ((entry + target) * quantity * fee_rate / 100) if fee_rate is not None else 0.0
    stop_fees = ((entry + stop) * quantity * fee_rate / 100) if fee_rate is not None else 0.0
    net_target, net_stop = gross_target - target_fees, gross_stop - stop_fees
    stop_risk = abs(net_stop)
    notional = entry * quantity
    return {
        "direction": direction,
        "quantity": quantity,
        "lots": int(lots_n),
        "entry": entry, "stop": stop, "target": target,
        "entry_notional": round(notional, 2),
        "capital": round(capital, 2),
        "capital_share_pct": round(notional / capital * 100, 2),
        "capital_covers_entry": notional <= capital if direction == "long" else None,
        "gross_target_pnl": round(gross_target, 2),
        "gross_stop_pnl": round(gross_stop, 2),
        "fee_rate_per_side_pct": fee_rate,
        "target_round_trip_fees": round(target_fees, 2),
        "stop_round_trip_fees": round(stop_fees, 2),
        "fees_included": fee_rate is not None,
        "net_target_pnl": round(net_target, 2),
        "net_stop_pnl": round(net_stop, 2),
        "net_reward_risk": round(net_target / stop_risk, 2) if stop_risk else None,
        "short_availability_verified": False if direction == "short" else None,
        "disclaimer": "Estimasi skenario kotor/neto berdasarkan level mesin dan input pengguna. Tidak memasukkan slippage, pajak, atau biaya yang tidak diinput; hasil nyata dapat berbeda.",
    }


__all__ = ["build"]
