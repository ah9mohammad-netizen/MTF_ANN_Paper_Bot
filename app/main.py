"""
Main entrypoint for Railway 24/7 deployment.

python -m app.main

- Paper trades XAU-USDT from $100 USDT
- Persists signals/trades/balance to /data/history.db (Railway Volume)
- Telegram bot as UI + push alerts
- Live market data only (never synthetic prices)
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Optional

from app.config import config
from app.database import db
from app.market_data import market_feed
from app.paper_trader import paper_trader
from app.telegram_ui import telegram_ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("MainService")

_shutdown_event: Optional[asyncio.Event] = None


async def market_data_feed_loop() -> None:
    """
    Continuous live XAU-USDT evaluation loop.
    Always manages open paper positions; new entries respect pause flag.
    """
    logger.info(
        "Market loop started | poll=%.1fs | symbol=%s | db=%s",
        config.POLL_INTERVAL_SECONDS,
        config.SYMBOL,
        db.db_path,
    )

    while _shutdown_event is None or not _shutdown_event.is_set():
        try:
            market_tick = await market_feed.get_latest_market_tick()
            if market_tick is None:
                logger.warning(
                    "Skipping cycle — no live market data (failures=%s)",
                    market_feed.consecutive_failures,
                )
            else:
                # Manage SL/TP even when paused; try_open respects new_entries_enabled
                paper_trader.process_new_market_data(market_tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Market loop error: %s", exc, exc_info=True)

        try:
            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


async def heartbeat_loop() -> None:
    """Periodic health log so Railway logs show the worker is alive."""
    while _shutdown_event is None or not _shutdown_event.is_set():
        try:
            bal = db.get_current_balance()
            opens = len(db.get_open_trades())
            logger.info(
                "❤️ heartbeat | bal=$%.2f | open=%s | price=%s | entries=%s | db=%s",
                bal,
                opens,
                f"${market_feed.last_known_price:.2f}"
                if market_feed.last_known_price > 0
                else "n/a",
                "ON" if paper_trader.new_entries_enabled else "PAUSED",
                db.db_path,
            )
        except Exception as exc:
            logger.error("Heartbeat error: %s", exc)
        await asyncio.sleep(300.0)


async def main() -> None:
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    logger.info("=" * 64)
    logger.info("XAU-USDT Layered Scalper + Telegram UI starting")
    logger.info(
        "  mode=%s | balance=$%.2f | symbol=%s | lev=%sx",
        "PAPER" if config.PAPER_TRADING else "LIVE",
        db.get_current_balance(),
        config.SYMBOL,
        config.MAX_LEVERAGE,
    )
    logger.info("  db_path=%s", db.db_path)
    logger.info("  telegram=%s", "configured" if config.TELEGRAM_BOT_TOKEN else "not set")
    logger.info("=" * 64)

    paper_trader.set_alert_callback(telegram_ui.send_message)
    # Do not force-resume if user previously paused (state restored from DB)
    if db.get_state("paused", "false").lower() not in ("true", "1", "yes"):
        paper_trader.new_entries_enabled = True

    await telegram_ui.send_message(
        "🟢 <b>Gold Edge v3 Online 24/7</b>\n\n"
        f"• Strategy: <code>{config.STRATEGY_VERSION}</code>\n"
        f"• Env: <b>Railway ({config.ENV})</b>\n"
        f"• Balance: <b>${db.get_current_balance():.2f} USDT</b>\n"
        f"• Pair: <b>{config.SYMBOL}</b> · <b>{config.MAX_LEVERAGE}x</b>\n"
        f"• Setups: Asia sweep · Asia break · NY ORB\n"
        f"• Risk {config.RISK_PER_TRADE_PCT}% · SL {config.SL_ATR_MULTIPLIER}×ATR · "
        f"TP {config.TP_RR_RATIO:.1f}R · BE+trail\n"
        f"• DB: <code>{db.db_path}</code>\n"
        "• /help · /status · /get_db · /stats"
    )

    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("Shutdown signal received")
        if _shutdown_event:
            _shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows / restricted environments
            pass

    tasks = [
        asyncio.create_task(telegram_ui.poll_updates_loop(), name="telegram"),
        asyncio.create_task(market_data_feed_loop(), name="market"),
        asyncio.create_task(heartbeat_loop(), name="heartbeat"),
    ]

    # Wait until shutdown, then cancel children
    await _shutdown_event.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Bot service shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot service interrupted.")
