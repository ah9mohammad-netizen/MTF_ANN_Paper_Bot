"""
Paper Trading Engine — Gold Edge v3.

Exit model (research-backed):
  • Hard SL always
  • At BE_TRIGGER_R → move SL to breakeven (do NOT full-close / cap winners)
  • Trail SL by TRAIL_ATR after BE
  • Full close only at runner TP (large R) or SL / manual

Also: daily loss lock, max trades/day, post-exit cooldown.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Set

from app.config import config
from app.database import db
from app.engine import engine

logger = logging.getLogger("PaperTrader")


class PaperTradingEngine:
    def __init__(self) -> None:
        self.new_entries_enabled: bool = True
        self.alert_callback: Optional[Callable] = None
        self.last_price: float = 0.0
        self.last_atr: float = 1.8
        self.last_tick_at: Optional[datetime] = None
        self._entries_blocked_until: Optional[datetime] = None
        self._entry_cooldown_seconds: float = float(config.ENTRY_COOLDOWN_SECONDS)
        # In-memory BE / trail state keyed by trade_id
        self._be_armed: Set[int] = set()
        self._restore_state()

    def _restore_state(self) -> None:
        paused = db.get_state("paused", "false").lower() in ("true", "1", "yes")
        self.new_entries_enabled = not paused

    def set_alert_callback(self, callback: Callable) -> None:
        self.alert_callback = callback

    def emit_alert(self, message: str) -> None:
        logger.info(message.replace("\n", " | "))
        if not self.alert_callback:
            return
        try:
            if asyncio.iscoroutinefunction(self.alert_callback):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.alert_callback(message))
                except RuntimeError:
                    asyncio.run(self.alert_callback(message))
            else:
                self.alert_callback(message)
        except Exception as exc:
            logger.error("Failed to emit alert: %s", exc)

    @property
    def is_running(self) -> bool:
        return self.new_entries_enabled

    @is_running.setter
    def is_running(self, value: bool) -> None:
        self.new_entries_enabled = bool(value)
        db.set_state("paused", "false" if value else "true")

    def _pnl_pct(self, pnl_usd: float, entry: float, size_oz: float, leverage: int) -> float:
        margin = (entry * size_oz) / max(int(leverage), 1)
        if margin <= 0:
            return 0.0
        return round((pnl_usd / margin) * 100.0, 2)

    def _arm_entry_cooldown(self) -> None:
        self._entries_blocked_until = datetime.now(timezone.utc) + timedelta(
            seconds=self._entry_cooldown_seconds
        )

    def _trades_opened_today(self) -> int:
        return db.count_trades_opened_today()

    def evaluate_open_trades(
        self, current_price: float, current_high: float, current_low: float, atr: float
    ) -> None:
        open_trades = db.get_open_trades()
        if not open_trades:
            return

        for trade in open_trades:
            trade_id = int(trade["id"])
            direction = trade["direction"]
            entry = float(trade["entry_price"])
            sl = float(trade["sl_price"])
            be_level = float(trade["tp1_price"])  # soft BE trigger
            tp_runner = float(trade["tp2_price"])  # hard full TP
            size_oz = float(trade["size_oz"])
            leverage = int(trade["leverage"] or config.MAX_LEVERAGE)
            sl_dist = abs(entry - float(trade.get("sl_price") or entry))
            # Prefer original distance from entry vs initial SL side
            if direction == "LONG":
                initial_risk = max(entry - sl, 0.01) if trade_id not in self._be_armed else max(
                    abs(entry - be_level) / max(config.BE_TRIGGER_RR, 0.1), 0.01
                )
            else:
                initial_risk = max(sl - entry, 0.01) if trade_id not in self._be_armed else max(
                    abs(entry - be_level) / max(config.BE_TRIGGER_RR, 0.1), 0.01
                )
            # Use stored distance if we can infer from entry and original tp1
            risk_unit = abs(be_level - entry) / max(config.BE_TRIGGER_RR, 0.01)
            if risk_unit <= 0:
                risk_unit = max(sl_dist, 1.0)

            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None

            if direction == "LONG":
                # 1) Hard SL
                if current_low <= sl:
                    exit_price, exit_reason = sl, (
                        "BE_STOP" if trade_id in self._be_armed and sl >= entry else "SL_HIT"
                    )
                # 2) Runner TP full close
                elif current_high >= tp_runner:
                    exit_price, exit_reason = tp_runner, "TP_HIT"
                else:
                    # 3) Arm breakeven at BE trigger (do not close)
                    if trade_id not in self._be_armed and current_high >= be_level:
                        new_sl = round(entry, 2)  # pure BE
                        db.update_trade_sl(trade_id, new_sl)
                        self._be_armed.add(trade_id)
                        self.emit_alert(
                            f"🔒 <b>BE ARMED #{trade_id}</b> LONG @ ${entry:.2f}\n"
                            f"SL moved to breakeven ${new_sl:.2f} | runner TP ${tp_runner:.2f}"
                        )
                        sl = new_sl
                    # 4) Trail after BE
                    if trade_id in self._be_armed:
                        trail_dist = max(atr * config.TRAIL_ATR_MULTIPLIER, risk_unit * 0.5)
                        trail_sl = round(current_price - trail_dist, 2)
                        # Never trail below BE
                        trail_sl = max(trail_sl, entry)
                        if trail_sl > sl:
                            db.update_trade_sl(trade_id, trail_sl)
                            logger.info("Trail LONG #%s SL → $%.2f", trade_id, trail_sl)

            elif direction == "SHORT":
                if current_high >= sl:
                    exit_price, exit_reason = sl, (
                        "BE_STOP" if trade_id in self._be_armed and sl <= entry else "SL_HIT"
                    )
                elif current_low <= tp_runner:
                    exit_price, exit_reason = tp_runner, "TP_HIT"
                else:
                    if trade_id not in self._be_armed and current_low <= be_level:
                        new_sl = round(entry, 2)
                        db.update_trade_sl(trade_id, new_sl)
                        self._be_armed.add(trade_id)
                        self.emit_alert(
                            f"🔒 <b>BE ARMED #{trade_id}</b> SHORT @ ${entry:.2f}\n"
                            f"SL moved to breakeven ${new_sl:.2f} | runner TP ${tp_runner:.2f}"
                        )
                        sl = new_sl
                    if trade_id in self._be_armed:
                        trail_dist = max(atr * config.TRAIL_ATR_MULTIPLIER, risk_unit * 0.5)
                        trail_sl = round(current_price + trail_dist, 2)
                        trail_sl = min(trail_sl, entry)
                        if trail_sl < sl:
                            db.update_trade_sl(trade_id, trail_sl)
                            logger.info("Trail SHORT #%s SL → $%.2f", trade_id, trail_sl)

            if exit_price is not None and exit_reason:
                if direction == "LONG":
                    pnl_usd = round((exit_price - entry) * size_oz, 2)
                else:
                    pnl_usd = round((entry - exit_price) * size_oz, 2)
                pnl_pct = self._pnl_pct(pnl_usd, entry, size_oz, leverage)
                db.close_trade(trade_id, exit_price, pnl_usd, pnl_pct, exit_reason)
                self._be_armed.discard(trade_id)
                self._arm_entry_cooldown()
                self._emit_close_alert(
                    trade_id,
                    trade["symbol"],
                    direction,
                    entry,
                    exit_price,
                    pnl_usd,
                    pnl_pct,
                    exit_reason,
                )

    def _emit_close_alert(
        self,
        trade_id: int,
        symbol: str,
        direction: str,
        entry: float,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        exit_reason: str,
    ) -> None:
        new_bal = db.get_current_balance()
        icons = {
            "SL_HIT": ("🔴", "SL HIT"),
            "BE_STOP": ("🟡", "BE STOP"),
            "TP_HIT": ("🎉", "RUNNER TP HIT"),
            "TP1_HIT": ("🎯", "TP1 HIT"),
            "TP2_HIT": ("🎉", "TP2 HIT"),
            "MANUAL_CLOSE": ("⚪", "MANUAL CLOSE"),
        }
        emoji, title = icons.get(exit_reason, ("⚪", exit_reason))
        sign = "+" if pnl_usd >= 0 else ""
        self.emit_alert(
            f"{emoji} <b>[{title}] Trade #{trade_id} Closed</b>\n"
            f"Pair: {symbol} ({direction})\n"
            f"Entry: ${entry:.2f} ➡️ Exit: ${exit_price:.2f}\n"
            f"PnL: <b>{sign}${pnl_usd:.2f} ({pnl_pct:+.2f}%)</b>\n"
            f"💰 Updated Balance: <b>${new_bal:.2f} USDT</b>"
        )

    def _daily_loss_breached(self, balance: float) -> bool:
        day_pnl = db.get_daily_realized_pnl()
        max_loss = config.INITIAL_BALANCE_USDT * (config.MAX_DAILY_LOSS_PCT / 100.0)
        if day_pnl <= -max_loss:
            return True
        if balance <= config.INITIAL_BALANCE_USDT * (
            1.0 - config.MAX_DAILY_LOSS_PCT / 100.0
        ):
            return day_pnl < 0
        return False

    def try_open_new_trade(self, market_data: Dict[str, Any]) -> None:
        if not self.new_entries_enabled:
            return

        now = datetime.now(timezone.utc)
        force = bool(market_data.get("force_signal"))

        if (
            self._entries_blocked_until
            and now < self._entries_blocked_until
            and not force
        ):
            return

        if len(db.get_open_trades()) >= config.MAX_OPEN_TRADES:
            return

        if not force and self._trades_opened_today() >= config.MAX_TRADES_PER_DAY:
            logger.info(
                "Max trades/day (%s) reached — no new entries", config.MAX_TRADES_PER_DAY
            )
            return

        bal = db.get_current_balance()
        if bal < 5.0:
            logger.warning("Balance too low ($%.2f)", bal)
            return

        if self._daily_loss_breached(bal) and not force:
            logger.warning("Daily loss limit — new entries blocked")
            return

        plan = engine.evaluate(market_data, bal)
        if not plan:
            return

        signal_id = db.save_signal(plan)
        plan["signal_id"] = signal_id
        plan["opened_at"] = now.isoformat()
        trade_id = db.open_trade(plan)
        db.update_signal_status(signal_id, "EXECUTED")

        setup = plan.get("setup_name", "SETUP")
        self.emit_alert(
            f"🚀 <b>NEW PAPER TRADE #{trade_id}</b> · <code>{config.STRATEGY_VERSION}</code>\n"
            f"Setup: <b>{setup}</b> | <b>{plan['direction']}</b> {plan['symbol']}\n"
            f"Entry: <b>${plan['entry_price']:.2f}</b>\n"
            f"SL: <b>${plan['sl_price']:.2f}</b> (−${plan['sl_distance']:.2f})\n"
            f"BE trigger: <b>${plan['tp1_price']:.2f}</b> ({config.BE_TRIGGER_RR:.1f}R)\n"
            f"Runner TP: <b>${plan['tp2_price']:.2f}</b> ({config.TP_RR_RATIO:.1f}R)\n"
            f"Trail: <b>{config.TRAIL_ATR_MULTIPLIER:.1f}×ATR</b> after BE\n"
            f"Size: <b>{plan['size_oz']} oz</b> | Margin "
            f"<b>${plan['required_margin_usd']:.2f}</b> ({plan['leverage']}x)\n"
            f"Risk: <b>${plan['dollar_risk']:.2f}</b>\n"
            f"💡 {plan['layer2_structure']}"
        )

    def process_new_market_data(self, market_data: Dict[str, Any]) -> None:
        price = float(market_data["close"])
        high = float(market_data.get("high", price))
        low = float(market_data.get("low", price))
        atr = float(market_data.get("atr_14", self.last_atr))
        self.last_price = price
        self.last_atr = atr
        self.last_tick_at = datetime.now(timezone.utc)

        self.evaluate_open_trades(price, high, low, atr)
        self.try_open_new_trade(market_data)

    def close_all_open_trades(self) -> int:
        open_trades = db.get_open_trades()
        closed = 0
        px = self.last_price if self.last_price > 0 else 0.0
        for trade in open_trades:
            entry = float(trade["entry_price"])
            size_oz = float(trade["size_oz"])
            direction = trade["direction"]
            leverage = int(trade["leverage"] or config.MAX_LEVERAGE)
            exit_px = px if px > 0 else entry
            if direction == "LONG":
                pnl = round((exit_px - entry) * size_oz, 2)
            else:
                pnl = round((entry - exit_px) * size_oz, 2)
            pct = self._pnl_pct(pnl, entry, size_oz, leverage)
            db.close_trade(trade["id"], exit_px, pnl, pct, "MANUAL_CLOSE")
            self._be_armed.discard(int(trade["id"]))
            closed += 1
        if closed:
            bal = db.get_current_balance()
            self.emit_alert(
                f"⚠️ <b>[MANUAL] Closed {closed} trade(s)</b>\n"
                f"💰 Balance: <b>${bal:.2f} USDT</b>"
            )
            self._arm_entry_cooldown()
        return closed


PaperTradingEngine.last_simulated_price = property(  # type: ignore[attr-defined]
    lambda self: self.last_price,
    lambda self, v: setattr(self, "last_price", v),
)

paper_trader = PaperTradingEngine()
