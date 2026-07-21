"""
Live Market Data Feed — XAU-USDT Gold Edge v3.

STRICT LIVE DATA ONLY. Builds full indicator pack for the v3 engine:
  EMA200, EMA50, ATR14, ATR avg, RSI, ADX/+DI/-DI,
  true calendar Asian range (00:00–07:00 UTC),
  NY ORB range (13:00–16:00 UTC), session VWAP proxy,
  prev closes for turn confirmation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import config
from app.engine import TechnicalIndicators

logger = logging.getLogger("LiveMarketData")

try:
    import ccxt  # type: ignore

    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _http_get_json(url: str, timeout: float = 8.0) -> Optional[Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("HTTP GET failed %s: %s", url, exc)
        return None


def _bar_hour_utc(ts_ms: float) -> int:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).hour


def _bar_date_utc(ts_ms: float) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


class LiveMarketDataFeed:
    def __init__(self) -> None:
        self.tech = TechnicalIndicators()
        self.last_known_price: float = 0.0
        self.last_valid_source: str = "NONE"
        self.last_tick: Optional[Dict[str, Any]] = None
        self.consecutive_failures: int = 0

    def _build_tick_result(
        self,
        source_name: str,
        timestamps_ms: List[float],
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: Optional[List[float]],
        spread: float,
    ) -> Dict[str, Any]:
        latest_close = float(closes[-1])
        latest_high = float(highs[-1])
        latest_low = float(lows[-1])
        prev_close = float(closes[-2]) if len(closes) >= 2 else latest_close
        prev_close_2 = float(closes[-3]) if len(closes) >= 3 else prev_close

        ema_200 = round(self.tech.calculate_ema(closes, config.EMA_TREND_PERIOD), 2)
        ema_50 = round(self.tech.calculate_ema(closes, config.EMA_FAST_PERIOD), 2)
        atr_14 = round(self.tech.calculate_atr(highs, lows, closes, config.ATR_PERIOD), 2)
        rsi_14 = round(self.tech.calculate_rsi(closes, config.RSI_PERIOD), 1)
        adx, plus_di, minus_di = self.tech.calculate_adx(
            highs, lows, closes, config.ADX_PERIOD
        )

        atr_series = self.tech.atr_series(highs, lows, closes, config.ATR_PERIOD)
        look = config.ATR_AVG_LOOKBACK
        if atr_series:
            window = atr_series[-look:] if len(atr_series) >= look else atr_series
            atr_avg = round(sum(window) / len(window), 2)
        else:
            atr_avg = atr_14

        # Session VWAP proxy (volume-weighted typical price over available bars)
        vwap_num = 0.0
        vwap_den = 0.0
        for i, h in enumerate(highs):
            tp = (h + lows[i] + closes[i]) / 3.0
            vol = float(volumes[i]) if volumes and i < len(volumes) and volumes[i] else 1.0
            vwap_num += tp * vol
            vwap_den += vol
        vwap = round(vwap_num / vwap_den, 2) if vwap_den else latest_close

        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        # ── True calendar Asian range (today 00:00–ASIAN_END UTC) ──
        asian_highs: List[float] = []
        asian_lows: List[float] = []
        for i, ts in enumerate(timestamps_ms):
            d = _bar_date_utc(ts)
            h = _bar_hour_utc(ts)
            if d == today and config.ASIAN_START_HOUR_UTC <= h < config.ASIAN_END_HOUR_UTC:
                asian_highs.append(highs[i])
                asian_lows.append(lows[i])

        asian_range_ready = len(asian_highs) >= 6  # ≥30m of 5m bars
        if asian_highs:
            asian_high = round(max(asian_highs), 2)
            asian_low = round(min(asian_lows), 2)
        else:
            # Fallback rolling ~6h if calendar Asia not in window yet
            lb = 72 if len(highs) >= 72 else min(30, len(highs))
            asian_high = round(max(highs[-lb:]), 2)
            asian_low = round(min(lows[-lb:]), 2)
            asian_range_ready = now.hour >= config.ASIAN_END_HOUR_UTC

        # After Asia end, freeze "ready"
        if now.hour >= config.ASIAN_END_HOUR_UTC and asian_highs:
            asian_range_ready = True

        # ── NY ORB range (today 13:00–16:00 UTC) ──
        ny_highs: List[float] = []
        ny_lows: List[float] = []
        for i, ts in enumerate(timestamps_ms):
            d = _bar_date_utc(ts)
            h = _bar_hour_utc(ts)
            if (
                d == today
                and config.NY_ORB_START_HOUR_UTC
                <= h
                < config.NY_ORB_END_HOUR_UTC
            ):
                ny_highs.append(highs[i])
                ny_lows.append(lows[i])

        ny_orb_ready = (
            len(ny_highs) >= 6
            and now.hour >= config.NY_ORB_DECISION_HOUR_UTC
        )
        ny_orb_high = round(max(ny_highs), 2) if ny_highs else 0.0
        ny_orb_low = round(min(ny_lows), 2) if ny_lows else 0.0

        tick = {
            "timestamp": now,
            "source": source_name,
            "close": latest_close,
            "open": float(opens[-1]) if opens else latest_close,
            "high": latest_high,
            "low": latest_low,
            "spread": float(spread),
            "atr_14": atr_14,
            "atr_avg": atr_avg,
            "rsi_14": rsi_14,
            "ema_200": ema_200,
            "ema_50": ema_50,
            "vwap": vwap,
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "asian_high": asian_high,
            "asian_low": asian_low,
            "asian_range_ready": asian_range_ready,
            "ny_orb_high": ny_orb_high,
            "ny_orb_low": ny_orb_low,
            "ny_orb_ready": ny_orb_ready,
            "prev_close": prev_close,
            "prev_close_2": prev_close_2,
        }
        self.last_known_price = latest_close
        self.last_valid_source = source_name
        self.last_tick = tick
        self.consecutive_failures = 0
        return tick

    def _from_ohlcv_matrix(
        self, source: str, ohlcv: List[list], spread: float
    ) -> Optional[Dict[str, Any]]:
        """ohlcv rows: [ts, o, h, l, c, v]"""
        if not ohlcv or len(ohlcv) < 30:
            return None
        ts = [float(b[0]) for b in ohlcv]
        opens = [float(b[1]) for b in ohlcv]
        highs = [float(b[2]) for b in ohlcv]
        lows = [float(b[3]) for b in ohlcv]
        closes = [float(b[4]) for b in ohlcv]
        vols = [float(b[5] or 0) for b in ohlcv]
        return self._build_tick_result(
            source, ts, opens, highs, lows, closes, vols, spread
        )

    # ── CCXT ─────────────────────────────────────────────────────
    def _fetch_via_ccxt_sync(self) -> Optional[Dict[str, Any]]:
        if not CCXT_AVAILABLE:
            return None
        pairs: List[Tuple[str, str]] = [
            ("bybit", "XAU/USDT:USDT"),
            ("okx", "XAU/USDT:USDT"),
            ("binance", "PAXG/USDT"),
            ("phemex", "XAU/USDT:USDT"),
            ("gate", "PAXG/USDT"),
        ]
        for ex_id, symbol in pairs:
            try:
                cls = getattr(ccxt, ex_id, None)
                if not cls:
                    continue
                ex = cls({"timeout": 8000, "enableRateLimit": True})
                ohlcv = ex.fetch_ohlcv(symbol, timeframe="5m", limit=200)
                if not ohlcv or len(ohlcv) < 30:
                    continue
                spread = 0.15
                try:
                    t = ex.fetch_ticker(symbol)
                    bid = float(t.get("bid") or 0)
                    ask = float(t.get("ask") or 0)
                    if ask > bid > 0:
                        spread = round(ask - bid, 2)
                except Exception:
                    pass
                return self._from_ohlcv_matrix(
                    f"LIVE_CCXT_{ex_id.upper()}_{symbol}", ohlcv, spread
                )
            except Exception as exc:
                logger.debug("CCXT %s failed: %s", ex_id, exc)
        return None

    # ── REST ─────────────────────────────────────────────────────
    def _fetch_bybit_rest_sync(self) -> Optional[Dict[str, Any]]:
        data = _http_get_json(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=XAUUSDT&interval=5&limit=200"
        )
        if not data or data.get("retCode") != 0:
            return None
        raw = list(reversed(data.get("result", {}).get("list") or []))
        if len(raw) < 30:
            return None
        # Bybit: [start, open, high, low, close, volume, turnover]
        ohlcv = [
            [float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)]
            for r in raw
        ]
        spread = 0.15
        tdata = _http_get_json(
            "https://api.bybit.com/v5/market/tickers?category=linear&symbol=XAUUSDT",
            timeout=5.0,
        )
        try:
            if tdata and tdata.get("retCode") == 0:
                row = tdata["result"]["list"][0]
                bid = float(row.get("bid1Price") or 0)
                ask = float(row.get("ask1Price") or 0)
                if ask > bid > 0:
                    spread = round(ask - bid, 2)
        except Exception:
            pass
        return self._from_ohlcv_matrix("LIVE_REST_BYBIT_XAUUSDT", ohlcv, spread)

    def _fetch_okx_rest_sync(self) -> Optional[Dict[str, Any]]:
        data = _http_get_json(
            "https://www.okx.com/api/v5/market/candles?instId=XAU-USDT-SWAP&bar=5m&limit=200"
        )
        if not data or data.get("code") != "0":
            return None
        raw = list(reversed(data.get("data") or []))
        if len(raw) < 30:
            return None
        # OKX: [ts, o, h, l, c, vol, ...]
        ohlcv = [
            [float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)]
            for r in raw
        ]
        spread = 0.18
        tdata = _http_get_json(
            "https://www.okx.com/api/v5/market/ticker?instId=XAU-USDT-SWAP", timeout=5.0
        )
        try:
            if tdata and tdata.get("code") == "0":
                row = tdata["data"][0]
                bid = float(row.get("bidPx") or 0)
                ask = float(row.get("askPx") or 0)
                if ask > bid > 0:
                    spread = round(ask - bid, 2)
        except Exception:
            pass
        return self._from_ohlcv_matrix("LIVE_REST_OKX_XAU-USDT-SWAP", ohlcv, spread)

    def _fetch_binance_paxg_rest_sync(self) -> Optional[Dict[str, Any]]:
        data = _http_get_json(
            "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=5m&limit=200"
        )
        if not data or not isinstance(data, list) or len(data) < 30:
            return None
        ohlcv = [
            [float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)]
            for r in data
        ]
        spread = 0.20
        tdata = _http_get_json(
            "https://api.binance.com/api/v3/ticker/bookTicker?symbol=PAXGUSDT",
            timeout=5.0,
        )
        try:
            if tdata:
                bid = float(tdata.get("bidPrice") or 0)
                ask = float(tdata.get("askPrice") or 0)
                if ask > bid > 0:
                    spread = round(ask - bid, 2)
        except Exception:
            pass
        return self._from_ohlcv_matrix("LIVE_REST_BINANCE_PAXGUSDT", ohlcv, spread)

    def _fetch_gate_paxg_rest_sync(self) -> Optional[Dict[str, Any]]:
        data = _http_get_json(
            "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=PAXG_USDT&interval=5m&limit=200"
        )
        if not data or not isinstance(data, list) or len(data) < 30:
            return None
        # Gate: [t, v, c, h, l, o, ...]  t is seconds
        ohlcv = []
        for r in data:
            ts = float(r[0]) * 1000 if float(r[0]) < 1e12 else float(r[0])
            ohlcv.append(
                [ts, float(r[5]), float(r[3]), float(r[4]), float(r[2]), float(r[1] or 0)]
            )
        return self._from_ohlcv_matrix("LIVE_REST_GATE_PAXG_USDT", ohlcv, 0.25)

    def _fetch_live_market_data_sync(self) -> Optional[Dict[str, Any]]:
        for fn in (
            self._fetch_via_ccxt_sync,
            self._fetch_bybit_rest_sync,
            self._fetch_okx_rest_sync,
            self._fetch_binance_paxg_rest_sync,
            self._fetch_gate_paxg_rest_sync,
        ):
            try:
                tick = fn()
                if tick and tick.get("close", 0) > 0:
                    return tick
            except Exception as exc:
                logger.debug("%s error: %s", fn.__name__, exc)
        return None

    async def get_latest_market_tick(self) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_running_loop()
        live = await loop.run_in_executor(None, self._fetch_live_market_data_sync)
        if live:
            logger.info(
                "Live %s | $%.2f | spr $%.2f | ATR $%.2f | ADX %.1f | RSI %.1f | Asia %s/%s",
                live["source"],
                live["close"],
                live["spread"],
                live["atr_14"],
                live["adx"],
                live["rsi_14"],
                live["asian_high"],
                live["asian_low"],
            )
            return live
        self.consecutive_failures += 1
        logger.warning(
            "All live endpoints failed (streak=%s). No synthetic fallback.",
            self.consecutive_failures,
        )
        return None


market_feed = LiveMarketDataFeed()
