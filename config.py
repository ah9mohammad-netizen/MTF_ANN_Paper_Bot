import os
from dataclasses import dataclass, field
from typing import List

@dataclass
class BotConfig:
    # Universe: BNB/HYPE deliberately dropped.
    pairs: List[str] = field(default_factory=lambda: ['BTC','ETH','SOL','AVAX','ENA'])
    core_pairs: List[str] = field(default_factory=lambda: ['BTC','ETH','SOL','AVAX'])
    transfer_pairs: List[str] = field(default_factory=lambda: ['ENA'])

    starting_balance: float = float(os.getenv('STARTING_BALANCE', '100'))
    risk_per_trade_pct: float = float(os.getenv('RISK_PER_TRADE_PCT', '0.75'))  # normal phase from risk study
    leverage: float = float(os.getenv('LEVERAGE', '3'))
    max_open_positions: int = int(os.getenv('MAX_OPEN_POSITIONS', '3'))
    max_total_margin_pct: float = float(os.getenv('MAX_TOTAL_MARGIN_PCT', '80'))
    max_margin_per_position_pct: float = float(os.getenv('MAX_MARGIN_PER_POSITION_PCT', '35'))
    max_notional_pct: float = float(os.getenv('MAX_NOTIONAL_PCT', '150'))

    # Worst-case paper assumptions, intentionally harsher than APEX level-1 taker fee.
    taker_fee_pct: float = float(os.getenv('TAKER_FEE_PCT', '0.10'))
    slippage_pct: float = float(os.getenv('SLIPPAGE_PCT', '0.20'))

    tp_r_multiple: float = float(os.getenv('TP_R_MULTIPLE', '1.8'))
    max_hold_hours: int = int(os.getenv('MAX_HOLD_HOURS', '96'))
    poll_seconds: int = int(os.getenv('POLL_SECONDS', '60'))

    db_path: str = os.getenv('DB_PATH', 'paper_bot.db')
    telegram_token: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    telegram_chat_id: str = os.getenv('TELEGRAM_CHAT_ID', '')

    model_path: str = os.getenv('MODEL_PATH', 'artifacts/ann_mtf_v3_model.joblib')
    meta_path: str = os.getenv('META_PATH', 'artifacts/ann_mtf_v3_meta.json')

    min_probability: float = float(os.getenv('MIN_PROBABILITY', '0.54'))
    paused: bool = os.getenv('PAUSED', 'false').lower() == 'true'
