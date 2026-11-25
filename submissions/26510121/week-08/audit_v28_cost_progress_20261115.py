"""Read-only cost/action attribution and v28 execution quality audit."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import json
import re
import sqlite3
import time
from r3.state import ROOT,read_json

BASE='logs/autonomous-deploy-before-20261114T055614486450Z.json'
SOURCE='logs/v28-progress-source-20261115T022256766670Z.json'
DIAG='logs/progress-diagnosis-20261115T022420511260Z.json'
KST=timezone(timedelta(hours=9))

def semantic(row):
    return json.dumps({k:row.get(k) for k in ('operating_policy','operating_intents','forum_focus','shop_plan_recommendation','trial_review_approved')},sort_keys=True)

def main():
    diag=read_json(ROOT/DIAG);src=read_json(ROOT/SOURCE)
    lo=read_json(ROOT/BASE)['at'];hi=src['responses']['/api/board']['finished_at'];now=time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind,start=lo,end=hi):
            return [dict(json.loads(raw),_id=eid,_ts=ts) for eid,ts,raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid',(kind,start,end))]
        def kv(k):
            row=db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()
            return json.loads(row[0]) if row else {}
        usage=events('model_usage');costs={};byday=defaultdict(lambda:{'calls':0,'usd':0})
        for row in usage:
            key=row.get('role','unknown')+' / '+row.get('model','unknown');u=row.get('usage') or {}
            b=costs.setdefault(key,{'calls':0,'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'usd':0})
            b['calls']+=1;b['input_tokens']+=u.get('input_tokens',0);b['output_tokens']+=u.get('output_tokens',0)
            b['cached_input_tokens']+=(u.get('input_tokens_details') or {}).get('cached_tokens',0)
            b['usd']+=row.get('estimated_usd',0)
            day=datetime.fromtimestamp(row['_ts'],KST).date().isoformat();byday[day]['calls']+=1;byday[day]['usd']+=row.get('estimated_usd',0)
        pubs=events('market_research_published');signatures=[semantic(row) for row in pubs]
        alternatives=Counter(signatures);changes=sum(a!=b for a,b in zip(signatures,signatures[1:]))
        caps=events('capability_plan_validated');tool_sets=Counter(tuple(sorted(r.get('tools',[]))) for r in caps)
        experiments=events('framework_experiment');feedback=events('feedback_policy_created')
        policies=Counter(r.get('id') for r in feedback);decisions=events('feedback_main_choice')
        errors={kind:dict(Counter(r.get('error_type',r.get('reason','unknown')) for r in events(kind)))
            for kind in ('cycle_supervisor_error','analytical_role_error','buyer_worker_error','market_research_deferred')}
        skips=events('buyer_economic_skip_v28');pauses=events('buyer_economic_routes_paused')
        last_control=kv('market_control');heartbeat=kv('cycle-heartbeat')
        purchases=events('purchase');goods=[r for r in purchases if r.get('kind')=='goods']
        mature_goods=[r for r in goods if hi-r.get('paid_at',r['_ts'])>=22200]
        outcome_rows=events('outcome',lo-172800);outcomes={}
        for row in sorted(outcome_rows,key=lambda r:r['_ts'],reverse=True):
            oid=row.get('order');old=outcomes.get(oid)
            if old is None or row.get('canonical') and not old.get('canonical'):outcomes[oid]=row
        quality=Counter()
        for p in goods:
            o=outcomes.get(p['order'],{});age=hi-p.get('paid_at',p['_ts'])
            quality['purchases']+=1
            if o.get('canonical') and o.get('broken'):
                quality['confirmed_broken']+=1
                quality['confirmed_full_refund']+=o.get('refund_status')=='confirmed' and o.get('refunded',0)>=p['price']
            if age<22200:quality['under_maturity_horizon']+=1
            else:
                quality['past_maturity_horizon']+=1
                quality['canonical_mature_outcome']+=bool(o.get('canonical') and o.get('matured'))
        refunds=events('public_refund')+events('public_broken')
        old_sales={r.get('unit'):r for r in events('shop_settled',lo-172800,lo) if r.get('unit')}
        new_sales={r.get('unit'):r for r in events('shop_settled') if r.get('unit')}
        refund_units={}
        for r in refunds:
            unit=r.get('unit');amount=r.get('refunded',r.get('amount'))
            if unit in old_sales or unit in new_sales:
                if type(amount) in (int,float) and amount>=0:refund_units[unit]=max(refund_units.get(unit,0),amount)
        refund_cohorts={k:{'units':sum(u in cohort for u in refund_units),
            'amount':sum(a for u,a in refund_units.items() if u in cohort)} for k,cohort in [('before_v28',old_sales),('since_v28',new_sales)]}
        cat=read_json(ROOT/'.market/catalog.json');before_inventory=read_json(ROOT/BASE)['inventory']
        starting_units=before_inventory['units'];starting_goods_cost=sum(u['cost'] for u in starting_units if u.get('kind')=='goods')
        baseline_expired=[u for u in starting_units if u.get('kind')=='food' and lo<=u.get('expires_at',0)<hi and u['unit'] not in new_sales]
        new_goods=[r for r in new_sales.values() if cat.get(r.get('sku'),{}).get('kind')=='goods']
        maturity={'sold_goods':len(new_goods),'at_least_6h10m_old':sum(hi-r.get('paid_at',r['_ts'])>=22200 for r in new_goods)}
        names=defaultdict(set)
        for p in cat.values():names[p.get('name')].add(p.get('kind'))
        goods_buys=[];unclassified=0
        for line in (ROOT/'logs/auto-join-20261114T055722661399Z.txt').read_text(encoding='utf-8',errors='replace').splitlines():
            m=re.match(r'^(\d\d):(\d\d):(\d\d) \[supply\] (\d+) x (.*?) for (\d+), balance (\d+)',line)
            if not m:continue
            # This runtime started on Nov14 and only crosses midnight once.
            hour=int(m[1]);day=14 if hour>=14 else 15
            at=datetime(2026,11,day,hour,int(m[2]),int(m[3]),tzinfo=KST).timestamp()
            if not lo<=at<hi:continue
            kinds=names.get(m[5],set())
            if kinds=={'goods'}:goods_buys.append((at,int(m[6])))
            elif kinds!={'food'}:unclassified+=int(m[6])
        exposure=starting_goods_cost*(hi-lo)+sum(cost*(hi-at) for at,cost in goods_buys)
        exposure-=sum(r['cost']*(hi-r.get('paid_at',r['_ts'])) for r in new_goods)
        storage={'integrated_goods_cost_seconds':exposure,'estimated_storage':exposure*.01/86400,
            'unclassified_supply_cost':unclassified,'baseline_food_expired_unsold_units':len(baseline_expired),
            'baseline_food_expired_unsold_cost':sum(u['cost'] for u in baseline_expired),
            'scope':'Reconstructed flow estimate; expiry only covers starting inventory with no recorded sale in the interval.'}
    total=sum(v['usd'] for v in costs.values());calls=sum(v['calls'] for v in costs.values())
    for b in costs.values():b['cost_share']=b['usd']/total if total else None
    old=diag['own']['buyers']['before'];new=diag['own']['buyers']['after'];n=new['deals']-old['deals']
    implied=((new['saved']*(new['deals']+100)-old['saved']*(old['deals']+100))/n) if n>0 else None
    result={'sources':[BASE,SOURCE,DIAG],'window':diag['window'],'roles':costs,'totals':{'calls':calls,'usd':total},
        'by_kst_day':dict(byday),'research':{'published':len(pubs),'approved_trials':sum(bool(r.get('trial_review_approved')) for r in pubs),
            'unique_operating_signatures':len(alternatives),'consecutive_identical':max(0,len(signatures)-1-changes),
            'signature_changes':changes,'signature_counts':[{'policy':json.loads(k),'count':v} for k,v in alternatives.most_common(8)],
            'queued_profiles':len(events('research_strategy_queued'))},
        'capabilities':{'validated_plans':len(caps),'unique_tool_sets':len(tool_sets),
            'tool_sets':[{'tools':list(k),'count':v} for k,v in tool_sets.most_common()]},
        'framework_experiments':dict(Counter(r.get('action') for r in experiments)),
        'numeric_policies':{'compiled':len(feedback),'unique_semantic_ids':len(policies),'main_decisions':len(decisions)},
        'buyer_economic_skips':len(skips),'buyer_pause_records':len(pauses),'errors':errors,'buyer_goods_quality':dict(quality),
        'refund_cohorts':refund_cohorts,'implied_saved_change_per_added_deal':implied,
        'shop_goods_maturity':maturity,'storage_and_starting_food_expiry':storage,
        'current':{'snapshot_at':now,'heartbeat_at':heartbeat.get('at'),'research_at':kv('research-last-result').get('at'),
            'control':{k:last_control.get(k) for k in ('status','reasons','pause_restock')},
            'feed_at':kv('public_feed_caught_up_at'),'research_readiness':kv('research-ready-status')},
        'sql':'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid',
        'limits':['Costs are configured token-price estimates, not the account-wide provider invoice; other applications are excluded.',
            'Repeated signatures can be justified revalidation; no cost is attributed as causal profit or score uplift.',
            'Implied Saved change per added deal mixes new contributions, retrospective quality changes and rounded board values.',
            'Known refund sale cohorts use a bounded 48h lookback; incomplete joins remain possible.',
            'Current live KV observations occur after the official board cutoff.']}
    p=ROOT/'logs'/('v28-cost-progress-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps({k:v for k,v in result.items() if k not in ('sources','sql','limits','roles')},ensure_ascii=True))
    print('ROLES',json.dumps(costs,ensure_ascii=True))

if __name__=='__main__':main()
