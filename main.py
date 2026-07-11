import time
import traceback
from config import BotConfig
from storage import Store
from strategy import StrategyBrain
from paper_engine import PaperEngine
from telegram_ui import TelegramUI

def main():
    # 1. Initialization (Happens only once at startup)
    cfg = BotConfig()
    store = Store(cfg.db_path)
    brain = StrategyBrain(cfg.model_path, cfg.meta_path)
    engine = PaperEngine(cfg, store, brain)
    tg = TelegramUI(cfg.telegram_token, cfg.telegram_chat_id, store)
    
    # Send one-time startup notification to Telegram
    start_msg = (
        f"🤖 MTF ANN V3 Paper Bot Started\n"
        f"💰 Balance: {store.balance():.2f} USDT\n"
        f"📈 Pairs: {', '.join(cfg.pairs)}\n"
        f"⚙️ Risk: {cfg.risk_per_trade_pct}% | Leverage: {cfg.leverage}x"
    )
    tg.send(start_msg)
    print(f"Bot started successfully for chat: {cfg.telegram_chat_id}")

    # 2. Main Worker Loop
    while True:
        try:
            # Handle Telegram commands (/stats, /open, etc.)
            tg.poll_once()
            
            # Run the strategy cycle (Fetch data -> Predict -> Execute)
            # This returns a dictionary of 'opened', 'closed', and 'skipped' trades
            result = engine.cycle()
            
            # Process Closed Positions
            for pos, reason, pnl in result.get('closed', []):
                msg = (
                    f"✅ **Closed {pos['pair']}**\n"
                    f"Reason: {reason}\n"
                    f"PnL: {pnl:.2f} USDT\n"
                    f"New Balance: {store.balance():.2f} USDT"
                )
                tg.send(msg)
            
            # Process New Opened Positions
            for sig in result.get('opened', []):
                msg = (
                    f"🚀 **New Trade Opened**\n"
                    f"Pair: {sig['pair']} ({sig['side']})\n"
                    f"Setup: {sig['setup']}\n"
                    f"Prob: {sig['probability']:.2f}\n"
                    f"Entry: {sig['entry']:.4f}"
                )
                tg.send(msg)
            
            # Log skipped signals to Railway Console (to avoid Telegram spam)
            for skip in result.get('skipped', []):
                print(f"Skipped {skip['pair']}: {skip['reason']} (Prob: {skip['probability']:.2f})")

        except Exception as e:
            # Report critical errors to Telegram
            error_msg = f"⚠️ **Bot Error**: {str(e)}"
            print(error_msg)
            traceback.print_exc()
            try:
                tg.send(error_msg)
            except:
                pass
            time.sleep(10) # Short pause on error before retrying
            
        # Wait for the next poll interval (default 60 seconds)
        time.sleep(cfg.poll_seconds)

if __name__ == '__main__':
    main()
