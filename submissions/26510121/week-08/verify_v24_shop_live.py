"""Read-only proof of runtime revision, actual contract execution and observer health."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy
from autonomous_status import ReadJournal


def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    manifest=read_json(sorted((ROOT/'logs').glob('v24-source-promotion-*.json'))[-1])
    mismatch=[r['path'] for r in manifest['files'] if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,now)
        def recent(kind,n=2000):return [r for r in j.events(kind,n) if r['_ts']>=start]
        sales=recent('shop_settled');purchases=recent('purchase');results={r['order']:r for r in recent('market_result')}
        packet=j.get('shop-reputation:v24',{});roles=j.get('analytical-role-status',{})
        errors={k:len(recent(k)) for k in ('market_control_error','cycle_supervisor_error','cycle_supply_error',
            'buyer_worker_error','analytical_role_error','objective_context_error','strategy_error')}
        candidates=[c for r in recent('prediction',500) if r.get('role')=='shop'
                    for c in r.get('candidates',[]) if c.get('action') not in (None,'hold')]
        model_usage=recent('model_usage',500)
        has_astra=any('astra' in r.get('model','').lower() for r in model_usage)
        has_astra|=any('astra' in r['model'].lower() for r in cfg['background_llm_roles'].values())
        goods=[r for r in sales if j.snapshot('catalog.json',{}).get(r.get('sku'),{}).get('kind')=='goods']
        observer=roles.get('account_reputation_observer',{})
        control=j.get('market_control',{})
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'runtime':runtime,
            'shop_revision':j.get('shop_strategy_revision'),'buyer_revision':j.get('buyer_strategy_revision'),
            'source_mismatches':mismatch,'astra_excluded':not has_astra,'roles':roles,
            'reputation_age_seconds':now-packet.get('at',0),'reputation_summary':packet.get('summary'),
            'cash_capacity':control.get('cash_capacity'),'optional_capital_caps':cfg['shop_internal_capital_caps_enabled'],
            'quantity_caps':cfg['shop_inventory_caps_enabled'],'errors':errors,
            'post_restart_shop_sales':len(sales),'post_restart_buyer_purchases':len(purchases),
            'verified_buyer_results':sum(results.get(r['order'],{}).get('verified') is True for r in purchases),
            'actual_new_goods_warranties':sorted({r.get('warranty_hours',0) for r in goods}),
            'goods_sold':len(goods),'candidate_warranties':sorted({c.get('warranty_hours',0) for c in candidates}),
            'reputation_adjusted_candidates':sum('account_reputation' in c.get('evidence',{}) for c in candidates),
            'observed_supply_units':sum(r.get('qty',0) for r in recent('supply_execution_status') if r.get('status')=='complete'),
            'below_cost_sales':sum(r.get('price',0)<r.get('cost',0) for r in sales),
            'verified':bool(not mismatch and not has_astra and observer.get('status')=='completed'
                and observer.get('at',0)>=start and now-packet.get('at',0)<180 and candidates and sales
                and all(r.get('warranty_hours',0)>=6 for r in goods) and not any(errors.values())),
            'economic_superiority_proven':False,'first_place_proven':False,'market_transactions_by_this_script':0,'model_calls':0}
    path=ROOT/'logs'/('v24-live-shop-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','source_mismatches','astra_excluded','post_restart_shop_sales',
        'post_restart_buyer_purchases','verified_buyer_results','actual_new_goods_warranties','reputation_summary',
        'observed_supply_units','below_cost_sales','errors')}))


if __name__=='__main__':main()
