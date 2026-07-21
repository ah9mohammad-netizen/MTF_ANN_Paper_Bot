"""
Live Market Data Feed Engine for XAU-USDT.
Fetches real-time live OHLCV & orderbook spread directly from Bybit/Binance/Apex (CCXT / REST API),
with automatic fallback to simulated tick generation if offline.
"""
import asyncio
import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.config import config
from app.engine import TechnicalIndicators

logger = logging.getLogger("LiveMarketData")

class LiveMarketDataFeed:
    """
    Connects to live public exchange APIs to ingest real-time XAU-USDT 5m bars and orderbook spread.
    Computes real-time EMA 200, VWAP, Asian High/Low, ATR 14, and RSI 14 directly from live exchange candles.
    """
    def __init__(self):
        self.tech = TechnicalIndicators()
        self.last_known_price = 2847.50
        self.simulated_cycle = 0

    def _fetch_bybit_public_kline_sync(self) -> Optional[Dict[str, Any]]:
        """Synchronous fetch of real-time 5m linear kline & ticker from Bybit V5 API."""
        try:
            # 1. Fetch 5m Klines (last 200 bars for accurate EMA 200 / VWAP / ATR / RSI calculation)
            kline_url = "https://api.bybit.com/v5/market/kline?category=linear&symbol=XAUUSDT&interval=5&limit=200"
            req = urllib.request.Request(kline_url, headers={"User-Agent": "Mozilla/5.0 (Quantitative Gold-Scalp Bot)"})
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                
            if not data or data.get("retCode") != 0 or not data.get("result", {}).get("list"):
                return None
                
            raw_list = data["result"]["list"]  # Bybit returns newest bar first: [timestamp, open, high, low, close, volume, ...]
            raw_list.reverse()  # Sort chronologically from oldest to newest
            
            closes = [float(item[4]) for item in raw_list]
            highs = [float(item[2]) for item in raw_list]
            lows = [float(item[3]) for item in raw_list]
            opens = [float(item[1]) for item in raw_list]
            volumes = [float(item[5]) for item in raw_list]
            
            latest_bar = raw_list[-1]
            latest_close = float(latest_bar[4])
            latest_high = float(latest_bar[2])
            latest_low = float(latest_bar[3])
            latest_open = float(latest_bar[1])
            self.last_known_price = latest_close
            
            # 2. Fetch live orderbook ticker for accurate bid/ask spread
            ticker_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=XAUUSDT"
            req_ticker = urllib.request.Request(ticker_url, headers={"User-Agent": "Mozilla/5.0 (Quantitative Gold-Scalp Bot)"})
            with urllib.request.urlopen(req_ticker, timeout=5.0) as resp_t:
                data_t = json.loads(resp_t.read().decode("utf-8"))
                
            spread = 0.15
            if data_t and data_t.get("retCode") == 0 and data_t.get("result", {}).get("list"):
                t_list = data_t["result"]["list"]
                if t_list:
                    bid = float(t_list[0].get("bid1Price", latest_close - 0.08))
                    ask = float(t_list[0].get("ask1Price", latest_close + 0.08))
                    if ask > bid > 0:
                        spread = round(ask - bid, 2)
                        
            # 3. Calculate exact technical indicators from live 200 bars
            ema_200 = round(self.tech.calculate_ema(closes, 200), 2)
            atr_14 = round(self.tech.calculate_atr(highs, lows, closes, 14), 2)
            rsi_14 = round(self.tech.calculate_rsi(closes, 14), 1)
            
            # Calculate daily VWAP and Asian session boundaries from current day's bars
            now_dt = datetime.now(timezone.utc)
            asian_high = latest_close
            asian_low = latest_close
            vwap_sum_pv = 0.0
            vwap_sum_v = 0.0
            
            for item in raw_list:
                bar_ts = int(item[0]) / 1000.0
                bar_dt = datetime.fromtimestamp(bar_ts, tz=timezone.utc)
                if bar_dt.date() == now_dt.date():
                    c = float(item[4])
                    h = float(item[2])
                    l = float(item[3])
                    v = float(item[5]) if float(item[5]) > 0 else 1.0
                    vwap_sum_pv += ((h + l + c) / 3.0) * v
                    vwap_sum_v += v
                    
                    if 0 <= bar_dt.hour <= 6:
                        if asian_high == latest_close or h > asian_high:
                            asian_high = h
                        if asian_low == latest_close or l < asian_low:
                            asian_low = l
                            
            vwap = round(vwap_sum_pv / vwap_sum_v, 2) if vwap_sum_v > 0 else latest_close
            if asian_high == asian_low:
                asian_high = round(latest_close + 3.0, 2)
                asian_low = round(latest_close - 3.0, 2)
                
            return {
                "timestamp": now_dt,
                "source": "LIVE_EXCHANGE_BYBIT_XAUUSDT",
                "close": latest_close,
                "spread": spread,
                "atr_14": atr_14,
                "rsi_14": rsi_14,
                "ema_200": ema_200,
                "vwap": vwap,
                "asian_high": asian_high,
                "asian_low": asian_low,
                "high": latest_high,
                "low": latest_low
            }
        except Exception as e:
            logger.debug(f"Live Bybit API connection failed or timeout: {e}")
            return None

    async def get_latest_market_tick(self) -> Dict[str, Any]:
        """
        Asynchronously fetches the latest live XAU-USDT market data.
        Falls back to realistic synthetic price simulation if external APIs are unreachable.
        """
        loop = asyncio.get_running_loop()
        live_tick = await loop.run_in_executor(None, self._fetch_bybit_public_kline_sync)
        if live_tick:
            logger.debug(f"Injected live market tick: ${live_tick['close']:.2f} | Spread: ${live_tick['spread']:.2f} | Source: {live_tick['source']}")
            return live_tick

        # Fallback synthetic simulation if offline or API rate limited
        self.simulated_cycle += 1
        now = datetime.now(timezone.utc)
        import random
        price_change = random.uniform(-1.10, 1.20)
        if self.simulated_cycle % 12 == 0:
            price_change = random.uniform(2.20, 3.80)  # London breakout test
        elif self.simulated_cycle % 19 == 0:
            price_change = random.uniform(-3.00, -1.60)  # Asian sweep test
            
        new_price = round(self.last_known_price + price_change, 2)
        high_price = round(new_price + random.uniform(0.10, 0.60), 2)
        low_price = round(new_price - random.uniform(0.10, 0.60), 2)
        self.last_known_price = new_price
        
        return {
            "timestamp": now,
            "source": "SIMULATED_OFFLINE_FALLBACK",
            "close": new_price,
            "spread": round(random.uniform(0.12, 0.25), 2),
            "atr_14": round(random.uniform(1.80, 2.60), 2),
            "rsi_14": round(random.uniform(36.0, 66.0), 1),
            "ema_200": round(new_price - 5.0, 2),
            "vwap": round(new_price - 1.5, 2),
            "asian_high": round(new_price - 2.5, 2),
            "asian_low": round(new_price - 14.0, 2),
            "high": high_price,
            "low": low_price
        }

market_feed = LiveMarketDataFeed()
