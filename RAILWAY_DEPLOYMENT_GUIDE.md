# 🚂 Railway 24/7 Deployment & Telegram UI Guide (`XAU-USDT` Paper Trading Engine)

*Author: Senior Quantitative Gold Trader (`Gold-Scalp` Repository)*  
*Target Asset: `XAU-USDT` (Crypto Perpetual Futures)*  
*Initial Paper Balance: `$100.00 USDT` (`50x` Max Leverage)*  
*Persistent Storage: Railway Volume mounted at `/data` $\rightarrow$ Database File `History.db` (`/data/History.db`)*

---

## 🏛️ System Architecture Overview

We have engineered and integrated a complete, asynchronous, 24/7 quantitative trading system right inside `/home/user/Gold-Scalp`. The system runs **Paper Trading (`paper_trading = True`)** on `XAU-USDT` starting with exactly **`$100.00 USDT`**, storing every signal, trade, and balance change inside a persistent SQLite database (`History.db` inside `/data/History.db` on your Railway Volume), and communicating bidirectionally with you via a **Telegram Bot UI**.

```
+-----------------------------------------------------------------------------------+
|                        RAILWAY 24/7 CLOUD WORKER (`python -m app.main`)           |
+-----------------------------------------------------------------------------------+
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
+---------------------+   +---------------------+   +---------------------+
| Layered Decision    |   | Paper Trading Engine|   | Database Engine     |
| Engine (app/engine) |   | (app/paper_trader)  |   | (app/database.py)   |
+---------------------+   +---------------------+   +---------------------+
| • Checks 4 Tiers    |   | • Manages $100 Bal  |   | • Volume /data/     |
| • Exact $100 Sizing |   | • Tick SL/TP1/TP2   |   | • File: History.db  |
|   (Risk / SL Dist)  |   | • Trailing Breakeven|   | • Auto-creates dir  |
+---------------------+   +---------------------+   +---------------------+
         ▲                                                   │
         │ (Evaluates & Executes)                            │ (Persists State)
         └─────────────────────────┬─────────────────────────┘
                                   ▼
+-----------------------------------------------------------------------------------+
|                  TELEGRAM BOT UI & PUSH ALERTS (`app/telegram_ui.py`)             |
+-----------------------------------------------------------------------------------+
| • Outgoing Push Alerts: Instant Trade Opened, SL/TP Hit, Trailing Stop updates    |
| • Incoming Commands: /status | /balance | /get_db | /signals | /trades | /stats     |
+-----------------------------------------------------------------------------------+
```

---

## 💾 Railway Volume Setup (`/data/History.db`)

To ensure your trading history (`signals`, `trades`, `account_history`) persists across Railway worker re-deploys and container restarts:

1. In your Railway project dashboard, click your deployed service (`Gold-Scalp`).
2. Go to **Volumes** $\rightarrow$ Click **+ Create Volume**.
3. Set the **Mount Path** to `/data`.
4. The bot (`app/database.py`) automatically checks and creates `/data/History.db` whenever `/data` is present or when `DB_PATH=/data/History.db` is set in your environment variables!

### 📥 Downloading `History.db` via Telegram (`/get_db`)
At any time—day or night—you can type **`/get_db`** in your Telegram Bot chat. The bot immediately packages the live `/data/History.db` binary directly from your Railway Volume and sends it to you as a file attachment!

You can open `History.db` on your laptop/phone using:
* **DB Browser for SQLite** (Free graphical UI)
* **DBeaver**
* **Python (`sqlite3` / `pandas`)** to run statistical order-flow backtests and quantitative strategy optimizations!

---

## ⚙️ How Position Sizing & Margin Work for `$100.00 USDT` Starting Capital

When trading `XAU-USDT` perpetual futures with a **`$100.00` account balance** at **`50x` leverage**, our `LayeredDecisionEngine` computes exact position sizes dynamically based on your **Account Equity Risk Percentage (`1.5% = $1.50 per trade`)** and the **Distance to Stop Loss (`ATR_14 * 1.5`)**:

$$\text{Contract Size (troy oz)} = \frac{\text{Dollar Risk Limit (`$1.50`)}}{| \text{Entry Price} - \text{Stop Loss Price} |}$$

$$\text{Required Margin (USDT)} = \frac{\text{Entry Price} \times \text{Contract Size (`oz`)}}{\text{Max Leverage (`50x`)}}$$

### Example from Our Live Sandbox Verification:
* **Current Account Balance:** `$100.00 USDT`
* **Entry Price (`XAU-USDT`):** `$2,847.50 / oz`
* **Dynamic Stop Loss:** `$2,843.90` ($\text{Distance} = \$3.60$)
* **Exact Position Size (`size_oz`):** $\frac{\$1.50}{\$3.60} = \mathbf{0.4167\text{ oz}}$
* **Notional Position Value:** $\$2,847.50 \times 0.4167 = \$1,186.55$
* **Required Margin at `50x`:** $\frac{\$1,186.55}{50} = \mathbf{\$23.73\text{ USDT}}$ ($\approx 23.7\%$ of your capital)
* **If TP1 Hit (`+2.0R` R:R):** You gain **`+$3.00 USDT`**, lifting your balance from **`$100.00` to `$103.00 USDT`** (`+3.0%` account growth in a single scalp!).

---

## 🤖 Telegram Bot UI Commands (`app/telegram_ui.py`)

Once deployed on Railway with your `TELEGRAM_BOT_TOKEN`, open your Telegram Bot and type any of these interactive commands:

| Command | Action & Return Preview |
| :--- | :--- |
| **`/status`** | Displays 24/7 bot status, execution mode (`PAPER TRADING ($100 Capital)`), last price, active trade count, and Volume path (`/data/History.db`). |
| **`/balance`** | Shows your real-time paper balance (`$100.00 -> $103.00`), total PnL `$ and %`, and exact dollar risk per trade. |
| **`/get_db`** *(or `/export_db`)* | **Downloads `History.db`:** The bot uploads `/data/History.db` directly to your Telegram chat so you can analyze and optimize your strategy. |
| **`/signals`** | Lists the last 5 signals from `History.db` (`signals` table) along with structural reasoning and status (`EXECUTED`, `SKIPPED`). |
| **`/trades`** | Displays all currently active open positions (Entry, SL, TP1, Size oz, Margin) and recent closed trades with exact PnL. |
| **`/stats`** | Returns comprehensive win rate (`%`), total return (`%`), profit factor, best win (`+$3.00`), and worst loss (`-$1.50`). |
| **`/close_all`** | **Emergency Override:** Immediately closes all active open paper positions at current market price and updates DB. |
| **`/pause` / `/resume`** | Toggles whether the bot opens new trades while continuing to monitor existing positions for SL/TP execution. |
| **`/force_long` / `/force_short`** | Dispatches a synthetic structural breakout tick to test live Telegram alert & paper order flow directly. |

---

## 📂 Complete Application Directory Structure (`Gold-Scalp`)

```
Gold-Scalp/
├── app/
│   ├── __init__.py         # Package initialization
│   ├── config.py           # Config variables (DB_PATH=/data/History.db, PAPER_BALANCE=100.00)
│   ├── database.py         # Database engine (auto-creates /data/ dir & tables in History.db)
│   ├── engine.py           # 4-Layer Decision Engine & dynamic $100 position sizing
│   ├── paper_trader.py     # Paper execution loop: monitors ticks, triggers TP/SL hits, logs to DB
│   ├── telegram_ui.py      # Async Telegram UI poller, push notifications & /get_db uploader
│   └── main.py             # 24/7 Async asyncio entrypoint (`python -m app.main`)
├── Procfile                # Railway worker config (`worker: python -m app.main`)
├── railway.json            # Railway deployment build rules
├── requirements.txt        # Production dependencies (aiogram, sqlalchemy, ccxt, aiohttp)
├── .env.example            # Environment variables template
├── .gitignore              # Protects local DBs (`*.db`) and pycache from git commits
├── GOLD_DAY_TRADING_AUTOMATION_AND_STRATEGIES.md # Comprehensive quantitative research
└── RAILWAY_DEPLOYMENT_GUIDE.md # This deployment guide
```

---

## 🚀 Step-by-Step Railway Deployment Guide (Take 3 Minutes)

### Step 1: Connect GitHub Repository to Railway
1. Log in to [Railway.app](https://railway.app/) and click **+ New Project**.
2. Select **Deploy from GitHub repo** and choose `ah9mohammad-netizen/Gold-Scalp` (branch `arena/019f837d-gold-scalp` or `main`).

### Step 2: Attach Volume & Set Environment Variables
1. Click on your deployed service $\rightarrow$ **Volumes** $\rightarrow$ **+ Create Volume** $\rightarrow$ Mount Path: `/data`.
2. Go to **Variables** and add:
```env
ENV=production
PAPER_TRADING=true
PAPER_BALANCE=100.00
SYMBOL=XAU-USDT
EXCHANGE_ID=bybit
MAX_LEVERAGE=50
RISK_PER_TRADE_PCT=1.5
DB_PATH=/data/History.db
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

### Step 3: Deploy & Monitor
When Railway launches `worker: python -m app.main`, your Telegram Bot will buzz immediately with:  
`🟢 XAU-USDT Strategy & Paper Trading Bot Online 24/7 | Starting Balance: $100.00 USDT`!
Type `/get_db` anytime to receive `History.db` right in your Telegram chat!
