"""Read-only status and optional nonexecuted supply shadow; no model/network calls."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from collections import Counter
from r3.state import ROOT,read_json,policy
from r3.economy import cash_capacity,restock_candidates


class ReadJournal:
    def __init__(self,connection,cutoff):self.c,self.cutoff,self.memory=connection,cutoff,{}
    def get(self,key,default=None):
        if key in self.memory:return self.memory[key]
        row=self.c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,key,value):self.memory[key]=value  # Shadow calculations only.
    def events(self,kind,limit=500):
        return [dict(json.loads(d),_event_id=i,_ts=t,_role=r) for i,t,r,d in self.c.execute(
            'SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',
            (kind,self.cutoff,limit))]
    def snapshot(self,name,default=None):return read_json(ROOT/'.market'/name,default)


def status(shadow=False):
    now=time.time();cfg=policy()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,now)
        epoch=j.get('objective_epoch',now)
        kinds=['market_control','strategy_evaluation','strategy_change','purchase','shop_settled','restock_decision',
               'supply_blocked','restock_blocked','checkout_attempt','buyer_fast_choice','market_control_error',
               'strategy_error','merchant_context_error','forum_error','model_usage']
        counts={kind:c.execute('SELECT COUNT(*) FROM events WHERE kind=? AND ts>=? AND ts<=?',
                               (kind,epoch,now)).fetchone()[0] for kind in kinds}
        inv=j.snapshot('inventory.json',{})
        result={'checked_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                'policy_version':cfg['version'],'objective_revision':j.get('objective_revision'),'epoch':epoch,
                'runtime':j.snapshot('runtime.json',{}),'health':j.snapshot('shop-health.json',{}),
                'transport_preference':j.snapshot('transport-provider.json',{}),
                'rules':j.get('market_rules',{}),'control':j.get('market_control',{}),
                'strategies':{r:j.get('adaptive:'+r,{}) for r in ['shop','buyer']},'counts_since_epoch':counts,
                'cash_capacity':cash_capacity(inv,j,cfg,now),'cash':inv.get('balance'),
                'stock_units':len(inv.get('units',[])),'stock_skus':len({u['sku'] for u in inv.get('units',[])}),
                'quality_epoch_mature':now-epoch>=21600,'stop_at':cfg.get('end_at'),
                'recent_strategy_actions':j.events('strategy_change',4),
                'recent_shop_settlements':j.events('shop_settled',5)}
        if shadow:
            started=time.monotonic()
            hypothetical=dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
            rows=restock_candidates(hypothetical,j.snapshot('catalog.json',{}),[],j,cfg,now)
            result['shadow']={'NOT_EXECUTED':True,'seconds':time.monotonic()-started,
                'capital_commitment':sum(r['capital_commitment'] for r in rows),
                'lines':[{'sku':r['sku'],'qty':r['qty'],'capital':r['capital_commitment'],
                          'book_scenario':r['expected_delta']['book']} for r in rows]}
        return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--save',action='store_true');p.add_argument('--shadow',action='store_true')
    args=p.parse_args();result=status(args.shadow)
    if args.save:
        path=ROOT/'logs'/('autonomous-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print('SOURCE',path)
    summary={k:result[k] for k in ['checked_at','policy_version','objective_revision','counts_since_epoch','cash_capacity',
                                  'stock_units','stock_skus','quality_epoch_mature','stop_at']}
    summary['control']={k:result['control'].get(k) for k in ['status','at','leader_gap','pause_restock','reasons','suggestions']}
    if args.shadow:summary['shadow']=result['shadow']
    print(json.dumps(summary,ensure_ascii=False,indent=2))
