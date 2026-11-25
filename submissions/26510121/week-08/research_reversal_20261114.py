"""Read-only reversal research: official scoring geometry and mature trade cohorts.

No marketplace mutations, model calls, policy writes or registration changes.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import math
import sqlite3
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = 'logs/patch-rules-source-20261114T045923368597Z.json'
KST = timezone(timedelta(hours=9))

def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))

def iso(ts):
    return datetime.fromtimestamp(ts, KST).isoformat()

def validate(result):
    """Check rounded-board scenarios with exact rational arithmetic."""
    d=result['buyer']['deals']
    s=Fraction(str(result['buyer']['saved']))
    for row in result['buyer_saved_target_work']:
        target=Fraction(str(row['target_saved']))
        for reward, n in row['additional_deals_by_mean_reward'].items():
            if n is None:continue
            r=Fraction(reward)
            assert (s*(d+100)+n*r)/(d+100+n)>=target
            assert (s*(d+100)+(n-1)*r)/(d+99+n)<target
    from r3.buyer_scoring import contribution,MARGIN
    for row in result['joint_value_scenarios']:
        L,C,q=row['list'],row['cost'],row['failure']
        p=row['price_upper_for_buyer']
        if C<=p<=L:
            assert abs(contribution(p,L,q,q,score_formula=MARGIN,unit_cost=C)-row['buyer_target'])<1e-10
        net=(1-q)*p-C
        assert abs(net-row['seller_expected_book_at_buyer_target'])<1e-8
        assert row['positive_joint_value_feasible']==(net>0)
    assert all(s<=row['saved_scenario']<=1 for row in result['buyer_scenarios_at_observed_rate'])
    return {'exact_fraction_buyer_target_checks':'passed','price_frontier_payoff_checks':'passed',
        'scope':'Arithmetic and current scoring implementation only; no realized strategy superiority asserted.'}

def main():
    source = read(SOURCE)
    board = json.loads(source['sources']['https://market.ourolabs.ai/api/board']['text'])
    end = datetime.fromisoformat(source['at']).timestamp()
    deadline = datetime(2026, 11, 17, 23, 59, tzinfo=KST).timestamp()
    remaining = (deadline-end)/3600
    catalog = read('.market/catalog.json')
    base = read('logs/autonomous-deploy-before-20261113T070936759881Z.json')
    old_buyer = next(r for r in base['board']['buyers'] if r['name']=='seochanit')
    me_b = next(r for r in board['buyers'] if r['name']=='seochanit')
    me_s = next(r for r in board['shops'] if r['name']=='seochanit')
    period = (end-base['at'])/3600
    observed_rate = (me_b['deals']-old_buyer['deals'])/period
    d, s = me_b['deals'], me_b['saved']
    n100 = math.floor(remaining*observed_rate)
    top_buyers = sorted(board['buyers'],key=lambda r:r.get('all',0),reverse=True)[:5]
    targets = []
    for target in sorted(set([.77,.80,.8126,.85]+[r['saved'] for r in top_buyers])):
        needed = {}
        for reward in (.85,.90,.95,1.):
            needed[str(reward)] = math.ceil((target-s)*(d+100)/(reward-target)) if s<target<reward else None
        targets.append({'target_saved':target,'additional_deals_by_mean_reward':needed})
    throughput = [{'future_mean_reward':r,'additional_deals':n100,
        'saved_scenario':(s*(d+100)+n100*r)/(d+100+n100)} for r in (.85,.9,.95,1.)]
    from r3.meta import eligible_rows, percentile
    cohort=eligible_rows(board,'buyer','seochanit')
    def buyer_proxy(saved,deals,posts,forum):
        proposed=dict(me_b,saved=saved,deals=deals,posts=posts,forum=forum)
        return sum(w*percentile([r[k] for r in cohort if r['name']!='seochanit']+[proposed[k]],proposed[k])
            for k,w in [('saved',.5),('deals',.1),('posts',.1)])+.3*forum
    current_proxy=buyer_proxy(s,d,me_b['posts'],me_b['forum'])
    for row in throughput:
        row['anchored_all_if_peers_frozen_saved_only']=me_b['all']+buyer_proxy(
            row['saved_scenario'],d,me_b['posts'],me_b['forum'])-current_proxy
        row['anchored_all_if_peers_frozen_with_future_deals']=me_b['all']+buyer_proxy(
            row['saved_scenario'],d+n100,me_b['posts'],me_b['forum'])-current_proxy
    thresholds = {}
    for role,own,keys in [('shops',me_s,('book','revenue','stars_rank')),('buyers',me_b,('saved','deals','posts'))]:
        thresholds[role] = {}
        for key in keys:
            above = sorted((r for r in board[role] if type(r.get(key)) in (int,float) and r[key]>own[key]),key=lambda r:r[key])
            below = sorted((r for r in board[role] if type(r.get(key)) in (int,float) and r[key]<own[key]),key=lambda r:r[key],reverse=True)
            thresholds[role][key] = {'value':own[key], 'next_above':[{ 'name':r['name'],'value':r[key], 'gap':r[key]-own[key]} for r in above[:3]],
                'next_below':[{'name':r['name'],'value':r[key], 'gap':own[key]-r[key]} for r in below[:1]]}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind, start):
            return [dict(json.loads(data),_ts=ts,_id=eid) for eid,ts,data in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid', (kind,start,end))]
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None
        own_sales = {r['unit']:r for r in events('shop_settled',end-172800) if r.get('unit')}
        broken = {r['unit']:r for r in events('public_broken',end-172800) if r.get('unit')}
        goods = defaultdict(list)
        for unit,r in own_sales.items():
            if catalog.get(r.get('sku'),{}).get('kind')=='goods' and r['_ts']<=end-22200:
                goods[r.get('grade')].append((r,broken.get(unit)))
        mature = {}
        for grade,rows in goods.items():
            costs = [r['cost']/catalog[r['sku']]['list_price'] for r,b in rows]
            q = sum(bool(b) for r,b in rows)/len(rows)
            ratio = statistics.mean(costs)
            mature[grade] = {'n':len(rows),'broken':sum(bool(b) for r,b in rows),
                'observed_failure_fraction':q, 'mean_cost_ratio':ratio,
                'mean_unitwise_zero_storage_breakeven_saved':statistics.mean(1-q/(1-c) for c in costs if c<1) if any(c<1 for c in costs) else None,
                'cohort_selected_not_causal':True}
        reviews = events('shop_review_observed',end-172800)
        unique = {}
        for r in reviews:
            if r.get('identity_known') and r.get('author') and type(r.get('stars')) in (int,float):
                unique[(r['author'],r.get('ledger'))]=r
        accounts = defaultdict(list)
        for r in unique.values():accounts[r['author']].append(r['stars'])
        reputation = {'review_rows':len(unique),'published_reviews':me_s['reviews'],
            'observed_accounts':len(accounts),'account_mean_proxy':statistics.mean(statistics.mean(v) for v in accounts.values()) if accounts else None,
            'accounts_with_at_most_10_observed_reviews':sum(len(v)<=10 for v in accounts.values()),
            'account_weight_is_partial_not_official_history':True,
            'largest_possible_single_5star_proxy_increments':sorted(
                [(5-statistics.mean(v))/(len(v)+1)/len(accounts) for v in accounts.values()],reverse=True)[:5]}
        public = events('public_settle',end-21600)
        ext = [r for r in public if r.get('buyer')!='seochanit' and (r.get('item') or r.get('sku')) in catalog]
        group=defaultdict(list)
        for r in ext:group[r.get('sku') or r.get('item')].append(r)
        competition=read('.market/market_stock_counts.json')
        inv=read('.market/inventory.json')
        raw_surge=inv.get('surge',[])
        surge={r['sku']:r['factor'] for r in raw_surge if isinstance(r,dict) and r.get('sku') and r.get('factor')} if isinstance(raw_surge,list) else {}
        opportunities=[]
        for sku,rows in group.items():
            if len(rows)<20 or len({r.get('buyer') for r in rows})<3:continue
            p=catalog[sku];com=competition.get('counts',{}).get(sku)
            prices=[r.get('amount',r.get('price')) for r in rows]
            prices=[v for v in prices if type(v) in (int,float)]
            if not prices:continue
            terms=defaultdict(list)
            for r in rows:
                price=r.get('amount',r.get('price'))
                if type(price) in (int,float):terms[(r.get('grade'),r.get('warranty_hours') or 0)].append(price)
            opportunities.append({'sku':sku,'name':p['name'],'kind':p['kind'],'category':p.get('category'),
                'list_price':p['list_price'],'external_settlements':len(rows),
                'external_buyers':len({r.get('buyer') for r in rows}),
                'current_competitor_shops':com,'latest_settlement':iso(max(r['_ts'] for r in rows)),
                'supply_surge_factor_from_inventory':surge.get(sku),
                'observed_median_price':statistics.median(prices),
                'terms':[{'grade':g,'warranty_hours':w,'n':len(ps),'median_price':statistics.median(ps)} for (g,w),ps in terms.items()],
                'observed_revenue_per_current_competitor_proxy':sum(prices)/max(1,com or 0),
                'proxy_not_expected_own_sales':True})
        opportunities.sort(key=lambda r:r['observed_revenue_per_current_competitor_proxy'],reverse=True)
        freshness = {'snapshot_at':iso(time.time()),'board_cutoff_at':iso(end),'feed_caught_up_at':kv('public_feed_caught_up_at'),
            'feed_seq':kv('public_ledger_seq'),'control':kv('market_control'),
            'last_research':kv('research-last-result'),
            'latest_public_settle_at':iso(max((r['_ts'] for r in public),default=0)),
            'competition_snapshot_at':iso(competition.get('at',0))}
    examples=[]
    for ratio,q in [(.65,0),(.65,.05),(.65,.10),(.35,.30)]:
        L=1_000_000;C=ratio*L;target=.80
        upper=L-target/(1-q)*(L-C)
        lower=C/(1-q)
        examples.append({'list':L,'cost':C,'failure':q,'buyer_target':target,
            'price_upper_for_buyer':upper,'price_lower_for_seller':lower,
            'positive_joint_value_feasible':upper>lower,
            'seller_expected_book_at_buyer_target':(1-target)*(L-C)-q*L,
            'fully_covered_refund_assumption':True,'illustrative_not_measured':True})
    result = {'source':SOURCE,'captured_at':iso(end),'remaining_hours_to_official_end':remaining,
        'shop':{k:me_s.get(k) for k in ('name','all','book','revenue','stars_rank','deals','stock_units')},
        'buyer':{k:me_b.get(k) for k in ('name','all','saved','deals','forum','posts')},
        'buyer_leaders':[{k:r.get(k) for k in ('name','all','saved','deals','forum','posts')} for r in top_buyers],
        'buyer_observed_deals_per_hour':observed_rate,'buyer_rate_comparison_hours':period,
        'buyer_saved_target_work':targets,'buyer_scenarios_at_observed_rate':throughput,
        'maximum_future_buyer_posts_from_now':math.floor(remaining*12),
        'forum_all_gain_if_rating_reaches_one':.3*(1-me_b['forum']),
        'component_thresholds':thresholds,'own_mature_goods':mature,'review_account_geometry':reputation,
        'external_settlement_opportunities':opportunities[:12], 'freshness':freshness,
        'joint_value_scenarios':examples,
        'paid_second_dislike_vote_cost_lower_bound':.03*me_s['book'],
        'limits':['Official published scores are rounded; scoring-cohort/tie details are not exactly reproduced.',
            'Buyer throughput and perfect future rewards are scenarios, not forecasts; peers continue changing.',
            'The public ledger may be incomplete while catching up; break counts can be downward-biased.',
            'Current competitor stock counts and historical sales do not identify causal latent demand.',
            'Both the 48h reviewer-account proxy and grade cohorts are selected local samples.',
            'Joint-value price examples assume known unit cost, settled food validity or full goods warranty; storage/expiry and uncertainty must be added.']}
    result['validation']=validate(result)
    name='logs/reversal-research-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'
    (ROOT/name).open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',name)
    compact={k:v for k,v in result.items() if k not in ('freshness','external_settlement_opportunities')}
    print(json.dumps(compact,ensure_ascii=True))

if __name__=='__main__':main()
