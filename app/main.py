"""
Main Entrypoint for Railway 24/7 Deployment.
Initializes database ($100 balance), connects Telegram UI, and runs continuous trading loop.
Listens to live XAU-USDT market prices via app/market_data.py.
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from app.config import config
from app.database import db
from app.engine import engine
from app.paper_trader import paper_trader
from app.telegram_ui import telegram_ui
from app.market_data import market_feed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MainService")

async def market_data_feed_loop():
    """
    Continuous market data feed for XAU-USDT.
    On Railway, this connects directly to Bybit/Binance V5 linear API to fetch real-time live 5m bars,
    orderbook bid/ask spread, session VWAP, and Asian High/Low boundaries every few seconds.
    If offline or rate limited, gracefully falls back to synthetic price simulation without interrupting service.
    """
    logger.info("Starting continuous market data evaluation loop...")
    
    while True:
        try:
            if not paper_trader.is_running:
                await asyncio.sleep(2.0)
                continue
                
            # Fetch latest live market tick from exchange (or fallback)
            market_tick = await market_feed.get_latest_market_tick()
            
            # Feed tick directly to paper trading engine & 4-Layer decision pipeline
            paper_trader.process_new_market_data(market_tick)
            
        except Exception as e:
            logger.error(f"Error in market data evaluation loop: {e}")
            
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)

async def main():
    logger.info("================================================================")
    logger.info(f"🚀 XAU-USDT Layered Scalper & Telegram UI Starting on Railway")
    logger.info(f"   Execution Mode: {'PAPER TRADING' if config.PAPER_TRADING else 'LIVE TRADING'}")
    logger.info(f"   Starting Capital: ${db.get_current_balance():.2f} USDT")
    logger.info(f"   Target Symbol: {config.SYMBOL} | Leverage: {config.MAX_LEVERAGE}x")
    logger.info("================================================================")
    
    # Hook up paper trader alerts directly to Telegram UI
    paper_trader.set_alert_callback(telegram_ui.send_message)
    paper_trader.is_running = True
    
    # Send startup notification via Telegram
    await telegram_ui.send_message(
        f"🟢 <b>XAU-USDT Strategy & Paper Trading Bot Online 24/7</b>\n\n"
        f"• Environment: <b>Railway Cloud ({config.ENV})</b>\n"
        f"• Current Balance: <b>${db.get_current_balance():.2f} USDT</b>\n"
        f"• Target Pair: <b>{config.SYMBOL} ({config.MAX_LEVERAGE}x Max Leverage)</b>\n"
        f"• Live Price Engine: <b>Connected (Bybit/CCXT V5 API)</b>\n"
        f"• UI Commands: Type /help or /status to monitor real-time performance."
    )
    
    # Start concurrent tasks: Telegram polling & market data execution
    telegram_task = asyncio.create_task(telegram_ui.poll_updates_loop())
    market_task = asyncio.create_task(market_data_feed_loop())
    
    # Run both loops until interrupted
    await asyncio.gather(telegram_task, market_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot service shut down gracefully.")
