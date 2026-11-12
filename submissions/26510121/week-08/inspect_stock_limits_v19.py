"""Read-only stock-limit lookup with fresh official-rule evidence when requested."""
from collections import Counter
from datetime import datetime,timezone,timedelta
import argparse
import json
import sqlite3
import time
import httpx
from r3.state import ROOT,read_json,policy
from r3.shop_growth import inventory_limit,target_stock
from r3.economy import cash_capacity,remaining
from r3.adaptive import effective
from diagnose_v17_inventory import FrozenJournal

KST=timezone(timedelta(hours=9))


def main(refresh_rules=False):
    now=time.time();names=['inventory.json','board.json','catalog.json','operating-policy-v16.json']
    files={n:read_json(ROOT/'.market'/n,{}) for n in names}
    cutoff=min(now,(ROOT/'.market/inventory.json').stat().st_mtime)
    inv=files['inventory.json'];counts=Counter(u['sku'] for u in inv.get('units',[]) if remaining(u,cutoff)>0)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=FrozenJournal(db,cutoff,files);base=policy();cfg,strategy=effective(j,'shop',base=base)
        capacity=cash_capacity(inv,j,cfg,cutoff)
        def events(kind):
            return [dict(json.loads(raw),event_at=at) for at,raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',
                (kind,cutoff-3600,cutoff))]
        supplied=events('supply_execution_status');sales=events('shop_settled');blocks=events('restock_blocked')
        merchant=j.get('merchant_context_v5',{});selected=j.get('research-last-result',{});rows=[]
        for sku in set(counts)|{s['sku'] for s in sales}:
            g=merchant.get('skus',{}).get(sku,{})
            paid=g.get('positive_paid_while_available',0);trend=cfg.get('shop_sku_trend_factors',{}).get(sku,1)
            rate=g.get('arrival_rate_scenario',0)*trend
            if paid>=3:rate=max(.01,g.get('paid_arrival_rate_scenario',rate/max(.01,trend))*trend)
            public=sku in cfg.get('shop_public_demand_skus',[])
            target=target_stock(rate,paid,cfg,public,cfg.get('shop_public_demand_caps',{}).get(sku,6))
            rows.append({'sku':sku,'held':counts[sku],'forecast_target':target,'own_positive_paid_evidence':paid,
                'sold_60min':sum(s['sku']==sku for s in sales),'public_qualified':public,
                'arrival_rate_scenario':rate})
        data={'at_kst':datetime.fromtimestamp(now,KST).isoformat(),
            'snapshot_cutoff_kst':datetime.fromtimestamp(cutoff,KST).isoformat(),
            'snapshot_age_seconds':now-cutoff,'population':'own inventory units; no peer or own Buyer stock',
            'configuration':{k:base.get(k) for k in ['inventory_target_units','shop_inventory_ceiling','restock_max_qty','restock_max_lines']},
            'effective':{k:cfg.get(k) for k in ['shop_inventory_ceiling','shop_demand_horizon_cycles','restock_max_qty','restock_max_lines']},
            'inventory_limit_effective':inventory_limit(cfg),'physical_units':len(inv.get('units',[])),
            'unexpired_units':sum(counts.values()),'distinct_skus':len(counts),'max_held_single_sku':max(counts.values(),default=0),
            'skus_at_held_cap':sum(n>=cfg['restock_max_qty'] for n in counts.values()),
            'held_quantity_histogram':dict(Counter(counts.values())),
            'cash':inv.get('balance'),'spendable':capacity['spendable'],
            'cash_capacity':capacity,'operating_policy':selected.get('operating_policy'),
            'own_sales_60min':len({s.get('unit') for s in sales}),
            'confirmed_acquired_60min':sum(s.get('qty',0) for s in supplied if s.get('status')=='complete'),
            'supply_status_60min':dict(Counter(s.get('status') for s in supplied)),
            'restock_blocks_60min':dict(Counter(s.get('reason') for s in blocks)),
            'sku_rows':sorted(rows,key=lambda r:(r['held'],r['sold_60min']),reverse=True),
            'market_rules':j.get('market_rules',{}),
            'source_db':'.market/r3.sqlite3 (read-only transaction)',
            'snapshot_sources':[str((ROOT/'.market'/n).relative_to(ROOT)) for n in names],
            'forecast_targets_are_not_observed_hidden_demand':True,'market_transactions':0,'model_calls':0}
    if refresh_rules:
        r=httpx.get('https://market.ourolabs.ai/agent.md',timeout=15,trust_env=False)
        r.raise_for_status()
        data['official_rules']={'url':str(r.url),'status':r.status_code,'text':r.text,
            'retrieved_at_utc':datetime.now(timezone.utc).isoformat()}
    path=ROOT/'logs'/('v19-stock-limits-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(data,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:v for k,v in data.items() if k not in ('official_rules','sku_rows','cash_capacity','market_rules')},ensure_ascii=True))
    if refresh_rules:
        print('RULE_LIMIT_LINES',[line for line in r.text.splitlines() if any(x in line.lower() for x in ('qty','20 units','limit','restock','supply window'))][:25])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--refresh-rules',action='store_true');args=p.parse_args();main(args.refresh_rules)
