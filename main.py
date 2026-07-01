import time
from config import BotConfig
from storage import Store
from strategy import StrategyBrain
from paper_engine import PaperEngine
from telegram_ui import TelegramUI


def main():
    cfg=BotConfig()
    store=Store(cfg.db_path)
    brain=StrategyBrain(cfg.model_path,cfg.meta_path)
    engine=PaperEngine(cfg,store,brain)
    tg=TelegramUI(cfg.telegram_token,cfg.telegram_chat_id,store)
    tg.send(f"🤖 MTF ANN V3 paper bot started. Balance={store.balance():.2f} USDT. Pairs={cfg.pairs}. Risk={cfg.risk_per_trade_pct}% lev={cfg.leverage}x")
    while True:
        try:
            tg.poll_once()
            result=engine.cycle()
            for p,reason,pnl in result['closed']:
                tg.send(f"✅ Closed #{p['id']} {p['pair']} {p['side']} {reason} PnL={pnl:.2f} balance={store.balance():.2f}")
            for s in result['opened']:
                tg.send(f"📈 Opened {s['pair']} {s['side']} {s['setup']} p={s['probability']:.2f}\nEntry={s['entry']:.4g} SL={s['sl']:.4g} TP={s['tp']:.4g}\nNotional={s['notional']:.2f} Margin={s['margin']:.2f}")
            for s in result['skipped']:
                tg.send(f"⚠️ Signal skipped {s['pair']} {s['side']} {s['setup']} p={s['probability']:.2f} reason={s['reason']} margin={s['margin']:.2f}")
        except Exception as e:
            tg.send(f"Bot error: {e}")
            print('error',e)
        time.sleep(cfg.poll_seconds)

if __name__=='__main__': main()
