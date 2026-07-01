import requests
import time

class TelegramUI:
    def __init__(self, token, chat_id, store=None):
        self.token=token; self.chat_id=str(chat_id) if chat_id else ''; self.store=store; self.offset=0
        self.base=f'https://api.telegram.org/bot{token}' if token else ''

    def enabled(self): return bool(self.token and self.chat_id)

    def send(self,text):
        if not self.enabled():
            print('[TELEGRAM disabled]',text); return
        try:
            requests.post(self.base+'/sendMessage',json={'chat_id':self.chat_id,'text':text[:3900]},timeout=10)
        except Exception as e:
            print('telegram send error',e)

    def format_stats(self):
        s=self.store.stats()
        pf=s['profit_factor']; pf='∞' if pf is None else f'{pf:.2f}'
        return (f"📊 Paper bot stats\n"
                f"Balance: {s['balance']:.2f} USDT\n"
                f"Realized PnL: {s['realized_pnl']:.2f}\n"
                f"Open: {s['open_positions']} | Closed: {s['closed_positions']}\n"
                f"Win rate: {s['win_rate']:.1f}% | PF: {pf}\n"
                f"Signals: {s['signals']}")

    def handle_text(self,text):
        t=text.strip().lower()
        if t in ['/start','/help']:
            self.send('Commands: /stats /open /recent /pause /resume')
        elif t=='/stats': self.send(self.format_stats())
        elif t=='/pause': self.store.set_paused(True); self.send('Paused new entries.')
        elif t=='/resume': self.store.set_paused(False); self.send('Resumed new entries.')
        elif t=='/open':
            rows=self.store.open_positions()
            if not rows: self.send('No open positions.'); return
            msg='Open positions:\n'+'\n'.join([f"#{r['id']} {r['pair']} {r['side']} {r['setup']} entry={r['entry']:.4g} SL={r['sl']:.4g} TP={r['tp']:.4g} margin={r['margin']:.2f}" for r in rows])
            self.send(msg)
        elif t=='/recent':
            rows=self.store.recent('signals',10)
            if not rows: self.send('No signals.'); return
            msg='Recent signals:\n'+'\n'.join([f"#{r['id']} {r['pair']} {r['side']} {r['setup']} p={r['probability']:.2f} {r['status']} {r['reason'] or ''}" for r in rows])
            self.send(msg)

    def poll_once(self):
        if not self.enabled(): return
        try:
            r=requests.get(self.base+'/getUpdates',params={'timeout':1,'offset':self.offset},timeout=5).json()
            for u in r.get('result',[]):
                self.offset=max(self.offset,u['update_id']+1)
                msg=u.get('message') or {}
                chat=str((msg.get('chat') or {}).get('id',''))
                if chat != self.chat_id: continue
                if 'text' in msg: self.handle_text(msg['text'])
        except Exception as e:
            print('telegram poll error',e)
