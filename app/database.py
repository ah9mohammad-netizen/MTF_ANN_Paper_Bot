"""
Database engine for XAU-USDT Scalping Bot.
Persists signals, trades, and account balance history in history.db
(mounted at /data/history.db on the Railway Volume).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import config

logger = logging.getLogger("DatabaseEngine")


class DatabaseEngine:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            if config.DATABASE_URL.startswith("sqlite:///"):
                self.db_path = config.DATABASE_URL.replace("sqlite:///", "", 1)
            else:
                self.db_path = config.DB_PATH
        else:
            self.db_path = db_path

        # Normalize legacy casing if operator used History.db
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self) -> None:
        """Ensure directory for history.db exists (e.g. /data on Railway Volume)."""
        abs_path = os.path.abspath(self.db_path)
        dir_name = os.path.dirname(abs_path)
        if dir_name and not os.path.exists(dir_name):
            try:
                os.makedirs(dir_name, exist_ok=True)
                logger.info("Created database directory path: %s", dir_name)
            except PermissionError:
                fallback = os.path.abspath("history.db")
                logger.warning(
                    "Permission denied creating %s. Falling back to %s",
                    dir_name,
                    fallback,
                )
                self.db_path = fallback

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        """Create tables for signals, trades, and account balance history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    sl_price REAL NOT NULL,
                    tp1_price REAL NOT NULL,
                    tp2_price REAL NOT NULL,
                    size_oz REAL,
                    leverage INTEGER,
                    dollar_risk REAL,
                    layer1_regime TEXT,
                    layer2_structure TEXT,
                    layer3_momentum TEXT,
                    status TEXT NOT NULL,
                    reason TEXT
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    sl_price REAL NOT NULL,
                    tp1_price REAL NOT NULL,
                    tp2_price REAL NOT NULL,
                    size_oz REAL NOT NULL,
                    leverage INTEGER NOT NULL,
                    required_margin_usd REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    exit_price REAL,
                    pnl_usd REAL DEFAULT 0.0,
                    pnl_pct REAL DEFAULT 0.0,
                    exit_reason TEXT,
                    status TEXT NOT NULL,
                    FOREIGN KEY (signal_id) REFERENCES signals (id)
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS account_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    balance_before REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    change_usd REAL NOT NULL,
                    change_reason TEXT NOT NULL,
                    trade_id INTEGER,
                    FOREIGN KEY (trade_id) REFERENCES trades (id)
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # Seed starting balance ($100 USDT) once
            cursor.execute("SELECT COUNT(*) FROM account_history")
            count = cursor.fetchone()[0]
            if count == 0:
                now_str = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT INTO account_history
                        (timestamp, balance_before, balance_after, change_usd, change_reason, trade_id)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        now_str,
                        0.0,
                        config.INITIAL_BALANCE_USDT,
                        config.INITIAL_BALANCE_USDT,
                        "INITIAL_DEPOSIT",
                    ),
                )
                logger.info(
                    "Initialized paper trading account with $%.2f USDT in %s",
                    config.INITIAL_BALANCE_USDT,
                    self.db_path,
                )

            conn.commit()

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------
    def get_current_balance(self) -> float:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT balance_after FROM account_history ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return float(row["balance_after"]) if row else float(config.INITIAL_BALANCE_USDT)

    def get_daily_realized_pnl(self) -> float:
        """Sum of closed-trade PnL for the current UTC day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(SUM(pnl_usd), 0.0) AS day_pnl
                FROM trades
                WHERE status = 'CLOSED' AND closed_at LIKE ?
                """,
                (f"{today}%",),
            )
            row = cursor.fetchone()
            return float(row["day_pnl"] or 0.0)

    def count_trades_opened_today(self) -> int:
        """Count trades opened on the current UTC calendar day (open + closed)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM trades WHERE opened_at LIKE ?",
                (f"{today}%",),
            )
            row = cursor.fetchone()
            return int(row["cnt"] or 0)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    def save_signal(self, signal_data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO signals (
                    timestamp, symbol, direction, entry_price, sl_price, tp1_price, tp2_price,
                    size_oz, leverage, dollar_risk,
                    layer1_regime, layer2_structure, layer3_momentum, status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_data["timestamp"],
                    signal_data["symbol"],
                    signal_data["direction"],
                    signal_data["entry_price"],
                    signal_data["sl_price"],
                    signal_data["tp1_price"],
                    signal_data["tp2_price"],
                    signal_data.get("size_oz"),
                    signal_data.get("leverage"),
                    signal_data.get("dollar_risk"),
                    signal_data.get("layer1_regime", "PASSED"),
                    signal_data.get("layer2_structure", "STRUCTURAL_BREAK"),
                    signal_data.get("layer3_momentum", "MOMENTUM_OK"),
                    signal_data.get("status", "NEW"),
                    signal_data.get("reason", signal_data.get("layer2_structure", "")),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_signal_status(self, signal_id: int, status: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE signals SET status = ? WHERE id = ?", (status, signal_id)
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    def open_trade(self, trade_data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trades (
                    signal_id, symbol, direction, entry_price, sl_price, tp1_price, tp2_price,
                    size_oz, leverage, required_margin_usd, opened_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
                """,
                (
                    trade_data.get("signal_id"),
                    trade_data["symbol"],
                    trade_data["direction"],
                    trade_data["entry_price"],
                    trade_data["sl_price"],
                    trade_data["tp1_price"],
                    trade_data["tp2_price"],
                    trade_data["size_oz"],
                    trade_data["leverage"],
                    trade_data["required_margin_usd"],
                    trade_data["opened_at"],
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_trade_sl(self, trade_id: int, new_sl: float) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE trades SET sl_price = ? WHERE id = ? AND status = 'OPEN'",
                (new_sl, trade_id),
            )
            conn.commit()

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        exit_reason: str,
    ) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        current_bal = self.get_current_balance()
        new_bal = max(0.0, current_bal + pnl_usd)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE trades
                SET closed_at = ?, exit_price = ?, pnl_usd = ?, pnl_pct = ?,
                    exit_reason = ?, status = 'CLOSED'
                WHERE id = ? AND status = 'OPEN'
                """,
                (now_str, exit_price, pnl_usd, pnl_pct, exit_reason, trade_id),
            )
            if cursor.rowcount == 0:
                conn.commit()
                return

            cursor.execute(
                """
                INSERT INTO account_history
                    (timestamp, balance_before, balance_after, change_usd, change_reason, trade_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now_str, current_bal, new_bal, pnl_usd, exit_reason, trade_id),
            )
            conn.commit()

        logger.info(
            "Trade #%s closed [%s] | PnL: $%.2f (%+.2f%%) | New Balance: $%.2f USDT",
            trade_id,
            exit_reason,
            pnl_usd,
            pnl_pct,
            new_bal,
        )

    def get_open_trades(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_signals(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_trades(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(pnl_usd), 0.0) AS total_pnl
                FROM trades WHERE status = 'CLOSED'
                """
            )
            row = cursor.fetchone()
            total_trades = int(row["total"] or 0)
            total_pnl = float(row["total_pnl"] or 0.0)

            cursor.execute(
                "SELECT COUNT(*) AS wins FROM trades WHERE status = 'CLOSED' AND pnl_usd > 0"
            )
            wins = int(cursor.fetchone()["wins"] or 0)

            cursor.execute(
                "SELECT COUNT(*) AS losses FROM trades WHERE status = 'CLOSED' AND pnl_usd < 0"
            )
            losses = int(cursor.fetchone()["losses"] or 0)

            cursor.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END), 0.0) AS gp,
                       COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END), 0.0) AS gl
                FROM trades WHERE status = 'CLOSED'
                """
            )
            pf_row = cursor.fetchone()
            gross_profit = float(pf_row["gp"] or 0.0)
            gross_loss = float(pf_row["gl"] or 0.0)
            profit_factor = (
                round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
            )

            win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

            cursor.execute(
                """
                SELECT MAX(pnl_usd) AS best, MIN(pnl_usd) AS worst
                FROM trades WHERE status = 'CLOSED'
                """
            )
            extreme_row = cursor.fetchone()
            best_trade = float(extreme_row["best"] or 0.0)
            worst_trade = float(extreme_row["worst"] or 0.0)

            # Exit reason breakdown
            cursor.execute(
                """
                SELECT exit_reason, COUNT(*) AS cnt
                FROM trades WHERE status = 'CLOSED'
                GROUP BY exit_reason
                """
            )
            exit_breakdown = {r["exit_reason"]: int(r["cnt"]) for r in cursor.fetchall()}

            current_bal = self.get_current_balance()
            total_return_pct = (
                (current_bal - config.INITIAL_BALANCE_USDT)
                / config.INITIAL_BALANCE_USDT
            ) * 100.0

            return {
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 2),
                "total_pnl_usd": round(total_pnl, 2),
                "total_return_pct": round(total_return_pct, 2),
                "profit_factor": profit_factor,
                "best_trade_usd": round(best_trade, 2),
                "worst_trade_usd": round(worst_trade, 2),
                "current_balance": round(current_bal, 2),
                "initial_balance": config.INITIAL_BALANCE_USDT,
                "exit_breakdown": exit_breakdown,
                "db_path": self.db_path,
            }

    # ------------------------------------------------------------------
    # Bot state helpers
    # ------------------------------------------------------------------
    def set_state(self, key: str, value: str) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now_str),
            )
            conn.commit()

    def get_state(self, key: str, default: str = "") -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return str(row["value"]) if row else default


# Global DB engine instance
db = DatabaseEngine()
