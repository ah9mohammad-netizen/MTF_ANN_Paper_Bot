# Gold-Scalp — Gold Edge v3 (XAU-USDT)

24/7 gold day-trading engine for **XAU-USDT** with research-backed **v3** logic:

- **Multi-setup stack:** Asia sweep-fade · Asia breakout · NY ORB  
- **ADX regime split** · cost gate · turn confirm · true Asia clock  
- **Exits:** BE @ 1R → ATR trail → runner TP @ 4R (no early partial that caps winners)  
- **Paper trading** from **$100 USDT** (Apex live later)  
- **SQLite** at **`/data/history.db`** + **Telegram** UI  

Full spec: [`STRATEGY_V3.md`](STRATEGY_V3.md)

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit TELEGRAM_* if you want UI
python -m app.main
```

Without a Telegram token the bot still runs and logs to stdout.

## Railway 24/7

1. Deploy this repo (Nixpacks / `python -m app.main`).
2. **Volumes → Create Volume → Mount Path: `/data`**
3. Set variables (see `.env.example`), especially:
   - `DB_PATH=/data/history.db`
   - `PAPER_TRADING=true`
   - `PAPER_BALANCE=100.00`
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
4. Message the bot: `/status`, `/get_db`

Full guide: [`RAILWAY_DEPLOYMENT_GUIDE.md`](RAILWAY_DEPLOYMENT_GUIDE.md)  
Strategy research: [`GOLD_DAY_TRADING_AUTOMATION_AND_STRATEGIES.md`](GOLD_DAY_TRADING_AUTOMATION_AND_STRATEGIES.md)

## Telegram commands

| Command | Action |
|--------|--------|
| `/status` | Mode, live price, DB path |
| `/balance` | Paper equity & risk |
| `/get_db` | Download `history.db` |
| `/signals` | Last signals |
| `/trades` | Open + closed trades |
| `/stats` | Win rate, PF, PnL |
| `/pause` `/resume` | Gate new entries |
| `/close_all` | Flatten paper book |
| `/force_long` `/force_short` | Test entry |

## Architecture

```
app/
  main.py          # asyncio worker (Railway)
  engine.py        # 4-layer decision + ATR sizing
  market_data.py   # live CCXT / Bybit / OKX / Binance
  paper_trader.py  # SL/TP execution + balance updates
  database.py      # history.db (signals, trades, account_history)
  telegram_ui.py   # commands + push alerts
  config.py        # env-driven settings
```

## Disclaimer

Educational / research software. High leverage on gold can wipe capital. Paper trade first. Not financial advice.
