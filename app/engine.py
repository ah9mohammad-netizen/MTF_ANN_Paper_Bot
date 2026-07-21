"""
Gold Edge v3 — Research-Backed Layered Decision Engine for XAU-USDT.

Layers
  1. Regime & session     — UTC kill windows, spread, daily book guards (caller)
  2. Volatility & cost    — ATR band, min ATR, SL distance vs round-trip cost
  3. Trend / ADX regime   — EMA200 + EMA50 + ADX range vs trend split
  4. Structure            — Asia sweep-fade | Asia breakout | NY ORB
  5. Confirmation         — turn bar, RSI anti-chase, close-beyond, DI spread
  6. Risk & sizing        — ATR SL, runner TP (large R), % equity size, margin cap

Sources absorbed: session ORB guides, Hermes NY ORB+5R, N30 ADX/cost/turn,
ICT time/liquidity ideas (session + sweep), prop risk caps. No grid/martingale.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import config

logger = logging.getLogger("DecisionEngine")


class TechnicalIndicators:
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        if not prices:
            return 0.0
        if len(prices) < period:
            return prices[-1]
        mult = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]:
            ema = (p - ema) * mult + ema
        return ema

    @staticmethod
    def calculate_atr(
        highs: List[float], lows: List[float], closes: List[float], period: int = 14
    ) -> float:
        if len(highs) < period + 1:
            return 1.80
        trs: List[float] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 1.80
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    @staticmethod
    def atr_series(
        highs: List[float], lows: List[float], closes: List[float], period: int = 14
    ) -> List[float]:
        """Wilder ATR value at each bar (aligned to closes; leading zeros skipped)."""
        if len(closes) < period + 1:
            return []
        trs: List[float] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        out: List[float] = []
        atr = sum(trs[:period]) / period
        out.append(atr)
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
            out.append(atr)
        return out

    @staticmethod
    def calculate_rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains: List[float] = []
        losses: List[float] = []
        for i in range(1, len(closes)):
            ch = closes[i] - closes[i - 1]
            gains.append(max(ch, 0.0))
            losses.append(max(-ch, 0.0))
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def calculate_adx(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> Tuple[float, float, float]:
        """
        Returns (ADX, +DI, -DI). Classic Wilder implementation.
        """
        n = len(closes)
        if n < period + 2:
            return 20.0, 20.0, 20.0

        plus_dm: List[float] = []
        minus_dm: List[float] = []
        trs: List[float] = []
        for i in range(1, n):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
            trs.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

        def wilder_smooth(vals: List[float], p: int) -> List[float]:
            if len(vals) < p:
                return []
            out = [sum(vals[:p])]
            for v in vals[p:]:
                out.append(out[-1] - (out[-1] / p) + v)
            return out

        atr_s = wilder_smooth(trs, period)
        p_s = wilder_smooth(plus_dm, period)
        m_s = wilder_smooth(minus_dm, period)
        if not atr_s or not p_s or not m_s:
            return 20.0, 20.0, 20.0

        dx_list: List[float] = []
        for i in range(len(atr_s)):
            atr_i = atr_s[i] if atr_s[i] != 0 else 1e-9
            pdi = 100.0 * p_s[i] / atr_i
            mdi = 100.0 * m_s[i] / atr_i
            denom = pdi + mdi
            dx = 100.0 * abs(pdi - mdi) / denom if denom else 0.0
            dx_list.append(dx)

        if len(dx_list) < period:
            adx = sum(dx_list) / len(dx_list)
        else:
            adx = sum(dx_list[:period]) / period
            for d in dx_list[period:]:
                adx = (adx * (period - 1) + d) / period

        atr_last = atr_s[-1] if atr_s[-1] != 0 else 1e-9
        plus_di = 100.0 * p_s[-1] / atr_last
        minus_di = 100.0 * m_s[-1] / atr_last
        return round(adx, 2), round(plus_di, 2), round(minus_di, 2)


class LayeredDecisionEngine:
    """Gold Edge v3 multi-setup engine."""

    def __init__(self) -> None:
        self.tech = TechnicalIndicators()

    # ------------------------------------------------------------------
    def evaluate(
        self, market_data: Dict[str, Any], account_balance: float
    ) -> Optional[Dict[str, Any]]:
        current_time = market_data.get("timestamp", datetime.now(timezone.utc))
        if isinstance(current_time, str):
            try:
                current_time = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
            except ValueError:
                current_time = datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        force = bool(market_data.get("force_signal", False))
        price = float(market_data["close"])
        high = float(market_data.get("high", price))
        low = float(market_data.get("low", price))
        spread = float(market_data.get("spread", 0.15))
        atr = float(market_data.get("atr_14", 1.8))
        atr_avg = float(market_data.get("atr_avg", atr))
        rsi = float(market_data.get("rsi_14", 50.0))
        ema_200 = float(market_data.get("ema_200", price))
        ema_50 = float(market_data.get("ema_50", price))
        vwap = float(market_data.get("vwap", price))
        adx = float(market_data.get("adx", 20.0))
        plus_di = float(market_data.get("plus_di", 20.0))
        minus_di = float(market_data.get("minus_di", 20.0))
        asian_high = float(market_data.get("asian_high", price + 5))
        asian_low = float(market_data.get("asian_low", price - 5))
        asian_ready = bool(market_data.get("asian_range_ready", True))
        ny_high = float(market_data.get("ny_orb_high", 0) or 0)
        ny_low = float(market_data.get("ny_orb_low", 0) or 0)
        ny_ready = bool(market_data.get("ny_orb_ready", False))
        prev_close = float(market_data.get("prev_close", price))
        prev_close_2 = float(market_data.get("prev_close_2", prev_close))

        utc_hour = current_time.astimezone(timezone.utc).hour

        # =============================================================
        # LAYER 1 — REGIME & SESSION
        # =============================================================
        session_ok = any(
            s <= utc_hour < e for s, e in config.ALLOWED_SESSIONS
        )
        # NY ORB decision hour is allowed even if slightly outside 12-16 end
        if config.ENABLE_NY_ORB and utc_hour == config.NY_ORB_DECISION_HOUR_UTC:
            session_ok = True

        if not session_ok and not force:
            logger.debug("L1 skip: outside session h=%02d UTC", utc_hour)
            return None

        if spread > config.MAX_ALLOWABLE_SPREAD_USD and not force:
            logger.warning(
                "L1 skip: spread $%.2f > $%.2f", spread, config.MAX_ALLOWABLE_SPREAD_USD
            )
            return None

        # =============================================================
        # LAYER 2 — VOLATILITY & COST GATE
        # =============================================================
        if atr < config.MIN_ATR_USD and not force:
            logger.debug("L2 skip: ATR $%.2f < min $%.2f", atr, config.MIN_ATR_USD)
            return None

        if atr_avg > 0 and not force:
            ratio = atr / atr_avg
            if ratio < config.ATR_VS_AVG_MIN or ratio > config.ATR_VS_AVG_MAX:
                logger.debug("L2 skip: ATR/avg ratio %.2f out of band", ratio)
                return None

        sl_distance = max(config.MIN_SL_USD, round(atr * config.SL_ATR_MULTIPLIER, 2))
        rt_cost = max(spread * 2.0, config.ROUND_TRIP_COST_USD)
        if not force:
            if sl_distance < rt_cost * config.MIN_SL_COST_MULTIPLE:
                logger.debug(
                    "L2 skip: SL $%.2f < %.1fx cost $%.2f",
                    sl_distance,
                    config.MIN_SL_COST_MULTIPLE,
                    rt_cost,
                )
                return None
            tp_dist = sl_distance * config.TP_RR_RATIO
            if tp_dist < rt_cost * config.MIN_TP_COST_MULTIPLE:
                logger.debug("L2 skip: TP distance fails cost multiple")
                return None

        # =============================================================
        # LAYER 3 — TREND / ADX REGIME
        # =============================================================
        trend_bull = price > ema_200
        trend_bear = price < ema_200
        swing_bull = price > ema_50
        swing_bear = price < ema_50
        di_spread = abs(plus_di - minus_di)
        is_ranging = adx <= config.ADX_RANGE_MAX
        is_trending = adx >= config.ADX_TREND_MIN

        # =============================================================
        # LAYER 4 — STRUCTURE (multi-setup, first match wins by priority)
        # Priority: NY ORB (timed) > Asia sweep-fade > Asia breakout
        # =============================================================
        bias: Optional[str] = None
        setup_name = ""
        layer4_reason = ""

        asian_range = max(0.0, asian_high - asian_low)

        # --- Setup A: NY Opening Range Breakout (Hermes DNA) ---
        if (
            not bias
            and config.ENABLE_NY_ORB
            and not force
            and ny_ready
            and ny_high > 0
            and ny_low > 0
            and utc_hour >= config.NY_ORB_DECISION_HOUR_UTC
            and is_trending
        ):
            if (
                price > ny_high
                and (not config.REQUIRE_CLOSE_BEYOND or price > ny_high)
                and swing_bull
                and trend_bull
                and plus_di > minus_di
                and di_spread >= config.MIN_DI_SPREAD
            ):
                bias = "LONG"
                setup_name = "NY_ORB_BREAKOUT"
                layer4_reason = (
                    f"NY ORB break ↑ ${ny_high:.2f} | EMA50/200 bull | ADX {adx:.1f}"
                )
            elif (
                price < ny_low
                and swing_bear
                and trend_bear
                and minus_di > plus_di
                and di_spread >= config.MIN_DI_SPREAD
            ):
                bias = "SHORT"
                setup_name = "NY_ORB_BREAKOUT"
                layer4_reason = (
                    f"NY ORB break ↓ ${ny_low:.2f} | EMA50/200 bear | ADX {adx:.1f}"
                )

        # --- Setup B: Asian liquidity sweep + rejection (ICT / Asia fade) ---
        if (
            not bias
            and config.ENABLE_SWEEP_FADE
            and not force
            and asian_ready
            and config.MIN_ASIAN_RANGE_USD <= asian_range <= config.MAX_ASIAN_RANGE_USD
            and is_ranging  # fade only in range regime (N30)
        ):
            pierce = config.SWEEP_MIN_PIERCE_USD
            # High sweep then close back inside → short
            if (
                high >= asian_high + pierce
                and price < asian_high
                and trend_bear
            ):
                bias = "SHORT"
                setup_name = "ASIA_SWEEP_FADE"
                layer4_reason = (
                    f"Asia high sweep ${high:.2f}≥${asian_high:.2f}+{pierce} "
                    f"reject close ${price:.2f} | ADX {adx:.1f} range"
                )
            elif (
                low <= asian_low - pierce
                and price > asian_low
                and trend_bull
            ):
                bias = "LONG"
                setup_name = "ASIA_SWEEP_FADE"
                layer4_reason = (
                    f"Asia low sweep ${low:.2f}≤${asian_low:.2f}-{pierce} "
                    f"reject close ${price:.2f} | ADX {adx:.1f} range"
                )

        # --- Setup C: Asia range breakout + VWAP (session breakout guides) ---
        if (
            not bias
            and config.ENABLE_ASIA_BREAKOUT
            and not force
            and asian_ready
            and config.MIN_ASIAN_RANGE_USD <= asian_range <= config.MAX_ASIAN_RANGE_USD
            and is_trending
        ):
            if (
                price > asian_high
                and price > vwap
                and trend_bull
                and swing_bull
                and plus_di > minus_di
                and di_spread >= config.MIN_DI_SPREAD
            ):
                # Prefer close-beyond confirmation
                if (not config.REQUIRE_CLOSE_BEYOND) or (price > asian_high):
                    bias = "LONG"
                    setup_name = "ASIA_BREAKOUT"
                    layer4_reason = (
                        f"Asia breakout ↑ ${asian_high:.2f} + VWAP ${vwap:.2f} "
                        f"| ADX {adx:.1f} trend"
                    )
            elif (
                price < asian_low
                and price < vwap
                and trend_bear
                and swing_bear
                and minus_di > plus_di
                and di_spread >= config.MIN_DI_SPREAD
            ):
                bias = "SHORT"
                setup_name = "ASIA_BREAKOUT"
                layer4_reason = (
                    f"Asia breakdown ↓ ${asian_low:.2f} + VWAP ${vwap:.2f} "
                    f"| ADX {adx:.1f} trend"
                )

        # --- Manual / Telegram force ---
        if not bias and force:
            bias = str(market_data.get("force_direction", "LONG")).upper()
            setup_name = "FORCE_OVERRIDE"
            layer4_reason = "Telegram / manual force signal"
            is_trending = True
            is_ranging = True

        if not bias:
            return None

        # =============================================================
        # LAYER 5 — CONFIRMATION
        # =============================================================
        if not force:
            # Turn confirmation (N30): bar stopped extending against intended fade
            # or for breakouts, last close continues in direction
            if config.REQUIRE_TURN_CONFIRM:
                if setup_name == "ASIA_SWEEP_FADE":
                    if bias == "SHORT" and not (prev_close <= prev_close_2):
                        logger.debug("L5 skip: no turn confirm for SHORT fade")
                        return None
                    if bias == "LONG" and not (prev_close >= prev_close_2):
                        logger.debug("L5 skip: no turn confirm for LONG fade")
                        return None
                elif setup_name in ("ASIA_BREAKOUT", "NY_ORB_BREAKOUT"):
                    if bias == "LONG" and prev_close < prev_close_2:
                        logger.debug("L5 skip: breakout long lacks momentum bar")
                        return None
                    if bias == "SHORT" and prev_close > prev_close_2:
                        logger.debug("L5 skip: breakout short lacks momentum bar")
                        return None

            # RSI anti-chase
            if bias == "LONG" and rsi > config.RSI_OVERBOUGHT:
                logger.debug("L5 skip: RSI %.1f overbought", rsi)
                return None
            if bias == "SHORT" and rsi < config.RSI_OVERSOLD:
                logger.debug("L5 skip: RSI %.1f oversold", rsi)
                return None

        # =============================================================
        # LAYER 6 — RISK, EXITS, SIZING
        # =============================================================
        be_distance = round(sl_distance * config.BE_TRIGGER_RR, 2)
        tp_distance = round(sl_distance * config.TP_RR_RATIO, 2)

        if bias == "LONG":
            sl_price = round(price - sl_distance, 2)
            # tp1_price used as BE trigger level (soft)
            tp1_price = round(price + be_distance, 2)
            tp2_price = round(price + tp_distance, 2)
        else:
            sl_price = round(price + sl_distance, 2)
            tp1_price = round(price - be_distance, 2)
            tp2_price = round(price - tp_distance, 2)

        dollar_risk = account_balance * (config.RISK_PER_TRADE_PCT / 100.0)
        if dollar_risk < 0.50 and not force:
            logger.warning("L6 skip: dollar risk too small ($%.2f)", dollar_risk)
            return None

        size_oz = round(dollar_risk / sl_distance, 4)
        if size_oz < 0.01:
            size_oz = 0.01

        notional = price * size_oz
        required_margin = round(notional / config.MAX_LEVERAGE, 2)
        cap = account_balance * (config.MARGIN_CAP_PCT / 100.0)
        if required_margin > cap:
            size_oz = round((cap * config.MAX_LEVERAGE) / price, 4)
            if size_oz < 0.01:
                logger.warning("L6 skip: cannot size under margin cap")
                return None
            required_margin = round((price * size_oz) / config.MAX_LEVERAGE, 2)
            dollar_risk = round(size_oz * sl_distance, 2)

        ts = current_time.isoformat()
        regime_txt = (
            f"h={utc_hour:02d}UTC spr=${spread:.2f} ADX={adx:.1f} "
            f"{'TREND' if is_trending else ('RANGE' if is_ranging else 'MID')}"
        )
        mom_txt = (
            f"ATR=${atr:.2f} (avg ${atr_avg:.2f}) RSI={rsi:.1f} "
            f"DI+={plus_di:.1f} DI-={minus_di:.1f} cost=${rt_cost:.2f}"
        )

        plan: Dict[str, Any] = {
            "timestamp": ts,
            "symbol": config.SYMBOL,
            "direction": bias,
            "entry_price": round(price, 2),
            "sl_price": sl_price,
            "tp1_price": tp1_price,  # BE trigger level
            "tp2_price": tp2_price,  # hard runner TP
            "size_oz": size_oz,
            "leverage": config.MAX_LEVERAGE,
            "required_margin_usd": required_margin,
            "dollar_risk": round(size_oz * sl_distance, 2),
            "sl_distance": sl_distance,
            "setup_name": setup_name,
            "be_trigger_rr": config.BE_TRIGGER_RR,
            "tp_rr": config.TP_RR_RATIO,
            "trail_atr_mult": config.TRAIL_ATR_MULTIPLIER,
            "atr_at_entry": atr,
            "layer1_regime": regime_txt,
            "layer2_structure": f"[{setup_name}] {layer4_reason}",
            "layer3_momentum": mom_txt,
            "reason": f"[{setup_name}] {layer4_reason}",
            "status": "NEW",
            "strategy_version": config.STRATEGY_VERSION,
        }

        logger.info(
            "✨ %s %s @ $%.2f | SL $%.2f | BE@$%.2f | TP $%.2f (%.1fR) | %.4f oz",
            setup_name,
            bias,
            price,
            sl_price,
            tp1_price,
            tp2_price,
            config.TP_RR_RATIO,
            size_oz,
        )
        return plan


engine = LayeredDecisionEngine()
