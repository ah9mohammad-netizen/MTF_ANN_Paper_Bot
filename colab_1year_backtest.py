# =========================================================================================
# 🪙 GOOGLE COLAB COPY-PASTE READY: 1-Year Real Historical Gold Backtester (XAU-USDT 5m)
# =========================================================================================
# Copy and paste this entire code block into Google Colab (https://colab.research.google.com)
# and press Shift + Enter to download 1 year of real historical 5m Gold candles (~105,120 bars)
# and execute the exact 4-Layer Decision Engine with real exchange fees (0.04%) and slippage.
#
# NOTE ON GEO-BLOCKING:
# Bybit (HTTP 403) and Binance (HTTP 451) automatically block Google Colab US data centers.
# This script uses OKX V5 Public `history-candles` API (XAU-USDT-SWAP) and Gate.io (PAXG_USDT),
# which DO NOT geo-block Google Colab and allow full 1-year historical downloads at top speed!
# =========================================================================================

import urllib.request
import json
import time
import math
from datetime import datetime, timezone, timedelta

# --- 1. CONFIGURATION ---
TARGET_BARS = 105120  # Exactly 1 Full Year of 5-minute bars (365 * 24 * 12)
INITIAL_BALANCE_USDT = 100.00
MAX_LEVERAGE = 50
RISK_PER_TRADE_PCT = 1.5  # Risk 1.5% ($1.50 per scalp on $100 starting equity)
MAX_SPREAD_USD = 0.45
COMMISSION_RATE = 0.0004  # 0.04% round-trip exchange fee
SLIPPAGE_PER_OZ = 0.12    # $0.12/oz execution slippage friction
MAX_ORDER_OZ = 50.0       # Exchange Tier 1 contract lot depth ceiling

# Active Sessions (UTC): London Open (07:00-10:00) & NY Overlap (12:00-16:00)
ALLOWED_SESSIONS = [(7, 10), (12, 16)]


# --- 2. TECHNICAL INDICATORS & MATHEMATICS ---
class TechnicalIndicators:
    @staticmethod
    def calculate_ema(prices, period):
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def calculate_atr(highs, lows, closes, period=14):
        if len(highs) < period + 1:
            return 1.80
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 1.80
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    @staticmethod
    def calculate_rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


# --- 3. NON-GEO-BLOCKED REAL HISTORICAL DATA DOWNLOADER (OKX & GATE.IO) ---
def download_real_historical_klines(target_bars=TARGET_BARS):
    print(f"⏳ Connecting to non-geo-blocked public APIs (OKX / Gate.io) to download up to {target_bars:,} real 5m Gold candles...")
    all_bars = {}
    
    # Step A: Download OKX V5 history-candles for XAU-USDT-SWAP (No 403 or 451 geo-block in Colab!)
    after_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    pages = 0
    
    while len(all_bars) < target_bars:
        try:
            url = f"https://www.okx.com/api/v5/market/history-candles?instId=XAU-USDT-SWAP&bar=5m&limit=100&after={after_ms}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "application/json"
            })
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                
            if not data or data.get("code") != "0" or not data.get("data"):
                break
                
            raw_list = data["data"]
            if not raw_list:
                break
                
            for item in raw_list:
                ts_ms = int(item[0])
                all_bars[ts_ms] = {
                    "timestamp_ms": ts_ms,
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]) if float(item[5]) > 0 else 1.0,
                    "symbol": "OKX_XAU-USDT-SWAP"
                }
                
            # OKX returns newest first. Oldest bar in batch is raw_list[-1][0]
            oldest_ts_ms = int(raw_list[-1][0])
            if oldest_ts_ms >= after_ms:
                break
            after_ms = oldest_ts_ms - 1
            pages += 1
            if pages % 15 == 0:
                print(f"   [OKX XAU-USDT-SWAP] Downloaded {len(all_bars):,} / {target_bars:,} candles...")
            time.sleep(0.12)
        except Exception as e:
            print(f"   ⚠️ OKX batch fetch paused or completed: {e}")
            break

    # Step B: If needed, backfill older history using Gate.io PAXG_USDT API (limit=1000, no US geo-block)
    if len(all_bars) < target_bars:
        print(f"🔄 Backfilling remaining {target_bars - len(all_bars):,} bars from Gate.io PAXG_USDT API (limit=1000)...")
        if all_bars:
            to_ts_sec = int(min(all_bars.keys()) / 1000)
        else:
            to_ts_sec = int(datetime.now(timezone.utc).timestamp())
            
        gate_pages = 0
        while len(all_bars) < target_bars:
            try:
                url = f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=PAXG_USDT&interval=5m&limit=1000&to={to_ts_sec}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    
                if not data or not isinstance(data, list) or not data:
                    break
                    
                # Gate.io returns: [timestamp_s, volume, close, high, low, open]
                for item in data:
                    ts_ms = int(item[0]) * 1000
                    all_bars[ts_ms] = {
                        "timestamp_ms": ts_ms,
                        "timestamp": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                        "open": float(item[5]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "close": float(item[2]),
                        "volume": float(item[1]) if float(item[1]) > 0 else 1.0,
                        "symbol": "GATEIO_PAXG_USDT"
                    }
                    
                oldest_ts_sec = int(data[0][0])
                if oldest_ts_sec >= to_ts_sec:
                    break
                to_ts_sec = oldest_ts_sec - 1
                gate_pages += 1
                if gate_pages % 5 == 0:
                    print(f"   [Gate.io] Total downloaded {len(all_bars):,} / {target_bars:,} candles...")
                time.sleep(0.12)
            except Exception as e:
                print(f"   ⚠️ Gate.io batch fetch paused: {e}")
                break

    sorted_bars = sorted(all_bars.values(), key=lambda x: x["timestamp_ms"])
    print(f"✅ Download Complete: {len(sorted_bars):,} exact real historical 5-minute Gold candles ready for backtest.")
    return sorted_bars


# --- 4. THE 4-LAYER DECISION ENGINE ---
class LayeredDecisionEngine:
    def __init__(self):
        self.tech = TechnicalIndicators()

    def evaluate(self, market_data, account_balance):
        current_time = market_data["timestamp"]
        price = market_data["close"]
        spread = market_data["spread"]
        atr = market_data["atr_14"]
        rsi = market_data["rsi_14"]
        ema_200 = market_data["ema_200"]
        vwap = market_data["vwap"]
        asian_high = market_data["asian_high"]
        asian_low = market_data["asian_low"]
        high = market_data["high"]
        low = market_data["low"]

        # LAYER 1: REGIME & SESSION WINDOWS
        current_utc_hour = current_time.astimezone(timezone.utc).hour
        if not any(start <= current_utc_hour < end for start, end in ALLOWED_SESSIONS):
            return None
        if spread > MAX_SPREAD_USD:
            return None

        # LAYER 2: STRUCTURAL LIQUIDITY & ORDER FLOW
        trend_bullish = price > ema_200
        trend_bearish = price < ema_200
        bias = None
        layer2_reason = ""

        if high >= asian_high and price < asian_high and trend_bearish:
            bias = "SHORT"
            layer2_reason = f"Asian High Sweep Rejection (${high:.2f} >= ${asian_high:.2f}) + Bearish EMA"
        elif low <= asian_low and price > asian_low and trend_bullish:
            bias = "LONG"
            layer2_reason = f"Asian Low Sweep Rejection (${low:.2f} <= ${asian_low:.2f}) + Bullish EMA"
        elif trend_bullish and price > vwap and price > asian_high:
            bias = "LONG"
            layer2_reason = f"London Breakout above Asian High (${asian_high:.2f}) & VWAP"
        elif trend_bearish and price < vwap and price < asian_low:
            bias = "SHORT"
            layer2_reason = f"London Breakout below Asian Low (${asian_low:.2f}) & VWAP"

        if not bias:
            return None

        # LAYER 3: MOMENTUM & VOLATILITY VALIDATION
        if atr < 1.00:
            return None
        if bias == "LONG" and rsi > 71.0:
            return None
        if bias == "SHORT" and rsi < 29.0:
            return None

        # LAYER 4: DYNAMIC EXACT POSITION SIZING & MARGIN ($100 Capital)
        sl_distance = round(atr * 1.5, 2)
        if sl_distance < 1.00:
            sl_distance = 1.00

        if bias == "LONG":
            sl_price = round(price - sl_distance, 2)
            tp1_price = round(price + (sl_distance * 2.0), 2)
            tp2_price = round(price + (sl_distance * 3.5), 2)
        else:
            sl_price = round(price + sl_distance, 2)
            tp1_price = round(price - (sl_distance * 2.0), 2)
            tp2_price = round(price - (sl_distance * 3.5), 2)

        dollar_risk = account_balance * (RISK_PER_TRADE_PCT / 100.0)
        if dollar_risk < 1.00:
            dollar_risk = 1.50

        size_oz = round(dollar_risk / sl_distance, 4)
        if size_oz < 0.01:
            size_oz = 0.01
        if size_oz > MAX_ORDER_OZ:
            size_oz = MAX_ORDER_OZ

        return {
            "timestamp": current_time,
            "symbol": "XAU-USDT",
            "direction": bias,
            "entry_price": price,
            "sl_price": sl_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "size_oz": size_oz,
            "leverage": MAX_LEVERAGE,
            "required_margin_usd": round((price * size_oz) / MAX_LEVERAGE, 2),
            "dollar_risk": round(size_oz * sl_distance, 2),
            "sl_distance": sl_distance,
            "layer2_structure": layer2_reason,
            "is_trailed": False
        }


# --- 5. EVENT-DRIVEN BACKTEST ENGINE ---
def run_quantitative_backtest(raw_bars):
    print(f"\n🚀 Running exact event-driven backtest across {len(raw_bars):,} real historical exchange bars...")
    engine = LayeredDecisionEngine()
    tech = TechnicalIndicators()
    
    balance = INITIAL_BALANCE_USDT
    peak_balance = INITIAL_BALANCE_USDT
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0
    
    total_commissions_paid = 0.0
    total_slippage_paid = 0.0
    
    open_trade = None
    closed_trades = []
    
    closes = [b["close"] for b in raw_bars]
    highs = [b["high"] for b in raw_bars]
    lows = [b["low"] for b in raw_bars]
    
    for idx in range(len(raw_bars)):
        bar = raw_bars[idx]
        price = bar["close"]
        high = bar["high"]
        low = bar["low"]
        now_dt = bar["timestamp"]
        
        start_slice = max(0, idx - 200)
        slice_closes = closes[start_slice : idx + 1]
        slice_highs = highs[start_slice : idx + 1]
        slice_lows = lows[start_slice : idx + 1]
        
        ema_200 = round(tech.calculate_ema(slice_closes, 200), 2)
        atr_14 = round(tech.calculate_atr(slice_highs, slice_lows, slice_closes, 14), 2)
        rsi_14 = round(tech.calculate_rsi(slice_closes, 14), 1)
        
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
            "spread": 0.18,
            "atr_14": atr_14,
            "rsi_14": rsi_14,
            "ema_200": ema_200,
            "vwap": vwap,
            "asian_high": asian_high,
            "asian_low": asian_low
        }
        
        # 1. Check open trade against bar high/low
        if open_trade:
            t = open_trade
            direction = t["direction"]
            entry = t["entry_price"]
            tp1 = t["tp1_price"]
            tp2 = t["tp2_price"]
            size_oz = t["size_oz"]
            
            # Check Trailing Breakeven Shield (+1.0R in profit -> move stop to entry + $0.25 buffer)
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
                    
                trade_commission = (entry * size_oz) * COMMISSION_RATE
                trade_slippage = size_oz * SLIPPAGE_PER_OZ
                
                total_commissions_paid += trade_commission
                total_slippage_paid += trade_slippage
                
                net_pnl = round(gross_pnl - trade_commission - trade_slippage, 2)
                balance = round(balance + net_pnl, 2)
                if balance < 0:
                    balance = 0.0
                    
                if balance > peak_balance:
                    peak_balance = balance
                dd_usd = round(peak_balance - balance, 2)
                dd_pct = round((dd_usd / peak_balance) * 100.0, 2) if peak_balance > 0 else 0.0
                if dd_usd > max_drawdown_usd:
                    max_drawdown_usd = dd_usd
                if dd_pct > max_drawdown_pct:
                    max_drawdown_pct = dd_pct
                    
                closed_trades.append({
                    "id": len(closed_trades) + 1,
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
                    "balance_after": balance
                })
                open_trade = None
                continue

        # 2. If no open trade, check new signal
        if not open_trade:
            trade_plan = engine.evaluate(market_tick, balance)
            if trade_plan:
                open_trade = trade_plan

    # Print Report
    total_trades = len(closed_trades)
    tp_wins = [t for t in closed_trades if t["exit_reason"] in ("TP1_HIT", "TP2_HIT")]
    scratches = [t for t in closed_trades if t["exit_reason"] == "TRAILING_BE"]
    sl_losses = [t for t in closed_trades if t["exit_reason"] == "SL_HIT"]
    
    net_wins = [t for t in closed_trades if t["pnl_usd"] > 0]
    net_losses = [t for t in closed_trades if t["pnl_usd"] < 0]
    
    decisive_trades = len(tp_wins) + len(sl_losses)
    effective_win_rate = round((len(tp_wins) / decisive_trades) * 100.0, 2) if decisive_trades > 0 else 0.0
    
    net_profit_sum = sum(t["pnl_usd"] for t in net_wins)
    net_loss_sum = abs(sum(t["pnl_usd"] for t in net_losses))
    net_pf = round(net_profit_sum / net_loss_sum, 2) if net_loss_sum > 0 else 99.9
    
    total_return_usd = round(balance - INITIAL_BALANCE_USDT, 2)
    total_return_pct = round((total_return_usd / INITIAL_BALANCE_USDT) * 100.0, 2)
    
    print("\n" + "="*80)
    print("🪙 GOOGLE COLAB EXACT REAL HISTORICAL BACKTEST REPORT")
    print("="*80)
    print(f"Data Sample       : {len(raw_bars):,} Real 5m Candles ({raw_bars[0]['timestamp'].strftime('%Y-%m-%d')} to {raw_bars[-1]['timestamp'].strftime('%Y-%m-%d')})")
    print(f"Starting Capital  : ${INITIAL_BALANCE_USDT:.2f} USDT (Risk 1.5% per trade, 50x Max Leverage)")
    print(f"Execution Friction: {COMMISSION_RATE*100:.2f}% Fee + ${SLIPPAGE_PER_OZ:.2f}/oz Slippage + Max {MAX_ORDER_OZ} oz lot cap")
    print("-" * 80)
    print(f"Total Real Trades Executed : {total_trades:,}")
    print(f"Decisive Win Rate (%)      : {effective_win_rate}% ({len(tp_wins)} Target Wins vs {len(sl_losses)} Initial Stop Losses)")
    print(f"Trailing Breakeven Scratches: {len(scratches)} Scratches (+1.0R triggered -> exited at +$0.25 buffer)")
    print(f"Net Profit Factor (PF)     : {net_pf}")
    print(f"Total Commissions Paid     : -${total_commissions_paid:,.2f} USDT")
    print(f"Total Execution Slippage   : -${total_slippage_paid:,.2f} USDT")
    print(f"Final Account Balance      : ${balance:,.2f} USDT ({total_return_pct:+,}% Net Return)")
    print(f"Maximum Account Drawdown   : -${max_drawdown_usd:,.2f} USDT ({max_drawdown_pct}%)")
    print("="*80)


# --- 6. EXECUTE COLAB SCRIPT ---
if __name__ == "__main__":
    real_candles = download_real_historical_klines(target_bars=TARGET_BARS)
    if real_candles:
        run_quantitative_backtest(real_candles)
    else:
        print("❌ Could not download real historical candles. Check Colab internet connection.")
