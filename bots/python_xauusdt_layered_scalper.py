#!/usr/bin/env python3
"""
🪙 XAU-USDT Layered Quantitative Scalping & Breakout Bot
--------------------------------------------------------
Target: XAU-USDT Perpetual Futures (e.g., Binance, Bybit, Phemex via CCXT)
Architecture: 4-Layer Decision Engine (Regime -> Structure -> Momentum -> Risk)

Author: Senior Quantitative Gold Trader
Repository: Gold-Scalp
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Set up clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("XAU_Layered_Scalper")


@dataclass
class BotConfig:
    """Configuration parameters for the XAU-USDT Layered Scalper."""
    exchange_id: str = "bybit"  # or binance, phemex, hyperliquid
    symbol: str = "XAU/USDT:USDT"  # CCXT unified perp symbol for Gold
    timeframe: str = "5m"
    max_leverage: int = 50
    risk_per_trade_pct: float = 1.0  # Risk 1.0% of account equity per trade
    max_allowable_spread_usd: float = 0.40  # Max spread in USD/oz to allow trading
    
    # Session Filter (UTC Hours)
    # London Open (07:00-10:00 UTC) & NY Overlap (12:00-16:00 UTC)
    allowed_sessions: List[Tuple[int, int]] = field(default_factory=lambda: [(7, 10), (12, 16)])
    
    # Technical & Volatility Parameters
    ema_trend_period: int = 200
    atr_period: int = 14
    rsi_period: int = 14
    rsi_overbought: float = 72.0
    rsi_oversold: float = 28.0
    
    # Dynamic Risk & Trailing Parameters
    sl_atr_multiplier: float = 1.5
    tp1_rr_ratio: float = 2.0
    tp2_rr_ratio: float = 3.5
    trailing_trigger_rr: float = 1.0  # Move SL to breakeven once 1.0R in profit
    
    # Simulation/Live Mode
    dry_run: bool = True  # Set False for live execution with API keys
    api_key: str = ""
    api_secret: str = ""


@dataclass
class MarketIndicators:
    """Calculated technical and structural indicators for a given timeframe."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float
    ema_200: float
    atr_14: float
    rsi_14: float
    asian_high: float
    asian_low: float
    vwap: float


class TechnicalAnalyzer:
    """Pure mathematical helper functions for technical indicators."""
    
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(highs) < period + 1:
            return 1.5  # Fallback typical Gold 5m ATR (~$1.50)
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 1.5
        # Wilder's smoothed ATR
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    @staticmethod
    def calculate_rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains = []
        losses = []
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


class LayeredDecisionEngine:
    """
    Implements the 4-Layer Decision-Making Engine:
      1. Session & Spread Filter
      2. Structural Liquidity & Trend Filter
      3. Momentum & Volatility Confirmation
      4. Dynamic Risk Calculation & Position Sizing
    """
    def __init__(self, config: BotConfig):
        self.config = config

    def check_layer_1_regime(self, indicators: MarketIndicators) -> Tuple[bool, str]:
        """Layer 1: Check session windows, spread limits, and market viability."""
        now_utc = indicators.timestamp.astimezone(timezone.utc)
        current_hour = now_utc.hour
        
        # Check allowed sessions
        session_valid = any(start <= current_hour < end for start, end in self.config.allowed_sessions)
        if not session_valid:
            return False, f"Outside active trading sessions (Current UTC hour: {current_hour}:00)"
        
        # Check spread limits
        if indicators.spread > self.config.max_allowable_spread_usd:
            return False, f"Spread excessive: ${indicators.spread:.2f} > limit ${self.config.max_allowable_spread_usd:.2f}"
        
        return True, "Layer 1 (Regime & Spread): PASSED"

    def check_layer_2_structure(self, indicators: MarketIndicators) -> Tuple[Optional[str], str]:
        """
        Layer 2: Check structural alignment (Asian Range Sweep / Breakout / EMA Trend).
        Returns: ('LONG', 'SHORT', or None) along with reason.
        """
        # Example Structural Logic: London Open Breakout / Asian Range Sweep
        # If price sweeps Asian High and rejects, or breaks out with EMA alignment
        trend_is_bullish = indicators.close > indicators.ema_200
        trend_is_bearish = indicators.close < indicators.ema_200
        
        # Check Asian High sweep & rejection (Mean Reversion Short)
        if indicators.high >= indicators.asian_high and indicators.close < indicators.asian_high and trend_is_bearish:
            return "SHORT", f"Asian High Sweep rejection (${indicators.high:.2f} >= ${indicators.asian_high:.2f}) aligned with EMA bearish trend"
            
        # Check Asian Low sweep & rejection (Mean Reversion Long)
        if indicators.low <= indicators.asian_low and indicators.close > indicators.asian_low and trend_is_bullish:
            return "LONG", f"Asian Low Sweep rejection (${indicators.low:.2f} <= ${indicators.asian_low:.2f}) aligned with EMA bullish trend"
            
        # Check strong London momentum breakout of VWAP & EMA
        if trend_is_bullish and indicators.close > indicators.vwap and indicators.close > indicators.asian_high:
            return "LONG", "Bullish Structural Break above Asian High & VWAP"
            
        if trend_is_bearish and indicators.close < indicators.vwap and indicators.close < indicators.asian_low:
            return "SHORT", "Bearish Structural Break below Asian Low & VWAP"
            
        return None, "No structural edge detected at current price action"

    def check_layer_3_momentum(self, bias: str, indicators: MarketIndicators) -> Tuple[bool, str]:
        """Layer 3: Validate with Volatility (ATR) and Momentum (RSI)."""
        # Ensure ATR indicates adequate market volatility (> $1.00 per 5m candle)
        if indicators.atr_14 < 1.00:
            return False, f"Insufficient ATR volatility (${indicators.atr_14:.2f} < $1.00 minimum)"
            
        if bias == "LONG":
            if indicators.rsi_14 > self.config.rsi_overbought:
                return False, f"RSI Overbought ({indicators.rsi_14:.1f} > {self.config.rsi_overbought})"
            return True, f"Momentum confirmed for LONG (RSI: {indicators.rsi_14:.1f}, ATR: ${indicators.atr_14:.2f})"
            
        elif bias == "SHORT":
            if indicators.rsi_14 < self.config.rsi_oversold:
                return False, f"RSI Oversold ({indicators.rsi_14:.1f} < {self.config.rsi_oversold})"
            return True, f"Momentum confirmed for SHORT (RSI: {indicators.rsi_14:.1f}, ATR: ${indicators.atr_14:.2f})"
            
        return False, "Invalid bias passed to Layer 3"

    def compute_layer_4_risk(self, bias: str, entry_price: float, account_equity: float, indicators: MarketIndicators) -> Dict[str, float]:
        """Layer 4: Dynamic position sizing, ATR-based Stop Loss, and Take Profit targets."""
        sl_distance = indicators.atr_14 * self.config.sl_atr_multiplier
        
        if bias == "LONG":
            sl_price = entry_price - sl_distance
            tp1_price = entry_price + (sl_distance * self.config.tp1_rr_ratio)
            tp2_price = entry_price + (sl_distance * self.config.tp2_rr_ratio)
        else:  # SHORT
            sl_price = entry_price + sl_distance
            tp1_price = entry_price - (sl_distance * self.config.tp1_rr_ratio)
            tp2_price = entry_price - (sl_distance * self.config.tp2_rr_ratio)
            
        # Calculate position size based on exact dollar risk
        max_dollar_risk = account_equity * (self.config.risk_per_trade_pct / 100.0)
        if sl_distance <= 0:
            position_units = 0.0
        else:
            position_units = max_dollar_risk / sl_distance
            
        return {
            "entry_price": entry_price,
            "sl_price": round(sl_price, 2),
            "tp1_price": round(tp1_price, 2),
            "tp2_price": round(tp2_price, 2),
            "sl_distance": round(sl_distance, 2),
            "position_units": round(position_units, 4),
            "dollar_risk": round(max_dollar_risk, 2),
            "leverage_used": self.config.max_leverage
        }

    def evaluate_market(self, indicators: MarketIndicators, account_equity: float) -> Optional[Dict]:
        """Run the full 4-Layer evaluation pipeline."""
        logger.info("==================================================")
        logger.info(f"Checking XAU-USDT Market | Price: ${indicators.close:.2f} | Spread: ${indicators.spread:.2f}")
        
        # Layer 1
        l1_pass, l1_msg = self.check_layer_1_regime(indicators)
        logger.info(f"[Layer 1 - Regime] {l1_msg}")
        if not l1_pass:
            return None
            
        # Layer 2
        bias, l2_msg = self.check_layer_2_structure(indicators)
        logger.info(f"[Layer 2 - Structure] {l2_msg}")
        if not bias:
            return None
            
        # Layer 3
        l3_pass, l3_msg = self.check_layer_3_momentum(bias, indicators)
        logger.info(f"[Layer 3 - Momentum] {l3_msg}")
        if not l3_pass:
            return None
            
        # Layer 4
        trade_plan = self.compute_layer_4_risk(bias, indicators.close, account_equity, indicators)
        trade_plan["bias"] = bias
        logger.info(f"[Layer 4 - Risk Plan] Bias: {bias} | Units: {trade_plan['position_units']} | SL: ${trade_plan['sl_price']} | TP1: ${trade_plan['tp1_price']}")
        return trade_plan


class XAUUSDTScalpingBot:
    """Main Trading Execution Loop (Supports Dry-Run & CCXT Integration)."""
    def __init__(self, config: BotConfig):
        self.config = config
        self.engine = LayeredDecisionEngine(config)
        self.analyzer = TechnicalAnalyzer()
        self.account_equity = 10000.0  # Simulated start balance $10,000 USDT
        self.is_running = False

    async def fetch_simulated_market_data(self) -> MarketIndicators:
        """Generates realistic synthetic indicator data for simulation/dry-run testing."""
        now = datetime.now(timezone.utc)
        # Simulate typical London open XAU price structure around $2,845.50
        return MarketIndicators(
            timestamp=now,
            open=2844.20,
            high=2847.80,
            low=2843.90,
            close=2847.50,
            volume=1450.0,
            spread=0.15,
            ema_200=2838.00,
            atr_14=2.40,
            rsi_14=64.5,
            asian_high=2846.50,
            asian_low=2839.00,
            vwap=2843.10
        )

    async def run_step(self):
        """Executes one tick/candle evaluation cycle."""
        indicators = await self.fetch_simulated_market_data()
        trade_plan = self.engine.evaluate_market(indicators, self.account_equity)
        
        if trade_plan:
            if self.config.dry_run:
                logger.info(f"🚀 [DRY-RUN EXECUTION] Placing {trade_plan['bias']} Order on {self.config.symbol}")
                logger.info(f"   Entry: ${trade_plan['entry_price']} | SL: ${trade_plan['sl_price']} (-{trade_plan['sl_distance']} USD)")
                logger.info(f"   TP1: ${trade_plan['tp1_price']} (2.0x R:R) | TP2: ${trade_plan['tp2_price']} (3.5x R:R)")
                logger.info(f"   Position Size: {trade_plan['position_units']} oz | Risked Equity: ${trade_plan['dollar_risk']}")
            else:
                logger.info("🔴 [LIVE EXECUTION] Sending live order via CCXT API...")
                # Insert live CCXT order execution logic here when deploying live

    async def start(self, cycles: int = 1, delay_sec: float = 1.0):
        """Starts the main trading loop."""
        logger.info(f"Starting XAU-USDT Layered Scalper (Dry-Run: {self.config.dry_run})")
        self.is_running = True
        for i in range(cycles):
            if not self.is_running:
                break
            await self.run_step()
            if i < cycles - 1:
                await asyncio.sleep(delay_sec)
        logger.info("Bot execution cycle completed.")


if __name__ == "__main__":
    config = BotConfig(
        symbol="XAU/USDT:USDT",
        timeframe="5m",
        max_leverage=50,
        risk_per_trade_pct=1.0,
        dry_run=True
    )
    bot = XAUUSDTScalpingBot(config)
    asyncio.run(bot.start(cycles=1))
