"""
Configuration — XAU-USDT Gold Edge v3 (research-backed).

Blends the sharpest factors from:
  • Asian range → London/NY structure (session breakout guides)
  • NY 3h ORB + EMA + large-R (Hermes/DEV survivor)
  • ADX regime split + cost gate + turn confirm (N30 Gold)
  • ATR risk + hard daily caps (prop-safe commercials)
  • No grid / no martingale / no early partial that caps winners
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Tuple


def _env_bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).lower() in ("true", "1", "yes", "on")


def _env_sessions(raw: str | None) -> List[Tuple[int, int]]:
    """Parse '7-10,12-16' or '7:10,12:16' into hour windows."""
    if not raw:
        # London open + NY overlap (prime gold liquidity)
        return [(7, 10), (12, 16)]
    windows: List[Tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip().replace(":", "-")
        if "-" not in part:
            continue
        a, b = part.split("-", 1)
        windows.append((int(a), int(b)))
    return windows or [(7, 10), (12, 16)]


def _resolve_db_path() -> str:
    explicit = os.getenv("DB_PATH") or os.getenv("DATABASE_PATH")
    if explicit:
        return explicit
    on_vol = (
        os.path.isdir("/data")
        or bool(os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))
        or bool(os.getenv("RAILWAY_VOLUME_NAME"))
    )
    if on_vol:
        for c in ("/data/history.db", "/data/History.db"):
            if os.path.exists(c):
                return c
        return "/data/history.db"
    for c in ("history.db", "History.db"):
        if os.path.exists(c):
            return c
    return "history.db"


def _resolve_database_url(db_path: str) -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    if db_path.startswith("/"):
        return f"sqlite:///{db_path}"
    return f"sqlite:///{os.path.abspath(db_path)}"


@dataclass
class AppConfig:
    # ── Environment ──────────────────────────────────────────────
    ENV: str = os.getenv("ENV", "production")
    RAILWAY_ENVIRONMENT: str = os.getenv("RAILWAY_ENVIRONMENT", "")
    STRATEGY_VERSION: str = "v3-gold-edge"

    # ── Capital & symbol ─────────────────────────────────────────
    INITIAL_BALANCE_USDT: float = float(os.getenv("PAPER_BALANCE", "100.00"))
    SYMBOL: str = os.getenv("SYMBOL", "XAU-USDT")
    EXCHANGE_ID: str = os.getenv("EXCHANGE_ID", "bybit")
    TIMEFRAME: str = os.getenv("TIMEFRAME", "5m")

    # ── Leverage & book risk ─────────────────────────────────────
    MAX_LEVERAGE: int = int(os.getenv("MAX_LEVERAGE", "50"))
    # Research: 1–2% survives; DEV ORB used 2%. Default 1.5 for $100 paper.
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.5"))
    MAX_ALLOWABLE_SPREAD_USD: float = float(os.getenv("MAX_SPREAD_USD", "0.40"))
    MAX_OPEN_TRADES: int = int(os.getenv("MAX_OPEN_TRADES", "1"))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))
    MAX_TRADES_PER_DAY: int = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
    ENTRY_COOLDOWN_SECONDS: float = float(os.getenv("ENTRY_COOLDOWN_SECONDS", "300"))
    MARGIN_CAP_PCT: float = float(os.getenv("MARGIN_CAP_PCT", "40.0"))

    # ── Session windows (UTC) ────────────────────────────────────
    # Layer-1: only trade liquid gold hours
    ALLOWED_SESSIONS: List[Tuple[int, int]] = field(
        default_factory=lambda: _env_sessions(os.getenv("ALLOWED_SESSIONS"))
    )
    # True Asian range clock (UTC) — structure, not entries
    ASIAN_START_HOUR_UTC: int = int(os.getenv("ASIAN_START_HOUR_UTC", "0"))
    ASIAN_END_HOUR_UTC: int = int(os.getenv("ASIAN_END_HOUR_UTC", "7"))
    # NY ORB observe window then decision (Hermes-style, on 5m stream)
    NY_ORB_START_HOUR_UTC: int = int(os.getenv("NY_ORB_START_HOUR_UTC", "13"))
    NY_ORB_END_HOUR_UTC: int = int(os.getenv("NY_ORB_END_HOUR_UTC", "16"))
    NY_ORB_DECISION_HOUR_UTC: int = int(os.getenv("NY_ORB_DECISION_HOUR_UTC", "16"))

    # ── Indicators ───────────────────────────────────────────────
    EMA_TREND_PERIOD: int = int(os.getenv("EMA_TREND_PERIOD", "200"))  # HTF bias on 5m
    EMA_FAST_PERIOD: int = int(os.getenv("EMA_FAST_PERIOD", "50"))     # ORB / swing filter
    ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", "14"))
    ATR_AVG_LOOKBACK: int = int(os.getenv("ATR_AVG_LOOKBACK", "50"))
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
    RSI_OVERBOUGHT: float = float(os.getenv("RSI_OVERBOUGHT", "72.0"))
    RSI_OVERSOLD: float = float(os.getenv("RSI_OVERSOLD", "28.0"))
    ADX_PERIOD: int = int(os.getenv("ADX_PERIOD", "14"))
    # N30-style regime split
    ADX_RANGE_MAX: float = float(os.getenv("ADX_RANGE_MAX", "22.0"))      # ≤ → allow sweep fade
    ADX_TREND_MIN: float = float(os.getenv("ADX_TREND_MIN", "25.0"))      # ≥ → allow breakout/ORB
    MIN_DI_SPREAD: float = float(os.getenv("MIN_DI_SPREAD", "5.0"))

    # ── Structure thresholds ─────────────────────────────────────
    SWEEP_MIN_PIERCE_USD: float = float(os.getenv("SWEEP_MIN_PIERCE_USD", "0.30"))
    MIN_ASIAN_RANGE_USD: float = float(os.getenv("MIN_ASIAN_RANGE_USD", "3.00"))
    MAX_ASIAN_RANGE_USD: float = float(os.getenv("MAX_ASIAN_RANGE_USD", "45.00"))
    REQUIRE_CLOSE_BEYOND: bool = _env_bool("REQUIRE_CLOSE_BEYOND", "true")
    REQUIRE_TURN_CONFIRM: bool = _env_bool("REQUIRE_TURN_CONFIRM", "true")
    ENABLE_SWEEP_FADE: bool = _env_bool("ENABLE_SWEEP_FADE", "true")
    ENABLE_ASIA_BREAKOUT: bool = _env_bool("ENABLE_ASIA_BREAKOUT", "true")
    ENABLE_NY_ORB: bool = _env_bool("ENABLE_NY_ORB", "true")

    # ── Cost / edge gate (N30) ───────────────────────────────────
    ROUND_TRIP_COST_USD: float = float(os.getenv("ROUND_TRIP_COST_USD", "0.35"))
    MIN_SL_COST_MULTIPLE: float = float(os.getenv("MIN_SL_COST_MULTIPLE", "4.0"))
    MIN_TP_COST_MULTIPLE: float = float(os.getenv("MIN_TP_COST_MULTIPLE", "6.0"))

    # ── Volatility band ──────────────────────────────────────────
    MIN_ATR_USD: float = float(os.getenv("MIN_ATR_USD", "0.80"))
    ATR_VS_AVG_MIN: float = float(os.getenv("ATR_VS_AVG_MIN", "0.40"))
    ATR_VS_AVG_MAX: float = float(os.getenv("ATR_VS_AVG_MAX", "2.50"))

    # ── Risk / exits (asymmetric — DEV lesson: don't cap winners early) ─
    SL_ATR_MULTIPLIER: float = float(os.getenv("SL_ATR_MULTIPLIER", "1.5"))
    MIN_SL_USD: float = float(os.getenv("MIN_SL_USD", "1.20"))
    # Soft TP1 = breakeven trigger only (not a full exit)
    BE_TRIGGER_RR: float = float(os.getenv("BE_TRIGGER_RR", "1.0"))
    # Trail after BE
    TRAIL_ATR_MULTIPLIER: float = float(os.getenv("TRAIL_ATR_MULTIPLIER", "1.5"))
    # Hard runner target (full close) — Hermes used 5R
    TP_RR_RATIO: float = float(os.getenv("TP_RR_RATIO", "4.0"))
    # Legacy aliases kept so old env/docs still parse
    TP1_RR_RATIO: float = float(os.getenv("TP1_RR_RATIO", os.getenv("BE_TRIGGER_RR", "1.0")))
    TP2_RR_RATIO: float = float(os.getenv("TP2_RR_RATIO", os.getenv("TP_RR_RATIO", "4.0")))

    # ── Database ─────────────────────────────────────────────────
    DB_PATH: str = field(default_factory=_resolve_db_path)
    DATABASE_URL: str = field(default="")

    # ── Telegram ─────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── Execution ────────────────────────────────────────────────
    PAPER_TRADING: bool = _env_bool("PAPER_TRADING", "true")
    POLL_INTERVAL_SECONDS: float = float(os.getenv("POLL_INTERVAL_SECONDS", "5.0"))

    # Apex (phase 2)
    APEX_API_KEY: str = os.getenv("APEX_API_KEY", "")
    APEX_API_SECRET: str = os.getenv("APEX_API_SECRET", "")
    APEX_PASSPHRASE: str = os.getenv("APEX_PASSPHRASE", "")

    def __post_init__(self) -> None:
        if not self.DATABASE_URL:
            self.DATABASE_URL = _resolve_database_url(self.DB_PATH)
        # Keep TP aliases coherent if only TP_RR set
        if os.getenv("TP_RR_RATIO") and not os.getenv("TP2_RR_RATIO"):
            self.TP2_RR_RATIO = self.TP_RR_RATIO
        if os.getenv("BE_TRIGGER_RR") and not os.getenv("TP1_RR_RATIO"):
            self.TP1_RR_RATIO = self.BE_TRIGGER_RR


config = AppConfig()
