"""
ANN Trend Bounce / Break / Transition Framework
───────────────────────────────────────────────
Pair-specific neural-network probability model.

Goal:
  For each pair, create candidate mini-trend decisions:
    - compression break up/down
    - distribution breakdown
    - capitulation continuation
    - capitulation recovery bounce
    - recovery continuation
    - trend-expansion pullback continuation

  Then train an ANN to estimate:
    P(candidate trade succeeds before it fails)

  Parameters are selected per pair using chronological walk-forward:
    train 60% -> fit ANN
    validation 20% -> select ANN architecture + probability threshold
    test 20% -> final unseen evaluation

This is a research framework, not a live bot.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import warnings
import numpy as np
import pandas as pd
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, brier_score_loss

from market_structure_strategy import prepare, df_to_md

warnings.filterwarnings("ignore")

OUT = Path('results')
OUT.mkdir(exist_ok=True)
INIT_CASH = 700.0
FEE = 0.0005
SLIP = 0.0010

NUM_FEATURES = [
    'atr_pct','bb_pct','atr_pct_rank','adx14','adx_slope','rsi14','rsi_slope',
    'range_pos96','ret24','ret72','ret168','ema50_slope','ema200_slope','ema800_slope',
    'vol_z','dist_ema20','dist_ema50','dist_ema200','dist_ema800','dist_hi48','dist_lo48',
    'state_age','body_pct'
]
CAT_FEATURES = ['state','setup','side','trend_bias']


@dataclass
class Candidate:
    i: int
    time: pd.Timestamp
    side: str
    setup: str
    state: str
    label: int
    label_ret: float
    entry: float
    sl: float
    tp: float
    exit_i: int
    exit_reason: str


def add_meta_features(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d['dist_ema20'] = d.close / d.ema20 - 1
    d['dist_ema50'] = d.close / d.ema50 - 1
    d['dist_ema200'] = d.close / d.ema200 - 1
    d['dist_ema800'] = d.close / d.ema800 - 1
    d['dist_hi48'] = d.close / d.hi48.shift(1) - 1
    d['dist_lo48'] = d.close / d.lo48.shift(1) - 1
    # state duration / age
    change = d.state.ne(d.state.shift()).cumsum()
    d['state_age'] = d.groupby(change).cumcount() + 1
    return d.replace([np.inf, -np.inf], np.nan).dropna().copy()


def setup_candidates_at(d: pd.DataFrame, i: int):
    row = d.iloc[i]
    out = []
    # Break from compression
    if row.state == 'COMPRESSION':
        if row.close > d.hi48.iloc[i-1]:
            out.append(('LONG', 'compression_up_break'))
        if row.close < d.lo48.iloc[i-1]:
            out.append(('SHORT', 'compression_down_break'))
    # Distribution top failure
    if row.state == 'DISTRIBUTION' and row.close < row.ema50:
        out.append(('SHORT', 'distribution_break'))
    # Capitulation: continuation or reversal bounce
    if row.state == 'CAPITULATION':
        if row.close < d.lo48.iloc[i-1]:
            out.append(('SHORT', 'capitulation_continuation'))
        if row.close > row.ema20 and bool(row.green_big):
            out.append(('LONG', 'capitulation_recovery'))
    # Recovery continuation / mean reversion leg
    if row.state == 'RECOVERY' and row.close > row.ema50:
        out.append(('LONG', 'recovery_continuation'))
    # Expansion pullbacks
    if row.state == 'EXPANSION_UP':
        if row.low <= max(row.ema20, row.ema50) and row.close > row.ema20:
            out.append(('LONG', 'expansion_pullback_long'))
    if row.state == 'EXPANSION_DOWN':
        if row.high >= min(row.ema20, row.ema50) and row.close < row.ema20:
            out.append(('SHORT', 'expansion_pullback_short'))
    return out


def trade_geometry(d: pd.DataFrame, i: int, side: str, rr: float = 1.8):
    if i >= len(d)-2:
        return None
    entry_raw = float(d.open.iloc[i+1])
    entry = entry_raw * (1+SLIP) if side == 'LONG' else entry_raw * (1-SLIP)
    row = d.iloc[i]
    a = float(row.atr14)
    if not math.isfinite(a) or a <= 0:
        return None
    if side == 'LONG':
        sl = min(float(row.lo48), entry - 2.0*a)
        sl = max(sl, entry - 3.2*a)  # cap very wide structure stops
        risk = entry - sl
        tp = entry + rr*risk
    else:
        sl = max(float(row.hi48), entry + 2.0*a)
        sl = min(sl, entry + 3.2*a)
        risk = sl - entry
        tp = entry - rr*risk
    if risk <= 0 or risk/entry < 0.003 or risk/entry > 0.12:
        return None
    return entry, sl, tp


def outcome_label(d: pd.DataFrame, i: int, side: str, horizon: int = 96, rr: float = 1.8):
    geom = trade_geometry(d, i, side, rr=rr)
    if geom is None:
        return None
    entry, sl, tp = geom
    entry_i = i + 1
    max_j = min(len(d)-1, entry_i+horizon)
    exit_raw = float(d.close.iloc[max_j]); exit_i = max_j; reason = 'TIME'
    for j in range(entry_i, max_j+1):
        hi = float(d.high.iloc[j]); lo = float(d.low.iloc[j])
        if side == 'LONG':
            if lo <= sl:
                exit_raw = sl; exit_i = j; reason = 'SL'; break
            if hi >= tp:
                exit_raw = tp; exit_i = j; reason = 'TP'; break
        else:
            if hi >= sl:
                exit_raw = sl; exit_i = j; reason = 'SL'; break
            if lo <= tp:
                exit_raw = tp; exit_i = j; reason = 'TP'; break
    if side == 'LONG':
        ex = exit_raw * (1-SLIP); gross = ex/entry - 1
    else:
        ex = exit_raw * (1+SLIP); gross = entry/ex - 1
    net = gross - 2*FEE
    label = 1 if net > 0 else 0
    return label, 100*net, entry, sl, tp, exit_i, reason


def make_dataset(d: pd.DataFrame, rr: float = 1.8) -> pd.DataFrame:
    rows = []
    for i in range(100, len(d)-98):
        setups = setup_candidates_at(d, i)
        if not setups:
            continue
        for side, setup in setups:
            out = outcome_label(d, i, side, horizon=96, rr=rr)
            if out is None:
                continue
            label, label_ret, entry, sl, tp, exit_i, reason = out
            row = d.iloc[i]
            rec = {f: row[f] for f in NUM_FEATURES}
            rec.update({
                'i': i, 'time': d.index[i], 'side': side, 'setup': setup, 'state': row.state,
                'trend_bias': str(int(row.trend_bias)), 'label': label, 'label_ret': label_ret,
                'entry': entry, 'sl': sl, 'tp': tp, 'exit_i': exit_i, 'exit_reason': reason,
                'entry_i': i+1
            })
            rows.append(rec)
    return pd.DataFrame(rows)


def split_d(d: pd.DataFrame):
    n = len(d); i1 = int(n*0.6); i2 = int(n*0.8)
    return d.iloc[:i1].copy(), d.iloc[i1:i2].copy(), d.iloc[i2:].copy()


def encode(train: pd.DataFrame, other: pd.DataFrame | None = None):
    cols = NUM_FEATURES + CAT_FEATURES
    Xtr = pd.get_dummies(train[cols], columns=CAT_FEATURES, dummy_na=False)
    if other is None:
        return Xtr, None
    Xo = pd.get_dummies(other[cols], columns=CAT_FEATURES, dummy_na=False)
    Xo = Xo.reindex(columns=Xtr.columns, fill_value=0)
    return Xtr, Xo


def build_model(hidden=(32,), alpha=0.001, seed=42):
    return Pipeline([
        ('scaler', StandardScaler()),
        ('mlp', MLPClassifier(hidden_layer_sizes=hidden, alpha=alpha, activation='relu', solver='adam',
                              learning_rate_init=0.001, max_iter=450, early_stopping=True,
                              validation_fraction=0.15, n_iter_no_change=25, random_state=seed))
    ])


def simulate_candidates(cands: pd.DataFrame, probs: np.ndarray, threshold: float):
    if cands.empty:
        return [], summary([])
    sim = cands.copy().reset_index(drop=True)
    sim['prob'] = probs
    sim = sim[sim.prob >= threshold].sort_values('entry_i')
    equity = INIT_CASH; trades=[]; last_exit = -1
    for _, r in sim.iterrows():
        if int(r.entry_i) <= last_exit:
            continue
        net = float(r.label_ret)
        equity *= 1 + net/100
        last_exit = int(r.exit_i)
        trades.append({
            'time': r.time, 'side': r.side, 'setup': r.setup, 'state': r.state,
            'prob': round(float(r.prob), 4), 'label': int(r.label), 'net_pct': net,
            'entry': float(r.entry), 'sl': float(r.sl), 'tp': float(r.tp), 'exit_reason': r.exit_reason,
            'bars': int(r.exit_i - r.entry_i + 1), 'equity': equity
        })
    return trades, summary(trades)


def summary(trades):
    if not trades:
        return dict(trades=0, win_rate=0, return_pct=0, final=INIT_CASH, profit_factor=0, expectancy=0, max_dd=0, sharpe=0, avg_prob=0, avg_bars=0)
    r = np.array([t['net_pct']/100 for t in trades]); wins = r[r>0]; losses = r[r<=0]
    eq = np.array([INIT_CASH] + [t['equity'] for t in trades]); peak = np.maximum.accumulate(eq); dd = (eq/peak - 1)*100
    return dict(trades=len(trades), win_rate=round(100*len(wins)/len(trades),1), return_pct=round(100*(eq[-1]/INIT_CASH-1),1),
                final=round(eq[-1],2), profit_factor=round(wins.sum()/abs(losses.sum()),2) if len(losses) and abs(losses.sum())>0 else np.inf,
                expectancy=round(100*r.mean(),3), max_dd=round(abs(dd.min()),1),
                sharpe=round(r.mean()/r.std(ddof=1)*math.sqrt(len(r)),2) if len(r)>1 and r.std(ddof=1)>0 else 0,
                avg_prob=round(np.mean([t['prob'] for t in trades]),3), avg_bars=round(np.mean([t['bars'] for t in trades]),1))


def objective(st):
    if st['trades'] < 5:
        return -1e9
    if st['profit_factor'] < 1.05:
        return -1e9
    return st['return_pct'] + 4*st['profit_factor'] + 0.4*st['win_rate'] - 0.8*st['max_dd'] + min(st['trades'], 30)*0.4


def train_pair(sym: str):
    d = add_meta_features(prepare(sym))
    if d.empty:
        return [], [], []
    trn_d, val_d, tst_d = split_d(d)
    train = make_dataset(trn_d)
    val = make_dataset(val_d)
    test = make_dataset(tst_d)
    print(f"{sym}: bars train/val/test={len(trn_d)}/{len(val_d)}/{len(tst_d)} candidates={len(train)}/{len(val)}/{len(test)}")
    if len(train) < 60 or train.label.nunique() < 2 or len(val) < 10:
        return [dict(pair=sym, status='not_enough_data', train_candidates=len(train), val_candidates=len(val), test_candidates=len(test))], [], []

    Xtr, Xval = encode(train, val)
    _, Xtst = encode(train, test)
    y = train.label.astype(int).values

    grids = [
        {'hidden': (24,), 'alpha': 0.001},
        {'hidden': (48,), 'alpha': 0.001},
        {'hidden': (32,16), 'alpha': 0.001},
        {'hidden': (48,24), 'alpha': 0.003},
    ]
    thresholds = [0.52, 0.56, 0.60, 0.64, 0.68, 0.72]
    best = None
    diagnostics=[]
    for gi, g in enumerate(grids):
        model = build_model(g['hidden'], g['alpha'], seed=100+gi)
        model.fit(Xtr, y)
        p_val = model.predict_proba(Xval)[:,1]
        auc = roc_auc_score(val.label, p_val) if val.label.nunique() > 1 else np.nan
        brier = brier_score_loss(val.label, p_val) if val.label.nunique() > 1 else np.nan
        for th in thresholds:
            val_trades, val_st = simulate_candidates(val, p_val, th)
            sc = objective(val_st)
            diagnostics.append({**g, 'threshold': th, 'val_auc': round(auc,3) if math.isfinite(auc) else np.nan, 'val_brier': round(brier,3) if math.isfinite(brier) else np.nan, **{f'val_{k}':v for k,v in val_st.items()}, 'score': sc})
            if best is None or sc > best['score']:
                best = {'model': model, 'grid': g, 'threshold': th, 'score': sc, 'val_st': val_st, 'auc': auc, 'brier': brier}
    if best is None or best['score'] <= -1e8:
        # Still report highest AUC config but no tradable threshold.
        best_diag = sorted(diagnostics, key=lambda x: x.get('val_auc', -999), reverse=True)[0]
        return [{**best_diag, 'pair': sym, 'status': 'no_profitable_validation_threshold', 'train_candidates': len(train), 'val_candidates': len(val), 'test_candidates': len(test)}], [], diagnostics

    p_train = best['model'].predict_proba(Xtr)[:,1]
    p_val = best['model'].predict_proba(Xval)[:,1]
    p_test = best['model'].predict_proba(Xtst)[:,1] if len(test) else np.array([])
    tr_trades, tr_st = simulate_candidates(train, p_train, best['threshold'])
    va_trades, va_st = simulate_candidates(val, p_val, best['threshold'])
    te_trades, te_st = simulate_candidates(test, p_test, best['threshold'])

    rows=[]
    for split, st in [('train', tr_st), ('val', va_st), ('test', te_st)]:
        rows.append({'pair': sym, 'split': split, 'status': 'ok', 'hidden': str(best['grid']['hidden']), 'alpha': best['grid']['alpha'], 'threshold': best['threshold'],
                     'train_candidates': len(train), 'val_candidates': len(val), 'test_candidates': len(test),
                     'val_auc': round(float(best['auc']),3) if math.isfinite(best['auc']) else np.nan,
                     'val_brier': round(float(best['brier']),3) if math.isfinite(best['brier']) else np.nan,
                     **st})
    trade_rows=[]
    for split, arr in [('train', tr_trades), ('val', va_trades), ('test', te_trades)]:
        for t in arr:
            trade_rows.append({'pair': sym, 'split': split, **t})
    return rows, trade_rows, diagnostics


def main():
    syms = ['BTC','ETH','SOL','BNB','HYPE','ENA','AVAX']
    all_rows=[]; all_trades=[]; all_diag=[]
    for sym in syms:
        rows, trades, diag = train_pair(sym)
        all_rows += rows
        all_trades += trades
        for d in diag:
            d['pair'] = sym
        all_diag += diag
    res = pd.DataFrame(all_rows)
    trd = pd.DataFrame(all_trades)
    diag = pd.DataFrame(all_diag)
    res.to_csv(OUT/'ann_transition_results.csv', index=False)
    trd.to_csv(OUT/'ann_transition_trades.csv', index=False)
    diag.to_csv(OUT/'ann_transition_diagnostics.csv', index=False)

    report = ['# ANN Trend Bounce/Break/Transition Framework', '',
              'Pair-specific MLPClassifier estimates probability that a candidate mini-trend decision succeeds before it fails.', '',
              'Chronological split: train 60%, validation 20%, test 20%. ANN architecture and probability threshold are selected on validation only.', '',
              '## Pair walk-forward results', '', df_to_md(res)]
    if len(trd):
        setup = trd.groupby(['split','pair','setup','side']).agg(
            trades=('net_pct','size'), win_rate=('net_pct', lambda x: round(100*(x>0).mean(),1)),
            ret_sum=('net_pct', lambda x: round(x.sum(),1)), avg_prob=('prob','mean'), avg_ret=('net_pct','mean')
        ).reset_index()
        setup['avg_prob'] = setup.avg_prob.round(3); setup['avg_ret'] = setup.avg_ret.round(2)
        setup = setup.sort_values(['split','ret_sum'], ascending=[True,False])
        setup.to_csv(OUT/'ann_transition_setup_breakdown.csv', index=False)
        report += ['', '## Setup breakdown', '', df_to_md(setup.head(100))]
    report += ['', '## Method', '',
               'For each candle the framework creates possible decisions: break, bounce/recovery, distribution breakdown, capitulation continuation, and expansion pullback continuation.', '',
               'Each candidate is labelled by whether TP is reached before SL over the next 96 hours. The neural network learns P(success | state, setup, volatility, range position, trend slopes, volume, distance to EMAs, etc.).', '',
               'Decision rule: trade only if predicted probability exceeds the pair-specific threshold selected on validation.']
    (OUT/'ann_transition_report.md').write_text('\n'.join(report), encoding='utf-8')
    print(res.to_string(index=False))
    print('Saved results/ann_transition_report.md')


if __name__ == '__main__':
    main()
