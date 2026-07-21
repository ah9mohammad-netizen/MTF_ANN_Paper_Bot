# Railway 24/7 Deployment & Telegram UI Guide

**Asset:** `XAU-USDT` paper perpetual  
**Start balance:** `$100.00 USDT` · up to `50x` leverage (risk-sized)  
**Persistent DB:** Railway Volume mount `/data` → **`/data/history.db`**

---

## Architecture

```
Railway worker: python -m app.main
 ├── LiveMarketDataFeed   (Bybit / OKX / Binance PAXG / CCXT)
 ├── LayeredDecisionEngine (4 layers → direction, entry, SL, TP)
 ├── PaperTradingEngine    ($100 book, SL/TP, balance updates)
 ├── DatabaseEngine        (/data/history.db)
 └── TelegramUI           (commands + push alerts)
```

---

## 1. Create Volume

1. Railway project → your service → **Volumes** → **+ Create Volume**
2. **Mount Path:** `/data`
3. Bot writes: **`/data/history.db`**

If `/data` is missing (local dev), the bot uses `./history.db`.

---

## 2. Environment variables

```env
ENV=production
PAPER_TRADING=true
PAPER_BALANCE=100.00
SYMBOL=XAU-USDT
EXCHANGE_ID=bybit
TIMEFRAME=5m
POLL_INTERVAL_SECONDS=5.0
MAX_LEVERAGE=50
RISK_PER_TRADE_PCT=1.5
MAX_SPREAD_USD=0.45
MAX_OPEN_TRADES=1
MAX_DAILY_LOSS_PCT=6.0
DB_PATH=/data/history.db
DATABASE_URL=sqlite:////data/history.db
TELEGRAM_BOT_TOKEN=xxxx:yyyy
TELEGRAM_CHAT_ID=123456789
```

### Telegram setup

1. `@BotFather` → `/newbot` → copy token → `TELEGRAM_BOT_TOKEN`
2. Open a chat with your bot, send `/start`
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat.id` → `TELEGRAM_CHAT_ID`

---

## 3. Deploy

- **Builder:** Nixpacks (`railway.json`)
- **Start:** `python -m app.main` (also in `Procfile` as `worker:`)
- **Restart:** on failure, up to 10 retries

On boot you should get:

`🟢 XAU-USDT Strategy & Paper Trading Bot Online 24/7`

---

## 4. Telegram commands

| Command | Purpose |
|--------|---------|
| `/status` | Mode, live price, feed source, DB path |
| `/balance` | Equity, total & day PnL, $ risk |
| `/get_db` | Upload **`history.db`** from the Volume |
| `/signals` | Last 5 signals + layer notes |
| `/trades` | Open positions + recent closes (SL/TP) |
| `/stats` | Win rate, profit factor, best/worst |
| `/pause` | Stop **new** entries (open trades still managed) |
| `/resume` | Allow new entries again |
| `/close_all` | Flatten all paper positions at last price |
| `/force_long` / `/force_short` | Synthetic structure test at **live** price |

---

## 5. What is stored in `history.db`

| Table | Contents |
|-------|----------|
| `signals` | Direction, entry, SL, TP1/TP2, layer reasons, status |
| `trades` | Size oz, margin, open/close, exit reason (`SL_HIT` / `TP1_HIT` / `TP2_HIT` / `MANUAL_CLOSE`), PnL |
| `account_history` | Balance before/after every fill (starts at $100) |
| `bot_state` | Pause flag etc. |

Download anytime with **`/get_db`**, then open in DB Browser for SQLite / DBeaver / pandas.

---

## 6. Risk model ($100 example)

\[
\text{size (oz)} = \frac{\text{balance} \times 1.5\%}{\text{ATR}\times 1.5}
\]

Margin ≈ `(entry × size) / leverage`, capped at **40%** of equity.

Sessions (UTC): **07–10** (London) and **12–16** (NY overlap).  
Outside those windows the engine stays flat unless you `/force_*`.

---

## 7. Apex (later)

Keep `PAPER_TRADING=true` until stats look stable.  
When ready, wire Apex keys (`APEX_API_*`) and flip paper off — execution adapter is the only piece left to swap; DB + Telegram stay the same.

---

## 8. Local smoke test

```bash
pip install -r requirements.txt
DB_PATH=./history.db PAPER_TRADING=true python -m app.main
```

Ctrl+C stops cleanly (SIGINT/SIGTERM).
