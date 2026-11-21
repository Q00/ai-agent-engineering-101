"""Read-only policy-path and reservation diagnostics. No market actions."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy
from autonomous_status import ReadJournal
from r3.shop_growth import target_stock


class View(ReadJournal):
    @contextmanager
    def connect(self):yield self.c
    def add(self,*args,**kwargs):pass


def main():
    now=time.time();cfg=policy()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=View(db,now);inv=j.snapshot('inventory.json');cat=j.snapshot('catalog.json')
        from r3.merchant import context
        from r3.shop_growth import demand_diagnostics
        merchant=context(j,cat,now,config=cfg);demand=j.get('shop-demand-policy:v21',{})
        held=Counter(u['sku'] for u in inv['units'])
        rejections=[r for r in j.events('stockout',2000) if now-3600<=r['_ts']<=now]
        top=Counter(r.get('sku') for r in rejections).most_common(10)
        reservations=db.execute('SELECT unit,task,until FROM reservations WHERE until>?',(now,)).fetchall()
        reserved={u for u,t,e in reservations};examples=[]
        for sku,n in top:
            g=merchant.get('skus',{}).get(sku,{});cal=g.get('demand_calibration') or {}
            rate=g.get('paid_arrival_rate_scenario',0);paid=g.get('positive_paid_while_available',0)
            hours=cal.get('coverage_hours',cfg['supply_cycle_seconds']/3600)
            examples.append({'sku':sku,'name':cat.get(sku,{}).get('name'),'stockouts_last_hour':n,
                'held':held[sku],'reserved_now':sum(u['sku']==sku and u['unit'] in reserved for u in inv['units']),
                'reasons':dict(Counter(r.get('reason') for r in rejections if r.get('sku')==sku)),
                'model':g,'active_coverage_hours':hours,
                'target_by_legacy_cycle_knob':{str(c):target_stock(rate,paid,dict(cfg,shop_demand_horizon_cycles=c),coverage_hours=hours)
                    for c in (1,1.5,2,3)}})
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'adaptive_enabled':cfg['shop_adaptive_demand_enabled'],'unit_caps_enabled':cfg['shop_inventory_caps_enabled'],
            'reservation_ttl_seconds':cfg['session_ttl_seconds'],'active_reservations':len(reservations),
            'stale_or_missing_reserved_units':sum(u not in {x['unit'] for x in inv['units']} for u,t,e in reservations),
            'demand_packet_age_seconds':now-demand.get('at',0),'merchant_demand_policy_id':merchant.get('demand_policy_id'),
            'current_diagnostics':demand_diagnostics(inv,merchant,cfg),'stockout_examples':examples,
            'code_paths':{'cycle_knob_ignored_in_adaptive':'r3/shop_growth.py:target_stock adaptive branch',
                'trend_knob_ignored_in_adaptive':'r3/economy.py:trend condition not shop_adaptive_demand_enabled',
                'exposure_is_physical_stock':'r3/demand_policy.py:fit timeline counts; reservations/grade eligibility excluded',
                'reservations_still_count_as_inventory':'r3/economy.py:restock_candidates counts all live units'},
            'limits':['Reserved-stock rejection is not proof a profitable alternative sale would have occurred.',
                'The legacy cycle knob is intentionally superseded by adaptive coverage; it should not be treated as live authority.',
                'These are current snapshots, not a reconstruction of every refusal.'],
            'market_transactions':0,'model_calls':0,'shared_state_writes':0}
    path=ROOT/'logs'/('v22-bottlenecks-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({'active_reservations':len(reservations),'current_diagnostics':result['current_diagnostics'],
        'examples':[{k:r[k] for k in ('sku','stockouts_last_hour','held','reserved_now','active_coverage_hours','target_by_legacy_cycle_knob')}
                    for r in examples[:5]]},ensure_ascii=True))


if __name__=='__main__':main()
