"""
Strict Real-World Quantitative Backtesting Engine (`app/backtester.py`).
STRICT RULE: NO SYNTHETIC OR RANDOM DATA GENERATION (`NO HistoricalDataGenerator`).
Downloads and backtests EXCLUSIVELY on REAL historical 5-minute OHLCV candles
pulled directly from public exchange APIs (Bybit V5 XAUUSDT Linear & Binance PAXGUSDT) via paginated requests.
"""
import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
from app.config import config
from app.engine import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RealBacktester")

class RealHistoricalDataFetcher:
    """
    Downloads REAL historical 5-minute OHLCV candles from Bybit/Binance public APIs via paginated requests.
    Caches real historical candles into a local SQLite table (`real_historical_klines` in History.db)
    to allow rigorous, zero-fake-data historical backtesting.
    """
    def __init__(self, db_path: str = config.DB_PATH if hasattr(config, "DB_PATH") else "History.db"):
        self.db_path = db_path
        self._init_cache_table()

    def _init_cache_table(self):
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS real_historical_klines (
                    timestamp_ms INTEGER PRIMARY KEY,
                    datetime_utc TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL
                )
                """)
                conn.commit()
        except Exception as e:
            logger.debug(f"Could not init real_historical_klines table: {e}")

    def fetch_real_bybit_klines_paginated(self, target_bars: int = 5000) -> List[Dict[str, Any]]:
        """
        Downloads real historical 5-minute linear klines for XAUUSDT from Bybit V5 API (`limit=1000` per page).
        Stepping backwards from current timestamp.
        """
        logger.info(f"⏳ Downloading up to {target_bars:,} REAL historical 5-minute candles for Bybit XAUUSDT...")
        all_bars = []
        end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        
        while len(all_bars) < target_bars:
            try:
                url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol=XAUUSDT&interval=5&limit=1000&end={end_time_ms}"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json"
                })
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    
                if not data or data.get("retCode") != 0 or not data.get("result", {}).get("list"):
                    logger.warning(f"Bybit API returned no more historical bars or error: {data.get('retMsg') if data else 'None'}")
                    break
                    
                raw_list = data["result"]["list"]
                if not raw_list:
                    break
                    
                for item in raw_list:
                    ts_ms = int(item[0])
                    dt_str = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()
                    all_bars.append({
                        "timestamp_ms": ts_ms,
                        "timestamp": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]) if float(item[5]) > 0 else 1.0,
                        "symbol": "BYBIT_XAUUSDT"
                    })
                    
                # Oldest bar timestamp in this batch is at raw_list[-1][0]
                oldest_ts_ms = int(raw_list[-1][0])
                if oldest_ts_ms >= end_time_ms:
                    break
                end_time_ms = oldest_ts_ms - 1
                time.sleep(0.15)  # Respect Bybit rate limits
            except Exception as e:
                logger.error(f"Error downloading historical batch from Bybit: {e}")
                break
                
        # Remove duplicates and sort chronologically from oldest to newest
        unique_bars = {b["timestamp_ms"]: b for b in all_bars}
        sorted_bars = sorted(unique_bars.values(), key=lambda x: x["timestamp_ms"])
        
        if sorted_bars:
            self._save_to_cache(sorted_bars)
            logger.info(f"✅ Downloaded and cached {len(sorted_bars):,} real Bybit XAUUSDT historical candles.")
        return sorted_bars

    def fetch_real_binance_klines_paginated(self, target_bars: int = 5000) -> List[Dict[str, Any]]:
        """
        Downloads real historical 5-minute klines for PAXGUSDT from Binance API (`limit=1000` per page).
        Used if Bybit linear history is unavailable.
        """
        logger.info(f"⏳ Downloading up to {target_bars:,} REAL historical 5-minute candles for Binance PAXGUSDT...")
        all_bars = []
        end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        
        while len(all_bars) < target_bars:
            try:
                url = f"https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=5m&limit=1000&endTime={end_time_ms}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    
                if not data or not isinstance(data, list) or not data:
                    break
                    
                for item in data:
                    ts_ms = int(item[0])
                    all_bars.append({
                        "timestamp_ms": ts_ms,
                        "timestamp": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]) if float(item[5]) > 0 else 1.0,
                        "symbol": "BINANCE_PAXGUSDT"
                    })
                    
                oldest_ts_ms = int(data[0][0])
                if oldest_ts_ms >= end_time_ms:
                    break
                end_time_ms = oldest_ts_ms - 1
                time.sleep(0.15)
            except Exception as e:
                logger.error(f"Error downloading historical batch from Binance: {e}")
                break
                
        unique_bars = {b["timestamp_ms"]: b for b in all_bars}
        sorted_bars = sorted(unique_bars.values(), key=lambda x: x["timestamp_ms"])
        
        if sorted_bars:
            self._save_to_cache(sorted_bars)
            logger.info(f"✅ Downloaded and cached {len(sorted_bars):,} real Binance PAXGUSDT historical candles.")
        return sorted_bars

    def _save_to_cache(self, bars: List[Dict[str, Any]]):
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.executemany("""
                INSERT OR REPLACE INTO real_historical_klines (timestamp_ms, datetime_utc, symbol, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, [(b["timestamp_ms"], b["timestamp"].isoformat(), b["symbol"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars])
                conn.commit()
        except Exception as e:
            logger.debug(f"Cache save error: {e}")

    def load_from_cache(self) -> List[Dict[str, Any]]:
        """Loads real historical klines stored in SQLite cache."""
        bars = []
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT timestamp_ms, datetime_utc, symbol, open, high, low, close, volume FROM real_historical_klines ORDER BY timestamp_ms ASC")
                for row in cursor.fetchall():
                    bars.append({
                        "timestamp_ms": row[0],
                        "timestamp": datetime.fromisoformat(row[1]),
                        "symbol": row[2],
                        "open": row[3],
                        "high": row[4],
                        "low": row[5],
                        "close": row[6],
                        "volume": row[7]
                    })
        except Exception as e:
            logger.debug(f"Cache load error: {e}")
        return bars

    def get_real_historical_bars(self, target_bars: int = 5000) -> List[Dict[str, Any]]:
        """
        Attempts to download real historical bars from Bybit/Binance.
        If offline, loads from real SQLite database cache.
        If both fail, throws an explicit error and halts. Never generates fake data.
        """
        bars = self.fetch_real_bybit_klines_paginated(target_bars)
        if not bars:
            bars = self.fetch_real_binance_klines_paginated(target_bars)
        if not bars:
            logger.warning("Network download failed or offline. Loading cached real historical bars from database...")
            bars = self.load_from_cache()
            
        if not bars:
            raise RuntimeError(
                "❌ REAL HISTORICAL BACKTEST FAILED: No real historical 5-minute candles could be downloaded from Bybit/Binance API, "
                "and no cached real historical bars exist inside the database (`real_historical_klines`). "
                "STRICT RULE ENFORCED: Synthetic/random data generation is permanently disabled."
            )
            
        return bars


class RealQuantitativeBacktestRunner:
    """
    Runs the exact 4-Layer Decision Engine across REAL historical 5-minute exchange bars.
    Includes exact 0.04% round-trip commission, $0.12/oz slippage friction, and 50 oz lot ceiling.
    """
    def __init__(self, initial_balance: float = 100.00, max_order_oz: float = 50.0, commission_rate: float = 0.0004, slippage_per_oz: float = 0.12):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.peak_balance = initial_balance
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0
        
        self.max_order_oz = max_order_oz
        self.commission_rate = commission_rate
        self.slippage_per_oz = slippage_per_oz
        
        self.total_commissions_paid = 0.0
        self.total_slippage_paid = 0.0
        
        self.open_trade: Optional[Dict[str, Any]] = None
        self.closed_trades: List[Dict[str, Any]] = []

    def run(self, raw_bars: List[Dict[str, Any]]) -> Dict[str, Any]:
        print(f"🚀 Running Real-World Backtest across {len(raw_bars):,} REAL historical 5-minute exchange candles...")
        
        # Pre-compute running 200 EMA, VWAP, Asian High/Low, ATR 14, RSI 14 directly from real bars
        from app.engine import TechnicalIndicators
        tech = TechnicalIndicators()
        
        closes = [b["close"] for b in raw_bars]
        highs = [b["high"] for b in raw_bars]
        lows = [b["low"] for b in raw_bars]
        
        for idx in range(len(raw_bars)):
            bar = raw_bars[idx]
            price = bar["close"]
            high = bar["high"]
            low = bar["low"]
            now_dt = bar["timestamp"]
            
            # Use up to last 200 bars for exact indicator calculation
            start_slice = max(0, idx - 200)
            slice_closes = closes[start_slice : idx + 1]
            slice_highs = highs[start_slice : idx + 1]
            slice_lows = lows[start_slice : idx + 1]
            
            ema_200 = round(tech.calculate_ema(slice_closes, 200), 2)
            atr_14 = round(tech.calculate_atr(slice_highs, slice_lows, slice_closes, 14), 2)
            rsi_14 = round(tech.calculate_rsi(slice_closes, 14), 1)
            
            # Calculate daily VWAP & Asian High/Low from current UTC day
            asian_high = price
            asian_low = price
            vwap_sum_pv = 0.0
            vwap_sum_v = 0.0
            
            for past_idx in range(start_slice, idx + 1):
                past_b = raw_bars[past_idx]
                if past_b["timestamp"].date() == now_dt.date():
                    h_p = past_b["high"]
                    l_p = past_b["low"]
                    c_p = past_b["close"]
                    v_p = past_b["volume"]
                    vwap_sum_pv += ((h_p + l_p + c_p) / 3.0) * v_p
                    vwap_sum_v += v_p
                    
                    if 0 <= past_b["timestamp"].hour <= 6:
                        if asian_high == price or h_p > asian_high:
                            asian_high = h_p
                        if asian_low == price or l_p < asian_low:
                            asian_low = l_p
                            
            vwap = round(vwap_sum_pv / vwap_sum_v, 2) if vwap_sum_v > 0 else price
            if asian_high == asian_low:
                asian_high = round(price + 3.0, 2)
                asian_low = round(price - 3.0, 2)
                
            market_tick = {
                "timestamp": now_dt,
                "close": price,
                "high": high,
                "low": low,
                "spread": 0.18,  # Typical real Bybit/Binance Gold spread
                "atr_14": atr_14,
                "rsi_14": rsi_14,
                "ema_200": ema_200,
                "vwap": vwap,
                "asian_high": asian_high,
                "asian_low": asian_low
            }
            
            # 1. Evaluate open position against current real bar high/low
            if self.open_trade:
                t = self.open_trade
                direction = t["direction"]
                entry = t["entry_price"]
                tp1 = t["tp1_price"]
                tp2 = t["tp2_price"]
                size_oz = t["size_oz"]
                
                # Check Trailing Breakeven Shield (+1.0R in profit)
                if direction == "LONG" and high >= entry + t["sl_distance"] and not t["is_trailed"]:
                    t["sl_price"] = round(entry + 0.25, 2)
                    t["is_trailed"] = True
                elif direction == "SHORT" and low <= entry - t["sl_distance"] and not t["is_trailed"]:
                    t["sl_price"] = round(entry - 0.25, 2)
                    t["is_trailed"] = True
                    
                exit_price = None
                exit_reason = None
                
                if direction == "LONG":
                    if low <= t["sl_price"]:
                        exit_price = t["sl_price"]
                        exit_reason = "TRAILING_BE" if t["is_trailed"] else "SL_HIT"
                    elif high >= tp2:
                        exit_price = tp2
                        exit_reason = "TP2_HIT"
                    elif high >= tp1:
                        exit_price = tp1
                        exit_reason = "TP1_HIT"
                else:  # SHORT
                    if high >= t["sl_price"]:
                        exit_price = t["sl_price"]
                        exit_reason = "TRAILING_BE" if t["is_trailed"] else "SL_HIT"
                    elif low <= tp2:
                        exit_price = tp2
                        exit_reason = "TP2_HIT"
                    elif low <= tp1:
                        exit_price = tp1
                        exit_reason = "TP1_HIT"
                        
                if exit_price is not None:
                    if direction == "LONG":
                        gross_pnl = (exit_price - entry) * size_oz
                    else:
                        gross_pnl = (entry - exit_price) * size_oz
                        
                    notional_value_usd = entry * size_oz
                    trade_commission = notional_value_usd * self.commission_rate
                    trade_slippage = size_oz * self.slippage_per_oz
                    
                    self.total_commissions_paid += trade_commission
                    self.total_slippage_paid += trade_slippage
                    
                    net_pnl = round(gross_pnl - trade_commission - trade_slippage, 2)
                    self.balance = round(self.balance + net_pnl, 2)
                    if self.balance < 0:
                        self.balance = 0.0
                        
                    if self.balance > self.peak_balance:
                        self.peak_balance = self.balance
                    dd_usd = round(self.peak_balance - self.balance, 2)
                    dd_pct = round((dd_usd / self.peak_balance) * 100.0, 2) if self.peak_balance > 0 else 0.0
                    if dd_usd > self.max_drawdown_usd:
                        self.max_drawdown_usd = dd_usd
                    if dd_pct > self.max_drawdown_pct:
                        self.max_drawdown_pct = dd_pct
                        
                    self.closed_trades.append({
                        "id": len(self.closed_trades) + 1,
                        "timestamp": now_dt.isoformat(),
                        "direction": direction,
                        "entry_price": entry,
                        "exit_price": exit_price,
                        "size_oz": size_oz,
                        "gross_pnl": round(gross_pnl, 2),
                        "commission_usd": round(trade_commission, 2),
                        "slippage_usd": round(trade_slippage, 2),
                        "pnl_usd": net_pnl,
                        "exit_reason": exit_reason,
                        "balance_after": self.balance
                    })
                    self.open_trade = None
                    continue

            # 2. If no open trade, check if new signal triggers on this real bar
            if not self.open_trade:
                trade_plan = engine.evaluate(market_tick, self.balance)
                if trade_plan:
                    if trade_plan["size_oz"] > self.max_order_oz:
                        trade_plan["size_oz"] = self.max_order_oz
                        trade_plan["required_margin_usd"] = round((price * self.max_order_oz) / config.MAX_LEVERAGE, 2)
                        trade_plan["dollar_risk"] = round(self.max_order_oz * trade_plan["sl_distance"], 2)
                    trade_plan["is_trailed"] = False
                    self.open_trade = trade_plan

        return self.compute_statistics()

    def compute_statistics(self) -> Dict[str, Any]:
        total_trades = len(self.closed_trades)
        if total_trades == 0:
            return {"error": "No trades triggered across the downloaded real historical candles."}
            
        tp_wins = [t for t in self.closed_trades if t["exit_reason"] in ("TP1_HIT", "TP2_HIT")]
        scratches = [t for t in self.closed_trades if t["exit_reason"] == "TRAILING_BE"]
        sl_losses = [t for t in self.closed_trades if t["exit_reason"] == "SL_HIT"]
        
        net_wins = [t for t in self.closed_trades if t["pnl_usd"] > 0]
        net_losses = [t for t in self.closed_trades if t["pnl_usd"] < 0]
        
        win_count = len(tp_wins)
        scratch_count = len(scratches)
        loss_count = len(sl_losses)
        
        decisive_trades = win_count + loss_count
        effective_win_rate = round((win_count / decisive_trades) * 100.0, 2) if decisive_trades > 0 else 0.0
        
        total_gross_profit = sum(t["gross_pnl"] for t in tp_wins) + sum(max(0, t["gross_pnl"]) for t in scratches)
        total_gross_loss = abs(sum(t["gross_pnl"] for t in sl_losses) + sum(min(0, t["gross_pnl"]) for t in scratches))
        
        net_profit_sum = sum(t["pnl_usd"] for t in net_wins)
        net_loss_sum = abs(sum(t["pnl_usd"] for t in net_losses))
        net_profit_factor = round(net_profit_sum / net_loss_sum, 2) if net_loss_sum > 0 else 99.9
        
        total_return_usd = round(self.balance - self.initial_balance, 2)
        total_return_pct = round((total_return_usd / self.initial_balance) * 100.0, 2)
        
        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.balance,
            "total_return_usd": total_return_usd,
            "total_return_pct": total_return_pct,
            "total_trades": total_trades,
            "wins": win_count,
            "losses": loss_count,
            "scratches": scratch_count,
            "win_rate": effective_win_rate,
            "total_commissions_paid": round(self.total_commissions_paid, 2),
            "total_slippage_paid": round(self.total_slippage_paid, 2),
            "net_profit_factor": net_profit_factor,
            "max_drawdown_usd": self.max_drawdown_usd,
            "max_drawdown_pct": self.max_drawdown_pct
        }


def run_real_backtest():
    fetcher = RealHistoricalDataFetcher()
    try:
        # Try downloading up to 5,000 real historical 5-minute bars (~17 days of pure 5m history)
        bars = fetcher.get_real_historical_bars(target_bars=5000)
    except RuntimeError as e:
        print(str(e))
        sys.exit(1)
        
    runner = RealQuantitativeBacktestRunner(initial_balance=100.00, max_order_oz=50.0)
    stats = runner.run(bars)
    
    if "error" in stats:
        print(f"Backtest warning: {stats['error']}")
        return stats
        
    report_md = f"""# 🪙 REAL Historical Quantitative Backtest Report (`XAU-USDT` Public Exchange History)

*Target Asset: `XAU-USDT` Perpetual Futures (`TIMEFRAME = 5m`)*  
*Data Source: **EXCLUSIVELY REAL HISTORICAL CANDLES** (`{len(bars):,} actual exchange 5-minute bars downloaded via Bybit/Binance REST API`)*  
*Starting Capital: **`${stats['initial_balance']:.2f} USDT`** (`50x Max Leverage, 1.5% Risk/Trade`)*  
*Execution Friction Included: **`0.04%` Round-Trip Commission**, **`$0.12/oz` Slippage**, and **`50.0 oz` Exchange Orderbook Cap***  

---

## 🛑 Important Note: Why Previous Backtest Reports Were Replaced

Our previous 2-year report was run on synthetically generated statistical bars (`HistoricalDataGenerator`), which produced overly smooth, artificial win rates (`~74.5%`). **That synthetic generator has been permanently deleted.**

This report is generated by **`app/backtester.py` (`RealQuantitativeBacktestRunner`)**, which downloads and backtests **exclusively on real historical 5-minute OHLCV candles pulled from Bybit/Binance APIs**.

---

## 🏛️ Real Historical Backtest Results (Exact Fee & Slippage Adjusted)

Across **`{len(bars):,} real historical 5-minute candles`** (`{bars[0]['timestamp'].strftime('%Y-%m-%d %H:%M')} to {bars[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}`):

| Metric | Real Historical Backtest Result | Analysis & Notes |
| :--- | :---: | :--- |
| **Total Real Trades Executed** | **`{stats['total_trades']}`** | Evaluated purely on real exchange candles (`{stats['wins']} Wins` vs `{stats['losses']} Losses`) |
| **Decisive Win Rate (`%`)** | **`{stats['win_rate']}%`** | True historical win rate across target TP hits vs initial SL hits |
| **Trailing Breakeven Scratches** | **`{stats['scratches']} Scratches`** | Trades where `+1.0R` hit and trailing stop closed at `+$0.25/oz` buffer |
| **Net Profit Factor (`PF`)** | **`{stats['net_profit_factor']}`** | True net payoff ratio after all exchange commissions and slippage deducted |
| **Initial Starting Balance** | **`${stats['initial_balance']:.2f} USDT`** | `$100.00` starting baseline |
| **Final Ending Equity** | **`${stats['final_balance']:,} USDT`** | True compounded performance over `{len(bars):,}` real bars (`{stats['total_return_pct']:+}%` Net Return) |
| **Total Commissions Paid** | **`-${stats['total_commissions_paid']:,} USDT`** | Exact `0.04%` paid to exchange across all `{stats['total_trades']}` trades |
| **Total Execution Slippage** | **`-${stats['total_slippage_paid']:,} USDT`** | Exact `$0.12/oz` execution friction absorbed |
| **Maximum Account Drawdown** | **`-${stats['max_drawdown_usd']:.2f} USDT` (`{stats['max_drawdown_pct']}%`)** | Peak-to-valley drawdown across real market volatility |

---

## 🔬 How to Download and Run This Real Backtester Anytime

You can download real historical candles from Bybit/Binance and re-run this exact verification at any time right from your terminal or Railway worker:

```bash
cd /home/user/Gold-Scalp
python3 -m app.backtester
```

If your terminal has internet access, `RealHistoricalDataFetcher` automatically downloads fresh batches of real historical 5-minute candles (`limit=1000` per page), saves them to `real_historical_klines` inside `History.db`, and runs the exact 4-Layer decision pipeline!
"""
    
    with open("BACKTEST_RESULTS_REAL_HISTORY.md", "w") as f:
        f.write(report_md)
        
    print("✨ Real Historical Backtest Report generated and saved to BACKTEST_RESULTS_REAL_HISTORY.md")
    print(f"   Real Stats -> Win Rate: {stats.get('win_rate', 0)}% | Net PF: {stats.get('net_profit_factor', 0)} | Final Balance: ${stats.get('final_balance', 0):,} USDT")
    return stats

if __name__ == "__main__":
    run_real_backtest()
