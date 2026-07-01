"""
Market Structure Transition Strategy
────────────────────────────────────
Research bot for classifying market into:
  1) Trend expansion
  2) Compression/range
  3) Distribution/top
  4) Capitulation/breakdown
  5) Recovery/mean-reversion

Then estimating transition probabilities and trading mini-trend bounces/breaks.

This is a research backtest, not a live bot.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import math
import numpy as np
import pandas as pd

DATA_DIR = Path('data')
OUT = Path('results')
OUT.mkdir(exist_ok=True)
INIT_CASH = 700.0
FEE = 0.0005
SLIP = 0.0010


def load(sym: str, tf='1h') -> pd.DataFrame:
    p = DATA_DIR / f'{sym}_{tf}.csv'
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / dn.ewm(alpha=1/n, adjust=False).mean().replace(0, np.nan)
    return 100 - 100/(1+rs)

def atr(h, l, c, n=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def adx(h, l, c, n=14):
    up = h.diff(); dn = -l.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1/n, adjust=False).mean() / a.replace(0, np.nan)
    mdi = 100 * minus.ewm(alpha=1/n, adjust=False).mean() / a.replace(0, np.nan)
    dx = 100 * (pdi-mdi).abs() / (pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()


def rolling_pct_rank(s: pd.Series, n: int) -> pd.Series:
    # percentile rank of latest value inside rolling window
    return s.rolling(n).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)


def prepare(sym: str) -> pd.DataFrame:
    d = load(sym, '1h')
    if d.empty: return d
    c, h, l, v = d.close, d.high, d.low, d.volume
    d['ema20'] = ema(c, 20); d['ema50'] = ema(c, 50); d['ema200'] = ema(c, 200); d['ema800'] = ema(c, 800)
    d['atr14'] = atr(h, l, c, 14); d['atr_pct'] = d.atr14 / c
    d['adx14'] = adx(h, l, c, 14); d['adx_slope'] = d.adx14 - d.adx14.shift(12)
    d['rsi14'] = rsi(c, 14); d['rsi_slope'] = d.rsi14 - d.rsi14.shift(12)
    mid = c.rolling(20).mean(); std = c.rolling(20).std()
    d['bb_width'] = (4 * std / mid).replace([np.inf, -np.inf], np.nan)
    d['bb_pct'] = rolling_pct_rank(d.bb_width, 240)
    d['atr_pct_rank'] = rolling_pct_rank(d.atr_pct, 240)
    d['hi48'] = h.rolling(48).max(); d['lo48'] = l.rolling(48).min()
    d['hi96'] = h.rolling(96).max(); d['lo96'] = l.rolling(96).min()
    d['range_pos96'] = ((c - d.lo96) / (d.hi96 - d.lo96).replace(0, np.nan)).clip(0, 1)
    d['ret24'] = c.pct_change(24); d['ret72'] = c.pct_change(72); d['ret168'] = c.pct_change(168)
    d['ema50_slope'] = d.ema50.pct_change(24); d['ema200_slope'] = d.ema200.pct_change(48); d['ema800_slope'] = d.ema800.pct_change(72)
    d['vol_z'] = (v - v.rolling(72).mean()) / v.rolling(72).std().replace(0, np.nan)
    d['body_pct'] = (d.close - d.open).abs() / d.close
    d['red_big'] = (d.close < d.open) & (d.body_pct > 0.8*d.atr_pct)
    d['green_big'] = (d.close > d.open) & (d.body_pct > 0.8*d.atr_pct)
    d['trend_bias'] = np.select(
        [(c > d.ema200) & (d.ema200_slope > 0), (c < d.ema200) & (d.ema200_slope < 0)],
        [1, -1], default=0
    )
    d = classify(d)
    d = add_outcomes(d)
    return d.dropna().copy()


def classify(d: pd.DataFrame) -> pd.DataFrame:
    c = d.close
    state = pd.Series('NEUTRAL', index=d.index, dtype=object)
    direction = pd.Series(0, index=d.index, dtype=int)

    compressed = ((d.bb_pct < 0.28) | (d.atr_pct_rank < 0.30)) & (d.adx14 < 23)

    up_exp = (c > d.ema200) & (d.ema50 > d.ema200) & (d.ema50_slope > 0) & (d.adx14 >= 22) & (d.ret72 > 0)
    dn_exp = (c < d.ema200) & (d.ema50 < d.ema200) & (d.ema50_slope < 0) & (d.adx14 >= 22) & (d.ret72 < 0)

    distribution = (
        (c > d.ema200) &
        (d.ret168 > 0.04) &
        (d.range_pos96 > 0.58) &
        ((d.adx_slope < -2) | (c < d.ema50) | (d.rsi_slope < -5)) &
        ~compressed
    )
    capitulation = (
        (c < d.ema200) &
        ((d.ret24 < -1.8*d.atr_pct) | (c < d.lo48.shift(1)) | (d.red_big & (d.vol_z > 0.8))) &
        ((d.adx14 > 20) | (d.vol_z > 0.8))
    )
    recovery = (
        (c < d.ema200) &
        ((c > d.ema20) | (d.green_big & (d.rsi_slope > 0))) &
        (d.rsi14 > 35) &
        (d.range_pos96 < 0.55) &
        (d.ret24 > 0)
    )

    state[compressed] = 'COMPRESSION'
    state[up_exp] = 'EXPANSION_UP'; direction[up_exp] = 1
    state[dn_exp] = 'EXPANSION_DOWN'; direction[dn_exp] = -1
    state[distribution] = 'DISTRIBUTION'; direction[distribution] = -1
    state[capitulation] = 'CAPITULATION'; direction[capitulation] = -1
    state[recovery] = 'RECOVERY'; direction[recovery] = 1

    # Priority matters: true panic/recovery should override generic expansion/compression.
    state[capitulation] = 'CAPITULATION'; direction[capitulation] = -1
    state[recovery] = 'RECOVERY'; direction[recovery] = 1
    state[distribution] = 'DISTRIBUTION'; direction[distribution] = -1

    d['state'] = state
    d['state_dir'] = direction
    return d


def add_outcomes(d: pd.DataFrame, horizon=48, up_r=1.6, dn_r=1.6) -> pd.DataFrame:
    # Outcome labels for probability estimation: which side reaches an ATR target first over next horizon.
    out = np.zeros(len(d), dtype=int)
    for i in range(len(d)-horizon-1):
        entry = float(d.close.iloc[i]); a = float(d.atr14.iloc[i])
        if not math.isfinite(a) or a <= 0: continue
        up = entry + up_r*a; dn = entry - dn_r*a
        label = 0
        for j in range(i+1, i+horizon+1):
            if d.high.iloc[j] >= up and d.low.iloc[j] <= dn:
                # ambiguous; choose closer open direction by candle close
                label = 1 if d.close.iloc[j] >= entry else -1; break
            if d.high.iloc[j] >= up:
                label = 1; break
            if d.low.iloc[j] <= dn:
                label = -1; break
        out[i] = label
    d['future_event'] = out
    return d


def context_key(row) -> tuple:
    # Keep bins coarse to avoid overfitting/sparse counts.
    rp = 'LOW' if row.range_pos96 < 0.33 else 'HIGH' if row.range_pos96 > 0.67 else 'MID'
    tb = int(row.trend_bias)
    return (row.state, tb, rp)


def build_prob_table(train: pd.DataFrame, min_count=20):
    keys = train.apply(context_key, axis=1)
    tmp = pd.DataFrame({'key': keys, 'event': train.future_event.values}, index=train.index)
    table = {}
    global_up = (tmp.event == 1).mean(); global_dn = (tmp.event == -1).mean()
    for key, g in tmp.groupby('key'):
        n = len(g); up = (g.event == 1).sum(); dn = (g.event == -1).sum()
        # Bayesian smoothing toward global rates.
        strength = 30
        p_up = (up + strength*global_up) / (n + strength)
        p_dn = (dn + strength*global_dn) / (n + strength)
        table[key] = {'n': n, 'p_up': p_up, 'p_down': p_dn}
    # State fallback
    for state, g in tmp.groupby(train.state):
        key = (state, 'ANY', 'ANY'); n = len(g); up = (g.event == 1).sum(); dn = (g.event == -1).sum(); strength=30
        table[key] = {'n': n, 'p_up': (up+strength*global_up)/(n+strength), 'p_down': (dn+strength*global_dn)/(n+strength)}
    table[('GLOBAL','ANY','ANY')] = {'n': len(tmp), 'p_up': global_up, 'p_down': global_dn}
    return table


def probs(row, table):
    k = context_key(row)
    if k in table and table[k]['n'] >= 20:
        return table[k]['p_up'], table[k]['p_down'], table[k]['n']
    k2 = (row.state, 'ANY', 'ANY')
    if k2 in table:
        return table[k2]['p_up'], table[k2]['p_down'], table[k2]['n']
    g = table[('GLOBAL','ANY','ANY')]
    return g['p_up'], g['p_down'], g['n']


@dataclass
class Trade:
    sym: str; side: str; setup: str; state: str
    entry_time: pd.Timestamp; exit_time: pd.Timestamp
    entry: float; exit: float; sl: float; tp: float
    p_up: float; p_down: float; prob_n: int
    bars: int; reason: str; net_pct: float; equity: float


def make_signal(d: pd.DataFrame, i: int, table, p_thr=0.50, edge=0.035):
    row = d.iloc[i]
    p_up, p_dn, n = probs(row, table)
    side = None; setup = None

    # Mini-trend breaks from compression/range.
    if row.state == 'COMPRESSION':
        if row.close > d.hi48.iloc[i-1] and p_up >= p_thr and p_up-p_dn >= edge:
            side='LONG'; setup='compression_up_break'
        elif row.close < d.lo48.iloc[i-1] and p_dn >= p_thr and p_dn-p_up >= edge:
            side='SHORT'; setup='compression_down_break'

    # Distribution: top-range loss/reclaim failure -> short.
    elif row.state == 'DISTRIBUTION':
        if row.close < row.ema50 and p_dn >= p_thr-0.03 and p_dn >= p_up:
            side='SHORT'; setup='distribution_break'

    # Capitulation has two tradable transitions: continuation breakdown or recovery snapback.
    elif row.state == 'CAPITULATION':
        if row.close < d.lo48.iloc[i-1] and p_dn >= p_thr and p_dn-p_up >= 0.02:
            side='SHORT'; setup='capitulation_continuation'
        elif row.close > row.ema20 and row.green_big and p_up >= p_thr and p_up > p_dn:
            side='LONG'; setup='capitulation_recovery'

    # Recovery: mean-reversion leg after reclaiming fast EMA.
    elif row.state == 'RECOVERY':
        if row.close > row.ema50 and p_up >= p_thr-0.02 and p_up >= p_dn:
            side='LONG'; setup='recovery_continuation'

    # Expansion: trade pullback/reclaim inside mini-trend.
    elif row.state == 'EXPANSION_UP':
        touched = row.low <= max(row.ema20, row.ema50)
        if touched and row.close > row.ema20 and p_up >= p_thr-0.03 and p_up >= p_dn:
            side='LONG'; setup='expansion_pullback_long'
    elif row.state == 'EXPANSION_DOWN':
        touched = row.high >= min(row.ema20, row.ema50)
        if touched and row.close < row.ema20 and p_dn >= p_thr-0.03 and p_dn >= p_up:
            side='SHORT'; setup='expansion_pullback_short'

    return side, setup, p_up, p_dn, n


def backtest(sym: str, d: pd.DataFrame, prob_train: pd.DataFrame, run_df: pd.DataFrame, p_thr=0.50, edge=0.035):
    table = build_prob_table(prob_train)
    equity = INIT_CASH; trades=[]; i=100
    while i < len(run_df)-2:
        side, setup, p_up, p_dn, n = make_signal(run_df, i, table, p_thr=p_thr, edge=edge)
        if side is None:
            i += 1; continue
        entry_i = i+1
        raw = float(run_df.open.iloc[entry_i])
        entry = raw*(1+SLIP) if side=='LONG' else raw*(1-SLIP)
        row = run_df.iloc[i]
        a = float(row.atr14)
        if not math.isfinite(a) or a <= 0:
            i += 1; continue
        if side == 'LONG':
            sl = min(float(row.lo48), entry - 2.2*a)
            # cap absurdly wide structure stop
            sl = max(sl, entry - 3.5*a)
            risk = entry - sl; tp = entry + 2.2*risk
        else:
            sl = max(float(row.hi48), entry + 2.2*a)
            sl = min(sl, entry + 3.5*a)
            risk = sl - entry; tp = entry - 2.2*risk
        if risk <= 0 or risk/entry > 0.12 or risk/entry < 0.003:
            i += 1; continue

        max_j = min(len(run_df)-1, entry_i+96)
        exit_raw = float(run_df.close.iloc[max_j]); exit_i=max_j; reason='TIME'
        for j in range(entry_i, max_j+1):
            rj = run_df.iloc[j]
            if side == 'LONG':
                if rj.low <= sl:
                    exit_raw = sl; exit_i=j; reason='SL'; break
                if rj.high >= tp:
                    exit_raw = tp; exit_i=j; reason='TP'; break
                if j > entry_i and rj.state in ['DISTRIBUTION','CAPITULATION','EXPANSION_DOWN'] and rj.close < rj.ema50:
                    exit_raw = float(rj.close); exit_i=j; reason='STATE_EXIT'; break
            else:
                if rj.high >= sl:
                    exit_raw = sl; exit_i=j; reason='SL'; break
                if rj.low <= tp:
                    exit_raw = tp; exit_i=j; reason='TP'; break
                if j > entry_i and rj.state in ['RECOVERY','EXPANSION_UP'] and rj.close > rj.ema50:
                    exit_raw = float(rj.close); exit_i=j; reason='STATE_EXIT'; break
        if side == 'LONG':
            ex = exit_raw*(1-SLIP); gross = ex/entry - 1
        else:
            ex = exit_raw*(1+SLIP); gross = entry/ex - 1
        net = gross - 2*FEE
        equity *= 1+net
        trades.append(Trade(sym, side, setup, str(row.state), run_df.index[entry_i], run_df.index[exit_i], round(entry,8), round(ex,8), round(sl,8), round(tp,8), round(float(p_up),3), round(float(p_dn),3), int(n), int(exit_i-entry_i+1), reason, 100*net, equity))
        i = exit_i + 1
    return trades, summarize(sym, trades)


def summarize(sym, trades):
    if not trades:
        return dict(pair=sym,trades=0,win_rate=0,return_pct=0,final=INIT_CASH,profit_factor=0,expectancy=0,max_dd=0,sharpe=0,avg_bars=0)
    r = np.array([t.net_pct/100 for t in trades]); wins=r[r>0]; losses=r[r<=0]
    eq=np.array([INIT_CASH]+[t.equity for t in trades]); peak=np.maximum.accumulate(eq); dd=(eq/peak-1)*100
    return dict(pair=sym,trades=len(trades),win_rate=round(100*len(wins)/len(trades),1),return_pct=round(100*(eq[-1]/INIT_CASH-1),1),final=round(eq[-1],2),profit_factor=round(wins.sum()/abs(losses.sum()),2) if len(losses) and abs(losses.sum())>0 else np.inf,expectancy=round(100*r.mean(),3),max_dd=round(abs(dd.min()),1),sharpe=round(r.mean()/r.std(ddof=1)*math.sqrt(len(r)),2) if len(r)>1 and r.std(ddof=1)>0 else 0,avg_bars=round(np.mean([t.bars for t in trades]),1))


def split_df(d):
    n=len(d); i1=int(n*.6); i2=int(n*.8)
    return d.iloc[:i1].copy(), d.iloc[i1:i2].copy(), d.iloc[i2:].copy()


def df_to_md(df):
    if len(df)==0: return 'No rows.'
    cols=list(df.columns); lines=['| '+' | '.join(cols)+' |','| '+' | '.join(['---']*len(cols))+' |']
    for _,r in df.iterrows(): lines.append('| '+' | '.join(str(r[c]) for c in cols)+' |')
    return '\n'.join(lines)


def state_report(sym, d):
    vc = d.state.value_counts(normalize=True).mul(100).round(1).to_dict()
    counts = d.state.value_counts().to_dict()
    rows=[]
    for st,g in d.groupby('state'):
        rows.append({'pair':sym,'state':st,'bars':len(g),'pct':vc.get(st,0),'p_up_event':round(100*(g.future_event==1).mean(),1),'p_down_event':round(100*(g.future_event==-1).mean(),1),'median_adx':round(g.adx14.median(),1),'median_range_pos':round(g.range_pos96.median(),2)})
    return rows


def main():
    syms=['BTC','ETH','SOL','BNB','HYPE','ENA','AVAX']
    all_stats=[]; all_trades=[]; all_states=[]; wf_rows=[]
    for sym in syms:
        d=prepare(sym)
        if d.empty: continue
        print(sym, len(d), d.index[0], d.index[-1])
        all_states += state_report(sym,d)
        trn,val,tst=split_df(d)
        # full-period in-sample diagnostic: probability from first 60%, run on full remaining 40% only
        for split_name, run in [('val',val),('test',tst),('valtest',pd.concat([val,tst]))]:
            trades, st = backtest(sym, d, trn, run)
            st['split']=split_name; wf_rows.append(st)
            for t in trades:
                td=asdict(t); td['split']=split_name; all_trades.append(td)
    wf=pd.DataFrame(wf_rows).sort_values(['split','sharpe','return_pct'], ascending=[True,False,False])
    trades_df=pd.DataFrame(all_trades)
    states_df=pd.DataFrame(all_states)
    wf.to_csv(OUT/'market_structure_wf_stats.csv',index=False)
    trades_df.to_csv(OUT/'market_structure_trades.csv',index=False)
    states_df.to_csv(OUT/'market_structure_state_profile.csv',index=False)

    cols=['pair','split','trades','win_rate','return_pct','max_dd','profit_factor','expectancy','sharpe','avg_bars','final']
    rep=['# Market Structure Transition Strategy', '',
         'Classifier states: EXPANSION_UP, EXPANSION_DOWN, COMPRESSION, DISTRIBUTION, CAPITULATION, RECOVERY, NEUTRAL.', '',
         'Probability table is built from the first 60% of each pair only, then applied to validation/test.', '',
         '## Walk-forward stats', '', df_to_md(wf[cols]), '',
         '## State profile', '', df_to_md(states_df.sort_values(['pair','state']))]
    if len(trades_df):
        setup = trades_df.groupby(['split','sym','setup','side']).agg(trades=('net_pct','size'), win_rate=('net_pct',lambda x: round(100*(x>0).mean(),1)), ret_sum=('net_pct',lambda x: round(x.sum(),1)), avg=('net_pct',lambda x: round(x.mean(),2))).reset_index().sort_values(['split','ret_sum'],ascending=[True,False])
        setup.to_csv(OUT/'market_structure_setup_breakdown.csv',index=False)
        rep += ['', '## Setup breakdown', '', df_to_md(setup.head(80))]
    (OUT/'market_structure_report.md').write_text('\n'.join(rep),encoding='utf-8')
    print(wf[cols].to_string(index=False))
    print('Saved results/market_structure_report.md')

if __name__=='__main__':
    main()
