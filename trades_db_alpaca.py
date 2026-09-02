"""
trades_db_alpaca.py — SQLite database exclusively for BARCO-Alpaca.

Database: trades_alpaca.db  — never writes to trades.db (BARCO-Binance)
"""

import sqlite3
import os
from datetime import datetime

# Exclusive DB for BARCO-Alpaca — separate from BARCO-Binance (trades.db)
DB_PATH = os.environ.get("ALPACA_DB_PATH", "/root/trading_agent_alpaca/trades_alpaca.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            side        TEXT    NOT NULL,
            qty         REAL    NOT NULL,
            price       REAL    NOT NULL,
            order_id    TEXT,
            pnl         REAL    DEFAULT 0,
            trade_type  TEXT    DEFAULT 'grid',  -- grid | option
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL UNIQUE,
            qty         REAL    NOT NULL,
            avg_entry   REAL    NOT NULL,
            side        TEXT    DEFAULT 'long',
            pos_type    TEXT    DEFAULT 'stock',  -- stock | crypto | option
            updated_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS claude_decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            action      TEXT    NOT NULL,
            symbol      TEXT,
            strike      REAL,
            expiry      TEXT,
            contracts   INTEGER,
            premium     REAL,
            reason      TEXT,
            executed    INTEGER DEFAULT 0,
            order_id    TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date        TEXT    PRIMARY KEY,
            pnl         REAL    DEFAULT 0,
            trades      INTEGER DEFAULT 0,
            decisions   INTEGER DEFAULT 0
        );
        """)


def log_trade(symbol: str, side: str, qty: float, price: float,
              order_id: str = None, pnl: float = 0, trade_type: str = "grid"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (symbol, side, qty, price, order_id, pnl, trade_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, side, qty, price, order_id, pnl, trade_type)
        )


def log_claude_decision(decision: dict, executed: bool = False, order_id: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO claude_decisions "
            "(action, symbol, strike, expiry, contracts, premium, reason, executed, order_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.get("action"),
                decision.get("symbol"),
                decision.get("strike"),
                decision.get("expiry"),
                decision.get("contracts"),
                decision.get("premium"),
                decision.get("reason"),
                1 if executed else 0,
                order_id,
            )
        )


def get_today_pnl() -> float:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades WHERE created_at LIKE ?",
            (f"{today}%",)
        ).fetchone()
    return row["total"] if row else 0.0


def get_recent_decisions(limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM claude_decisions ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
