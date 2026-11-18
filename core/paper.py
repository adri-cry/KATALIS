"""Paper-trading ledger — deliberately separate from the real portfolio.

Cron runs daily at 07:15 / 09:00 / 16:05 WIB. Buying and selling here only
touch these tables, never ``store.portfolio`` or ``store.cash``. Every fill is
recorded with its reason and mode so a "belajar" minimum-size trade can be
measured apart from a rule-driven one.

Nothing here places an order with a broker. This is a learning ledger.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import market, store

WIB = timezone(timedelta(hours=7))
DEFAULT_CAPITAL = 10_000_000.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_cash (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol     TEXT PRIMARY KEY,
    quantity   REAL NOT NULL,
    avg_price  REAL NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    side       TEXT NOT NULL,
    quantity   REAL NOT NULL,
    price      REAL NOT NULL,
    value      REAL NOT NULL,
    reason     TEXT,
    mode       TEXT NOT NULL DEFAULT 'aturan',
    pnl        REAL,
    created_at INTEGER NOT NULL
);
"""


def init() -> None:
    with store._connect() as db:
        db.executescript(SCHEMA)
        db.execute("INSERT OR IGNORE INTO paper_cash (key, value) VALUES ('cash', ?)",
                   (DEFAULT_CAPITAL,))


def cash() -> float:
    with store._connect() as db:
        row = db.execute("SELECT value FROM paper_cash WHERE key = 'cash'").fetchone()
    return float(row["value"]) if row else DEFAULT_CAPITAL


def set_cash(value: float) -> float:
    with store._connect() as db:
        db.execute("INSERT INTO paper_cash (key, value) VALUES ('cash', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (float(value),))
    return float(value)


def positions() -> list[dict]:
    with store._connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM paper_positions ORDER BY symbol")]


def state() -> dict:
    rows = positions()
    priced = []
    for row in rows:
        price = market.last_price(row["symbol"], row["avg_price"])
        value = price * row["quantity"]
        cost = row["avg_price"] * row["quantity"]
        priced.append({**row, "price": price, "value": value, "cost": cost,
                       "pnl": value - cost,
                       "pnl_pct": (value / cost - 1) if cost else None})
    cash_now = cash()
    market_value = sum(row["value"] for row in priced)
    cost_total = sum(row["cost"] for row in priced)
    return {
        "cash": cash_now, "positions": priced, "market_value": market_value,
        "cost": cost_total, "pnl": market_value - cost_total,
        "equity": cash_now + market_value,
        "exposure_pct": (market_value / (cash_now + market_value) * 100)
                        if (cash_now + market_value) else 0.0,
    }


def _record(db, side: str, symbol: str, quantity: float, price: float,
            reason: str, mode: str, pnl: float | None) -> None:
    db.execute(
        """INSERT INTO paper_trades
           (trade_date, symbol, side, quantity, price, value, reason, mode, pnl, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(WIB).strftime("%Y-%m-%d"), symbol.upper(), side, quantity, price,
         round(quantity * price, 2), str(reason)[:300], mode,
         None if pnl is None else round(pnl, 2), int(time.time())),
    )


def buy(symbol: str, quantity: float, price: float, reason: str = "",
        mode: str = "aturan") -> dict:
    """Open or add to a position. Raises when cash cannot cover the notional."""
    symbol = market.normalise(str(symbol or ""))
    quantity, price = float(quantity), float(price)
    if not symbol or quantity <= 0 or price <= 0:
        raise ValueError("Simbol, jumlah, dan harga harus valid")
    value = quantity * price
    balance = cash() - value
    if balance < 0:
        raise ValueError("Kas paper tidak cukup untuk pembelian ini")
    with store._connect() as db:
        row = db.execute("SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
        if row:
            total = row["quantity"] + quantity
            average = (row["quantity"] * row["avg_price"] + value) / total
            db.execute("UPDATE paper_positions SET quantity = ?, avg_price = ?, updated_at = ? "
                       "WHERE symbol = ?", (total, average, int(time.time()), symbol))
        else:
            db.execute("INSERT INTO paper_positions (symbol, quantity, avg_price, updated_at) "
                       "VALUES (?, ?, ?, ?)", (symbol, quantity, price, int(time.time())))
        db.execute("UPDATE paper_cash SET value = ? WHERE key = 'cash'", (balance,))
        _record(db, "beli", symbol, quantity, price, reason, mode, None)
    return {"symbol": symbol, "quantity": quantity, "price": price, "value": value, "cash": balance}


def sell(symbol: str, quantity: float, price: float, reason: str = "",
         mode: str = "aturan") -> dict:
    """Close all or part of a position; realised P/L uses its recorded average."""
    symbol = market.normalise(str(symbol or ""))
    quantity, price = float(quantity), float(price)
    if not symbol or quantity <= 0 or price <= 0:
        raise ValueError("Simbol, jumlah, dan harga harus valid")
    with store._connect() as db:
        row = db.execute("SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
        if row is None or row["quantity"] + 1e-9 < quantity:
            raise ValueError(f"Tidak ada posisi paper {symbol} sebesar itu")
        balance = cash() + quantity * price
        pnl = (price - row["avg_price"]) * quantity
        remaining = row["quantity"] - quantity
        if remaining <= 1e-9:
            db.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
        else:
            db.execute("UPDATE paper_positions SET quantity = ?, updated_at = ? WHERE symbol = ?",
                       (remaining, int(time.time()), symbol))
        db.execute("UPDATE paper_cash SET value = ? WHERE key = 'cash'", (balance,))
        _record(db, "jual", symbol, quantity, price, reason, mode, pnl)
    return {"symbol": symbol, "quantity": quantity, "price": price, "pnl": pnl, "cash": balance}


def trades(limit: int = 100) -> list[dict]:
    with store._connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,))]


__all__ = ["init", "cash", "set_cash", "positions", "state", "buy", "sell", "trades",
           "DEFAULT_CAPITAL"]
