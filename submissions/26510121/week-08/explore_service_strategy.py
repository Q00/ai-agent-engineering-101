"""Read-only evidence for rating and buyer conversion strategy research."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import json
import math
import re
import sqlite3
import statistics
import time
from r3.state import ROOT,read_json
from review_patch_rules import Page

KST=timezone(timedelta(hours=9))
def iso(t):return datetime.fromtimestamp(t,KST).isoformat()


def main():
    now=time.time();since=now-21600;catalog=read_json(ROOT/'.market/catalog.json')
    source=read_json(ROOT/'logs/patch-rules-source-20261113T033005101119Z.json')
    snippets=[]
    for url,response in source['sources'].items():
        if response.get('status')!=200 or not ('rules' in url or url.endswith('agent.md')):continue
        p=Page();p.feed(response['text']);text=p.text if '/rules' in url else response['text'].splitlines()
        for i,line in enumerate(text):
            if any(x in line.lower() for x in ('per buyer','per reviewing','each buyer account','one average','house buyer','saved','rating')):
                snippets.append({'source':url,'text':' '.join(text[max(0,i-1):i+2])[:1000]})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind,lo=since):
            return [dict(json.loads(raw),_ts=ts,_event_id=eid) for eid,ts,raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid',(kind,lo,now))]
        kinds=['buyer_order','purchase','outcome','attempt','shop_review_observed','buyer_receipt_guard',
               'quote_comparison','buyer_counter','customer_incoming','customer_visit','public_result',
               'buyer_declined_terms','buyer_objective_skip','shop_settled','buyer_quote_race','contract_failure']
        schema={k:list((events(k) or [{}])[-1].keys()) for k in kinds}
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=? AND ts<?',(k,since,now)).fetchone()[0] for k in kinds}
        allsales=events('shop_settled',now-172800);sale_by_ledger={r['_event_id'].partition('settlement:')[2]:r for r in allsales}
        review_rows={}
        for r in events('shop_review_observed'):
            sale=sale_by_ledger.get(str(r.get('ledger')))
            if not sale:continue
            row=dict(r,sale={k:sale.get(k) for k in ('task','unit','sku','grade','price','cost','warranty_hours','paid_at')})
            row['kind']=catalog.get(sale.get('sku'),{}).get('kind')
            row['ratio']=sale.get('price',0)/max(1,catalog.get(sale.get('sku'),{}).get('list_price',0))
            key=str(r.get('ledger'))
            if key not in review_rows or r.get('identity_known'):review_rows[key]=row
        reviews=list(review_rows.values());low=[r for r in reviews if r['stars']<=3]
        def reason(r):
            t=r.get('review','').lower()
            return 'short_shelf_life' if ('needed' in t or 'asks' in t) and ('shelf' in t or 'hours' in t) else 'break_or_warranty' if 'broke' in t or 'broken' in t else 'other_or_unspecified'
        groups=defaultdict(list)
        for r in reviews:groups[(r['kind'],r['sale'].get('grade'))].append(r)
        review_groups=[{'kind':k[0],'grade':k[1],'n':len(rs),'mean':statistics.mean(r['stars'] for r in rs),
            'low':sum(r['stars']<=3 for r in rs),'known_accounts':len({r['author'] for r in rs if r.get('identity_known')})} for k,rs in groups.items()]
        accounts=defaultdict(list)
        for r in reviews:
            if r.get('identity_known'):accounts[r['author']].append(r)
        account_means=[{'author':a,'reviews':len(rs),'mean':statistics.mean(r['stars'] for r in rs),
            'low':sum(r['stars']<=3 for r in rs),'kind_mix':dict(Counter(r['kind'] for r in rs))} for a,rs in accounts.items()]
        orders={r['order']:r for r in events('buyer_order')};results={r['order']:r for r in events('market_result')};paid={r['order']:r for r in events('purchase')}
        attempts=events('attempt');quotes=events('quote_comparison');counters=events('buyer_counter');declines=events('buyer_declined_terms');skips=events('buyer_objective_skip')
        by_order=defaultdict(lambda:defaultdict(list))
        for k,rows in [('attempts',attempts),('quotes',quotes),('counters',counters),('declines',declines),('skips',skips),('guards',events('buyer_receipt_guard'))]:
            for r in rows:by_order[r.get('order')][k].append(r)
        buyer={}
        for kind in ('food','goods'):
            ids={o for o,r in orders.items() if r.get('kind')==kind};closed=ids&results.keys();won=closed&paid.keys();lost=closed-paid.keys()
            funnel=Counter();examples=[]
            for oid in lost:
                r=by_order[oid];aa=r['attempts'];qs=r['quotes']
                key=('receipt_guard' if r['guards'] else 'unusable_terms' if r['declines'] and not qs else
                     'no_quote' if not qs else 'objective_skip' if r['skips'] else
                     'negotiation_without_agreement' if any(a.get('kind')=='no_agreement' for a in aa) else 'other')
                funnel[key]+=1
                examples.append({'order':oid,'category':orders[oid].get('category'),'budget':orders[oid].get('budget'),
                    'failure':key,'quotes':[{k:q.get(k) for k in ('shop','sku','ask','feasible_at_ask')}|{'cap':q.get('assessment',{}).get('cap'),
                        'conditional':q.get('assessment',{}).get('conditional_terms'),'failure_stress':q.get('assessment',{}).get('failure')} for q in qs],
                    'counters':[{k:r.get(k) for k in ('shop','sku','ask','price')} for r in r['counters']],
                    'attempts':[{k:a.get(k) for k in ('shop','sku','kind','reason','seconds')} for a in aa]})
            kind_quotes=[q for q in quotes if q.get('order') in closed]
            first={}
            for r in counters:
                if r.get('order') in closed and r.get('ask',0)>0:first.setdefault((r['order'],r['shop']),r)
            bins=defaultdict(list)
            for (oid,shop),r in first.items():
                bin_name=str(min(19,int(r['price']/r['ask']*20)))
                outcome=paid.get(oid,{});success=outcome.get('shop')==shop
                bins[bin_name].append({'paid':success,'one_bid_price_or_better':success and outcome.get('price',math.inf)<=r['price']})
            shops=defaultdict(list)
            for q in kind_quotes:shops[q['shop']].append(q)
            shop_summary=[]
            for shop,qs in shops.items():
                qids={q['order'] for q in qs};p=[paid[o] for o in qids if o in paid and paid[o].get('shop')==shop]
                shop_summary.append({'shop':shop,'quoted_orders':len(qids),'paid':len(p),
                    'quotes':len(qs),'missing_terms':sum(bool(q.get('assessment',{}).get('conditional_terms')) for q in qs),
                    'above_safe_cap':sum(q['ask']>q['assessment']['cap'] for q in qs),
                    'median_ask_list':statistics.median(q['ask']/max(1,catalog.get(q['sku'],{}).get('list_price',0)) for q in qs)})
            buyer[kind]={'orders':len(ids),'closed':len(closed),'paid':len(won),'conversion':len(won)/max(1,len(closed)),
                'lost_funnel':dict(funnel),'quotes':len(kind_quotes),
                'missing_terms':sum(bool(q.get('assessment',{}).get('conditional_terms')) for q in kind_quotes),
                'above_safe_cap':sum(q['ask']>q['assessment']['cap'] for q in kind_quotes),
                'attempt_reasons':dict(Counter(a.get('reason') or a.get('kind') for a in attempts if a.get('order') in closed)),
                'counter_bins':[{'ratio_lower':int(b)/20,'n':len(rs),'paid':sum(r['paid'] for r in rs),
                    'one_bid_price_or_better':sum(r['one_bid_price_or_better'] for r in rs)} for b,rs in sorted(bins.items())],
                'shops':sorted(shop_summary,key=lambda r:r['quoted_orders'],reverse=True),'lost_examples':examples[:15]}
        # Examine the actual selected price action; a rational decline is not a protocol failure.
        decisions={r.get('decision'):r.get('chosen') for r in events('decision') if r.get('decision')}
        price_stats=defaultdict(Counter);skip_accepts=[]
        for eid,ts,raw in db.execute("SELECT id,ts,data FROM events WHERE kind='prediction' AND ts>=? AND ts<? AND json_extract(data,'$.role')='buyer' AND json_extract(data,'$.context.operation') IN ('price','final_purchase') ORDER BY ts",(since,now)):
            p=json.loads(raw);ctx=p.get('context',{});oid=(ctx.get('order') or {}).get('order');kind=orders.get(oid,{}).get('kind')
            if kind not in buyer:continue
            chosen=decisions.get(p.get('decision'));cs=p.get('candidates',[]);selected=next((c for c in cs if c.get('id')==chosen),{})
            accept=next((c for c in cs if c.get('id')=='accept'),None)
            price_stats[kind]['decisions']+=1;price_stats[kind][str(chosen).split(':')[0]]+=1
            if accept:
                price_stats[kind]['feasible_accept_present']+=1
                if chosen!='accept':
                    price_stats[kind]['feasible_accept_not_selected']+=1
                    if len(skip_accepts)<12:skip_accepts.append({'order':oid,'kind':kind,'chosen':chosen,'paid_later':oid in paid,
                        'accept':{k:accept.get(k) for k in ('price','expected_delta','objective_utility')},
                        'selected':{k:selected.get(k) for k in ('price','expected_delta','objective_utility','completion_probability')}})
        stockouts=events('stockout');shelf=[r for r in stockouts if r.get('reason')=='shelf_life']
    output={'at':iso(now),'since':iso(since),'rules_source':'logs/patch-rules-source-20261113T033005101119Z.json',
        'rule_snippets':snippets,'schema':schema,'counts':counts,
        'shop':{'joined_reviews':len(reviews),'low_reviews':len(low),'low_reasons':dict(Counter(reason(r) for r in low)),
            'review_groups':review_groups,'low_examples':low[:25],'account_means':sorted(account_means,key=lambda r:r['mean']),
            'known_account_reviews':sum(r.get('identity_known',False) for r in reviews),
            'shelf_stockout_count':len(shelf),'shelf_stockout_examples':shelf[:12]},
        'buyer':buyer,'price_decisions':{k:dict(v) for k,v in price_stats.items()},'accept_not_selected_examples':skip_accepts,
        'market_transactions':0,'model_calls':0,'policy_writes':0,
        'limitations':['Counter bid cohorts are observational and selected; success differences are not causal bid effects.',
            'Review text is a customer claim, not independent proof of a contract violation.',
            'Six-hour observed review account means do not reconstruct all historical official account means.',
            'Positive Saved/ALL estimates are hidden-cost scenarios; new goods still need mature failure outcomes.']}
    path=ROOT/'logs'/('v26-service-strategy-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(output,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({'window':[output['since'],output['at']],'shop':{k:output['shop'][k] for k in ('joined_reviews','low_reviews','low_reasons','review_groups','known_account_reviews','shelf_stockout_count')},
        'buyer':{k:{a:b for a,b in r.items() if a not in ('lost_examples','shops')} for k,r in buyer.items()},'price_decisions':output['price_decisions']},ensure_ascii=True))


if __name__=='__main__':main()
