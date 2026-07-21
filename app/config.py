"""
Configuration module for XAU-USDT Paper Trading Bot & Telegram UI.
Loads from environment variables (Railway or .env) with safe defaults.
"""
import os
from dataclasses import dataclass, field
from typing import List, Tuple

@dataclass
class AppConfig:
    # Environment & Deployment
    ENV: str = os.getenv("ENV", "production")
    RAILWAY_ENVIRONMENT: str = os.getenv("RAILWAY_ENVIRONMENT", "railway")
    
    # Capital & Asset Configuration
    INITIAL_BALANCE_USDT: float = float(os.getenv("PAPER_BALANCE", "100.00"))
    SYMBOL: str = os.getenv("SYMBOL", "XAU-USDT")  # Unified crypto perp symbol
    EXCHANGE_ID: str = os.getenv("EXCHANGE_ID", "bybit")  # bybit, binance, apex, phemex
    TIMEFRAME: str = os.getenv("TIMEFRAME", "5m")
    
    # Trading & Leverage Parameters for $100 starting balance
    MAX_LEVERAGE: int = int(os.getenv("MAX_LEVERAGE", "50"))
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.5"))  # Risk 1.5% ($1.50 per trade on $100)
    MAX_ALLOWABLE_SPREAD_USD: float = float(os.getenv("MAX_SPREAD_USD", "0.45"))
    
    # Active Session Windows (UTC Hours)
    # London Open (07:00-10:00 UTC) & NY Overlap (12:00-16:00 UTC)
    ALLOWED_SESSIONS: List[Tuple[int, int]] = field(default_factory=lambda: [(7, 10), (12, 16)])
    
    # Technical & Risk Multipliers
    EMA_TREND_PERIOD: int = 200
    ATR_PERIOD: int = 14
    RSI_PERIOD: int = 14
    RSI_OVERBOUGHT: float = 71.0
    RSI_OVERSOLD: float = 29.0
    SL_ATR_MULTIPLIER: float = 1.5
    TP1_RR_RATIO: float = 2.0
    TP2_RR_RATIO: float = 3.5
    
    # Database Configuration (Railway Volume path /data/History.db by default if /data exists or requested)
    DB_PATH: str = os.getenv(
        "DB_PATH",
        "/data/History.db" if os.path.exists("/data") or os.getenv("RAILWAY_VOLUME_NAME") else "History.db"
    )
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
    
    # Telegram UI Configuration
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Execution Mode
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() in ("true", "1", "yes")
    POLL_INTERVAL_SECONDS: float = float(os.getenv("POLL_INTERVAL_SECONDS", "5.0"))

# Global config instance
config = AppConfig()
