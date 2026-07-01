from config import BotConfig
from storage import Store
from data_client import fetch_okx
from strategy import StrategyBrain

class PaperEngine:
    def __init__(self, cfg: BotConfig, store: Store, brain: StrategyBrain):
        self.cfg=cfg; self.store=store; self.brain=brain
        self.store.init_balance(cfg.starting_balance)

    def quote(self, pair):
        df=fetch_okx(pair,'15m',10)
        if df.empty: return None
        return float(df.close.iloc[-1])

    def used_margin(self):
        return sum(float(p['margin']) for p in self.store.open_positions())

    def calc_size(self, sig):
        equity=self.store.balance()
        risk_usdt=equity*(self.cfg.risk_per_trade_pct/100)
        stop_pct=sig['risk_pct']
        notional=risk_usdt/stop_pct
        notional=min(notional, equity*(self.cfg.max_notional_pct/100))
        margin=notional/self.cfg.leverage
        if margin > equity*(self.cfg.max_margin_per_position_pct/100):
            margin=equity*(self.cfg.max_margin_per_position_pct/100)
            notional=margin*self.cfg.leverage
        qty=notional/sig['entry']
        return notional, margin, qty

    def can_open(self, margin):
        equity=self.store.balance()
        if len(self.store.open_positions()) >= self.cfg.max_open_positions:
            return False,'max_open_positions'
        if self.used_margin()+margin > equity*(self.cfg.max_total_margin_pct/100):
            return False,'insufficient_margin_cap'
        return True,''

    def process_pair(self, pair):
        df15=fetch_okx(pair,'15m',500)
        df1h=fetch_okx(pair,'1h',1000)
        df4h=fetch_okx(pair,'4h',600)
        if df15.empty or df1h.empty or df4h.empty:
            return None
        sig=self.brain.latest_signal(pair,df15,df1h,df4h,rr=self.cfg.tp_r_multiple)
        if not sig: return None
        # Deduplicate same pair/setup/side/trigger time.
        last_key=self.store.get_state('last_signal_key',{})
        key=f"{pair}|{sig['side']}|{sig['setup']}|{sig['trigger_time']}"
        if last_key.get(pair)==key: return None
        notional,margin,qty=self.calc_size(sig)
        sig.update({'notional':notional,'margin':margin})
        can,reason=self.can_open(margin)
        if self.store.paused() or self.cfg.paused:
            sig.update({'status':'SKIPPED','reason':'paused'})
            self.store.add_signal(sig); return sig
        if not can:
            sig.update({'status':'SKIPPED','reason':reason})
            self.store.add_signal(sig); return sig
        sig.update({'status':'OPENED','reason':''})
        signal_id=self.store.add_signal(sig)
        self.store.add_position({**sig,'signal_id':signal_id,'qty':qty,'leverage':self.cfg.leverage})
        last_key[pair]=key; self.store.set_state('last_signal_key',last_key)
        return sig

    def update_positions(self):
        closed=[]
        for p in self.store.open_positions():
            px=self.quote(p['pair'])
            if px is None: continue
            side=p['side']; reason=None; exit_price=None
            if side=='LONG':
                if px <= p['sl']: reason='SL'; exit_price=p['sl']
                elif px >= p['tp']: reason='TP'; exit_price=p['tp']
            else:
                if px >= p['sl']: reason='SL'; exit_price=p['sl']
                elif px <= p['tp']: reason='TP'; exit_price=p['tp']
            if reason:
                notional=float(p['notional']); entry=float(p['entry'])
                gross = (exit_price/entry-1) if side=='LONG' else (entry/exit_price-1)
                # Worst-case simulation: apply configured fee+slippage on entry+exit as drag.
                drag=2*((self.cfg.taker_fee_pct+self.cfg.slippage_pct)/100)
                pnl_pct=gross-drag
                pnl=notional*pnl_pct
                fees=notional*2*(self.cfg.taker_fee_pct/100)
                self.store.close_position(p['id'],exit_price,reason,pnl,100*pnl_pct,fees)
                closed.append((p,reason,pnl))
        return closed

    def cycle(self):
        closed=self.update_positions()
        opened=[]; skipped=[]
        for pair in self.cfg.pairs:
            sig=self.process_pair(pair)
            if sig:
                (opened if sig['status']=='OPENED' else skipped).append(sig)
        return {'closed':closed,'opened':opened,'skipped':skipped,'stats':self.store.stats()}
