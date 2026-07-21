"""
Database engine for XAU-USDT Scalping Bot.
Manages Signals, Trades, and Account Balance history inside History.db (saved in /data Volume on Railway).
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from app.config import config

logger = logging.getLogger("DatabaseEngine")

class DatabaseEngine:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            if config.DATABASE_URL.startswith("sqlite:///"):
                self.db_path = config.DATABASE_URL.replace("sqlite:///", "")
            else:
                self.db_path = config.DB_PATH
        else:
            self.db_path = db_path
            
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self):
        """Ensures that the directory containing History.db (e.g. /data on Railway Volume) exists."""
        abs_path = os.path.abspath(self.db_path)
        dir_name = os.path.dirname(abs_path)
        if dir_name and not os.path.exists(dir_name):
            try:
                os.makedirs(dir_name, exist_ok=True)
                logger.info(f"Created database directory path: {dir_name}")
            except PermissionError:
                logger.warning(f"Permission denied creating {dir_name}. Falling back to local ./History.db")
                self.db_path = "History.db"

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.Connection(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Creates tables for signals, trades, and account balance history if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Signals Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                tp1_price REAL NOT NULL,
                tp2_price REAL NOT NULL,
                layer1_regime TEXT,
                layer2_structure TEXT,
                layer3_momentum TEXT,
                status TEXT NOT NULL
            )
            """)
            
            # 2. Trades Table
            cursor.execute("""
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
            """)
            
            # 3. Account History Table
            cursor.execute("""
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
            """)
            
            # Initialize with starting balance ($100 USDT) if account_history is empty
            cursor.execute("SELECT COUNT(*) FROM account_history")
            count = cursor.fetchone()[0]
            if count == 0:
                now_str = datetime.now(timezone.utc).isoformat()
                cursor.execute("""
                INSERT INTO account_history (timestamp, balance_before, balance_after, change_usd, change_reason, trade_id)
                VALUES (?, ?, ?, ?, ?, NULL)
                """, (now_str, 0.0, config.INITIAL_BALANCE_USDT, config.INITIAL_BALANCE_USDT, "INITIAL_DEPOSIT"))
                logger.info(f"Initialized paper trading account balance with ${config.INITIAL_BALANCE_USDT:.2f} USDT in {self.db_path}")
                
            conn.commit()

    def get_current_balance(self) -> float:
        """Returns the latest current USDT balance from account_history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance_after FROM account_history ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            return float(row["balance_after"]) if row else config.INITIAL_BALANCE_USDT

    def save_signal(self, signal_data: Dict[str, Any]) -> int:
        """Saves a generated trading signal and returns its ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO signals (timestamp, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, layer1_regime, layer2_structure, layer3_momentum, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_data["timestamp"],
                signal_data["symbol"],
                signal_data["direction"],
                signal_data["entry_price"],
                signal_data["sl_price"],
                signal_data["tp1_price"],
                signal_data["tp2_price"],
                signal_data.get("layer1_regime", "PASSED"),
                signal_data.get("layer2_structure", "STRUCTURAL_BREAK"),
                signal_data.get("layer3_momentum", "MOMENTUM_OK"),
                signal_data.get("status", "NEW")
            ))
            conn.commit()
            return cursor.lastrowid

    def update_signal_status(self, signal_id: int, status: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))
            conn.commit()

    def open_trade(self, trade_data: Dict[str, Any]) -> int:
        """Records a new open paper trade."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO trades (signal_id, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, size_oz, leverage, required_margin_usd, opened_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """, (
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
                trade_data["opened_at"]
            ))
            conn.commit()
            return cursor.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, pnl_usd: float, pnl_pct: float, exit_reason: str):
        """Closes an open trade and updates the account balance history."""
        now_str = datetime.now(timezone.utc).isoformat()
        current_bal = self.get_current_balance()
        new_bal = current_bal + pnl_usd
        if new_bal < 0:
            new_bal = 0.0
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Update trade
            cursor.execute("""
            UPDATE trades
            SET closed_at = ?, exit_price = ?, pnl_usd = ?, pnl_pct = ?, exit_reason = ?, status = 'CLOSED'
            WHERE id = ?
            """, (now_str, exit_price, pnl_usd, pnl_pct, exit_reason, trade_id))
            
            # Insert balance history record
            cursor.execute("""
            INSERT INTO account_history (timestamp, balance_before, balance_after, change_usd, change_reason, trade_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (now_str, current_bal, new_bal, pnl_usd, exit_reason, trade_id))
            
            conn.commit()
        logger.info(f"Trade #{trade_id} closed [{exit_reason}] | PnL: ${pnl_usd:.2f} ({pnl_pct:+.2f}%) | New Balance: ${new_bal:.2f} USDT")

    def get_open_trades(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_signals(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_trades(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, Any]:
        """Calculates comprehensive trading performance statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total, SUM(pnl_usd) as total_pnl FROM trades WHERE status = 'CLOSED'")
            row = cursor.fetchone()
            total_trades = row["total"] or 0
            total_pnl = row["total_pnl"] or 0.0
            
            cursor.execute("SELECT COUNT(*) as wins FROM trades WHERE status = 'CLOSED' AND pnl_usd > 0")
            wins = cursor.fetchone()["wins"] or 0
            
            cursor.execute("SELECT COUNT(*) as losses FROM trades WHERE status = 'CLOSED' AND pnl_usd < 0")
            losses = cursor.fetchone()["losses"] or 0
            
            win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
            
            cursor.execute("SELECT MAX(pnl_usd) as best, MIN(pnl_usd) as worst FROM trades WHERE status = 'CLOSED'")
            extreme_row = cursor.fetchone()
            best_trade = extreme_row["best"] or 0.0
            worst_trade = extreme_row["worst"] or 0.0
            
            current_bal = self.get_current_balance()
            total_return_pct = ((current_bal - config.INITIAL_BALANCE_USDT) / config.INITIAL_BALANCE_USDT) * 100.0
            
            return {
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 2),
                "total_pnl_usd": round(total_pnl, 2),
                "total_return_pct": round(total_return_pct, 2),
                "best_trade_usd": round(best_trade, 2),
                "worst_trade_usd": round(worst_trade, 2),
                "current_balance": round(current_bal, 2),
                "initial_balance": config.INITIAL_BALANCE_USDT,
                "db_path": self.db_path
            }

# Global DB engine instance
db = DatabaseEngine()
