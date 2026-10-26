"""SQLite state: the watchlist, every decision the desk has made, and the book.

One file, no server, no migration tool. The decisions table is the reason this
program exists alongside the analysis: a rating with no record of what it was
worth is an opinion, not a track record.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "katalis.db"

SEED = ["BBCA.JK", "BBRI.JK", "TLKM.JK", "ASII.JK", "ANTM.JK", "ADRO.JK", "ICBP.JK", "GOTO.JK"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    symbol   TEXT PRIMARY KEY,
    added_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    trade_date     TEXT NOT NULL,
    rating         TEXT NOT NULL,
    price          REAL,
    benchmark      TEXT,
    holding_days   INTEGER NOT NULL DEFAULT 5,
    summary_json   TEXT,
    sections_json  TEXT,
    ai_rating      TEXT,
    ai_conviction  REAL,
    ai_model       TEXT,
    ai_json        TEXT,
    settled        INTEGER NOT NULL DEFAULT 0,
    exit_date      TEXT,
    exit_price     REAL,
    benchmark_return REAL,
    raw_return     REAL,
    alpha          REAL,
    created_at     INTEGER NOT NULL,
    UNIQUE (symbol, trade_date)
);
CREATE TABLE IF NOT EXISTS portfolio (
    symbol     TEXT PRIMARY KEY,
    quantity   REAL NOT NULL,
    avg_price  REAL NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _migrate(db) -> None:
    """Add the AI columns to a database created before they existed."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(decisions)")}
    for name, kind in (("ai_rating", "TEXT"), ("ai_conviction", "REAL"),
                       ("ai_model", "TEXT"), ("ai_json", "TEXT")):
        if name not in existing:
            db.execute(f"ALTER TABLE decisions ADD COLUMN {name} {kind}")


def init() -> None:
    with _connect() as db:
        db.executescript(SCHEMA)
        _migrate(db)
        empty = db.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"] == 0
        if empty:
            now = int(time.time())
            db.executemany("INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)",
                           [(symbol, now) for symbol in SEED])


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------


def watchlist() -> list[str]:
    with _connect() as db:
        return [row["symbol"] for row in db.execute("SELECT symbol FROM watchlist ORDER BY added_at")]


def add_watch(symbols: list[str]) -> list[str]:
    now = int(time.time())
    with _connect() as db:
        db.executemany("INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)",
                       [(symbol.upper(), now) for symbol in symbols if symbol])
    return watchlist()


def remove_watch(symbol: str) -> list[str]:
    with _connect() as db:
        db.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
    return watchlist()


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def record(symbol: str, trade_date: str, rating: str, price: float | None,
           benchmark: str, holding_days: int, summary: dict, sections: dict,
           ai: dict | None = None) -> int | None:
    """Store one decision per (symbol, date); a repeat of the same day is not a new call.

    A same-day repeat may still attach the AI reading to the row it already has,
    but only when that row has none — the first opinion of the day is the one
    kept, so a later refresh cannot quietly rewrite what was recorded. A settled
    row is never touched.
    """
    ai = ai or {}
    ai_values = (ai.get("rating"), ai.get("conviction"), ai.get("model"),
                 json.dumps(ai, ensure_ascii=False) if ai else None)
    with _connect() as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO decisions
               (symbol, trade_date, rating, price, benchmark, holding_days,
                summary_json, sections_json, ai_rating, ai_conviction, ai_model, ai_json,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol.upper(), trade_date, rating, price, benchmark, holding_days,
             json.dumps(summary, ensure_ascii=False), json.dumps(sections, ensure_ascii=False),
             *ai_values, int(time.time())),
        )
        if cursor.rowcount:
            return cursor.lastrowid
        if ai:
            db.execute(
                """UPDATE decisions
                   SET ai_rating = ?, ai_conviction = ?, ai_model = ?, ai_json = ?
                   WHERE symbol = ? AND trade_date = ? AND settled = 0 AND ai_rating IS NULL""",
                (*ai_values, symbol.upper(), trade_date),
            )
        return None


def pending() -> list[dict]:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM decisions WHERE settled = 0 ORDER BY trade_date, id"
        ).fetchall()
    return [dict(row) for row in rows]


def settle(decision_id: int, exit_date: str, exit_price: float,
           benchmark_return: float, raw_return: float, alpha: float) -> None:
    with _connect() as db:
        db.execute(
            """UPDATE decisions
               SET settled = 1, exit_date = ?, exit_price = ?, benchmark_return = ?,
                   raw_return = ?, alpha = ?
               WHERE id = ? AND settled = 0""",
            (exit_date, exit_price, benchmark_return, raw_return, alpha, decision_id),
        )


def journal(limit: int = 100) -> list[dict]:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM decisions ORDER BY trade_date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        entry = dict(row)
        entry["summary"] = json.loads(entry.pop("summary_json") or "{}")
        entry.pop("sections_json", None)
        entry.pop("ai_json", None)          # only the detail endpoint carries the full text
        out.append(entry)
    return out


def decision(decision_id: int) -> dict | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        return None
    entry = dict(row)
    entry["summary"] = json.loads(entry.pop("summary_json") or "{}")
    entry["sections"] = json.loads(entry.pop("sections_json") or "{}")
    entry["ai"] = json.loads(entry.pop("ai_json") or "null") if row["ai_json"] else None
    return entry


_DIRECTION = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}


def _score_rows(rows, key: str) -> dict:
    """Hit rate and mean alpha per rating, for whichever rating column ``key`` names.

    A rating that claimed a direction is scored on whether alpha went that way.
    Hold claims none, so it gets no hit rate rather than a free win.
    """
    grouped: dict[str, list[float]] = {}
    for row in rows:
        rating = row[key]
        if rating:
            grouped.setdefault(rating, []).append(row["alpha"])

    scores = {}
    for rating, alphas in grouped.items():
        side = _DIRECTION.get(rating, 0)
        scores[rating] = {
            "count": len(alphas),
            "hit_rate": (sum(1 for a in alphas if a * side > 0) / len(alphas)) if side else None,
            "mean_alpha": sum(alphas) / len(alphas),
        }
    return scores


def stats() -> dict:
    """The record of the desk and of the AI, side by side.

    Both ratings are scored against the same settled outcomes, which is the only
    way to answer whether the model added anything over the mechanical reading.
    """
    with _connect() as db:
        rows = db.execute(
            "SELECT rating, ai_rating, alpha FROM decisions "
            "WHERE settled = 1 AND alpha IS NOT NULL"
        ).fetchall()
        pending_count = db.execute(
            "SELECT COUNT(*) AS n FROM decisions WHERE settled = 0"
        ).fetchone()["n"]

    by_rating = _score_rows(rows, "rating")
    by_rating_ai = _score_rows(rows, "ai_rating")
    return {
        "settled": sum(score["count"] for score in by_rating.values()),
        "pending": pending_count,
        "by_rating": by_rating,
        "by_rating_ai": by_rating_ai,
        "ai_settled": sum(score["count"] for score in by_rating_ai.values()),
    }


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


def positions() -> list[dict]:
    with _connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM portfolio ORDER BY symbol")]


def set_position(symbol: str, quantity: float, avg_price: float) -> list[dict]:
    with _connect() as db:
        if quantity <= 0 or avg_price <= 0:
            db.execute("DELETE FROM portfolio WHERE symbol = ?", (symbol.upper(),))
        else:
            db.execute(
                """INSERT INTO portfolio (symbol, quantity, avg_price, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                       quantity = excluded.quantity,
                       avg_price = excluded.avg_price,
                       updated_at = excluded.updated_at""",
                (symbol.upper(), quantity, avg_price, int(time.time())),
            )
    return positions()


def remove_position(symbol: str) -> list[dict]:
    with _connect() as db:
        db.execute("DELETE FROM portfolio WHERE symbol = ?", (symbol.upper(),))
    return positions()


def cash() -> float:
    """Cash is whatever the caller last declared; a missing row means unset, not zero."""
    with _connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key = 'cash'").fetchone()
    return float(row["value"]) if row else 0.0


def set_cash(value: float) -> float:
    with _connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO meta (key, value) VALUES ('cash', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(float(value)),))
    return float(value)
