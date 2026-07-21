# Gold Edge v3 — Strategy Spec

Research-backed rewrite of the XAU-USDT paper engine. Absorbs the sharpest
factors from public gold systems (session ORB, Hermes NY ORB+large R, N30
ADX/cost/turn, ICT sweep timing, prop risk caps). **No grid / no martingale.**

## Decision stack

```
L1 Session/spread     → London 07–10 & NY 12–16 UTC (+ NY ORB decision hour)
L2 Vol + cost gate    → ATR band vs avg, SL/TP ≫ round-trip cost
L3 ADX regime         → range (≤22) vs trend (≥25) + DI spread
L4 Structure          → NY ORB | Asia sweep-fade | Asia breakout
L5 Confirm            → turn bar, RSI anti-chase
L6 Risk               → 1.5% equity, SL=1.5×ATR, BE@1R, trail, TP@4R
```

## Setups

| Setup | Regime | Trigger | Bias filters |
|-------|--------|---------|--------------|
| **NY_ORB_BREAKOUT** | ADX trend | Break 13–16 UTC range after 16:00 | EMA50+EMA200, DI |
| **ASIA_SWEEP_FADE** | ADX range | Pierce Asia H/L then close back | EMA200 with fade |
| **ASIA_BREAKOUT** | ADX trend | Close beyond Asia + VWAP | EMA50+200, DI |

**Asian range:** calendar **00:00–07:00 UTC** (not rolling fake Asia).

## Exits (critical change)

- **No full close at 1–2R** (research: partial/early TP caps winners).
- At **1R** → move SL to **breakeven**.
- Then **trail** by `1.5×ATR`.
- Full profit take at **4R** runner (tunable `TP_RR_RATIO`; Hermes used 5R).

## Book guards

- Max **1** open trade  
- Max **3** trades/day  
- Daily loss **5%** of initial  
- Cooldown **300s** after exit  
- Margin ≤ **40%** equity  

## Env knobs

See `.env.example`. Highest-impact: `TP_RR_RATIO`, `SL_ATR_MULTIPLIER`,
`ADX_*`, `ALLOWED_SESSIONS`, `RISK_PER_TRADE_PCT`, setup enable flags.

## Code map

- `app/engine.py` — layers + setups  
- `app/market_data.py` — Asia clock, NY ORB, ADX pack  
- `app/paper_trader.py` — BE + trail + runner TP  
- `app/config.py` — all thresholds  
